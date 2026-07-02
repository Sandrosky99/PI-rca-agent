"""
agent.py — Agente de Análisis de Causa Raíz (RCA)

¿Qué hace este fichero?
  Contiene la lógica central del agente: recibe la información de la alerta
  de PI System y, paso a paso, usa el modelo de IA Claude para identificar
  las posibles causas raíz y recomendar acciones correctivas.

¿Cómo encaja en el flujo completo?
  webhook.py recibe la notificación de PI  →  llama a run_rca_analysis()  →
  (Steps 2-5 a continuación)

Estado actual:
  Solo está implementado el Step 1 (recepción de la notificación, en webhook.py).
  Este fichero contiene los stubs documentados de los Steps 2-5, listos para
  implementar en sesiones posteriores.

Flujo completo del agente (los TODOs indican los pasos pendientes):
  Step 2 → Preparar el contexto estructurado para Claude
  Step 3 → Claude decide qué variables de PI necesita analizar
  Step 4 → Obtener datos históricos de PI vía MCP Server
  Step 5 → Claude analiza los datos y produce el diagnóstico final
"""

import logging
from datetime import datetime

import pytz

import config

# Usamos el mismo logger que el resto del proyecto.
# Los mensajes aparecerán en la consola con timestamp y nivel (INFO, WARNING...).
log = logging.getLogger(__name__)


# =============================================================================
# System prompt del agente (dominio fijo, se envía en TODAS las llamadas a
# Claude — Step 3 y Step 5 — vía el parámetro `system` de la API de Anthropic).
# =============================================================================
# La API de Claude es stateless entre llamadas: no hay "memoria" real entre el
# Step 3 y el Step 5 salvo lo que se envíe en cada request. Por eso el rol y el
# dominio del agente se fijan aquí como constante, separados del mensaje
# dinámico de cada alerta (build_analysis_context), en vez de repetirlos "a
# mano" en cada prompt: así el agente nunca actúa como un asistente genérico,
# pase lo que pase en el mensaje concreto.
SYSTEM_PROMPT = (
    "Eres el agente de análisis de causa raíz (RCA) de una planta de tratamiento "
    "de aguas residuales (WWTP) y sus estaciones de bombeo externas, "
    "monitorizadas con AVEVA PI System. Tu función es ayudar a un ingeniero de "
    "procesos a diagnosticar desviaciones operacionales detectadas por el "
    "sistema, para mitigarlas de forma rápida y eficaz apoyándote en los datos "
    "históricos disponibles en PI System. Todo tu razonamiento debe estar "
    "anclado en el dominio de depuración de aguas residuales y bombeo "
    "(hidráulica, eficiencia de bombas, caudal, presión, nivel, vibración, "
    "calidad de agua, etc.); no actúes como un asistente genérico."
)


def _valid_field(payload: dict, key: str, expected_type):
    """Devuelve payload[key] solo si su tipo coincide con expected_type; si no, None.

    PI puede enviar un campo con un tipo inesperado (p.ej. Limit como texto de
    fecha en vez de número, visto en producción el 2026-07-02). En ese caso no
    queremos meter ese valor "roto" en el mensaje a Claude — mejor omitirlo y
    dejar constancia en el log para poder corregir la configuración en PI.

    expected_type: un tipo (str) o tupla de tipos (p.ej. (int, float)) para
    campos numéricos. bool se excluye explícitamente de los campos numéricos,
    ya que en Python bool es subclase de int.
    """
    if key not in payload:
        return None
    raw = payload[key]
    type_tuple = expected_type if isinstance(expected_type, tuple) else (expected_type,)
    if isinstance(raw, bool) and bool not in type_tuple:
        valid = False
    else:
        valid = isinstance(raw, expected_type)
    if not valid:
        log.warning(
            "Campo '%s' con tipo inesperado (se omite del mensaje): %r (se esperaba %s)",
            key, raw, expected_type,
        )
        return None
    return raw


def _describe_threshold(threshold_type: str) -> str:
    """Explica en lenguaje natural por qué salta un umbral 'Low'/'High' de PI.

    No asume qué KPI es "bueno" alto o bajo (eso depende del KPI concreto y lo
    interpreta Claude con el contexto de dominio) — solo describe el hecho:
    qué disparó la alerta.
    """
    if threshold_type == "Low":
        return "un umbral 'Low': la alerta salta porque el valor ha caído por debajo del mínimo aceptable"
    if threshold_type == "High":
        return "un umbral 'High': la alerta salta porque el valor ha superado el máximo aceptable"
    return "un umbral no especificado"


