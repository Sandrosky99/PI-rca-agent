"""
webhook.py — Servidor HTTP que recibe notificaciones de PI System (Step 1)

¿Qué hace este fichero?
  Levanta un servidor web ligero que está siempre escuchando en segundo plano.
  Cuando PI System detecta una desviación en un parámetro monitorizado, envía
  una notificación HTTP POST a este servidor. El servidor la recibe, la registra
  en el log y activa el workflow RCA para iniciar el análisis.

¿Cómo arrancarlo?
  Ejecuta start.bat  (o bien: .venv\Scripts\python -m uvicorn webhook:app --host 0.0.0.0 --port 8080)

¿Cómo configurar PI System para que envíe aquí las notificaciones?
  En PI Notifications, configura el canal de entrega "HTTP" con la URL:
      http://<IP_DE_ESTE_SERVIDOR>:<WEBHOOK_PORT>/notification
  Método: POST, Content-Type: application/json

Endpoints disponibles:
  GET  /health                  → comprobación de estado (para verificar que el servidor está vivo)
  POST /notification            → recibe las alertas de PI System
  GET  /notifications/history   → lista las últimas notificaciones recibidas (para verificar pruebas)
"""

import logging
from datetime import datetime, timedelta, timezone

import asyncio

from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

import config
import incidents
import observability
import workflow

# La página de la pantalla, relativa a este fichero y no al directorio de
# trabajo: el servicio de Windows arranca desde donde lo lance NSSM.
_PAGINA = Path(__file__).resolve().parent / "static" / "pantalla.html"

# Techo del parámetro 'limite' de GET /incidentes. Lo teclea una persona en la
# pantalla, así que conviene que un cero de más no se traduzca en leer miles de
# ficheros del disco. 500 ya es mucho más de lo que se puede mirar en un monitor.
_LIMITE_MAXIMO = 500


# Valor del semáforo cuando el límite está desactivado (MAX_CONCURRENT_ANALYSES
# <= 0). Se usa un cupo enorme en vez de un camino de código sin semáforo para
# que haya una sola ruta que mantener: la de "pide turno y sigue".
_SIN_LIMITE = 10_000

# Límite de análisis simultáneos (config.MAX_CONCURRENT_ANALYSES).
#
# Va AQUÍ y no dentro de run_rca_analysis() por una razón concreta: así el
# incidente espera turno en 'recibido' y solo pasa a 'analizando' cuando de
# verdad arranca. Si el semáforo estuviera dentro del workflow, un incidente
# encolado figuraría como 'analizando' sin estarlo -- y ese estado es justo lo
# que va a leer la pantalla de la sala de control. Un estado que miente es peor
# que no tenerlo.
#
# Efecto secundario útil: los que esperan cuentan como 'recibido' en /health, o
# sea que la cola ya es visible sin añadir nada.
#
# En Python 3.10+ un Semaphore creado fuera del bucle se asocia al bucle en el
# primer uso, así que crearlo a nivel de módulo es correcto.
_ANALISIS_EN_CURSO = asyncio.Semaphore(
    config.MAX_CONCURRENT_ANALYSES if config.MAX_CONCURRENT_ANALYSES > 0 else _SIN_LIMITE
)


async def _analizar_incidente(payload: dict, registro: dict) -> None:
    """Envuelve el análisis para dejar constancia de cómo terminó.

    El workflow ya captura sus propios errores y corta de forma controlada
    devolviendo None; el try/except de aquí es para lo imprevisto, de forma que
    un incidente nunca se quede colgado en 'analizando' mientras el proceso
    sigue vivo. El diagnóstico se guarda en el fichero del incidente, que es el
    primer paso hacia un canal de salida de verdad (hoy solo iba al log).

    Espera turno si ya hay MAX_CONCURRENT_ANALYSES análisis en marcha. Esperar y
    no rechazar es deliberado: la alerta ya está registrada y perderla sería lo
    único inaceptable. Tardar más, no.
    """
    if _ANALISIS_EN_CURSO.locked():
        log.info("Análisis en cola: se ha alcanzado el límite de simultáneos.",
                 extra={"incidentId": registro["id"], "asset": registro["asset"],
                        "limite": config.MAX_CONCURRENT_ANALYSES})
    async with _ANALISIS_EN_CURSO:
        await _ejecutar_analisis(payload, registro)


