"""
pi_client.py — Cliente MCP para aveva-pi-mcp (Step 5 del workflow RCA)

¿Qué hace este fichero?
  Lanza aveva-pi-mcp como subproceso (protocolo MCP sobre stdio, igual que
  graph_client.py con afkg-graph-mcp) y usa sus tools create_timeseries_bucket
  + query_by_path para obtener los datos históricos de las variables que el
  modelo eligió en el Step 4, en la ventana de tiempo y resolución fijadas
  (ver CLAUDE.md, "Step 5"):
    - Ventana: [StartTime - PI_LOOKBACK_HOURS, StartTime] -- se mira hacia
      atrás desde la detección de la alerta, nunca hacia adelante (son
      desviaciones de KPI, degradación gradual, no picos instantáneos).
    - Resolución: PI_QUERY_INTERVAL_VALUE/PI_QUERY_INTERVAL_UNIT, uniforme
      para todas las variables -- un único bucket y una única llamada a
      query_by_path, para que los valores sean correlacionables directamente
      por timestamp.
"""

import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import config
import observability

log = logging.getLogger(__name__)

_SERVER_PARAMS = StdioServerParameters(
    command="uv",
    args=[
        "run", "--directory", config.AVEVA_PI_MCP_DIR,
        "python", "src/aveva_pi_mcp/server.py",
    ],
)

# aveva-pi-mcp resuelve TODOS los piApiPath a WebID antes de consultar nada;
# si UNO solo no resuelve (server.py: resolve_attribute_webids), descarta el
# batch entero y devuelve este texto -- ningún piApiPath obtiene datos, ni
# siquiera los que sí existen. Visto en producción el 2026-07-28: el modelo
# eligió un atributo presente en el grafo AF pero ya no en PI Web API
# ("Mechanic Power"). _query_with_retry detecta este patrón, excluye los
# paths señalados como culpables y reintenta solo con el resto.
_WEBID_ERROR_PREFIX = "❌ Error al resolver WebIDs:"
_FAILED_PATH_RE = re.compile(r"Error en '(.*?)':")


class PIQueryError(Exception):
    """No se pudo obtener el histórico de PI (bucket o query_by_path fallaron)."""


# Resoluciones admisibles, de más fina a más gruesa. Se usa una escalera de
# valores "redondos" en vez de calcular un intervalo exacto porque el resultado
# tiene que ser legible para quien lea el prompt del Step 6 o el log: "cada 2
# horas" se entiende, "cada 137 minutos" no.
_INTERVAL_LADDER: list[tuple[int, str]] = [
    (1, "minutes"), (5, "minutes"), (10, "minutes"), (15, "minutes"),
    (30, "minutes"), (1, "hours"), (2, "hours"), (3, "hours"),
    (6, "hours"), (12, "hours"), (1, "days"),
]

_UNIT_MINUTES = {"minutes": 1, "hours": 60, "days": 1440}


def derive_interval(lookback_hours: int, target_points: int) -> tuple[int, str]:
    """Elige la resolución más fina que mantenga la serie por debajo de target_points.

    Ventana y resolución tienen que moverse juntas: ampliar la ventana sin
    tocar la resolución multiplica el tamaño del prompt del Step 6 por el mismo
    factor, y son decenas de variables. Con el objetivo por defecto (120
    puntos), 24 h siguen saliendo a 15 min -- exactamente la resolución fija
    que había antes, así que la primera pasada no cambia -- y 7 días salen a
    2 h, que son menos puntos que la ventana corta.

    Si ni la resolución más gruesa de la escalera baja del objetivo, devuelve
    esa (1 día): es preferible una serie larga y gruesa a no poder consultar.
    """
    for value, unit in _INTERVAL_LADDER:
        points = lookback_hours * 60 / (value * _UNIT_MINUTES[unit])
        if points <= target_points:
            return value, unit
    return _INTERVAL_LADDER[-1]


def _window_id(hours: int) -> str:
    """Identificador corto y legible de una ventana: '24h', '7d'."""
    if hours < 48 or hours % 24:
        return f"{hours}h"
    return f"{hours // 24}d"


