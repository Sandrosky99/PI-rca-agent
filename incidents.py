"""
incidents.py — Registro de incidentes: deduplicación y persistencia

¿Qué problema resuelve?
  Dos problemas de fiabilidad que comparten el mismo estado:

  1. DEDUPLICACIÓN. PI puede enviar varias notificaciones del mismo incidente.
     Está documentado en real: una Notification Rule con NonrepetitionInterval=0
     genera una notificación en CADA evaluación periódica de la alarma, no solo
     al cruzar el umbral. Sin guarda, cada una lanzaría un análisis completo con
     su coste de LLM y sus subprocesos MCP, y produciría diagnósticos
     potencialmente contradictorios sobre el mismo suceso.

     Esto se arregla ANTES en PI (NonrepetitionInterval y el deadband del
     análisis que calcula el KPI). Lo de aquí es una red de seguridad, no el
     mecanismo: 30 líneas frente al coste de que PI se desconfigure.

  2. PERSISTENCIA. El análisis corre como background task en memoria. Si el
     proceso muere después de haber respondido 202 a PI, el trabajo se pierde
     sin dejar rastro.

¿Por qué ficheros y no SQLite?
  Porque el sistema de ficheros ya da la primitiva que hace falta:

      open(ruta, "x")   -> creación exclusiva, falla si ya existe

  Eso ES la reserva del incidente, atómica y sin ventana de carrera entre
  "consultar" e "insertar" -- y sigue funcionando si algún día se levantan
  varios workers de uvicorn, donde un diccionario en memoria no serviría.

  Además, el fichero del incidente es ya el registro del caso: cuando haya
  revisión humana, se le añaden los campos de revisión al mismo JSON sin
  diseñar nada nuevo. Y en una demo se puede abrir y leer, que un .db no.

  Lo que se pierde: no hay consultas ni agregación. Para las métricas de
  evaluación habrá que leer todos los ficheros y agregarlos en Python -- con
  unas pocas alertas al día es trivial. Migrar a SQLite después es mecánico:
  cada fichero es una fila.

Identidad de un incidente
  NO es el payload completo. Los campos se reparten en tres grupos:

    FIJOS -- identifican al elemento y a la alarma. Entran en la clave:
        Asset, KPIName, LimitThresholdType, Subsystem, System, Plant

    VOLÁTIL -- "KPI" es el valor actual y cambia entre re-evaluaciones de la
        misma alarma (58.4 -> 58.1 -> "No Result"). Comparar el payload entero
        no deduplicaría nada. Fuera de la clave. ("Limit" también queda fuera:
        podría recalcularse.)

    CERCANO -- "StartTime" no se repite exacto, pero sí queda próximo: la
        re-notificación del mismo suceso llega unos minutos después. No sirve
        como igualdad; sirve como proximidad.

  De ahí las dos capas, en orden de importancia:

    1. ENFRIAMIENTO (la que hace el trabajo). Misma identidad dentro de
       INCIDENT_COOLDOWN_MINUTES -> mismo incidente, aunque el StartTime sea
       otro. Es la que corta la notificación que llega dos minutos después.

    2. Clave exacta (red secundaria). Misma identidad Y mismo StartTime al
       carácter: es literalmente el mismo POST, reenviado. Solo dispara si PI
       repite el envío sin recalcular el campo.

  El nombre del fichero refleja las dos: <hash de los fijos>__<StartTime>.
"""

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
import observability

log = logging.getLogger(__name__)

# Estados por los que pasa un incidente.
RECIBIDO = "recibido"        # reservado, aún no analizado
ANALIZANDO = "analizando"    # análisis en curso
FINALIZADO = "finalizado"    # análisis completo, con diagnóstico
FALLIDO = "fallido"          # el análisis se cortó de forma controlada
INTERRUMPIDO = "interrumpido"  # el proceso murió a mitad (detectado al arrancar)
PAUSADO = "pausado"          # recibido con el workflow deshabilitado (WORKFLOW_ENABLED=false)

_EN_CURSO = (RECIBIDO, ANALIZANDO)