async def _ejecutar_analisis(payload: dict, registro: dict) -> None:
    """El análisis propiamente dicho, ya con turno concedido."""
    incidents.mark(registro["id"], incidents.ANALIZANDO)
    trace: dict = {}
    try:
        # Tope duro. Es la única garantía que cubre TODO el análisis, incluidas
        # las llamadas MCP de los Steps 2 y 5, que no tienen timeout propio y
        # pueden quedarse colgadas para siempre si el subproceso deja de
        # responder. Sin esto, un análisis atascado ocupaba su plaza del
        # semáforo indefinidamente y los siguientes no arrancaban nunca.
        #
        # Cancelar no mata el hilo donde corre la llamada al modelo (to_thread
        # no es interrumpible): ese hilo termina por su cuenta y se descarta.
        # Como la llamada ya tiene su propio LLM_TIMEOUT_SECONDS, ese rezagado
        # dura como mucho un par de minutos. La plaza del semáforo sí se libera
        # al instante, que es lo que importa para los que esperan turno.
        diagnostico = await asyncio.wait_for(
            workflow.run_rca_analysis(payload, trace),
            timeout=config.ANALYSIS_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, TimeoutError):
        log.error("Analisis abortado por exceder el tiempo maximo.",
                  extra={"incidentId": registro["id"],
                         "limiteSegundos": config.ANALYSIS_TIMEOUT_SECONDS})
        incidents.mark(registro["id"], incidents.FALLIDO, trace=trace,
                       motivo="El análisis superó el tiempo máximo.")
        return
    except Exception:
        log.exception("Analisis abortado por un error no controlado.",
                      extra={"incidentId": registro["id"]})
        incidents.mark(registro["id"], incidents.FALLIDO, trace=trace,
                       motivo="El análisis se detuvo por un error inesperado. "
                              "El detalle técnico está en el log del servicio.")
        return
    if diagnostico is None:
        # El workflow corta de forma controlada devolviendo None, y deja dicho
        # en el trace por qué. Si no lo dejó -- no debería pasar --, se guarda
        # algo antes que nada: un incidente que solo dice "fallido" no le sirve
        # a quien está mirando la pantalla.
        incidents.mark(registro["id"], incidents.FALLIDO, trace=trace,
                       motivo=trace.get("motivo_fallo")
                       or "El análisis no llegó a completarse. El detalle está "
                          "en el log del servicio.")
    else:
        # Etiquetado de contenido generado por IA (ai-governance 5.4): quien lea
        # el fichero del incidente debe saber que el diagnostico lo produjo un
        # modelo y que es una hipotesis pendiente de verificacion humana.
        diagnostico["_ai_generated"] = {
            "provider": config.LLM_PROVIDER,
            "model": config.GEMINI_MODEL if config.LLM_PROVIDER == "gemini" else config.ANTHROPIC_MODEL,
            "notice": ("Hipotesis generada por un modelo de lenguaje a partir de datos de PI. "
                       "NO es una causa raiz confirmada: requiere verificacion por un ingeniero "
                       "de procesos antes de actuar."),
        }
        incidents.mark(registro["id"], incidents.FINALIZADO, diagnostico=diagnostico, trace=trace)


class Expect100ContinueMiddleware(BaseHTTPMiddleware):
    """Middleware que gestiona la cabecera 'Expect: 100-continue' de HTTP/1.1.

    Algunos clientes HTTP (como PI System Notifications) envían primero las
    cabeceras con 'Expect: 100-continue' y esperan que el servidor responda
    '100 Continue' antes de enviar el body. Si el servidor no responde, el
    cliente se queda bloqueado esperando indefinidamente.

    Este middleware detecta esa cabecera y responde '100 Continue' de inmediato,
    desbloqueando al cliente para que envíe el body.
    """

    async def dispatch(self, request: Request, call_next):
        # Si PI envía 'Expect: 100-continue', respondemos inmediatamente
        # para que PI proceda a enviar el body de la notificación.
        if request.headers.get("expect", "").lower() == "100-continue":
            log.debug("Expect: 100-continue detectado — respondiendo 100 Continue")

        # Continuar con el procesamiento normal del request
        return await call_next(request)

# Historial en memoria de las últimas notificaciones recibidas.
# Se almacenan aquí para que el endpoint /notifications/history pueda devolverlas.
# Se pierde al reiniciar el servidor (es solo para verificación, no persistencia).
_notification_history: list[dict] = []
# Número máximo de notificaciones que se guardan en memoria
_MAX_HISTORY = 50

# =============================================================================
# Configuración del sistema de logs
# =============================================================================
# El logging es JSON estructurado, una línea por evento, con los campos que
# exige extensions/observability-spec.md §1 (ts, level, component, msg). Va a
# consola y a un fichero rotativo (10 MB x 5): una Notification Rule de PI con
# NonrepetitionInterval=0 puede reenviar sin parar, y el disco no es infinito.
#
# El audit trail va aparte, a config.AUDIT_FILE (§2.3). Ver observability.py.
observability.configure_logging()

log = logging.getLogger(__name__)


# =============================================================================
# Creación de la aplicación FastAPI
# =============================================================================
# FastAPI es el framework que convierte funciones Python normales en endpoints
# HTTP. La descripción y versión aparecen en la documentación automática que
# FastAPI genera en http://localhost:8090/docs
app = FastAPI(
    title="RCA Workflow — Webhook de PI System",
    description=(
        "Servidor HTTP que recibe notificaciones de AVEVA PI System y "
        "activa el workflow de análisis de causa raíz (RCA)."
    ),
    version="1.0.0",
)

# Registrar el middleware para gestionar 'Expect: 100-continue' de PI System
app.add_middleware(Expect100ContinueMiddleware)