def window_menu(hours_ladder: list[int], target_points: int) -> list[dict]:
    """Construye el catálogo de pares ventana/resolución que se le ofrecen al modelo.

    Un catálogo cerrado en vez de un número libre de horas tiene dos ventajas:
    elimina de raíz las respuestas inservibles (horas como texto, o que no
    cambian nada), y sobre todo **le enseña al modelo el coste de lo que
    pide**. Antes solicitaba horas y la resolución cambiaba por debajo sin que
    lo supiera; ahora ve el intercambio entre histórico y detalle, y decide con
    esa información delante.

    Cada opción se deriva de derive_interval(), así que el catálogo y el
    cálculo real de la resolución no pueden desalinearse.

    Returns:
        Lista de {"id", "hours", "interval": (valor, unidad), "points"},
        ordenada de menor a mayor ventana y sin duplicados.
    """
    menu: list[dict] = []
    for hours in sorted(set(hours_ladder)):
        if hours <= 0:
            continue
        value, unit = derive_interval(hours, target_points)
        menu.append({
            "id": _window_id(hours),
            "hours": hours,
            "interval": (value, unit),
            "points": int(hours * 60 / (value * _UNIT_MINUTES[unit])),
        })
    return menu


def _parse_batch_json(raw_text: str) -> dict:
    """Extrae el diccionario {piApiPath: {...}} embebido en el texto que
    devuelve query_by_path (aveva-pi-mcp).

    La tool antepone una cabecera legible ("📊 Consulta Batch...", "WebIDs
    resueltos...", "Status: NNN") antes del JSON real de la respuesta (ver
    query_attributes_batch en aveva_pi_mcp/server.py) -- json.loads(raw_text)
    a secas siempre falla por esa cabecera. Además, PI Web API's /batch puede
    devolver 207 Multi-Status (éxito parcial) en vez de 200 aunque cada
    sub-respuesta interna sea válida; aveva-pi-mcp lo etiqueta igualmente
    como "❌ Error en la petición" pese a que el JSON que sigue es utilizable,
    así que se prueban ambos marcadores en vez de asumir que solo el de
    éxito (200) puede traer datos reales."""
    for marker in ("Petición exitosa\n\n", "Error en la petición\n"):
        idx = raw_text.find(marker)
        if idx == -1:
            continue
        try:
            return json.loads(raw_text[idx + len(marker):])
        except json.JSONDecodeError:
            continue
    return {}


def _dedupe_pi_paths(variables: list[dict]) -> list[str]:
    """Extrae los piApiPath de la lista del Step 4 y elimina duplicados.

    El modelo a veces repite el mismo piApiPath bajo distintos "elementos"
    lógicos (visto en pruebas reales, p.ej. el mismo Hydraulic Effiency
    referenciado dos veces) -- no tiene sentido pedirlo dos veces.
    Preserva el orden de aparición.
    """
    seen: set[str] = set()
    paths: list[str] = []
    for var in variables:
        path = var.get("piApiPath")
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


async def _query_with_retry(session: ClientSession, pi_paths: list[str], bucket_id: str) -> tuple[str, list[str], list[str]]:
    """Llama a query_by_path; si falla por piApiPath que no resuelven a WebID,
    los excluye y reintenta con el resto. Cada iteración descarta al menos un
    path, así que termina en como mucho len(pi_paths) intentos.

    Returns:
        (raw_text, piApiPaths_consultados, piApiPaths_excluidos)
    """
    remaining = list(pi_paths)
    excluded: list[str] = []

    while remaining:
        result = await session.call_tool(
            "query_by_path", {"piApiPath": remaining, "bucket_id": bucket_id},
        )
        raw_text = result.content[0].text
        if not raw_text.lstrip().startswith(_WEBID_ERROR_PREFIX):
            return raw_text, remaining, excluded

        failed = [p for p in _FAILED_PATH_RE.findall(raw_text) if p in remaining]
        if not failed:
            raise PIQueryError(f"aveva-pi-mcp devolvió un error no reconocido: {raw_text}")

        for p in failed:
            remaining.remove(p)
        excluded.extend(failed)
        log.warning(
            "query_by_path: %d piApiPath no resuelven a WebID, se excluyen y se reintenta: %s",
            len(failed), failed,
        )

    raise PIQueryError(f"Ningún piApiPath resolvió a WebID (excluidos: {excluded})")


