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
# Variables OBLIGATORIAS
# =============================================================================

# Clave de API de Anthropic: sin ella el agente no puede llamar a Claude.
# Se define en el fichero .env como: ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")


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


# =============================================================================
# Validación de configuración
# =============================================================================

def validate_config() -> list[str]:
    """Comprueba que todas las variables obligatorias están definidas.

    Devuelve una lista con los nombres de las variables que faltan.
    Si la lista está vacía, la configuración es correcta y el agente puede arrancar.

    Uso típico al arrancar el servidor:
        missing = validate_config()
        if missing:
            print("Faltan variables:", missing)
    """
    missing = []

    if not ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")

    return missing
