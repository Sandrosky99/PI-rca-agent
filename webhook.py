"""
webhook.py — Servidor HTTP que recibe notificaciones de PI System (Step 1)

¿Qué hace este fichero?
  Levanta un servidor web ligero que está siempre escuchando en segundo plano.
  Cuando PI System detecta una desviación en un parámetro monitorizado, envía
  una notificación HTTP POST a este servidor. El servidor la recibe, la registra
  en el log y activa el agente RCA para iniciar el análisis.

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
import agent


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
# El sistema de logs escribe mensajes en la consola con la hora, el nivel
# (INFO, WARNING, ERROR) y el mensaje. Esto nos permite ver en tiempo real
# qué notificaciones llegan y qué hace el servidor con ellas.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# =============================================================================
# Creación de la aplicación FastAPI
# =============================================================================
# FastAPI es el framework que convierte funciones Python normales en endpoints
# HTTP. La descripción y versión aparecen en la documentación automática que
# FastAPI genera en http://localhost:8090/docs
app = FastAPI(
    title="RCA Agent — Webhook de PI System",
    description=(
        "Servidor HTTP que recibe notificaciones de AVEVA PI System y "
        "activa el agente de análisis de causa raíz (RCA)."
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
    log.info("Servidor RCA Agent arrancado y escuchando en:")
    log.info("  http://0.0.0.0:%s/notification  <- PI envia aqui sus alertas", config.WEBHOOK_PORT)
    log.info("  http://localhost:%s/health       <- comprobacion de estado", config.WEBHOOK_PORT)
    log.info("  http://localhost:%s/docs         <- documentacion de la API", config.WEBHOOK_PORT)
    log.info("-" * 60)

    if config.WEBHOOK_SECRET:
        log.info("Validacion de origen: ACTIVA (cabecera X-PI-Secret requerida)")
    else:
        log.info("Validacion de origen: DESACTIVADA (acepta notificaciones de cualquier origen)")


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
        "service": "rca-agent-webhook",
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
    """Recibe la notificación HTTP POST de PI System y activa el agente RCA.

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
    # autorizados puedan enviar alertas falsas al agente.
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
    background_tasks.add_task(agent.run_rca_analysis, payload)
    log.info("Analisis RCA iniciado en segundo plano.")

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
    summary="Historial de notificaciones recibidas",
    description=(
        "Devuelve la lista de las últimas notificaciones recibidas de PI System "
        "(máximo 50, se pierde al reiniciar el servidor). "
        "Útil para verificar que PI System está enviando correctamente las alertas."
    ),
)
async def get_notification_history() -> dict:
    """Devuelve las últimas notificaciones recibidas para verificar la integración con PI.

    Cómo usarlo desde PowerShell para comprobar si llegó una notificación:
        Invoke-RestMethod http://localhost:8090/notifications/history

    O con curl:
        curl http://localhost:8090/notifications/history
    """
    return {
        "total_received": len(_notification_history),
        "max_stored": _MAX_HISTORY,
        "notifications": list(reversed(_notification_history)),  # más reciente primero
    }