# =============================================================================
# Evento de arranque: se ejecuta UNA sola vez cuando el servidor inicia
# =============================================================================
@app.on_event("startup")
async def startup_event() -> None:
    """Comprueba la configuración y muestra en log la URL de escucha."""
    missing = config.validate_config()
    if missing:
        # El servidor arranca igualmente, pero avisa de lo que falta.
        # Así el operador puede corregirlo sin que el proceso se interrumpa.
        log.warning("ADVERTENCIA: Faltan las siguientes variables en .env:")
        for var in missing:
            log.warning("  - %s", var)
        log.warning("Copia .env.example a .env y rellena los valores que faltan.")
    else:
        log.info("Configuracion correcta.")

    log.info("-" * 60)
    log.info("Servidor RCA Workflow arrancado y escuchando en:")
    log.info("  http://0.0.0.0:%s/notification  <- PI envia aqui sus alertas", config.WEBHOOK_PORT)
    log.info("  http://localhost:%s/health       <- comprobacion de estado", config.WEBHOOK_PORT)
    log.info("  http://localhost:%s/docs         <- documentacion de la API", config.WEBHOOK_PORT)
    log.info("-" * 60)

    if config.WEBHOOK_SECRET:
        log.info("Validacion de origen: ACTIVA (cabecera X-PI-Secret requerida)")
    else:
        log.info("Validacion de origen: DESACTIVADA (ver DECISIONES DE SEGURIDAD en webhook.py)")

    if config.NOTIFICATION_HISTORY_ENABLED:
        log.warning(
            "GET /notifications/history ACTIVO: expone payloads de planta sin autenticar. "
            "Apagarlo al terminar de depurar.")
    else:
        log.info("GET /notifications/history: deshabilitado (responde 404)")

    log.info("Registro de incidentes: %s", config.INCIDENTS_DIR)
    log.info("Enfriamiento de duplicados: %d min", config.INCIDENT_COOLDOWN_MINUTES)

    # Si el proceso murio a mitad de un analisis, esos incidentes quedaron en
    # 'analizando' y nadie los va a terminar. Se marcan para que no queden
    # colgados en silencio (ver incidents.sweep_interrupted).
    interrumpidos = incidents.sweep_interrupted()
    if interrumpidos:
        log.warning(
            "%d incidente(s) quedaron a medias en la ejecucion anterior y se han marcado "
            "como interrumpidos. Se relanzaran si PI vuelve a enviar la alerta.", interrumpidos,
        )

    # Poda del material de trabajo antiguo. Solo elimina prompts y respuestas;
    # el caso -- payload, diagnostico y revision -- no se toca nunca.
    if config.INCIDENT_TRACE_RETENTION_DAYS > 0:
        podados, liberados = incidents.podar_traces()
        if podados:
            log.info("Traces podados por antiguedad.", extra={
                "incidentes": podados, "bytesLiberados": liberados,
                "retencionDias": config.INCIDENT_TRACE_RETENTION_DAYS})
        log.info("Retencion de traces: %d dias", config.INCIDENT_TRACE_RETENTION_DAYS)
    else:
        log.info("Retencion de traces: desactivada (se conserva todo)")


# =============================================================================
# Endpoint GET /health — Comprobación de estado
# =============================================================================
@app.get(
    "/health",
    summary="Comprobación de estado",
    description="Devuelve 'ok' si el servidor está en marcha. Útil para monitorización.",
)
async def health_check() -> dict:
    """Indica que el servidor está funcionando correctamente.

    Puedes llamar a este endpoint desde el navegador o con curl para verificar
    que el servidor está vivo antes de configurar PI System:
        curl http://localhost:8090/health

    Incluye el recuento de incidentes por estado. No es una interfaz de
    usuario, pero convierte en visible algo que hasta ahora solo aparecía en un
    WARNING del arranque: que hay incidentes atascados en 'interrumpido'.
    Cualquier monitorización que haga polling del endpoint lo ve.

    Solo van números -- ni nombres de activo ni datos de planta -- porque este
    endpoint no está autenticado (ver DECISIONES DE SEGURIDAD).
    """
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "rca-workflow-webhook",
        "workflow_enabled": config.WORKFLOW_ENABLED,
        "incidentes": incidents.contar_por_estado(),
    }