def _directorio() -> Path:
    d = Path(config.INCIDENTS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


# Campos que identifican al sujeto de la alarma. Son los FIJOS: describen qué
# elemento es y qué alarma suya ha saltado, no en qué estado está.
#
# Se incluye la jerarquía completa (Subsystem/System/Plant) aunque Asset ya sea
# único en el AF: describe el elemento y protege de una colisión de nombres
# entre plantas.
#
# Queda fuera "KPI" -- es el valor actual y cambia entre re-evaluaciones de la
# misma alarma (58.4 -> 58.1 -> "No Result"). Y queda fuera "Limit", que podría
# recalcularse. LimitThresholdType sí entra: una alarma High y una Low sobre el
# mismo KPI son alarmas distintas.
_CAMPOS_IDENTIDAD = ("Asset", "KPIName", "LimitThresholdType", "Subsystem", "System", "Plant")


def _identidad(payload: dict) -> str:
    """Hash corto del sujeto de la alarma (no de su valor actual).

    Cada campo se normaliza (espacios colapsados, sin distinguir mayúsculas)
    antes de mezclarlo. Con una clave de seis campos, basta con que uno llegue
    con un espacio de más para que el hash cambie y la deduplicación falle
    justo cuando tenía que actuar. La normalización cuesta nada y quita ese
    modo de fallo.
    """
    partes = [
        " ".join(str(payload.get(k, "")).split()).casefold()
        for k in _CAMPOS_IDENTIDAD
    ]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()[:12]


def _marca_temporal(payload: dict) -> str:
    """Parte variable del nombre, a partir de StartTime.

    Si PI no lo envía o llega con un tipo raro, se usa la hora de recepción:
    el incidente queda registrado igualmente, pero solo lo protegerá el
    enfriamiento, no la clave exacta.
    """
    crudo = payload.get("StartTime")
    if isinstance(crudo, str) and crudo.strip():
        limpio = re.sub(r"[^0-9A-Za-z]", "", crudo)
        if limpio:
            return limpio

    # Sin StartTime no puede haber clave exacta, así que este marcador solo
    # tiene que ser ÚNICO. Se le añade un sufijo aleatorio en vez de fiarlo
    # todo al reloj: en Windows dos llamadas seguidas pueden devolver el mismo
    # microsegundo, y entonces la segunda notificación se rechazaría como
    # duplicado exacto sin serlo. Lo detectó test_incidents al fallar de forma
    # intermitente (2026-09-07).
    log.warning("Incidente sin 'StartTime' utilizable; se usa la hora de recepción.",
                extra={"startTimeRecibido": repr(crudo)})
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"recibido{marca}{uuid.uuid4().hex[:6]}"


def _escribir(ruta: Path, registro: dict) -> None:
    """Escribe el registro de forma atómica (temporal + os.replace).

    os.replace es atómico también en Windows dentro del mismo volumen, así que
    nunca queda un fichero a medio escribir si el proceso muere escribiendo.
    """
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ruta)


def _leer(ruta: Path) -> dict | None:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("No se pudo leer el incidente %s: %s", ruta.name, exc)
        return None


