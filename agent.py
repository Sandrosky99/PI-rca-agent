"""
agent.py — Agente de Análisis de Causa Raíz (RCA)

¿Qué hace este fichero?
  Contiene la lógica central del agente: recibe la información de la alerta
  de PI System y, paso a paso, usa el modelo de IA Claude para identificar
  las posibles causas raíz y recomendar acciones correctivas.

¿Cómo encaja en el flujo completo?
  webhook.py recibe la notificación de PI  →  llama a run_rca_analysis()  →
  (Steps 2-6 a continuación)

Estado actual:
  Están implementados los Steps 1-4 (recepción de la notificación en
  webhook.py; consulta del AF vía graph_client.py; construcción del mensaje
  en build_analysis_context(); llamada al modelo). Los Steps 5-6 siguen como
  stubs documentados, listos para implementar en sesiones posteriores.

Flujo completo del agente (los TODOs indican los pasos pendientes):
  Step 2 → Consultar la estructura real del AF (afkg-graph-mcp, graph_client.py)
           para el asset y el subsistema de la alerta y obtener sus atributos
           disponibles (piApiPath)
  Step 3 → Preparar el contexto estructurado para el modelo, incluyendo los
           atributos reales del Step 2 (para que el modelo elija solo entre
           variables que existen de verdad, no nombres "de libro")
  Step 4 → El modelo decide, de esas variables reales, cuáles necesita analizar
  Step 5 → Obtener datos históricos de PI vía MCP Server
  Step 6 → El modelo analiza los datos y produce el diagnóstico final
"""

import json
import logging
from datetime import datetime

import pytz

import config
import graph_client
import llm_client

# Usamos el mismo logger que el resto del proyecto.
# Los mensajes aparecerán en la consola con timestamp y nivel (INFO, WARNING...).
log = logging.getLogger(__name__)


# =============================================================================
# System prompt del agente (dominio fijo, se envía en TODAS las llamadas al
# modelo — Step 4 y Step 6 — vía el parámetro system/system_instruction).
# =============================================================================
# La API del modelo es stateless entre llamadas: no hay "memoria" real entre el
# Step 4 y el Step 6 salvo lo que se envíe en cada request. Por eso el rol y el
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


# =============================================================================
# Secciones fijas del mensaje de usuario del Step 3 (ver CLAUDE.md, "Motivo
# del cambio 2026-07-03"). Solo la Sección 2 (payload) y la Sección 4 (datos
# del AF) son dinámicas por alerta; se construyen en build_analysis_context().
# =============================================================================
_OBJECTIVE_SECTION = (
    "Se ha detectado una desviación de proceso. Con la información compartida "
    "en el mensaje acerca del tipo y contexto de la desviación producida, e "
    "información en el Asset Framework de PI System debes de ayudar al "
    "usuario a identificar la causa raíz. Para eso en esta interacción debes "
    "devolver una lista con las variables de proceso necesarias para "
    "investigar la desviación concreta e identificar su causa. Debes "
    "seleccionar dichas variables de la lista que se comparte, no inventarlas."
)

_DATA_MODEL_SECTION = (
    "A continuación se te proporciona la estructura real del Asset Framework "
    "(AF) de PI relevante para esta alerta, extraída directamente del grafo "
    "de conocimiento — no debes asumir ni inventar variables que no aparezcan "
    "aquí.\n\n"
    "Los datos están organizados en dos bloques:\n"
    "- `main_asset_context`: el árbol completo del activo directamente "
    "afectado por la alerta (el valor Asset del payload) y todos sus "
    "sub-elementos — sensores, transmisores, indicadores/KPIs calculados, "
    "alarmas configuradas, etc.\n"
    "- `nearby_elements_context`: el árbol del elemento que agrupa al activo "
    "principal (el valor Subsystem del payload) y sus otros elementos "
    "hermanos — útil para comparar contra activos similares o para "
    "descartar/confirmar causas fuera del activo principal (p.ej. un "
    "problema aguas arriba que afecta a ambas bombas de la estación).\n\n"
    "La estructura de los assets se divide en meters (incluye todos los "
    "medidores del asset y dispositivos que porten medidas de este, como un "
    "power meter o un variador de velocidad) y en indicadores, que calculan "
    "KPIs para el activo jerárquicamente superior.\n\n"
    "Cada bloque es una lista plana de elementos del AF (se han omitido los "
    "nodos puramente organizativos como \"Meters\" o \"Indicators\", que no "
    "tienen atributos propios; su función solo se refleja en el campo "
    "element como parte del breadcrumb). Cada elemento tiene:\n"
    "- element: nombre del elemento, con su ruta jerárquica abreviada (>) "
    "cuando cuelga de un elemento padre.\n"
    "- description: descripción del elemento en el AF. Puede venir vacía "
    "(\"\") si no está documentada — no significa que el elemento carezca de "
    "relevancia.\n"
    "- attributes: lista de variables de ese elemento, cada una con:\n"
    "  - name: nombre del atributo.\n"
    "  - uom: unidad de ingeniería. Vacía si el atributo es adimensional o "
    "un estado/enum.\n"
    "  - description: descripción del atributo en el AF. Vacía si no está "
    "documentada.\n"
    "  - piApiPath: identificador exacto de PI Web API para esa variable. "
    "Debes copiarlo literalmente, sin modificarlo, cuando decidas qué "
    "variables consultar — es el valor que se pasará directamente a la tool "
    "query_by_path.\n\n"
    "En los meters e indicadores debes enfocarte en el nombre del elemento "
    "para identificar qué es o cuál es su función. Son \"subordinados\" al "
    "elemento principal, que es el asset. La estructura confiere "
    "significado.\n\n"
    "Regla estricta: elige exclusivamente entre los piApiPath listados en "
    "estos dos bloques. Si para tu diagnóstico necesitarías una variable que "
    "no aparece aquí, indícalo explícitamente en tu respuesta en vez de "
    "inventar un nombre de atributo o una ruta que no existe."
)