# =============================================================================
# Endpoint POST /notification — Recepción de alertas de PI System
# =============================================================================
@app.post(
    "/notification",
    summary="Recibe una notificación de alerta de PI System",
    description=(
        "PI System llama a este endpoint cuando detecta una desviación en un "
        "parámetro monitorizado. El servidor registra la alerta y activa el "
        "análisis de causa raíz en segundo plano."
    ),
    status_code=202,
)
async def receive_notification(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Recibe la notificación HTTP POST de PI System y activa el workflow RCA.

    Flujo interno:
      1. Lee y parsea el cuerpo JSON de la petición.
      2. Registra el contenido completo en el log.
      3. Valida el token secreto (si está configurado).
      4. Lanza el análisis RCA en segundo plano (no bloquea la respuesta).
      5. Responde a PI con 202 Accepted de forma inmediata.

    El código 202 ("Accepted") significa: "recibí tu mensaje y lo voy a procesar,
    pero no esperes el resultado ahora mismo". Esto es importante porque el análisis
    puede tardar varios segundos y PI no debe quedarse esperando la respuesta.
    """
    # ------------------------------------------------------------------
    # Paso 1: Leer el cuerpo de la petición
    # ------------------------------------------------------------------
    # Leemos primero el body RAW con timeout de 10 segundos para evitar que el
    # servidor se quede bloqueado si PI no cierra la conexión correctamente.
    # Después intentamos parsearlo como JSON; si falla, lo aceptamos igual
    # como texto plano para poder ver exactamente qué está enviando PI.
    try:
        raw_body: bytes = await asyncio.wait_for(request.body(), timeout=10.0)
    except asyncio.TimeoutError:
        log.warning("Timeout leyendo el cuerpo de la peticion de PI (>10s). Conexion lenta o malformada.")
        raise HTTPException(status_code=408, detail="Timeout leyendo el cuerpo de la peticion.")

    # Registrar SIEMPRE el request completo (cabeceras + body raw) para diagnóstico.
    # Esto es especialmente útil durante la configuración inicial con PI System.
    log.info("--- REQUEST DE PI ---")
    log.info("Metodo: %s | Content-Type: %s | Content-Length: %s",
             request.method,
             request.headers.get("content-type", "no definido"),
             request.headers.get("content-length", "no definido"))
    log.info("Body raw (primeros 1000 bytes): %s", raw_body[:1000])
    log.info("---------------------")

    # Intentar parsear como JSON. Si PI envía XML, form-data u otro formato,
    # lo aceptamos igualmente guardando el body como texto para diagnóstico.
    try:
        import json
        payload = json.loads(raw_body)
    except Exception:
        # No es JSON — lo guardamos como texto para poder analizarlo
        payload = {"_raw": raw_body.decode("utf-8", errors="replace"), "_format": "no-json"}
        log.warning("El body de PI no es JSON valido. Guardado como texto para diagnostico.")

    # ------------------------------------------------------------------
    # Paso 2: Registrar la notificación en el log y en el historial
    # ------------------------------------------------------------------
    # Escribimos el contenido completo de la alerta en el log para que quede
    # constancia y para que puedas ver exactamente qué formato envía PI System.
    # Esto es especialmente útil durante la configuración inicial.
    received_at = datetime.utcnow().isoformat() + "Z"
    origin = request.client.host if request.client else "desconocido"

    log.info("=" * 60)
    log.info("NOTIFICACION RECIBIDA de PI System")
    log.info("Origen: %s", origin)
    log.info("Contenido: %s", payload)
    log.info("=" * 60)

    # Guardar en el historial en memoria para que /notifications/history pueda devolverla.
    # Si se supera el límite, se elimina la más antigua (la primera de la lista).
    entry = {"received_at": received_at, "origin": origin, "payload": payload}
    _notification_history.append(entry)
    if len(_notification_history) > _MAX_HISTORY:
        _notification_history.pop(0)

    # ------------------------------------------------------------------
    # Paso 3: Validar el token secreto (solo si está configurado)
    # ------------------------------------------------------------------
    # Si en .env definiste un WEBHOOK_SECRET, comprobamos que PI lo envía
    # en la cabecera HTTP "X-PI-Secret". Así evitamos que sistemas no
    # autorizados puedan enviar alertas falsas al workflow.
    if config.WEBHOOK_SECRET:
        received_token = request.headers.get("X-PI-Secret", "")
        if received_token != config.WEBHOOK_SECRET:
            log.warning(
                "Notificacion RECHAZADA: token invalido desde %s",
                request.client.host if request.client else "desconocido",
            )
            raise HTTPException(
                status_code=401,
                detail="Token de autenticacion invalido. Comprueba la cabecera X-PI-Secret.",
            )

    # ------------------------------------------------------------------
    # Paso 4: Lanzar el análisis RCA en segundo plano
    # ------------------------------------------------------------------
    # BackgroundTasks permite que el análisis (que puede tardar varios segundos)
    # se ejecute después de que este endpoint ya haya respondido a PI.
    # De este modo PI recibe su confirmación inmediatamente y no se bloquea.
    # ------------------------------------------------------------------
    # Paso 3-bis: ¿es esto una notificación analizable?
    # ------------------------------------------------------------------
    # Antes de crear nada. Sin esta puerta, un cuerpo sin campos utilizables
    # -- incluido uno que ni siquiera fuese JSON -- creaba su incidente y
    # llegaba hasta el Step 4, GASTANDO una llamada al modelo antes de que la
    # validacion del Step 5 lo cortara por no haber af_context.
    #
    # Se responde 202 y no 400 por el mismo motivo que con los duplicados: un
    # 4xx solo conseguiria que PI reintentase, y un payload malformado no se
    # arregla reintentandolo. El rechazo queda MUY visible en el log y en el
    # audit trail, que es donde hay que verlo para corregir la configuracion
    # de PI. La notificacion sigue en /notifications/history para diagnostico.
    problemas = workflow.validate_notification(payload)
    if problemas:
        observability.audit(
            "notification.accept",
            {"origin": origin, "keys": ",".join(sorted(payload)[:8]) if isinstance(payload, dict) else "-"},
            status="BLOCKED", detail="; ".join(problemas),
        )
        log.warning(
            "Notificacion RECHAZADA: no es analizable. No se registra incidente ni se llama al modelo.",
            extra={"origin": origin, "motivos": "; ".join(problemas)},
        )
        return JSONResponse(
            content={
                "status": "rejected",
                "message": "Notificacion no analizable: " + "; ".join(problemas),
                "received_at": received_at,
            },
            status_code=202,
        )

    # ------------------------------------------------------------------
    # Paso 4: Deduplicar — ¿es este incidente uno que ya estamos tratando?
    # ------------------------------------------------------------------
    # Va después de validar el token para no crear registros a partir de
    # peticiones no autenticadas. Si es un duplicado se responde 202 igual: un
    # error solo conseguiria que PI reintentase.
    registro = incidents.claim(payload)
    if registro is None:
        return JSONResponse(
            content={
                "status": "duplicate",
                "message": "Notificacion duplicada: ya hay un analisis para este incidente.",
                "received_at": received_at,
            },
            status_code=202,
        )

    # ------------------------------------------------------------------
    # Paso 5: Interruptor de parada en caliente (ai-governance 9.5)
    # ------------------------------------------------------------------
    # Con WORKFLOW_ENABLED=false la notificacion se sigue recibiendo y
    # registrando -- no se pierde nada -- pero no se lanza ningun analisis: ni
    # LLM ni MCP servers. Permite cortar el comportamiento automatico sin
    # desplegar ni revertir. El incidente queda en 'pausado' para poder
    # relanzarlo a mano despues.
    if not config.WORKFLOW_ENABLED:
        observability.audit(
            "run_rca_analysis", {"incidentId": registro["id"], "asset": registro["asset"]},
            status="BLOCKED", detail="WORKFLOW_ENABLED=false (parada en caliente)",
        )
        incidents.mark(registro["id"], incidents.PAUSADO)
        log.warning(
            "Analisis NO lanzado: el workflow esta deshabilitado por configuracion.",
            extra={"incidentId": registro["id"]},
        )
        return JSONResponse(
            content={
                "status": "paused",
                "message": "Notificacion registrada. El analisis automatico esta deshabilitado.",
                "received_at": received_at,
            },
            status_code=202,
        )

    observability.audit(
        "run_rca_analysis",
        {"incidentId": registro["id"], "asset": registro["asset"], "kpiName": registro["kpi_name"]},
    )
    background_tasks.add_task(_analizar_incidente, payload, registro)
    log.info("Analisis RCA iniciado en segundo plano.", extra={"incidentId": registro["id"]})

    # ------------------------------------------------------------------
    # Paso 5: Responder a PI System con confirmación inmediata
    # ------------------------------------------------------------------
    return JSONResponse(
        content={
            "status": "accepted",
            "message": "Notificacion recibida correctamente. Analisis de causa raiz en curso.",
            "received_at": received_at,
        },
        status_code=202,
    )


# =============================================================================
# Endpoint GET /notifications/history — Historial de notificaciones recibidas
# =============================================================================
@app.get(
    "/notifications/history",
    summary="Historial de notificaciones recibidas (deshabilitado por defecto)",
    description=(
        "Devuelve las últimas notificaciones recibidas de PI System (máximo 50, se "
        "pierde al reiniciar). **Deshabilitado por defecto**: expone datos de planta "
        "sin autenticar. Se activa con NOTIFICATION_HISTORY_ENABLED=true, y solo de "
        "forma temporal para depurar una integración."
    ),
)
async def get_notification_history() -> dict:
    """Devuelve las últimas notificaciones recibidas para verificar la integración con PI.

    Apagado por defecto desde el 2026-09-07: devuelve los payloads COMPLETOS
    (activo, jerarquía de planta, KPI, umbral) a cualquiera que alcance el
    puerto, sin autenticación. Ver "DECISIONES DE SEGURIDAD" más abajo.

    Para depurar una integración nueva, poner NOTIFICATION_HISTORY_ENABLED=true
    y volver a apagarlo al terminar.
    """
    if not config.NOTIFICATION_HISTORY_ENABLED:
        # 404 y no 403: a quien no debería estar aquí no se le confirma que el
        # endpoint existe.
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "total_received": len(_notification_history),
        "max_stored": _MAX_HISTORY,
        "notifications": list(reversed(_notification_history)),  # más reciente primero
    }


# =============================================================================
# La pantalla de la sala de control (Fase 1: solo lectura)
# =============================================================================
# Diseño completo en docs/DISENO-INTERACCION-HUMANA.md. Esta primera fase solo
# MUESTRA: ni veredicto, ni feedback, ni botones. Es la única parte del diseño
# que no tiene decisiones pendientes, y resuelve hoy un problema real -- hasta
# ahora no había forma de ver qué había hecho el sistema salvo abrir JSON a mano.
#
# Sobre la exposición de datos, ver la nota al final de DECISIONES DE SEGURIDAD.

@app.get(
    "/incidentes",
    summary="Lista de incidentes para la vista de conjunto",
    description=(
        "Resumen de incidentes, del más reciente al más antiguo, acotado por "
        "fecha y cantidad. No incluye el payload, el trace ni el texto de las "
        "causas: para eso está GET /incidentes/{id}.\n\n"
        "Sin parámetros devuelve la ventana por defecto de la pantalla "
        "(SCREEN_DEFAULT_HOURS / SCREEN_DEFAULT_LIMIT). Los incidentes fallidos "
        "de hace más de unas horas quedan fuera salvo que se pidan por estado."
    ),
)
async def listar_incidentes(
    desde: str | None = None,
    hasta: str | None = None,
    limite: int | None = None,
    estado: str | None = None,
    horas: int | None = None,
    cerrados: bool | None = None,
    cierre: str | None = None,
) -> dict:
    """Alimenta la lista de la pantalla.

    Acotar no es un lujo: no se borra ningún incidente nunca, así que la lista
    crece sin techo y un monitor con miles de elementos no lo lee nadie.

    'horas' es un atajo para el caso normal (las últimas N horas) y 'desde' /
    'hasta' permiten el rango explícito que pida el operario. Si llegan los dos,
    manda el rango explícito: es el más concreto.
    """
    if desde is None and horas is None and hasta is None:
        horas = config.SCREEN_DEFAULT_HOURS
    # horas=0 significa "sin límite por abajo", que es como lo ofrece la
    # pantalla ("Todo"). Restar cero daría 'desde = ahora' y dejaría fuera todo
    # -- el fallo se coló en una prueba que pasó de casualidad, porque las
    # marcas tienen resolución de segundo y caían en el mismo.
    if horas is not None and desde is None and horas > 0:
        desde = (datetime.now(timezone.utc)
                 - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # El límite lo teclea el operario, así que se acota aquí: un 999999 de más
    # en la casilla no debe hacer que el servidor lea y serialice miles de
    # ficheros para un navegador que no los va a poder pintar.
    if limite is None:
        limite = config.SCREEN_DEFAULT_LIMIT
    limite = max(1, min(int(limite), _LIMITE_MAXIMO))

    resultado = incidents.listar(desde=desde, hasta=hasta, limite=limite,
                                 estado=estado, cerrados=cerrados,
                                 cierre_filtro=cierre)
    resultado["filtro"] = {"desde": desde, "hasta": hasta, "limite": limite,
                           "estado": estado, "cerrados": cerrados, "cierre": cierre}
    # 'total' se conserva por compatibilidad con lo que ya consumía este
    # endpoint; es el número de los que se devuelven.
    resultado["total"] = resultado["mostrados"]
    return resultado


def _con_estado_de_causas(registro: dict) -> dict:
    """Añade lo que se calcula y no se guarda: el estado de cada causa y el cierre.

    Va aquí y no en el fichero porque los veredictos son una lista de solo
    añadir: el estado de una causa es el de su último apunte, y el cierre es su
    consecuencia. Guardarlos sería un segundo sitio del que fiarse, y los dos
    pueden desincronizarse.
    """
    etiqueta, _ = incidents.cierre(registro)
    # El fichero guarda la LISTA de pasadas del análisis (ver incidents.py); la
    # API sigue ofreciendo un 'diagnostico' en singular, que es la pasada
    # vigente. No es un apaño de compatibilidad: es que quien mira la pantalla
    # opina sobre UN diagnóstico, el que tiene delante. Cuando la Fase 3 necesite
    # enseñar el contador de reanálisis, se añadirá exactamente ese dato y no la
    # lista entera -- que multiplicaría por tres el tamaño de la respuesta con
    # causas que la pantalla no pinta.
    sin_lista = {k: v for k, v in registro.items() if k != "diagnosticos"}
    return dict(sin_lista,
                diagnostico=incidents.diagnostico_vigente(registro),
                estadoCausas=incidents.estado_de_causas(registro),
                cierre=etiqueta,
                cerrado=incidents.esta_cerrado(registro))


@app.get(
    "/incidentes/{incidente_id}",
    summary="Detalle de un incidente",
    description=(
        "El incidente completo salvo el trace (los prompts enviados al modelo y "
        "sus respuestas en crudo, que son el 98,9 % del fichero y no pintan nada "
        "en una pantalla de sala de control)."
    ),
)
async def detalle_incidente(incidente_id: str) -> dict:
    """Detalle de un incidente. 404 si no existe o si el id no tiene formato válido.

    La validación del formato vive en incidents.leer_por_id() y no es cosmética:
    el id llega desde la URL, así que sin ella un id con ".." serviría para leer
    cualquier fichero de la máquina.
    """
    registro = incidents.leer_por_id(incidente_id)
    if registro is None:
        raise HTTPException(status_code=404, detail="Incidente no encontrado")
    return _con_estado_de_causas(registro)


class _Veredicto(BaseModel):
    """Lo que una persona concluye sobre UNA causa concreta."""
    causa: int
    veredicto: str
    evidencia: str = ""
    sospecha: str = ""
    iteracion: int = 1


class _Reclasificacion(BaseModel):
    """`null` retira la marca; hoy el único valor es "alerta_no_valida"."""
    reclasificacion: str | None = None


@app.post(
    "/incidentes/{incidente_id}/veredicto",
    summary="Registra lo que una persona concluye sobre una causa",
    description=(
        "Confirma, descarta o devuelve a pendiente UNA causa del diagnóstico. "
        "Al descartar hace falta la evidencia; la sospecha es opcional y entra "
        "marcada como pista a contrastar, nunca como conclusión.\n\n"
        "No caduca: se puede dar en caliente o días después, desde la pestaña "
        "de cerrados."
    ),
)
async def registrar_veredicto(incidente_id: str, cuerpo: _Veredicto) -> dict:
    """Escribe en el expediente desde la pantalla.

    Es el primer endpoint del sistema que MODIFICA el registro de un caso, y no
    lleva autenticación: autentica la cerradura de la sala de control, porque la
    pantalla solo se alcanza desde su HMI. Ver el punto 6 de DECISIONES DE
    SEGURIDAD más abajo, y el aviso de que esa premisa y esta decisión se
    sostienen mutuamente desde ficheros distintos.
    """
    try:
        registro = incidents.registrar_veredicto(
            incidente_id, cuerpo.causa, cuerpo.veredicto,
            evidencia=cuerpo.evidencia, sospecha=cuerpo.sospecha,
            iteracion=cuerpo.iteracion,
        )
    except incidents.RevisionError as exc:
        # 400 con el mensaje tal cual: está escrito para que lo lea quien está
        # delante de la pantalla, no para depurar.
        raise HTTPException(status_code=400, detail=str(exc))
    return _con_estado_de_causas(registro)


@app.post(
    "/incidentes/{incidente_id}/reclasificacion",
    summary="Marca que la alerta no debió existir",
    description=(
        "Para el falso positivo: el modelo no se equivocó, le dieron un "
        "problema que no existía. Es información para quien mantiene los "
        "umbrales de PI. Enviar `null` retira la marca."
    ),
)
async def registrar_reclasificacion(incidente_id: str, cuerpo: _Reclasificacion) -> dict:
    try:
        registro = incidents.registrar_reclasificacion(incidente_id, cuerpo.reclasificacion)
    except incidents.RevisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _con_estado_de_causas(registro)


@app.get(
    "/pantalla",
    summary="Pantalla de la sala de control",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def pantalla() -> HTMLResponse:
    """Sirve la página del monitor.

    Se lee del disco en cada petición, no al arrancar: durante el desarrollo eso
    permite retocar el HTML y recargar el navegador sin reiniciar un servicio
    que hay que parar con permisos de administrador.

    El coste es leer un fichero pequeño por visita, y la pantalla la abren unas
    pocas personas en una sala de control -- no es un endpoint de tráfico.
    """
    try:
        return HTMLResponse(_PAGINA.read_text(encoding="utf-8"))
    except OSError as exc:
        log.error("No se pudo leer la página de la pantalla: %s", exc)
        raise HTTPException(status_code=500, detail="No se pudo cargar la pantalla")


# =============================================================================
# DECISIONES DE SEGURIDAD (evaluadas y cerradas el 2026-09-07)
# =============================================================================
# Este webhook acepta notificaciones sin autenticar, por HTTP plano, en una VM
# con el firewall desactivado. Eso NO es un descuido: se evaluó punto por punto
# y estas son las conclusiones, con sus motivos, para que nadie tenga que
# volver a deducirlas -- ni las dé por buenas fuera de contexto.
#
# TODO ESTO SE SOSTIENE SOBRE UNA PREMISA:
#   plataforma de PRUEBAS, en red industrial interna (172.21.28.x), sin datos
#   personales y sin capacidad de actuar sobre la planta. Riesgo clasificado
#   como BAJO en docs/AI-GOVERNANCE.md §1.
#   El día que esto apunte a una planta real, los cuatro puntos vuelven a estar
#   abiertos y el firewall deja de ser opcional.
#
# 1. AUTENTICACIÓN POR CABECERA (X-PI-Secret) -- NO SE IMPLEMENTA
#    El código lo soporta (WEBHOOK_SECRET), pero el canal de entrega HTTP de PI
#    Notifications no permite añadir cabeceras propias. Exigirla dejaría el
#    webhook rechazando todas las notificaciones legítimas. Un control que no
#    puede cumplir el único emisor autorizado no da seguridad: da la apariencia
#    de tenerla, que es peor.
#
# 2. TLS / HTTPS -- NO SE IMPLEMENTA
#    Probado en julio de 2026 y falla en silencio: con https:// el handshake TLS
#    no completa, la conexión TCP queda ESTABLISHED y a FastAPI no llega ni una
#    petición. Nada en el log, ningún error en PI. Es el peor modo de fallo
#    posible, así que se sirve por HTTP plano a propósito. Si algún día se
#    retoma, empezar por el certificado: lo más probable es que PI rechace uno
#    autofirmado.
#
# 3. FIREWALL / RESTRICCIÓN POR IP -- NO SE IMPLEMENTA (decisión, no bloqueo)
#    Este SÍ es técnicamente viable: no depende de PI en absoluto, bastaría con
#    permitir el 8090 solo desde 172.21.28.55. Está desactivado en ambas VMs
#    desde el montaje, para eliminar variables durante la integración.
#    Reactivarlo tiene hoy más riesgo de romper el escenario de demostración que
#    de protegerlo. Es una decisión de infraestructura, y es la PRIMERA que hay
#    que revertir si esto sale del entorno de pruebas.
#
# 4. /notifications/history -- SÍ SE IMPLEMENTA (apagado por defecto)
#    El único de los cuatro que no dependía de nada externo. Devolvía los
#    payloads completos sin autenticar, lo que era incoherente con mantener
#    webhook.log, incidents/ y audit.jsonl fuera del control de versiones
#    precisamente por contener esos mismos datos. Se apaga por defecto.
#    /docs se deja accesible: expone la forma de la API, no datos, y ayuda a
#    quien tenga que integrar.
#
# 5. LA PANTALLA Y SUS ENDPOINTS -- SÍ SE IMPLEMENTAN, ENCENDIDOS (2026-09-14)
#    /incidentes, /incidentes/{id} y /pantalla exponen sin autenticar nombres de
#    activo, valores de proceso y los diagnósticos del modelo. Es MÁS de lo que
#    exponía /notifications/history, que se apagó por esto mismo -- así que la
#    diferencia hay que justificarla y no darla por supuesta.
#
#    La diferencia es el propósito. El historial era una herramienta de
#    depuración: su valor no compensaba la exposición, y apagarlo no le quitaba
#    nada a nadie. La pantalla ES la funcionalidad; apagada por defecto no habría
#    pantalla, y el diseño entero (docs/DISENO-INTERACCION-HUMANA.md) se apoya en
#    que haya un monitor consultable en la sala de control.
#
#    Se sostiene sobre la MISMA premisa que todo lo anterior: red interna de
#    pruebas, sin datos personales, sin exposición a internet. Si esa premisa
#    cambia, esto pasa a ser lo SEGUNDO que hay que arreglar, justo detrás del
#    firewall: hace falta autenticación antes de que la pantalla salga de aquí.
#
#    Lo que sí se acotó desde el principio, porque no costaba nada:
#      - El trace no sale nunca. Son los prompts en crudo y el 98,9 % del
#        fichero; la pantalla no lo necesita.
#      - El id de la URL se valida contra una expresión regular antes de tocar
#        el disco. Sin eso, un id con ".." leería cualquier fichero de la
#        máquina: verificado que el riesgo era real, no teórico.
#      - La lista devuelve un resumen, no los registros enteros.
#
# 6. LOGIN EN LA PANTALLA -- NO SE IMPLEMENTA (decidido el 2026-09-15)
#    La Fase 2 de la pantalla (docs/DISENO-INTERACCION-HUMANA.md) le dará al
#    navegador capacidad de ESCRIBIR en el expediente: veredictos sobre las
#    causas y la evidencia que los sostiene. Eso es otra clase de riesgo que
#    leer, así que se evaluó aparte en vez de heredar el punto 5.
#
#    No lleva login porque la pantalla es alcanzable SOLO desde el HMI de la
#    sala de control -- en planta, una regla de firewall de dos IPs fijas: PI y
#    ese puesto. Quien autentica es la cerradura de la sala: el conjunto de
#    personas que pueden escribir en un expediente es el de las que han entrado
#    ahí. Por eso tampoco el veredicto lleva autor.
#
#    Nótese que este SÍ es una decisión, a diferencia de los puntos 1 y 2, que
#    son limitaciones de PI. El navegador puede mandar cabeceras, hablar TLS y
#    pedir credenciales; si algún día hace falta, la puerta está abierta.
#
#    ⚠️ DEPENDENCIA INVISIBLE, y es el motivo de que esto esté escrito aquí:
#    una regla de red y una decisión de diseño de la aplicación se sostienen
#    mutuamente, y viven en sitios distintos. El día que la pantalla se abra a
#    más máquinas -- una peticion razonable: "¿puedo verla desde mi mesa?" --
#    esta decisión se vuelve incorrecta EN SILENCIO: nada falla, nada avisa.
#    Si eso pasa, hay que reabrir si el veredicto necesita autor.
#
# Lo que sí protege este despliegue, con independencia de lo anterior:
#   - Ninguna credencial en el código ni en el log (config.py, .gitignore).
#   - Los errores no exponen trazas ni rutas internas (observability.py).
#   - La salida del modelo se valida antes de usarse; nunca se ejecuta.
#   - El gasto está acotado: deduplicación, enfriamiento y un tope de reajustes.
#   - WORKFLOW_ENABLED corta el comportamiento automático sin desplegar.