def _duplicado_por_enfriamiento(identidad: str, ahora: datetime) -> dict | None:
    """Busca un incidente del mismo sujeto dentro de la ventana de enfriamiento.

    Bloquea con independencia de cómo terminase el anterior: si la desviación
    sigue ahí media hora después, re-analizarla da la misma respuesta, y si el
    anterior falló, reintentar en bucle cada pocos minutos tampoco ayuda. Los
    fallos quedan visibles en el log.
    """
    if config.INCIDENT_COOLDOWN_MINUTES <= 0:
        return None
    limite = ahora - timedelta(minutes=config.INCIDENT_COOLDOWN_MINUTES)
    for ruta in _directorio().glob(f"{identidad}__*.json"):
        registro = _leer(ruta)
        if not registro:
            continue
        try:
            recibido = datetime.fromisoformat(registro["recibido_en"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError):
            continue
        if recibido >= limite:
            return registro
    return None


def claim(payload: dict) -> dict | None:
    """Reserva el incidente. Devuelve su registro, o None si es un duplicado.

    Función SÍNCRONA a propósito: no tiene ningún await dentro, así que dentro
    de un mismo proceso asyncio la comprobación y la reserva no pueden
    entrelazarse con otra notificación. Entre procesos, la reserva de la clave
    exacta la garantiza open(..., "x").

    La comprobación de enfriamiento sí tiene una ventana de carrera teórica
    entre procesos (dos StartTime distintos llegando a la vez); la consecuencia
    sería un análisis de más, no un dato corrupto.
    """
    ahora = datetime.now(timezone.utc)
    identidad = _identidad(payload)

    previo = _duplicado_por_enfriamiento(identidad, ahora)
    if previo is not None:
        observability.audit(
            "incident.claim", {"identity": identidad, "asset": payload.get("Asset")},
            status="BLOCKED",
            detail=f"enfriamiento {config.INCIDENT_COOLDOWN_MINUTES} min; previo {previo.get('id')}",
        )
        log.warning(
            "Notificación descartada por enfriamiento (%d min): '%s' de '%s' ya se registró "
            "el %s y quedó en estado '%s'. No se lanza un análisis nuevo.",
            config.INCIDENT_COOLDOWN_MINUTES, previo.get("kpi_name"), previo.get("asset"),
            previo.get("recibido_en"), previo.get("estado"),
        )
        return None

    ruta = _directorio() / f"{identidad}__{_marca_temporal(payload)}.json"
    registro = {
        "id": ruta.stem,
        "asset": payload.get("Asset"),
        "kpi_name": payload.get("KPIName"),
        "threshold_type": payload.get("LimitThresholdType"),
        "start_time": payload.get("StartTime"),
        "recibido_en": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actualizado_en": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "estado": RECIBIDO,
        "payload": payload,
        "diagnostico": None,
    }

    # Auditoría ANTES de crear el registro (spec §2.2): esto persiste estado.
    observability.audit(
        "incident.claim",
        {"incidentId": ruta.stem, "asset": registro["asset"], "kpiName": registro["kpi_name"]},
    )
    try:
        # Creación exclusiva: si el fichero ya existe, es exactamente la misma
        # notificación (mismo sujeto y mismo StartTime). Esta es la reserva
        # atómica; no hay hueco entre comprobar y crear.
        with open(ruta, "x", encoding="utf-8") as f:
            json.dump(registro, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        observability.audit(
            "incident.claim", {"incidentId": ruta.stem},
            status="BLOCKED", detail="duplicado exacto (mismo sujeto y StartTime)",
        )
        log.warning(
            "Notificación duplicada exacta (mismo activo, KPI y StartTime): %s. "
            "No se lanza un análisis nuevo.", ruta.stem,
        )
        return None

    log.info("Incidente registrado.", extra={
        "incidentId": ruta.stem, "asset": registro["asset"], "kpiName": registro["kpi_name"]})
    return registro


def mark(registro: dict, estado: str, diagnostico: dict | None = None,
         trace: dict | None = None) -> None:
    """Actualiza el estado del incidente en disco. Nunca propaga excepciones:
    un fallo escribiendo el registro no debe tumbar el análisis en curso."""
    ruta = _directorio() / f"{registro['id']}.json"
    observability.audit(
        "incident.mark", {"incidentId": registro["id"], "estado": estado},
    )
    registro["estado"] = estado
    registro["actualizado_en"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if diagnostico is not None:
        registro["diagnostico"] = diagnostico
    if trace:
        # Prompts y respuestas del modelo. Aquí y no en el log: con el logging
        # estructurado se truncarían a 200 caracteres, y este fichero es el
        # registro del caso -- base/software-spec §1.5 (procedencia del prompt).
        registro["trace"] = trace
    try:
        _escribir(ruta, registro)
    except OSError as exc:
        log.error("No se pudo actualizar el incidente %s a '%s': %s", registro["id"], estado, exc)


def sweep_interrupted() -> int:
    """Marca como interrumpidos los incidentes que quedaron a medias.

    Se llama al arrancar. Si hay un incidente en 'recibido' o 'analizando',
    significa que el proceso murió durante su análisis: nadie lo va a terminar.

    NO se relanzan automáticamente a propósito: si uno reventó por un payload
    problemático, un relanzamiento en cada arranque sería un bucle. Marcarlos y
    avisar ya es mucho mejor que el silencio de antes; relanzar con un contador
    de intentos puede añadirse después.
    """
    total = 0
    for ruta in _directorio().glob("*.json"):
        registro = _leer(ruta)
        if not registro or registro.get("estado") not in _EN_CURSO:
            continue
        registro["estado"] = INTERRUMPIDO
        registro["actualizado_en"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            _escribir(ruta, registro)
            total += 1
            log.warning(
                "Incidente interrumpido por un reinicio: %s ('%s' de '%s', recibido el %s). "
                "No se relanza automáticamente.",
                registro["id"], registro.get("kpi_name"), registro.get("asset"),
                registro.get("recibido_en"),
            )
        except OSError as exc:
            log.error("No se pudo marcar como interrumpido %s: %s", ruta.name, exc)
    return total