def _parse_detection_time(payload: dict) -> tuple[datetime, datetime]:
    """Obtiene el momento de detección de la alerta en UTC y en hora local.

    Usa el campo "StartTime" que envía PI (UTC, ISO 8601 con 'Z'), igual que
    search_event_frames en aveva-pi-mcp. Si algún payload no lo trae (p.ej.
    pruebas antiguas), se aproxima con la hora actual del servidor y se avisa.
    """
    tz = pytz.timezone(config.PI_LOCAL_TIMEZONE)
    start_time_raw = payload.get("StartTime")
    if start_time_raw:
        try:
            detected_at_utc = datetime.fromisoformat(start_time_raw.replace("Z", "+00:00"))
            return detected_at_utc, detected_at_utc.astimezone(tz)
        except ValueError:
            log.warning("Campo 'StartTime' con formato inesperado: %s", start_time_raw)

    log.warning("Payload sin 'StartTime' valido; se usa la hora actual del servidor como aproximacion.")
    detected_at_local = datetime.now(tz)
    return detected_at_local.astimezone(pytz.utc), detected_at_local


def build_analysis_context(payload: dict) -> dict:
    """Step 2: construye el contexto estructurado que se enviará a Claude en el Step 3.

    Toma el payload real que envía PI System (ver CLAUDE.md → "Payload real de
    PI System") y lo convierte en un mensaje dinámico (los datos concretos de
    esta alerta) que se combina con SYSTEM_PROMPT (rol y dominio fijos) en las
    llamadas del Step 3.

    Args:
        payload: diccionario con las claves KPIName, Asset, Subsystem, System,
                 Plant, KPI, Limit, LimitThresholdType, StartTime. Puede incluir
                 opcionalmente AssetType/AssetModel (u otros campos de contexto
                 adicional que PI incorpore en el futuro); si no están, se
                 omiten del mensaje.

    Returns:
        Diccionario con los campos extraídos y la clave "claude_prompt" lista
        para enviarse al modelo en el Step 3 (junto con SYSTEM_PROMPT).
    """
    kpi_name = _valid_field(payload, "KPIName", str) or "KPI desconocido"
    asset = _valid_field(payload, "Asset", str) or "activo desconocido"
    subsystem = _valid_field(payload, "Subsystem", str) or ""
    system = _valid_field(payload, "System", str) or ""
    plant = _valid_field(payload, "Plant", str) or ""
    kpi_value = _valid_field(payload, "KPI", (int, float))
    limit_value = _valid_field(payload, "Limit", (int, float))
    threshold_type = _valid_field(payload, "LimitThresholdType", str) or ""
    asset_type = _valid_field(payload, "AssetType", str) or ""
    asset_model = _valid_field(payload, "AssetModel", str) or ""

    detected_at_utc, detected_at_local = _parse_detection_time(payload)

    hierarchy = "/".join(part for part in (subsystem, system, plant) if part)
    threshold_note = _describe_threshold(threshold_type)
    model_parts = [part for part in (asset_type, asset_model) if part]
    model_note = f" ({', '.join(model_parts)})" if model_parts else ""

    if kpi_value is not None and limit_value is not None:
        value_clause = (
            f"El valor con el que se ha sobrepasado el límite es {kpi_value}, "
            f"frente al límite configurado de {limit_value} "
            f"(se trata de {threshold_note})."
        )
    elif kpi_value is not None:
        value_clause = (
            f"El valor que ha disparado la alerta es {kpi_value} (se trata de "
            f"{threshold_note}); el límite configurado no está disponible "
            f"porque PI envió un valor con un tipo de dato inválido para 'Limit'."
        )
    elif limit_value is not None:
        value_clause = (
            f"El límite configurado es {limit_value} (se trata de {threshold_note}); "
            f"el valor que disparó la alerta no está disponible porque PI envió un "
            f"valor con un tipo de dato inválido para 'KPI'."
        )
    else:
        value_clause = (
            f"Se trata de {threshold_note}; ni el valor que disparó la alerta ni el "
            f"límite configurado están disponibles porque PI envió datos con un "
            f"tipo inválido para 'KPI' y 'Limit'."
        )

    summary = (
        f"La alerta detectada es una desviación en el KPI '{kpi_name}' de "
        f"'{asset}'{model_note}. Este equipo se encuentra en {hierarchy}. "
        f"{value_clause} "
        f"Ha sido detectado el {detected_at_local.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"({detected_at_utc.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC)."
    )

    request = (
        "Hazme una lista de variables (atributos de PI System) necesarias para "
        "diagnosticar las posibles causas raíz de esta desviación. No te "
        "limites a los atributos propios del equipo (p.ej. presión, caudal, "
        "vibración): incluye también variables de proceso aguas arriba/abajo u "
        "otros sistemas relacionados que puedan explicar la desviación, si "
        "tienen sentido para este caso (p.ej. turbidez o sólidos en suspensión "
        "del agua). Para cada variable, si existen varias formas de medirla u "
        "obtenerla, indica una lista de prioridad de alternativas (p.ej.: "
        "'turbidez del agua de entrada; si no existe analizador de turbidez, "
        "sólidos en suspensión (TSS) como alternativa'). Responde únicamente "
        "con esa lista priorizada y los timestamps (o ventana temporal) que "
        "necesitas para identificar la causa raíz, sin explicaciones "
        "adicionales."
    )

    return {
        "kpi_name": kpi_name,
        "asset": asset,
        "subsystem": subsystem,
        "system": system,
        "plant": plant,
        "asset_type": asset_type,
        "asset_model": asset_model,
        "kpi_value": kpi_value,
        "limit_value": limit_value,
        "threshold_type": threshold_type,
        "detected_at_utc": detected_at_utc.isoformat(),
        "detected_at_local": detected_at_local.isoformat(),
        "summary": summary,
        "system_prompt": SYSTEM_PROMPT,
        "claude_prompt": f"{summary}\n\n{request}",
    }


async def run_rca_analysis(notification_payload: dict) -> None:
    """Punto de entrada principal del agente RCA.

    Se llama desde webhook.py cada vez que llega una notificación de PI System.
    Se ejecuta en segundo plano (no bloquea la respuesta al servidor de PI).

    Args:
        notification_payload: Diccionario Python con los datos de la alerta
                              tal como los envió PI System. Formato real
                              (confirmado 2026-07-02, ver CLAUDE.md):
                              {
                                  "KPIName": "Hydraulic Efficiency",
                                  "Asset": "PS20102 A03 PS02 Pump 02",
                                  "Subsystem": "Pumping Station 01",
                                  "System": "External Pumping",
                                  "Plant": "WWTP",
                                  "KPI": 60.0,
                                  "Limit": 70.0,
                                  "LimitThresholdType": "Low",
                                  "StartTime": "2026-07-02T09:15:47Z"
                              }
    """
    log.info("=" * 60)
    log.info("AGENTE RCA: Iniciando análisis de causa raíz")
    log.info("Alerta recibida: %s", notification_payload)
    log.info("=" * 60)

    # -------------------------------------------------------------------------
    # Step 2: Preparar el contexto estructurado para Claude
    # -------------------------------------------------------------------------
    context = build_analysis_context(notification_payload)
    log.info("Contexto construido para Claude:")
    log.info(context["claude_prompt"])

    # -------------------------------------------------------------------------
    # TODO — Step 3: Claude responde con las variables que necesita analizar
    # -------------------------------------------------------------------------
    # Se llamará a la API de Anthropic (Claude) con el contexto del Step 2.
    # Claude analizará la situación y responderá con una lista estructurada
    # de los atributos de PI que necesita para el diagnóstico:
    #   - Temperatura del fluido de entrada
    #   - Caudal de refrigeración
    #   - Posición de la válvula de control
    #   - etc.
    #
    # Estas variables son rutas (piApiPath) que el MCP Server puede consultar
    # directamente en PI Web API.
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # TODO — Step 4: Obtener datos históricos de PI vía MCP Server
    # -------------------------------------------------------------------------
    # Con la lista de atributos del Step 3, se llamará al MCP Server
    # "aveva-pi-mcp" (que ya está funcionando en C:\MCPServer\MCP Server) para:
    #   a) Crear un bucket de timestamps con create_timeseries_bucket()
    #      (ventana temporal alrededor del momento de la alerta)
    #   b) Consultar los valores históricos con query_by_path()
    #      usando los piApiPath devueltos por Claude en el Step 3
    #
    # El resultado será una tabla de valores en el tiempo para cada atributo.
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # TODO — Step 5: Claude analiza los datos y produce el diagnóstico final
    # -------------------------------------------------------------------------
    # Se volverá a llamar a Claude, esta vez con los datos históricos del Step 4.
    # Claude analizará las tendencias y correlaciones entre variables y producirá:
    #   - Lista de posibles causas raíz, ordenadas por probabilidad
    #   - Explicación de por qué cada causa es plausible
    #   - Recomendación de acciones correctivas para cada causa
    #
    # El resultado se presentará al usuario (log, notificación, interfaz web...)
    # -------------------------------------------------------------------------

    log.info("AGENTE RCA: Step 2 completado. Steps 3-5 pendientes de implementar.")
