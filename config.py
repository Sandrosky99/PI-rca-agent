"""
config.py — Carga y validación de la configuración del Agente RCA

¿Qué hace este fichero?
  Lee las variables de entorno definidas en el fichero ".env" y las pone
  disponibles para el resto del código. También comprueba que las variables
  obligatorias están presentes y avisa si falta alguna.

¿Por qué usar variables de entorno en lugar de escribir los valores directamente?
  Las credenciales (como la clave de API de Anthropic) no deben estar escritas
  en el código fuente, ya que el código puede acabar en un repositorio git o
  compartirse con otros. Las variables de entorno permiten separar la
  configuración sensible del código.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Cargar el fichero .env situado en la misma carpeta que este script.
# Si el fichero no existe (por ejemplo, en producción con variables ya definidas
# en el sistema), simplemente no hace nada y usa las variables del entorno.
load_dotenv(Path(__file__).parent / ".env")


# =============================================================================
# Proveedor de LLM (Gemini por defecto, o Anthropic)
# =============================================================================
# Ver llm_client.py: el resto del agente llama siempre a llm_client.generate()
# sin conocer qué proveedor hay detrás. Solo se exige la clave de API del
# proveedor realmente seleccionado (ver validate_config()).

# "gemini" (por defecto) o "anthropic".
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

# Clave de API de Google (Gemini). Obligatoria si LLM_PROVIDER=gemini.
# Se obtiene en https://aistudio.google.com/apikey
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")

# Modelo de Gemini a usar. Ajusta aquí si prefieres otro (p.ej. una variante
# más rápida/económica) sin tocar el código.
# Nota: se usa "or" en vez de os.environ.get(key, default) porque .env.example
# deja la variable presente pero vacía ("GEMINI_MODEL="), y get() solo aplica
# el valor por defecto cuando la clave no existe, no cuando está vacía.
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"

# Clave de API de Anthropic. Obligatoria solo si LLM_PROVIDER=anthropic.
# Se obtiene en https://console.anthropic.com/settings/keys
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")

# Modelo de Claude a usar si LLM_PROVIDER=anthropic.
ANTHROPIC_MODEL: str = os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-8"


# =============================================================================
# Variables OPCIONALES (tienen valor por defecto)
# =============================================================================

# Puerto en el que el servidor webhook escuchará peticiones de PI System.
# PI debe apuntar sus notificaciones a: http://ESTE_SERVIDOR:<WEBHOOK_PORT>/notification
WEBHOOK_PORT: int = int(os.environ.get("WEBHOOK_PORT", "8090"))

# Token secreto para verificar el origen de las notificaciones.
# Si está configurado, PI debe enviar este valor en la cabecera "X-PI-Secret".
# Si está vacío, no se valida el origen (útil en redes seguras internas).
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")

# Zona horaria local para mostrar timestamps en el log.
# Usa nombres de zona IANA, por ejemplo: Europe/Madrid, America/New_York
PI_LOCAL_TIMEZONE: str = os.environ.get("PI_LOCAL_TIMEZONE", "Europe/Madrid")

# Carpeta del proyecto afkg-graph-mcp (Step 2: consulta de la estructura del AF).
# Se usa para lanzar el servidor MCP como subproceso vía "uv run --directory ...",
# igual que hace Claude Code según C:\MCPServer\MCP Server\.mcp.json.
AFKG_GRAPH_MCP_DIR: str = os.environ.get("AFKG_GRAPH_MCP_DIR") or r"C:\MCPServer\afkg-graph-mcp"

# Carpeta del proyecto aveva-pi-mcp (Step 5: datos históricos de PI Web API).
AVEVA_PI_MCP_DIR: str = os.environ.get("AVEVA_PI_MCP_DIR") or r"C:\MCPServer\MCP Server"

# Ventana de lookback (horas antes de StartTime) para la PRIMERA consulta del
# Step 5. Las alertas son desviaciones de KPI (degradación gradual), así que se
# mira hacia atrás desde la detección, nunca hacia adelante.
#
# 24 h es un punto de partida, no un límite: basta para diagnosticar una
# degradación gradual -- validado en pruebas reales, donde la tendencia
# relevante ya es visible en las últimas ~14 h -- y mantiene el prompt del
# Step 6 en un tamaño razonable. Si el modelo, ya con los datos delante,
# concluye que necesita más histórico, puede pedirlo y el workflow repite los
# Steps 5-6 con una ventana mayor (ver MAX_HISTORY_ADJUSTMENTS).
#
# NOTA (2026-08-31): hasta esta fecha, este valor se justificaba en parte por
# mantener al modelo por debajo del ciclo con el que se generan las
# desviaciones sintéticas del entorno de demo, para que no detectase esa
# periodicidad. Eso era ocultar evidencia para dirigir el diagnóstico, y era
# incompatible con dejar que la ventana se amplíe. Se ha sustituido por
# DEMO_MODE, que se lo dice al modelo de forma explícita en vez de escondérselo.
PI_LOOKBACK_HOURS: int = int(os.environ.get("PI_LOOKBACK_HOURS", "24"))

# Techo absoluto de la ventana. El modelo puede pedir más histórico, pero el
# código decide cuánto concede: la petición se recorta a este valor. Misma
# filosofía que la puerta de autorización del Step 4 -- el modelo propone, el
# código autoriza. 336 h = 14 días.
PI_MAX_LOOKBACK_HOURS: int = int(os.environ.get("PI_MAX_LOOKBACK_HOURS", "336"))

# Cuántas veces se permite repetir los Steps 5-6 con otra ventana, a petición
# del modelo. Cuenta los reajustes en las dos direcciones: ampliaciones y
# estrechamientos. Cada uno cuesta una consulta a PI (barata) y una llamada al
# LLM (no tanto), así que por defecto se concede uno solo.
# 0 desactiva el mecanismo y deja el comportamiento fijo de antes.
MAX_HISTORY_ADJUSTMENTS: int = int(os.environ.get("MAX_HISTORY_ADJUSTMENTS", "1"))

# Puntos por variable a los que se ajusta la resolución de cada ventana del
# catálogo. Ventana y resolución se mueven juntas: ampliar manteniendo los
# 15 min dispararía el tamaño del prompt (7 días a 15 min son ~672 puntos por
# variable, y son decenas de variables). Con este objetivo, 7 días salen a 2 h
# y ~84 puntos -- menos que la ventana corta de 24 h a 15 min -- y 2 h salen a
# 1 minuto. Ver derive_interval en pi_client.py.
PI_TARGET_POINTS_PER_VARIABLE: int = int(os.environ.get("PI_TARGET_POINTS_PER_VARIABLE", "120"))

# Escalera de ventanas (en horas) que se le ofrecen al modelo como catálogo
# cerrado en el Step 6. PI_LOOKBACK_HOURS debe estar en la lista: es la que se
# usa en la primera pasada y la que el modelo ve como "actual".
#
# Se le ofrece un catálogo en vez de dejarle proponer un número libre de horas
# por dos razones: elimina las respuestas inservibles (horas como texto, o que
# no cambian nada), y le hace visible el coste de lo que pide -- cada opción
# lleva su resolución al lado, así que ve el intercambio entre histórico y
# detalle. La resolución de cada opción NO se configura aquí: la deriva
# pi_client.window_menu() con derive_interval(), para que catálogo y cálculo
# real no puedan desalinearse.
#
# El catálogo va en las dos direcciones. Hacia arriba (48 h, 7 d, 14 d) para
# tendencias lentas; hacia abajo (8 h, 2 h) para fenómenos que a 15 minutos son
# invisibles o quedan solapados -- cavitación, golpe de ariete, ciclado anómalo
# de arranques, oscilación de una válvula. Estrechar la ventana solo se concede
# si sigue cubriendo el inicio de la desviación: ver _parse_history_request.
#
# Las opciones por encima de PI_MAX_LOOKBACK_HOURS se descartan al construir el
# menú. La resolución de cada una NO se configura aquí (ver arriba).
PI_WINDOW_LADDER_HOURS: list[int] = [
    int(h) for h in (os.environ.get("PI_WINDOW_LADDER_HOURS") or "2,8,24,48,168,336").split(",")
    if h.strip()
]

# Modo demostración. En este entorno las desviaciones de prueba se generan de
# forma sintética con un ciclo periódico. Con DEMO_MODE=true se le advierte al
# modelo en el prompt del Step 6, para que sepa que un patrón repetitivo puede
# ser un artefacto del generador y no del proceso.
#
# Sustituye a la práctica anterior de recortar la ventana para que no llegase a
# ver esa periodicidad: si existe un patrón en los datos, el sistema debe poder
# verlo, y la respuesta correcta es etiquetarlo, no ocultarlo. En producción
# debe quedarse en false.
DEMO_MODE: bool = os.environ.get("DEMO_MODE", "").strip().lower() in ("1", "true", "yes")

# Resolución temporal (intervalo entre puntos) para las consultas del Step 5.
# Uniforme para todas las variables del bucket, para poder correlacionarlas
# directamente por timestamp.
PI_QUERY_INTERVAL_VALUE: int = int(os.environ.get("PI_QUERY_INTERVAL_VALUE", "15"))
PI_QUERY_INTERVAL_UNIT: str = os.environ.get("PI_QUERY_INTERVAL_UNIT") or "minutes"

# Número máximo de variables que se aceptan de la selección del Step 4.
# El prompt pide al modelo que elija solo lo que tenga una razón de causalidad
# plausible, pero nada se lo impide: sin tope, una respuesta que liste medio AF
# infla el batch del Step 5 y, sobre todo, el prompt del Step 6. Si se supera,
# se recortan las sobrantes (no se aborta) -- ver _validate_selected_variables
# en agent.py.
#
# El valor está calibrado contra la ejecución real del 2026-08-20 (alerta de
# Hydraulic Efficiency en PS20102 A03 PS02 Pump 02): de 164 atributos
# disponibles en el AF, el modelo seleccionó 30, todas legítimas. Un tope de 20
# habría recortado 10, incluida la comparación con la bomba hermana
# (PS20101 Pump 01) que el propio prompt pide como prioridad 4. Se deja en 40
# para dar margen sobre ese caso sin dejar de frenar una respuesta desbocada.
# Es un guardarraíl de coste y ruido, no un límite del dominio.
MAX_SELECTED_VARIABLES: int = int(os.environ.get("MAX_SELECTED_VARIABLES", "40"))


# =============================================================================
# Validación de configuración
# =============================================================================

def validate_config() -> list[str]:
    """Comprueba que todas las variables obligatorias están definidas.

    Devuelve una lista con los nombres de las variables que faltan.
    Si la lista está vacía, la configuración es correcta y el agente puede arrancar.
    Solo se exige la clave de API del proveedor seleccionado en LLM_PROVIDER
    (no ambas), ya que el agente solo llama a uno de los dos.

    Uso típico al arrancar el servidor:
        missing = validate_config()
        if missing:
            print("Faltan variables:", missing)
    """
    missing = []

    if LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
    elif LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    else:
        missing.append(f"LLM_PROVIDER (valor invalido: '{LLM_PROVIDER}', usa 'gemini' o 'anthropic')")

    return missing