_FINAL_INSTRUCTION = (
    "Devuelve la lista de atributos cuyos datos requieres consultar para "
    "identificar la causa raíz de la desviación de la que nos informa la "
    "notificación, indicando para cada uno de ellos el nombre de su elemento "
    "y el piApiPath."
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


def build_analysis_context(payload: dict, af_context: dict) -> dict:
    """Step 3: construye el mensaje de 4 secciones que se enviará al modelo en el Step 4.

    Toma el payload real que envía PI System (ver CLAUDE.md → "Payload real de
    PI System") y el resultado del Step 2 (graph_client.build_af_context) y los
    combina con SYSTEM_PROMPT (rol y dominio fijos, Sección 0) en un único
    mensaje de usuario con 4 secciones:
      1. Objetivo de la interacción (fijo, _OBJECTIVE_SECTION)
      2. Payload de la notificación (dinámico, construido aquí como "summary")
      3. Explicación del modelo de datos (fijo, _DATA_MODEL_SECTION)
      4. Datos del grafo de AF (dinámico, af_context en JSON)
    seguido de la instrucción final (_FINAL_INSTRUCTION) que fija el formato
    de respuesta esperado.

    Args:
        payload: diccionario con las claves KPIName, Asset, Subsystem, System,
                 Plant, KPI, Limit, LimitThresholdType, StartTime. Puede incluir
                 opcionalmente AssetType/AssetModel (u otros campos de contexto
                 adicional que PI incorpore en el futuro); si no están, se
                 omiten del mensaje.
        af_context: resultado de graph_client.build_af_context() -- dict con
                    las claves "main_asset_context" y "nearby_elements_context".

    Returns:
        Diccionario con los campos extraídos y la clave "claude_prompt" lista
        para enviarse al modelo en el Step 4 (junto con SYSTEM_PROMPT).
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

    af_context_json = json.dumps(af_context, ensure_ascii=False, indent=2)

    claude_prompt = (
        f"1. Objetivo de la interacción\n\n{_OBJECTIVE_SECTION}\n\n"
        f"2. Payload de la notificación\n\n{summary}\n\n"
        f"3. Explicación del modelo de datos\n\n{_DATA_MODEL_SECTION}\n\n"
        f"4. Datos del grafo de AF\n\n{af_context_json}\n\n"
        f"{_FINAL_INSTRUCTION}"
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
        "claude_prompt": claude_prompt,
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
    # Step 2: Consultar la estructura real del AF (afkg-graph-mcp)
    # -------------------------------------------------------------------------
    asset = _valid_field(notification_payload, "Asset", str)
    subsystem = _valid_field(notification_payload, "Subsystem", str) or ""
    if asset:
        af_context = await graph_client.build_af_context(asset, subsystem)
    else:
        log.warning("Payload sin 'Asset' valido; se omite la consulta al AF (Step 2).")
        af_context = {"main_asset_context": [], "nearby_elements_context": []}
    log.info(
        "AF consultado: %d elementos en main_asset_context, %d en nearby_elements_context",
        len(af_context["main_asset_context"]), len(af_context["nearby_elements_context"]),
    )

    # -------------------------------------------------------------------------
    # Step 3: Preparar el contexto estructurado para el modelo
    # -------------------------------------------------------------------------
    context = build_analysis_context(notification_payload, af_context)
    log.info("Contexto construido para el modelo:")
    log.info(context["claude_prompt"])

    # -------------------------------------------------------------------------
    # Step 4: el modelo responde con las variables que necesita analizar
    # -------------------------------------------------------------------------
    # Estas variables son rutas (piApiPath) que el MCP Server puede consultar
    # directamente en PI Web API.
    variables_response = llm_client.generate(SYSTEM_PROMPT, context["claude_prompt"])
    log.info("Respuesta del modelo (Step 4) -- variables a consultar:")
    log.info(variables_response)

    # -------------------------------------------------------------------------
    # TODO — Step 5: Obtener datos históricos de PI vía MCP Server
    # -------------------------------------------------------------------------
    # Con la lista de atributos del Step 4, se llamará al MCP Server
    # "aveva-pi-mcp" (que ya está funcionando en C:\MCPServer\MCP Server) para:
    #   a) Crear un bucket de timestamps con create_timeseries_bucket()
    #      (ventana temporal alrededor del momento de la alerta)
    #   b) Consultar los valores históricos con query_by_path()
    #      usando los piApiPath devueltos por el modelo en el Step 4
    #
    # El resultado será una tabla de valores en el tiempo para cada atributo.
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # TODO — Step 6: el modelo analiza los datos y produce el diagnóstico final
    # -------------------------------------------------------------------------
    # Se volverá a llamar a llm_client.generate(), esta vez con los datos
    # históricos del Step 5. El modelo analizará las tendencias y correlaciones
    # entre variables y producirá:
    #   - Lista de posibles causas raíz, ordenadas por probabilidad
    #   - Explicación de por qué cada causa es plausible
    #   - Recomendación de acciones correctivas para cada causa
    #
    # El resultado se presentará al usuario (log, notificación, interfaz web...)
    # -------------------------------------------------------------------------

    log.info("AGENTE RCA: Steps 2-4 completados. Steps 5 y 6 pendientes de implementar.")
