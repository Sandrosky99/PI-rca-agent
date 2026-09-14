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

# Estados desde los que un incidente SÍ se puede volver a reservar: en los dos
# la causa fue externa al análisis y ya no está, así que reenviar la
# notificación tiene todas las papeletas de funcionar. El resto siguen
# bloqueados. La justificación de cada uno, en claim().
_RECLAMABLES = (INTERRUMPIDO, PAUSADO)


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


# Un id de incidente es siempre "<identidad>__<marca>": la identidad es un
# resumen hexadecimal y la marca sale de re.sub(r"[^0-9A-Za-z]", "", StartTime),
# con un sufijo hexadecimal cuando hay colisión. Así que estos son todos los
# caracteres que puede llevar.
#
# Importa porque el id llega desde la URL en GET /incidentes/{id}: sin esta
# comprobación, un id con ".." o con separadores de ruta serviría para leer
# cualquier fichero de la máquina. Se valida el formato en vez de intentar
# limpiarlo -- lo segundo es una carrera que se pierde.
_ID_VALIDO = re.compile(r"^[A-Za-z0-9_]{1,120}$")


def _resumen(registro: dict) -> dict:
    """Los campos que necesita la vista de conjunto, y ninguno más.

    Deja fuera el payload entero, el trace y el texto de las causas: la lista
    puede tener docenas de incidentes y solo necesita identificarlos y decir en
    qué estado están. El detalle se pide por separado.
    """
    diagnostico = registro.get("diagnostico") or {}
    return {
        "id": registro.get("id"),
        "asset": registro.get("asset"),
        "kpiName": registro.get("kpi_name"),
        "thresholdType": registro.get("threshold_type"),
        "startTime": registro.get("start_time"),
        "recibidoEn": registro.get("recibido_en"),
        "actualizadoEn": registro.get("actualizado_en"),
        "estado": registro.get("estado"),
        "numCausas": len(diagnostico.get("root_causes", [])),
        "intentos": registro.get("intentos", 1),
    }


# Cuánto tiempo sigue apareciendo un incidente FALLIDO en la vista por defecto.
#
# Fijo a propósito, sin variable de entorno. Un fallo técnico interesa mientras
# alguien pueda hacer algo con él -- mirarlo, reintentarlo cuando lo haya --, y
# pasado ese rato solo estorba: se acumula en la lista de un monitor donde lo
# que importa son las alertas vivas. Sigue estando, y se ve pidiendo el estado
# 'fallido' expresamente o ampliando el rango de fechas; lo que deja de hacer es
# competir por la atención.
#
# 12 h y no 6: un turno completo. Con 6 h, un fallo de la madrugada ya no estaba
# cuando entraba el turno de mañana, que es justo quien podía hacer algo.
_HORAS_FALLIDO_EN_VISTA = 12


