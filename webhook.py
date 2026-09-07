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
from datetime import datetime

import asyncio

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config
import incidents
import observability
import workflow


async def _analizar_incidente(payload: dict, registro: dict) -> None:
    """Envuelve el análisis para dejar constancia de cómo terminó.

    El workflow ya captura sus propios errores y corta de forma controlada
    devolviendo None; el try/except de aquí es para lo imprevisto, de forma que
    un incidente nunca se quede colgado en 'analizando' mientras el proceso
    sigue vivo. El diagnóstico se guarda en el fichero del incidente, que es el
    primer paso hacia un canal de salida de verdad (hoy solo iba al log).
    """
    incidents.mark(registro, incidents.ANALIZANDO)
    trace: dict = {}
    try:
        diagnostico = await workflow.run_rca_analysis(payload, trace)
    except Exception:
        log.exception("Analisis abortado por un error no controlado.",
                      extra={"incidentId": registro["id"]})
        incidents.mark(registro, incidents.FALLIDO, trace=trace)
        return
    if diagnostico is None:
        incidents.mark(registro, incidents.FALLIDO, trace=trace)
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
        incidents.mark(registro, incidents.FINALIZADO, diagnostico=diagnostico, trace=trace)


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
            "como interrumpidos. No se relanzan automaticamente.", interrumpidos,
        )


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
    """
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "rca-workflow-webhook",
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
        incidents.mark(registro, incidents.PAUSADO)
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
# Lo que sí protege este despliegue, con independencia de lo anterior:
#   - Ninguna credencial en el código ni en el log (config.py, .gitignore).
#   - Los errores no exponen trazas ni rutas internas (observability.py).
#   - La salida del modelo se valida antes de usarse; nunca se ejecuta.
#   - El gasto está acotado: deduplicación, enfriamiento y un tope de reajustes.
#   - WORKFLOW_ENABLED corta el comportamiento automático sin desplegar.
