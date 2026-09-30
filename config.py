"""
config.py — Carga y validación de la configuración del Workflow RCA

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


def _entero(nombre: str, por_defecto: int) -> int:
    """Lee una variable de entorno numérica, tolerando que venga vacía.

    Por qué existe: os.environ.get("X", "24") solo aplica el valor por defecto
    si la clave NO EXISTE. Pero .env.example define las variables opcionales
    vacías ("PI_LOOKBACK_HOURS="), y load_dotenv las crea con cadena vacía --
    así que la clave existe, get() devuelve "" e int("") revienta.

    El resultado era que seguir la instrucción del README ("copia .env.example
    a .env") dejaba la aplicación sin arrancar, con un ValueError que no dice
    de qué variable viene. Detectado el 2026-09-08.

    Un valor no numérico también cae al defecto en vez de tumbar el arranque:
    más vale arrancar con el valor documentado y un aviso en consola que no
    arrancar y que PI envíe a un puerto muerto.
    """
    crudo = os.environ.get(nombre)
    if crudo is None or not crudo.strip():
        return por_defecto
    try:
        return int(crudo.strip())
    except ValueError:
        # Aún no hay logging configurado (observability se importa después),
        # así que se avisa por consola.
        print(f"[config] {nombre}={crudo!r} no es un entero; se usa {por_defecto}.")
        return por_defecto


# =============================================================================
# Proveedor de LLM (Gemini por defecto, o Anthropic)
# =============================================================================
# Ver llm_client.py: el resto del workflow llama siempre a llm_client.generate()
# sin conocer qué proveedor hay detrás. Solo se exige la clave de API del
# proveedor realmente seleccionado (ver validate_config()).

# "gemini" (por defecto), "anthropic" o "deepseek".
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

# Razonamiento extendido en la ruta de Anthropic.
#
# Existe para IGUALAR LOS DOS PROVEEDORES, que es el problema que arregla:
# gemini-2.5-flash razona por defecto y la llamada a Anthropic no lo pedía, así
# que cambiar de proveedor cambiaba algo más que el proveedor. Comparar un
# diagnóstico de una ruta con el de la otra no medía el modelo, medía eso.
#
# Es "adaptive", no un presupuesto de tokens: el propio modelo decide cuánto
# razona, que es lo que hace viable encenderlo también en las llamadas cortas
# --  la puerta de la búsqueda y las vueltas del bucle -- sin pagar en todas el
# precio de la más difícil. Con `budget_tokens` habría que elegir un número
# para pasos que no se parecen en nada, y además en claude-opus-4-8 ese
# parámetro ya no se acepta: la API responde 400.
#
# Se puede apagar sin tocar código (ANTHROPIC_THINKING=false) por dos motivos:
# para comparar con y sin él el día que haya con qué medir, y porque razonar
# alarga las llamadas -- si alguna vez empieza a rozar LLM_TIMEOUT_SECONDS,
# esta es la palanca inmediata.
ANTHROPIC_THINKING: bool = (
    os.environ.get("ANTHROPIC_THINKING") or "true"
).strip().lower() in ("1", "true", "yes")

# -----------------------------------------------------------------------------
# DeepSeek (LLM_PROVIDER=deepseek)
# -----------------------------------------------------------------------------
# Se habla con el SDK de Anthropic, no con uno propio: DeepSeek publica un
# endpoint compatible con el protocolo de Anthropic y la llamada tiene la misma
# forma -- model, max_tokens, system, messages. Eso evita una dependencia nueva,
# que en este proyecto no es gratis: cada una lleva revisión de mantenimiento,
# adopción y licencia (izSpecs §1.3, AI-TRACEABILITY.md §6).
#
# Clave de API de DeepSeek. Obligatoria solo si LLM_PROVIDER=deepseek.
# Se obtiene en https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")

# Modelo de DeepSeek. Por defecto el rápido, por el mismo criterio que hace que
# el de Gemini sea 'flash' y no 'pro': lo caro se elige a propósito.
#   deepseek-flash    rápido y barato
#   deepseek-v4-pro   el grande
DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL") or "deepseek-flash"

# El endpoint compatible con Anthropic. Se deja configurable por si mañana
# cambia o hay que pasar por una pasarela interna; el valor por defecto es el
# que documenta DeepSeek (https://api-docs.deepseek.com/guides/anthropic_api).
DEEPSEEK_BASE_URL: str = (
    os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/anthropic"
)

# Techo de tokens de la respuesta del modelo, para AMBOS proveedores.
#
# Es un techo duro que la API aplica cortando la generación en seco: el modelo
# no sabe que existe y no se autorregula. Si el Step 4 o el Step 6 lo alcanzan,
# la respuesta se trunca a mitad del JSON, json.loads() falla y el análisis se
# corta habiendo pagado ya todos los pasos anteriores.
#
# Estaba en 2048 (hardcodeado en llm_client.py desde el 2026-07-02) y se
# quedaba CORTO: la respuesta del Step 4 de la ejecución real del 2026-08-20
# ocupa 8.394 caracteres (~2.100-2.800 tokens) con 30 variables, y con
# MAX_SELECTED_VARIABLES=40 puede llegar a ~3.700. No llegó a fallar porque el
# proveedor por defecto es Gemini, cuya llamada no fijaba ningún tope.
#
# 16000 cubre el peor caso con holgura, deja sitio para los tokens de thinking
# si algún día se activa (cuentan contra este mismo tope en Anthropic) y se
# mantiene por debajo de los timeouts HTTP del SDK sin necesitar streaming.
# Subirlo no cuesta nada: solo se pagan los tokens realmente generados.
#
# Por qué se aplica también a Gemini, que no lo exige (decisión 2026-09-03):
# los dos SDK tienen el mismo concepto, pero Anthropic lo hace OBLIGATORIO
# (messages.create falla sin max_tokens) y Gemini lo deja opcional, aplicando
# el máximo de salida del modelo si se omite. De ahí venía el fallo: en
# Anthropic hubo que poner un número sí o sí y se eligió mal; en Gemini nunca
# hubo techo bajo porque nunca se declaró ninguno.
#
# Ponérselo a Gemini BAJA su techo efectivo, no lo alinea sin más. Se hace a
# propósito: coste acotado y predecible por llamada, y el mismo comportamiento
# en ambas rutas, que es justo para lo que existe llm_client. Si alguna vez
# truncara, se vería en el log como un JSONDecodeError del Step 4 o 6, no en
# silencio.
LLM_MAX_TOKENS: int = _entero("LLM_MAX_TOKENS", 16000)


# =============================================================================
# Variables OPCIONALES (tienen valor por defecto)
# =============================================================================

# Puerto en el que el servidor webhook escuchará peticiones de PI System.
# PI debe apuntar sus notificaciones a: http://ESTE_SERVIDOR:<WEBHOOK_PORT>/notification
WEBHOOK_PORT: int = _entero("WEBHOOK_PORT", 8090)

# Token secreto para verificar el origen de las notificaciones.
# Si está configurado, PI debe enviar este valor en la cabecera "X-PI-Secret".
# Si está vacío, no se valida el origen.
#
# En este despliegue está vacío y así se queda: el canal de entrega HTTP de PI
# Notifications no permite añadir cabeceras propias, así que exigirla dejaría el
# webhook rechazando todas las notificaciones legítimas. Ver la nota
# "DECISIONES DE SEGURIDAD" en webhook.py.
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")

# Expone GET /notifications/history, que devuelve las últimas 50 notificaciones
# COMPLETAS: nombres de activo, jerarquía de planta, valores de KPI y umbrales.
#
# Apagado por defecto (decisión 2026-09-07). Se creó para averiguar qué formato
# enviaba PI durante la integración de julio, y el registro de incidentes cubre
# hoy esa necesidad mucho mejor. Mantenerlo encendido significa servir el
# histórico operacional reciente por HTTP y sin autenticar a cualquiera que
# alcance el puerto -- incoherente con tener webhook.log, incidents/ y
# audit.jsonl fuera del control de versiones justo por contener esos datos.
#
# Encenderlo solo de forma temporal, para depurar una integración nueva.
NOTIFICATION_HISTORY_ENABLED: bool = (
    os.environ.get("NOTIFICATION_HISTORY_ENABLED") or "false"
).strip().lower() in ("1", "true", "yes")

# Zona horaria local para mostrar timestamps en el log.
# Usa nombres de zona IANA, por ejemplo: Europe/Madrid, America/New_York
PI_LOCAL_TIMEZONE: str = os.environ.get("PI_LOCAL_TIMEZONE", "Europe/Madrid")

# =============================================================================
# Observabilidad -- ver observability.py y extensions/observability-spec.md
# =============================================================================

# Log de aplicación: una línea JSON por evento, rotativo a 10 MB x 5.
LOG_FILE: str = os.environ.get("LOG_FILE") or str(Path(__file__).parent / "webhook.log")

# Audit trail. DEBE ser un destino distinto del log de aplicación (spec §2.3) y
# sobrevivir a los reinicios (§2.4). Formato JSON Lines, append-only.
AUDIT_FILE: str = os.environ.get("AUDIT_FILE") or str(Path(__file__).parent / "audit.jsonl")

# Interruptor de parada en caliente (ai-governance §9.5).
# Con WORKFLOW_ENABLED=false el webhook sigue aceptando y registrando las
# notificaciones de PI, pero NO lanza ningún análisis: no se llama al LLM ni a
# los MCP servers. Permite cortar el comportamiento automático sin desplegar ni
# revertir nada. Se lee en cada notificación, así que basta con reiniciar el
# proceso tras cambiarlo (o exportar la variable en el entorno del servicio).
WORKFLOW_ENABLED: bool = (os.environ.get("WORKFLOW_ENABLED") or "true").strip().lower() \
    not in ("0", "false", "no")

# =============================================================================
# Registro de incidentes (deduplicación y persistencia) -- ver incidents.py
# =============================================================================

# Carpeta donde se guarda un fichero JSON por incidente. Es a la vez el
# mecanismo de deduplicación (la creación exclusiva del fichero es la reserva
# atómica) y el registro que sobrevive a un reinicio. NO se purga sola: esos
# ficheros son el archivo de casos, y serán la base de la revisión humana y de
# las métricas de evaluación.
INCIDENTS_DIR: str = os.environ.get("INCIDENTS_DIR") or str(Path(__file__).parent / "incidents")

# Minutos durante los cuales una alerta del mismo activo y KPI se considera el
# mismo incidente, aunque llegue con otro StartTime. Es la capa que protege si
# PI abre un event frame nuevo en cada evaluación de la alarma.
#
# 20 min: el caso a cortar son re-notificaciones cada pocos minutos, así que
# 20 va sobrado. Se bajó desde 60 (2026-09-03) porque una hora suprimiría
# también una desviación genuinamente nueva sobre el mismo activo y KPI.
# 0 desactiva esta capa y deja solo la deduplicación exacta por StartTime.
#
# Recordatorio: esto es una red de seguridad. La deduplicación se arregla en PI
# (NonrepetitionInterval de la Notification Rule y deadband del análisis).
INCIDENT_COOLDOWN_MINUTES: int = _entero("INCIDENT_COOLDOWN_MINUTES", 20)

# Cuántos análisis pueden estar ejecutándose A LA VEZ. Los que sobran esperan
# turno en estado 'recibido'; no se rechaza ninguna alerta.
#
# Hace falta porque la deduplicación protege de repetir el MISMO incidente, no
# de que cinco activos distintos disparen a la vez. Cada análisis consume tres
# recursos que no son nuestros: llamadas al modelo (coste y límites de ritmo),
# un subproceso MCP, y consultas a PI Web API. Ese último es el que manda: PI es
# el historiador de la planta y lo usan más cosas que este workflow -- entre
# ellas PI Vision. Degradarlo a base de consultas en paralelo sería un daño muy
# superior a tardar unos minutos más en diagnosticar.
#
# 3 por defecto: suficiente para que varias alertas de una misma maniobra no se
# encolen de una en una, y lo bastante bajo para no notarse en PI. 0 o menos
# desactiva el límite.
#
# Sin esto no había ninguno: hasta el 2026-09-11 quedaba enmascarado porque la
# llamada al modelo bloqueaba el bucle de eventos y los análisis se serializaban
# solos. Al arreglar aquello, la concurrencia pasó a ser real.
MAX_CONCURRENT_ANALYSES: int = _entero("MAX_CONCURRENT_ANALYSES", 3)

# Segundos que se espera a UNA llamada al modelo antes de darla por perdida.
#
# Sin esto se heredaba el defecto del SDK de Anthropic: 600 s de lectura y dos
# reintentos propios, que se multiplicaban por los tres de tenacity. Nueve
# intentos de diez minutos: hora y media para una sola llamada. Nadie decidió
# eso; salió de encajar dos políticas de reintento sin mirar.
#
# 120 s es holgado: una respuesta real del Step 6 ronda los 2.800 tokens y
# tarda menos de un minuto.
LLM_TIMEOUT_SECONDS: int = _entero("LLM_TIMEOUT_SECONDS", 120)

# Tope DURO de un análisis completo, de principio a fin.
#
# Es la única garantía de verdad, porque cubre también lo que no tiene timeout
# propio: las llamadas MCP de los Steps 2 y 5 pueden colgarse indefinidamente
# si el subproceso deja de responder.
#
# Importa para la persona, no para la máquina: a quien recibe la alerta se le
# avisa UNA vez, y si llega a la sala de control y ve su análisis en cola,
# necesita saber cuánto puede tardar. Con este tope y MAX_CONCURRENT_ANALYSES,
# la espera peor es calculable: ceil(alertas / simultáneos) x este valor.
#
# 10 min: un análisis normal son 2-3. Quien lo agote está roto, no lento.
ANALYSIS_TIMEOUT_SECONDS: int = _entero("ANALYSIS_TIMEOUT_SECONDS", 900)

# Cuánto se sigue reintentando UNA llamada al modelo cuando el error es de los
# que se arreglan solos (5xx, límite de ritmo, conexión).
#
# Antes eran 3 intentos con esperas de 2 y 4 segundos: seis segundos en total.
# Ante un corte de tres minutos del proveedor, el análisis se rendía cuando
# faltaban 174 segundos para que volviera. El problema no era no saber cuándo
# reintentar -- era dejar de intentarlo enseguida.
#
# Se cuenta por TIEMPO y no por número de intentos porque lo que importa es
# cuánto corte se aguanta, no cuántas veces se pregunta.
#
# 90 s NO es un número redondo: sale de la cuenta del presupuesto.
#
# Un análisis hace hasta 3 llamadas al modelo (Step 4 una vez, Step 6 hasta dos
# con MAX_HISTORY_ADJUSTMENTS=1). Y la ventana se puede REBASAR: tenacity decide
# si sigue ANTES de dormir, así que un intento programado dentro de la ventana
# puede ejecutarse hasta 30 s después (la espera más larga). O sea:
#
#     ventana efectiva = 90 + 30 = 120 s por llamada
#     peor caso = trabajo normal (~150 s) + 3 x 120 = 510 s
#
# REHECHO el 2026-09-28, al añadir la búsqueda del reanálisis. Ya no son 3
# llamadas: la puerta añade una y el bucle hasta 5, y cada una puede irse a 120 s
# si el proveedor tiene un corte. Pero el bucle entero lleva su propio tope
# (BUSQUEDA_TIMEOUT_SECONDS), así que no se suman sus 5 llamadas sino ese tope:
#
#     peor caso = 150 + 3 x 120 (Steps 4 y 6) + 120 (la puerta) + 180 (el bucle)
#               = 810 s
#
# y 810 cabe en ANALYSIS_TIMEOUT_SECONDS=900. Con 600 no cabía, y el conjunto
# moriría llevándose por delante un diagnóstico que ya estaba hecho.
#
# Si se sube esta ventana, o se añaden llamadas, hay que rehacer esta cuenta.
LLM_RETRY_WINDOW_SECONDS: int = _entero("LLM_RETRY_WINDOW_SECONDS", 90)

# Qué ventana muestra la pantalla de la sala de control al abrirse, en horas, y
# cuántos incidentes como mucho.
#
# Hacen falta porque no se borra ningún incidente nunca: con veinte alertas al
# día son 7.300 al año, y una lista de 7.300 elementos en un monitor no la lee
# nadie. El operario puede ampliar el rango o pedir más desde la propia
# pantalla; esto es solo con qué se encuentra al llegar.
#
# 24 h y 50: un turno entero de contexto sin que la lista deje de ser abarcable.
SCREEN_DEFAULT_HOURS: int = _entero("SCREEN_DEFAULT_HOURS", 24)
SCREEN_DEFAULT_LIMIT: int = _entero("SCREEN_DEFAULT_LIMIT", 50)

# Días tras los cuales se PODA el "trace" de un incidente: los prompts enviados
# al modelo y sus respuestas. El caso en sí -- payload, estado, diagnóstico y,
# cuando exista, la revisión humana -- NO se borra nunca.
#
# Por qué podar solo el trace: medido sobre un incidente real, el trace es el
# 98,9 % del fichero (501 KB de 507 KB); el caso son 5,7 KB. Podarlo recorta el
# 99 % del espacio sin tocar lo que hace falta para la evaluación -- contrastar
# hipótesis contra causas confirmadas -- que es justo lo que un borrado por
# antigüedad destruiría cuando empezara a tener valor estadístico.
#
# Se conserva un resumen (qué claves había y cuánto ocupaban) en vez de borrar
# sin rastro: ai-governance §4.5 pide mantener inmutables los METADATOS de
# trazabilidad aunque el CONTENIDO se elimine.
#
# 90 días: tiempo de sobra para que alguien cuestione un diagnóstico y se
# investigue con el prompt original delante. Pasado eso, su valor es casi nulo.
# 0 desactiva la poda y lo conserva todo indefinidamente.
#
# Nota: esto es una decisión OPERATIVA (crecimiento de disco), no de
# cumplimiento. El §4.5 exige una política de retención para prompts "que
# contengan datos personales", y estos no los tienen.
INCIDENT_TRACE_RETENTION_DAYS: int = _entero("INCIDENT_TRACE_RETENTION_DAYS", 90)


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
PI_LOOKBACK_HOURS: int = _entero("PI_LOOKBACK_HOURS", 24)

# Techo absoluto de la ventana. El modelo puede pedir más histórico, pero el
# código decide cuánto concede: la petición se recorta a este valor. Misma
# filosofía que la puerta de autorización del Step 4 -- el modelo propone, el
# código autoriza. 336 h = 14 días.
PI_MAX_LOOKBACK_HOURS: int = _entero("PI_MAX_LOOKBACK_HOURS", 336)

# Cuántas veces se permite repetir los Steps 5-6 con otra ventana, a petición
# del modelo. Cuenta los reajustes en las dos direcciones: ampliaciones y
# estrechamientos. Cada uno cuesta una consulta a PI (barata) y una llamada al
# LLM (no tanto), así que por defecto se concede uno solo.
# 0 desactiva el mecanismo y deja el comportamiento fijo de antes.
MAX_HISTORY_ADJUSTMENTS: int = _entero("MAX_HISTORY_ADJUSTMENTS", 1)

# Puntos por variable a los que se ajusta la resolución de cada ventana del
# catálogo. Ventana y resolución se mueven juntas: ampliar manteniendo los
# 15 min dispararía el tamaño del prompt (7 días a 15 min son ~672 puntos por
# variable, y son decenas de variables). Con este objetivo, 7 días salen a 2 h
# y ~84 puntos -- menos que la ventana corta de 24 h a 15 min -- y 2 h salen a
# 1 minuto. Ver derive_interval en pi_client.py.
PI_TARGET_POINTS_PER_VARIABLE: int = _entero("PI_TARGET_POINTS_PER_VARIABLE", 120)

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
PI_QUERY_INTERVAL_VALUE: int = _entero("PI_QUERY_INTERVAL_VALUE", 15)
PI_QUERY_INTERVAL_UNIT: str = os.environ.get("PI_QUERY_INTERVAL_UNIT") or "minutes"

# Número máximo de variables que se aceptan de la selección del Step 4.
# El prompt pide al modelo que elija solo lo que tenga una razón de causalidad
# plausible, pero nada se lo impide: sin tope, una respuesta que liste medio AF
# infla el batch del Step 5 y, sobre todo, el prompt del Step 6. Si se supera,
# se recortan las sobrantes (no se aborta) -- ver _validate_selected_variables
# en workflow.py.
#
# El valor está calibrado contra la ejecución real del 2026-08-20 (alerta de
# Hydraulic Efficiency en PS20102 A03 PS02 Pump 02): de 164 atributos
# disponibles en el AF, el modelo seleccionó 30, todas legítimas. Un tope de 20
# habría recortado 10, incluida la comparación con la bomba hermana
# (PS20101 Pump 01) que el propio prompt pide como prioridad 4. Se deja en 40
# para dar margen sobre ese caso sin dejar de frenar una respuesta desbocada.
# Es un guardarraíl de coste y ruido, no un límite del dominio.
MAX_SELECTED_VARIABLES: int = _entero("MAX_SELECTED_VARIABLES", 40)


# =============================================================================
# Alcance dentro del Asset Framework
# Reanalisis permitidos por incidente (Fase 3). El primer analisis no cuenta:
# con 2, un expediente puede llegar a tener tres pasadas.
#
# Mientras quede presupuesto, descartar TODAS las causas deja de cerrar el
# incidente: se intercala el relanzado, y solo al agotarlo se concluye que la
# causa no se determino. Hasta el 2026-09-23 era terminal de inmediato.
#
# Se enseña en la pantalla ("te queda 1 de 2"): un presupuesto que se ve se
# gasta mejor que uno que no se ve.
MAX_REANALISIS: int = _entero("MAX_REANALISIS", 2)

# Cuándo dos causas son la misma, en porcentaje.
#
# Se compara por CONTENCIÓN: qué parte de las palabras con contenido de la más
# corta aparece en la más larga. No por Jaccard, y no es indiferente -- medido
# el 2026-09-29 sobre el primer reanálisis real (OX313, dos pasadas, cinco
# causas):
#
#   par                                        jaccard   contención
#   "colmatación de los difusores..." x2          0,38         1,00   <- repetida
#   carga orgánica en la entrada / MLSS           0,14         0,38   distintas
#   el resto de pares                            <=0,10       <=0,40
#
# Jaccard deja 0,38 contra 0,14: separa, pero por poco, y se estrecha en cuanto
# la repetición viene con más palabras alrededor -- que es justo lo que hace un
# modelo al reformular. La contención dio 1,00 contra 0,40, un hueco donde 80
# cae holgado por los dos lados.
#
# Es UNA medida sobre UN caso. Si aparecen falsos positivos, esta es la perilla;
# el log dice siempre qué se descartó y por qué parecido.
UMBRAL_CAUSA_REPETIDA_PCT: int = _entero("UMBRAL_CAUSA_REPETIDA_PCT", 80)


# =============================================================================
# La búsqueda de la sospecha (Fase 3, §5 ter del documento de diseño)
# =============================================================================
# Cuando el operario escribe una sospecha que señala un activo que NO está en
# ninguno de los tres bloques del af_context, el modelo no puede pedir esa
# variable ni queriendo. La búsqueda lo resuelve, y es el ÚNICO sitio del
# proyecto donde el modelo recibe una herramienta.
#
# Dos topes, y el que manda es el de iteraciones:
#
#   - 5 iteraciones. Medido el 2026-09-23: una llamada al modelo son ~15 s y una
#     consulta al grafo ~2 s, así que cinco vueltas son unos 85 s.
#   - 180 s de reloj. NO es el mando: es la guarda contra el cuelgue. Son 180 y
#     no 120 porque una sola llamada puede irse a LLM_TIMEOUT_SECONDS=120, y con
#     120 de tope una llamada lenta se comería la búsqueda entera.
#
# Agotar cualquiera de los dos NO es un fallo: la búsqueda devuelve "no lo he
# encontrado", el análisis sigue y el diagnóstico se produce igual. Es contexto
# que mejora la selección, no un requisito -- igual que la ficha del equipo.
MAX_ITERACIONES_BUSQUEDA: int = _entero("MAX_ITERACIONES_BUSQUEDA", 5)
BUSQUEDA_TIMEOUT_SECONDS: int = _entero("BUSQUEDA_TIMEOUT_SECONDS", 180)

# Cuántas sospechas distintas se pueden atender en UNA pasada. El presupuesto de
# arriba es del paso entero, no de cada objetivo: dos búsquedas más una elección
# son 3 llamadas de las 5, así que dos objetivos caben sin tocar la aritmética
# de ANALYSIS_TIMEOUT_SECONDS.
#
# Lo que sobra del tope NO se pierde ni se descarta en silencio: queda como
# sospecha pendiente colgada de su causa, visible en la pantalla y en el orden
# en que la persona las ve. Por eso el tope puede ser bajo sin que nadie se
# quede sin respuesta.
MAX_BUSQUEDAS_POR_PASADA: int = _entero("MAX_BUSQUEDAS_POR_PASADA", 2)

# Tope de atributos de TODO el bloque añadido, sumando los objetivos atendidos.
#
# 80 sale de una medida, no de una intuición: el subárbol completo de la bomba
# PS20102 son 16 grupos y 74 atributos, y sus atributos PROPIOS son sólo 4 --
# identidad y configuración. Los 70 útiles (Head, Hydraulic Efficiency,
# Hydraulic Power) cuelgan de 'Indicators'. Añadir un activo sin sus hijos es la
# apariencia de haber atendido la sospecha sin atenderla, así que se baja; y 80
# es lo que hace falta para que quepa uno entero con margen.
#
# Un instrumento suelto, en cambio, es 1 atributo (FT105, SS101...).
MAX_ATRIBUTOS_ANADIDOS: int = _entero("MAX_ATRIBUTOS_ANADIDOS", 80)

# Ramas del AF que la búsqueda NO puede devolver nunca. No son proceso: son
# diagramas, modelos importados y nodos de navegación, y un elemento de ahí
# metido en el contexto sería ruido con pinta de dato.
#
# Va aparte del filtro de planta porque son dos cosas distintas: aquel dice en
# qué instalación estamos, éste qué partes de ella son mediciones.
AF_RAMAS_NO_PROCESO: list[str] = [
    r.strip() for r in (os.environ.get("AF_RAMAS_NO_PROCESO")
                        or "Sankey Diagrams|PM_from_PME").split("|") if r.strip()
]


# =============================================================================
# El grafo del AF NO contiene solo esta planta. Es un entorno compartido de
# demostración: 8.404 elementos en 26 raíces (una cementera, Green H2, CPG,
# Pharma Lab, un conector de Wonderware, árboles de navegación...), de las que
# WWTP es una, con 357 elementos. Medido el 2026-09-22 sobre el grafo entero.
#
# Eso importa porque los nombres se repiten: 47 nombres de elemento del WWTP
# existen también en otra raíz, y 45 se repiten dentro del propio WWTP.
#
# El caso que más duele no es un choque entre plantas distintas, sino un ESPEJO
# de esta misma:
#
#     WWTP\Operational\1 - Intake\Line 1\PS01102            <- el modelo del AF
#     WonderwareHistorian Connector\...\WWTP_Demo\...\PS01102   <- la misma bomba, otro conector
#
# Mismos nombres de equipo, atributos distintos. Un recorrido que empareje por
# nombre y no acote puede devolver un contexto perfectamente formado con las
# variables del modelo equivocado, sin que nada falle de forma visible.
#
# OJO: el filtro se aplica ANCLADO a la raíz (WWTP + separador), porque
# path_contains es una SUBCADENA y "WWTP" a secas también casa con
# "WWTP_Demo". Ver graph_client.build_af_context().
#
# Este es el techo de todo lo que se consulte en el AF, no solo del Step 2.
AF_PLANT_ROOT: str = (os.environ.get("AF_PLANT_ROOT") or "WWTP").strip()

# Elementos del AF que se añaden al contexto de TODA alerta de esta planta,
# estén donde estén en la jerarquía.
#
# Para qué: el Step 2 recorre el subárbol del activo y el de su subsistema, así
# que solo ve lo que cuelga de ahí. Una alerta del biológico no alcanza la
# turbidez del intake -- que vive en WWTP\Operational - Intake, otra rama --
# aunque sea justo lo que la explica.
#
# **Vacía a propósito**: la lista la decide quien conoce la planta, no el
# código. Un elemento por línea o separados por "|", con el nombre tal como
# aparece en el AF. Entran en el prompt como la prioridad MÁS BAJA de todas:
# pueden influir o no, y se miran solo si lo cercano no explica la desviación.
# Tope de atributos que puede aportar el bloque de planta entero.
#
# Con la lista escrita a mano no hace falta -- diez sensores de un atributo cada
# uno son diez variables --, pero una entrada con el sufijo "\*" arrastra su
# subarbol, y ahi si: "Operational\*" serian cientos, en el prompt de CADA
# alerta. Es un guardarrail de coste, no un limite del dominio, y cuando recorta
# lo dice en el log.
MAX_PLANT_CONTEXT_ATTRIBUTES: int = _entero("MAX_PLANT_CONTEXT_ATTRIBUTES", 60)

AF_PLANT_CONTEXT_ELEMENTS: list[str] = [
    e.strip() for e in (os.environ.get("AF_PLANT_CONTEXT_ELEMENTS") or "").split("|")
    if e.strip()
]


# =============================================================================
# Validación de configuración
# =============================================================================

def validate_config() -> list[str]:
    """Comprueba que todas las variables obligatorias están definidas.

    Devuelve una lista con los nombres de las variables que faltan.
    Si la lista está vacía, la configuración es correcta y el workflow puede arrancar.
    Solo se exige la clave de API del proveedor seleccionado en LLM_PROVIDER
    (no ambas), ya que el workflow solo llama a uno de los dos.

    Uso típico al arrancar el servidor:
        missing = validate_config()
        if missing:
            print("Faltan variables:", missing)
    """
    missing = []

    if LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
    elif LLM_PROVIDER == "deepseek":
        if not DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
    elif LLM_PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY:
            missing.append("ANTHROPIC_API_KEY")
    else:
        missing.append(f"LLM_PROVIDER (valor invalido: '{LLM_PROVIDER}', "
                       f"usa 'gemini', 'anthropic' o 'deepseek')")

    return missing