def listar(desde: str | None = None, hasta: str | None = None,
           limite: int | None = None, estado: str | None = None) -> dict:
    """Resumen de incidentes, del más reciente al más antiguo, acotado.

    Sin acotar esto no sirve: no se borra ningún incidente nunca, así que con
    veinte alertas al día son 7.300 al año, y una lista de 7.300 elementos en un
    monitor de sala de control no la lee nadie.

    Filtra por 'recibido_en' y no por 'start_time' a propósito: el operario
    razona sobre cuándo se enteró el sistema, que es lo que ordena la lista.

    Devuelve también cuántos coincidían ANTES de aplicar el límite, para que la
    pantalla pueda decir "50 de 213" en vez de mentir por omisión.
    """
    resumenes = []
    try:
        for ruta in _directorio().glob("*.json"):
            registro = _leer(ruta)
            if registro:
                resumenes.append(_resumen(registro))
    except OSError as exc:
        log.warning("No se pudo recorrer el registro de incidentes", extra={"errorMsg": str(exc)})

    # Las marcas de tiempo son ISO 8601 en UTC con formato fijo, así que se
    # comparan como cadenas sin necesidad de parsearlas.
    if desde:
        resumenes = [r for r in resumenes if (r.get("recibidoEn") or "") >= desde]
    if hasta:
        resumenes = [r for r in resumenes if (r.get("recibidoEn") or "") <= hasta]

    # Recuento por estado ANTES de filtrar y de recortar. Es lo que alimenta las
    # pastillas de la pantalla, y cada una dice exactamente lo que saldría al
    # pulsarla: por eso se cuenta sin aplicar el envejecimiento de los fallidos,
    # que solo rige en la vista por defecto.
    recuento: dict[str, int] = {}
    for r in resumenes:
        e = r.get("estado", "desconocido")
        recuento[e] = recuento.get(e, 0) + 1

    # Cuántos saldrían sin filtrar por estado -- o sea, con el envejecimiento de
    # los fallidos aplicado. Es lo que debe decir la pastilla "Todos", y no
    # coincide con sumar el resto: los fallidos viejos cuentan en su pastilla
    # pero no aquí, que es justo lo que pasaría al pulsar una u otra.
    _corte = (datetime.now(timezone.utc)
              - timedelta(hours=_HORAS_FALLIDO_EN_VISTA)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recuento_sin_filtro = sum(
        1 for r in resumenes
        if r.get("estado") != FALLIDO or (r.get("recibidoEn") or "") >= _corte
    )

    if estado:
        resumenes = [r for r in resumenes if r.get("estado") == estado]
    else:
        # Los fallidos viejos salen de la vista por defecto. Solo cuando NO se
        # ha pedido un estado concreto: si alguien filtra por 'fallido' es que
        # los está buscando, y esconderlos entonces sería absurdo.
        corte = (datetime.now(timezone.utc)
                 - timedelta(hours=_HORAS_FALLIDO_EN_VISTA)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resumenes = [r for r in resumenes
                     if r.get("estado") != FALLIDO or (r.get("recibidoEn") or "") >= corte]

    resumenes.sort(key=lambda r: r.get("recibidoEn") or "", reverse=True)

    # El límite se aplica AQUÍ, después del filtro por estado. Al revés --como
    # estaba-- el servidor recortaba a los N más recientes de todos y luego el
    # navegador descartaba de esos los que no eran del estado pedido: con
    # "listo" y máximo 2 podían salir cero. Lo que se pide son los N más
    # recientes DE LO FILTRADO.
    coincidentes = len(resumenes)
    if limite and limite > 0:
        resumenes = resumenes[:limite]
    return {
        "incidentes": resumenes,
        "mostrados": len(resumenes),
        "coincidentes": coincidentes,
        "truncado": coincidentes > len(resumenes),
        "recuento": recuento,
        "recuentoSinFiltro": recuento_sin_filtro,
    }


def leer_por_id(incidente_id: str) -> dict | None:
    """Un incidente completo, o None si no existe o el id no es válido.

    Se devuelve SIN el trace. Medido sobre un incidente real, el trace es el
    98,9 % del fichero (501 KB de 507 KB) y son los prompts enviados al modelo y
    sus respuestas en crudo: nada que pinte en una pantalla de sala de control,
    y mucho que transferir en cada refresco.
    """
    if not _ID_VALIDO.match(incidente_id or ""):
        log.warning("Id de incidente con formato no válido; se rechaza.",
                    extra={"incidentId": (incidente_id or "")[:60]})
        return None
    registro = _leer(_directorio() / f"{incidente_id}.json")
    if registro is None:
        return None
    return {k: v for k, v in registro.items() if k != "trace"}


def _duplicado_por_enfriamiento(identidad: str, ahora: datetime) -> dict | None:
    """Busca un incidente del mismo sujeto dentro de la ventana de enfriamiento.

    Bloquea aunque el anterior fallara: si la desviación sigue ahí veinte
    minutos después, re-analizarla da la misma respuesta, y si el análisis falló
    por algo suyo, reintentarlo cada pocos minutos daría el mismo error y
    quemaría llamadas al modelo.

    Las excepciones son INTERRUMPIDO y PAUSADO. Ver la nota en claim(): en los
    dos la causa fue externa y ya no está, así que un reintento tiene todas las
    papeletas de funcionar. No deben quedar bloqueados.
    """
    if config.INCIDENT_COOLDOWN_MINUTES <= 0:
        return None
    limite = ahora - timedelta(minutes=config.INCIDENT_COOLDOWN_MINUTES)
    for ruta in _directorio().glob(f"{identidad}__*.json"):
        registro = _leer(ruta)
        if not registro or registro.get("estado") in _RECLAMABLES:
            continue
        try:
            recibido = datetime.fromisoformat(registro["recibido_en"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError):
            continue
        if recibido >= limite:
            return registro
    return None


def contar_por_estado() -> dict[str, int]:
    """Recuento de incidentes por estado, para exponerlo en /health.

    No es una interfaz de usuario, pero convierte en visible algo que hasta
    ahora solo aparecía en un WARNING del arranque: que hay incidentes
    atascados. Cualquier monitorización que haga polling del endpoint lo ve.

    Solo devuelve números -- ni nombres de activo ni datos de planta -- porque
    /health no está autenticado.
    """
    recuento: dict[str, int] = {}
    try:
        for ruta in _directorio().glob("*.json"):
            registro = _leer(ruta)
            if registro:
                estado = registro.get("estado", "desconocido")
                recuento[estado] = recuento.get(estado, 0) + 1
    except OSError as exc:
        log.warning("No se pudo recorrer el registro de incidentes", extra={"errorMsg": str(exc)})
    return recuento


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
        # Hay incidentes que SÍ se pueden volver a reservar (2026-09-07 y -09-11).
        #
        # Motivo: la deduplicación y la persistencia, construidas por separado,
        # se estorbaban. El fichero que garantiza no perder el incidente era el
        # mismo que impedía reintentarlo: open(ruta,"x") fallaba siempre, así
        # que un análisis cortado a mitad no se podía relanzar NUNCA, ni
        # reenviando la notificación ni esperando a que pasara el enfriamiento.
        # La única salida era borrar el fichero a mano, y eso no se le puede
        # pedir a nadie -- menos aún sin una interfaz desde la que verlo.
        #
        # Se distingue por el estado en que quedó:
        #   INTERRUMPIDO -> la causa fue externa (murió el proceso) y ya no
        #       está. Se re-reserva. Como la alarma sigue activa en PI, en
        #       cuanto vuelva a evaluar la regla y reenvíe, se recoge solo:
        #       la vía de recuperación es la vía normal, sin herramientas.
        #   PAUSADO      -> llegó con el interruptor de parada echado, así que
        #       el análisis NUNCA se ejecutó. El argumento es aún más fuerte que
        #       en INTERRUMPIDO: allí queda la duda de si fue el propio payload
        #       el que tumbó el proceso, y aquí no hay ninguna -- nadie llegó a
        #       mirarlo. Bloquearlo dejaba atrapada toda alerta recibida durante
        #       una parada en caliente, que es justo lo contrario de para lo que
        #       se registran. Lo destapó la prueba del interruptor del
        #       2026-09-08, que dejó un incidente sin salida.
        #   FALLIDO      -> el análisis se ejecutó y falló por algo suyo.
        #       Reintentar daría el mismo error, así que se sigue bloqueando.
        previo = _leer(ruta)
        estado_previo = (previo or {}).get("estado")
        if previo is not None and estado_previo in _RECLAMABLES:
            registro["intentos"] = previo.get("intentos", 1) + 1
            _escribir(ruta, registro)
            observability.audit(
                "incident.claim", {"incidentId": ruta.stem, "intento": registro["intentos"]},
                detail=f"re-reserva de un incidente en '{estado_previo}'",
            )
            log.warning(
                "Se re-lanza un incidente que había quedado en '%s'.", estado_previo,
                extra={"incidentId": ruta.stem, "asset": registro["asset"],
                       "intento": registro["intentos"]},
            )
            return registro

        observability.audit(
            "incident.claim", {"incidentId": ruta.stem},
            status="BLOCKED",
            detail=f"duplicado exacto; el previo quedó en '{(previo or {}).get('estado')}'",
        )
        log.warning(
            "Notificación duplicada exacta (mismo activo, KPI y StartTime).",
            extra={"incidentId": ruta.stem, "estadoPrevio": (previo or {}).get("estado")},
        )
        return None

    log.info("Incidente registrado.", extra={
        "incidentId": ruta.stem, "asset": registro["asset"], "kpiName": registro["kpi_name"]})
    return registro


def mark(registro: dict, estado: str, diagnostico: dict | None = None,
         trace: dict | None = None, motivo: str | None = None) -> None:
    """Actualiza el estado del incidente en disco. Nunca propaga excepciones:
    un fallo escribiendo el registro no debe tumbar el análisis en curso.

    'motivo' explica en una frase por qué el incidente acabó así. Se añadió el
    2026-09-14 porque un incidente en FALLIDO no decía en ninguna parte qué
    había fallado: el log lo tenía, pero el fichero del caso no, y la pantalla
    solo podía enseñar "fallo del análisis" y encogerse de hombros. Quien mira
    un monitor en una sala de control no va a abrir webhook.log.

    Va en un campo propio y no dentro del trace a propósito: el trace se poda a
    los 90 días (podar_traces) y el motivo del fallo forma parte del caso.
    """
    ruta = _directorio() / f"{registro['id']}.json"
    observability.audit(
        "incident.mark", {"incidentId": registro["id"], "estado": estado},
    )
    registro["estado"] = estado
    registro["actualizado_en"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if motivo:
        registro["motivo"] = motivo
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


def podar_traces() -> tuple[int, int]:
    """Elimina el 'trace' de los incidentes antiguos y conserva el caso.

    Qué se poda: los prompts enviados al modelo y sus respuestas, que son el
    98,9 % del peso de un incidente (medido: 501 KB de 507 KB).

    Qué NO se toca, nunca: el payload original, el estado, el diagnóstico y --
    cuando exista -- la revisión humana y la causa confirmada. Eso son 5,7 KB y
    son la base de la evaluación: contrastar hipótesis contra causas reales.
    Un borrado por antigüedad del incidente entero destruiría precisamente eso
    cuando empezara a tener valor estadístico.

    En lugar de borrar sin rastro, deja un resumen de lo que había (qué claves
    y cuánto ocupaban). ai-governance §4.5 pide mantener inmutables los
    METADATOS de trazabilidad aunque el CONTENIDO se elimine.

    La antigüedad se mide sobre 'actualizado_en', no sobre 'recibido_en': lo
    que importa es cuándo terminó el análisis que produjo esos prompts.

    Se audita cada poda ANTES de ejecutarla: elimina estado de forma
    permanente, y eso lo exige observability-spec §2.

    Returns:
        (incidentes podados, bytes liberados)
    """
    if config.INCIDENT_TRACE_RETENTION_DAYS <= 0:
        return 0, 0

    ahora = datetime.now(timezone.utc)
    limite = ahora - timedelta(days=config.INCIDENT_TRACE_RETENTION_DAYS)
    podados = liberados = 0

    for ruta in _directorio().glob("*.json"):
        registro = _leer(ruta)
        if not registro or not registro.get("trace"):
            continue

        marca = registro.get("actualizado_en") or registro.get("recibido_en")
        try:
            cuando = datetime.fromisoformat(str(marca).replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            log.warning("Incidente con fecha ilegible; no se poda.",
                        extra={"incidentId": registro.get("id"), "marca": repr(marca)})
            continue
        if cuando >= limite:
            continue

        trace = registro["trace"]
        peso = len(json.dumps(trace, ensure_ascii=False))

        observability.audit(
            "incident.prune_trace",
            {"incidentId": registro.get("id"), "bytes": peso,
             "edadDias": (ahora - cuando).days},
        )

        registro["trace_podado"] = {
            "fecha": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "claves": sorted(trace),
            "bytes": peso,
            "motivo": f"retencion de {config.INCIDENT_TRACE_RETENTION_DAYS} dias",
        }
        del registro["trace"]

        try:
            _escribir(ruta, registro)
            podados += 1
            liberados += peso
            log.info("Trace podado por antiguedad.", extra={
                "incidentId": registro.get("id"), "bytes": peso,
                "edadDias": (ahora - cuando).days})
        except OSError as exc:
            log.error("No se pudo podar el trace.", extra={
                "incidentId": registro.get("id"), "errorMsg": str(exc)})

    return podados, liberados


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