async def fetch_historical_data(
    variables: list[dict],
    detected_at_utc: str,
    lookback_hours: int | None = None,
    interval: tuple[int, str] | None = None,
) -> dict:
    """Step 5: crea un bucket y consulta los piApiPath elegidos por el modelo en el Step 4.

    Args:
        variables: lista de {"element": ..., "piApiPath": ...} ya parseada del
                   JSON que devolvió el modelo en el Step 4.
        detected_at_utc: momento de detección de la alerta en UTC, ISO 8601
                         con 'Z' (context["detected_at_utc"] de
                         build_analysis_context) -- extremo final de la ventana.
        lookback_hours: horas hacia atrás desde detected_at_utc. Por defecto
                        config.PI_LOOKBACK_HOURS. workflow.py lo sobrescribe
                        cuando el modelo pide más histórico en el Step 6.
        interval: (valor, unidad) de la resolución. Por defecto la configurada
                  en PI_QUERY_INTERVAL_*. En las ampliaciones, workflow.py pasa
                  aquí el resultado de derive_interval().

    Returns:
        {"bucket_id": ..., "start_date": ..., "end_date": ...,
        "lookback_hours": ..., "interval": (valor, unidad), "piApiPaths":
        [...consultados con éxito...], "excluded_piApiPaths": [...no
        resolvieron a WebID...], "data": {piApiPath: {"Content": {"Items":
        [{"Timestamp":..., "Value":...}, ...]}}, ...} -- dict parseado del
        JSON embebido en la respuesta de query_by_path (ver
        _parse_batch_json); si el parseo falla, el texto crudo tal cual.
        Listo para pasarse a workflow.build_diagnosis_context() en el Step 6.

    Raises:
        PIQueryError: si la llamada al MCP Server falla, o si ningún
                      piApiPath resuelve a WebID.
    """
    pi_paths = _dedupe_pi_paths(variables)
    if not pi_paths:
        raise PIQueryError("La lista de variables del Step 4 no contiene ningún piApiPath.")

    if lookback_hours is None:
        lookback_hours = config.PI_LOOKBACK_HOURS
    if interval is None:
        interval = (config.PI_QUERY_INTERVAL_VALUE, config.PI_QUERY_INTERVAL_UNIT)
    interval_value, interval_unit = interval

    end_dt = datetime.fromisoformat(detected_at_utc.replace("Z", "+00:00"))
    start_dt = end_dt - timedelta(hours=lookback_hours)
    start_date = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_date = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    bucket_id = f"rca_{uuid.uuid4().hex[:8]}"

    try:
        async with stdio_client(_SERVER_PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # Único punto del workflow que muta estado FUERA del proceso:
                # crea un bucket en aveva-pi-mcp. Se audita antes de ejecutarlo
                # (observability-spec §2.2). El resto de llamadas a PI son de
                # solo lectura y no requieren auditoría.
                observability.audit("create_timeseries_bucket", {
                    "bucketId": bucket_id, "startDate": start_date, "endDate": end_date,
                    "interval": f"{interval_value} {interval_unit}", "paths": len(pi_paths),
                })
                await session.call_tool(
                    "create_timeseries_bucket",
                    {
                        "bucket_id": bucket_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "interval_value": interval_value,
                        "interval_unit": interval_unit,
                    },
                )

                raw_text, used_paths, excluded_paths = await _query_with_retry(session, pi_paths, bucket_id)
    except PIQueryError:
        raise
    except Exception as exc:
        raise PIQueryError(f"Fallo consultando aveva-pi-mcp: {exc}") from exc

    data = _parse_batch_json(raw_text)
    if not data:
        log.warning("No se pudo extraer el JSON de la respuesta de query_by_path; se guarda el texto crudo.")
        data = raw_text

    return {
        "bucket_id": bucket_id,
        "start_date": start_date,
        "end_date": end_date,
        "lookback_hours": lookback_hours,
        "interval": (interval_value, interval_unit),
        "piApiPaths": used_paths,
        "excluded_piApiPaths": excluded_paths,
        "data": data,
    }
