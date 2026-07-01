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

# Usamos el mismo logger que el resto del proyecto.
# Los mensajes aparecerán en la consola con timestamp y nivel (INFO, WARNING...).
log = logging.getLogger(__name__)


async def run_rca_analysis(notification_payload: dict) -> None:
    """Punto de entrada principal del agente RCA.

    Se llama desde webhook.py cada vez que llega una notificación de PI System.
    Se ejecuta en segundo plano (no bloquea la respuesta al servidor de PI).

    Args:
        notification_payload: Diccionario Python con los datos de la alerta
                              tal como los envió PI System. Por ejemplo:
                              {
                                  "EventName": "High Temperature",
                                  "Asset": "PET01",
                                  "Attribute": "Temperature",
                                  "Value": 95.3,
                                  "Timestamp": "2026-07-01T14:30:00Z"
                              }
    """
    log.info("=" * 60)
    log.info("AGENTE RCA: Iniciando análisis de causa raíz")
    log.info("Alerta recibida: %s", notification_payload)
    log.info("=" * 60)

    # -------------------------------------------------------------------------
    # TODO — Step 2: Preparar el contexto estructurado para Claude
    # -------------------------------------------------------------------------
    # Aquí se construirá el mensaje que se enviará al modelo Claude.
    # El mensaje debe incluir:
    #   - El tipo de alerta (p.ej. "temperatura por encima del límite")
    #   - El asset afectado (p.ej. "Reactor PET01")
    #   - El valor que disparó la alerta y el umbral configurado
    #   - El momento en que ocurrió
    #   - Instrucciones a Claude sobre el formato de respuesta esperado
    #     (p.ej. "devuelve una lista de variables de PI que necesitas analizar")
    #
    # Ejemplo de lo que se enviará a Claude:
    # "Se ha detectado una desviación en PET01. La temperatura ha superado
    #  el umbral de 90°C (valor actual: 95.3°C) a las 14:30 UTC.
    #  Indica qué atributos de PI necesitas consultar para determinar la causa."
    # -------------------------------------------------------------------------

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

    log.info("AGENTE RCA: Steps 2-5 pendientes de implementar.")
    log.info("El payload recibido ha sido registrado correctamente.")
