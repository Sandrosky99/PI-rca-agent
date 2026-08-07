"""
pi_client.py — Cliente MCP para aveva-pi-mcp (Step 5 del agente RCA)

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


async def fetch_historical_data(variables: list[dict], detected_at_utc: str) -> dict:
    """Step 5: crea un bucket y consulta los piApiPath elegidos por el modelo en el Step 4.

    Args:
        variables: lista de {"element": ..., "piApiPath": ...} ya parseada del
                   JSON que devolvió el modelo en el Step 4.
        detected_at_utc: momento de detección de la alerta en UTC, ISO 8601
                         con 'Z' (context["detected_at_utc"] de
                         build_analysis_context) -- extremo final de la ventana.

    Returns:
        {"bucket_id": ..., "start_date": ..., "end_date": ..., "piApiPaths":
        [...consultados con éxito...], "excluded_piApiPaths": [...no
        resolvieron a WebID...], "data": {piApiPath: {"Content": {"Items":
        [{"Timestamp":..., "Value":...}, ...]}}, ...} -- dict parseado del
        JSON embebido en la respuesta de query_by_path (ver
        _parse_batch_json); si el parseo falla, el texto crudo tal cual.
        Listo para pasarse a agent.build_diagnosis_context() en el Step 6.

    Raises:
        PIQueryError: si la llamada al MCP Server falla, o si ningún
                      piApiPath resuelve a WebID.
    """
    pi_paths = _dedupe_pi_paths(variables)
    if not pi_paths:
        raise PIQueryError("La lista de variables del Step 4 no contiene ningún piApiPath.")

    end_dt = datetime.fromisoformat(detected_at_utc.replace("Z", "+00:00"))
    start_dt = end_dt - timedelta(hours=config.PI_LOOKBACK_HOURS)
    start_date = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_date = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    bucket_id = f"rca_{uuid.uuid4().hex[:8]}"

    try:
        async with stdio_client(_SERVER_PARAMS) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                await session.call_tool(
                    "create_timeseries_bucket",
                    {
                        "bucket_id": bucket_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "interval_value": config.PI_QUERY_INTERVAL_VALUE,
                        "interval_unit": config.PI_QUERY_INTERVAL_UNIT,
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
        "piApiPaths": used_paths,
        "excluded_piApiPaths": excluded_paths,
        "data": data,
    }
