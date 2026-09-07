"""
workflow.py — Workflow de Análisis de Causa Raíz (RCA)

¿Qué hace este fichero?
  Contiene la lógica central del workflow: recibe la información de la alerta
  de PI System y, paso a paso, usa el modelo de IA Claude para identificar
  las posibles causas raíz y recomendar acciones correctivas.

¿Cómo encaja en el flujo completo?
  webhook.py recibe la notificación de PI  →  llama a run_rca_analysis()  →
  (Steps 2-6 a continuación)

Estado actual:
  Están implementados los Steps 1-6 (recepción de la notificación en
  webhook.py; consulta del AF vía graph_client.py; construcción del mensaje
  en build_analysis_context(); llamada al modelo; datos históricos de PI vía
  pi_client.py; diagnóstico final vía build_diagnosis_context()). Pendiente:
  decidir el canal de salida del diagnóstico (hoy solo queda en el log).

Flujo completo del workflow:
  Step 2 → Consultar la estructura real del AF (afkg-graph-mcp, graph_client.py)
           para el asset y el subsistema de la alerta y obtener sus atributos
           disponibles (piApiPath)
  Step 3 → Preparar el contexto estructurado para el modelo, incluyendo los
           atributos reales del Step 2 (para que el modelo elija solo entre
           variables que existen de verdad, no nombres "de libro")
  Step 4 → El modelo decide, de esas variables reales, cuáles necesita analizar
           (su respuesta pasa por _validate_selected_variables antes de usarse:
           el modelo propone, el código autoriza)
  Step 5 → Obtener datos históricos de esas variables vía MCP Server
           (aveva-pi-mcp, pi_client.py), en la ventana [StartTime -
           PI_LOOKBACK_HOURS, StartTime] y resolución PI_QUERY_INTERVAL_*
  Step 6 → El modelo analiza los datos y produce el diagnóstico final
"""

import json
import logging
from datetime import datetime

import pytz

import config
import graph_client
import llm_client
import pi_client

# Usamos el mismo logger que el resto del proyecto.
# Los mensajes aparecerán en la consola con timestamp y nivel (INFO, WARNING...).
log = logging.getLogger(__name__)


# =============================================================================
# System prompt del workflow (dominio fijo, se envía en TODAS las llamadas al
# modelo — Step 4 y Step 6 — vía el parámetro system/system_instruction).
# =============================================================================
# La API del modelo es stateless entre llamadas: no hay "memoria" real entre el
# Step 4 y el Step 6 salvo lo que se envíe en cada request. Por eso el rol y el
# dominio del workflow se fijan aquí como constante, separados del mensaje
# dinámico de cada alerta (build_analysis_context), en vez de repetirlos "a
# mano" en cada prompt: así el workflow nunca actúa como un asistente genérico,
# pase lo que pase en el mensaje concreto.
SYSTEM_PROMPT = (
    "Eres el analista de causa raíz (RCA) de una planta de tratamiento "
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
    "seleccionar dichas variables de la lista que se comparte, no inventarlas.\n\n"
    "Un KPI calculado (como el que ha disparado esta alerta) casi nunca se "
    "explica por una sola variable física: prioriza tu selección en este "
    "orden, sin quedarte solo en el primer nivel:\n"
    "1. El propio KPI en alerta y su 'Reference' o baseline homólogo, si "
    "existe -- para saber si la desviación es real frente a su "
    "comportamiento habitual, no solo frente al límite de alarma.\n"
    "2. Otros indicadores calculados del mismo activo (bajo su nodo "
    "'Indicators') que puedan compartir entradas físicas con el KPI en "
    "alerta o mostrar una tendencia correlacionada -- una desviación "
    "calculada suele dejar huella en más de un indicador del mismo equipo, "
    "no solo en el que disparó la alerta. De cada indicador, quédate solo "
    "con su resultado propio (atributos como 'Calculation', 'Reference', "
    "'Deviation', 'Inhibition', o nombres del tipo 'Theoretical <algo>'); "
    "ignora sus atributos que sean simplemente una copia de una magnitud "
    "física de entrada a su fórmula (p. ej. Flow, Head, Pump Speed, Active "
    "Power, Fluid Density, Gravity Acceleration) -- si esa magnitud es "
    "relevante, pídela una sola vez desde su fuente física (ver punto 3), "
    "no la repitas por cada indicador que la use como entrada.\n"
    "3. Los meters/atributos físicos que alimentan directamente el cálculo "
    "de esas variables. Si el mismo activo expone la misma magnitud física "
    "en más de un sitio del árbol -- por ejemplo, un atributo directo del "
    "elemento, un 'Value' bajo su rama 'Meters', o el mismo nombre repetido "
    "como entrada de varios indicadores distintos -- con nombre o unidad "
    "coincidentes, elige solo uno: probablemente sea la misma fuente de "
    "datos vista desde varios puntos del AF, y pedir más de una copia no "
    "aporta información nueva.\n"
    "4. Contexto del nivel superior (el elemento que agrupa al activo) solo "
    "si aporta una causa compartida plausible (p. ej. una condición aguas "
    "arriba) o permite comparar con un activo equivalente.\n"
    "No selecciones una variable solo porque aparece en los datos: cada una "
    "debe tener una razón de causalidad o comparación plausible con la "
    "desviación concreta, para no acumular variables irrelevantes."
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

_DIAGNOSIS_OBJECTIVE_SECTION = (
    "Tienes los datos históricos de las variables de proceso que tú mismo "
    "elegiste en la interacción anterior para investigar esta desviación. Tu "
    "objetivo ahora es identificar qué ha provocado la desviación concreta "
    "descrita más abajo, apoyándote únicamente en estos datos y en tu "
    "conocimiento del dominio de depuración de aguas residuales y bombeo.\n\n"
    "Devuelve entre 2 y 3 causas raíz plausibles, ordenadas de mayor a menor "
    "probabilidad según la evidencia de los datos -- no rellenes hasta 3 si "
    "los datos solo sustentan una o dos con confianza razonable. Para cada "
    "causa, incluye una acción de resolución concreta que un ingeniero de "
    "procesos pueda ejecutar.\n\n"
    "La ventana de histórico que se te entrega (indicada más abajo) es un "
    "punto de partida, no un límite del sistema. Si al analizarla concluyes "
    "que la causa no puede determinarse sin mirar más atrás -- porque la "
    "tendencia ya viene degradada desde el inicio de la ventana, porque "
    "necesitas comparar contra el comportamiento habitual del activo, o "
    "porque el fenómeno tiene un periodo más largo que la ventana -- puedes "
    "solicitar una ventana mayor mediante la clave history_request del "
    "formato de respuesta, eligiendo una de las opciones concretas que se te "
    "ofrecen allí. No propongas una ventana distinta de esas. Pídela solo si "
    "de verdad cambiaría tu diagnóstico: cada ampliación repite la consulta a "
    "PI y este análisis, y una ventana más larga se entrega con menos "
    "resolución. Y pídela además de diagnosticar, nunca en lugar de hacerlo: "
    "devuelve siempre tus mejores causas con los datos que ya tienes."
)

# Advertencia que se inyecta solo si config.DEMO_MODE está activo. Sustituye a
# la práctica anterior de recortar la ventana para que el modelo no llegase a
# ver la periodicidad del generador de pruebas: si el patrón está en los datos,
# se etiqueta, no se esconde.
_DEMO_MODE_NOTE = (
    "\n\nAviso sobre el origen de los datos: este análisis se ejecuta sobre un "
    "entorno de demostración, en el que las desviaciones de este activo se "
    "provocan de forma sintética siguiendo un ciclo periódico. Si observas un "
    "patrón repetitivo o una periodicidad regular en las series, considera "
    "que probablemente sea un artefacto del generador de pruebas y no un "
    "comportamiento real del proceso, y no lo propongas como causa raíz. El "
    "resto del análisis debe ser el que harías sobre datos reales."
)

_DIAGNOSIS_DATA_SECTION_INTRO = (
    "A continuación tienes la serie histórica de cada variable. Cada entrada "
    "incluye el elemento del AF de origen (mismo 'element' que elegiste en "
    "la interacción anterior), la unidad de ingeniería, y la lista de pares "
    "timestamp/valor en UTC.\n\n"
    "Algunas variables no tienen historización real (son atributos estáticos "
    "o de configuración, no series temporales) -- se marcan explícitamente "
    "como tales en vez de mostrarte una lista de valores; no las trates "
    "como una medición real. Algunos puntos pueden venir como un estado "
    "digital (texto) en vez de un número -- por ejemplo, cuando el cálculo "
    "no pudo evaluarse en ese instante; interprétalo como información de "
    "contexto (el KPI no estaba disponible en ese momento), no como un "
    "fallo que debas explicar."
)

_DIAGNOSIS_FINAL_INSTRUCTION = (
    "Devuelve tu respuesta como un único objeto JSON válido con exactamente "
    "dos claves.\n\n"
    "1. \"root_causes\": un array de 2 o 3 objetos (nunca menos de 2 ni más "
    "de 3), cada uno con exactamente tres claves:\n"
    "- \"cause\": descripción breve de la causa raíz.\n"
    "- \"explanation\": por qué los datos respaldan esta causa (referencia "
    "las variables y la tendencia concreta que la sustentan).\n"
    "- \"recommended_action\": acción de resolución concreta para esta causa.\n"
    "El array debe estar ordenado de mayor a menor probabilidad (el primer "
    "elemento es la causa más probable).\n\n"
    "2. \"history_request\": un objeto con exactamente tres claves, para "
    "solicitar más histórico si lo necesitas:\n"
    "- \"needed\": true o false. Pon false si la ventana actual te basta.\n"
    "- \"window_option\": el identificador de UNA de las opciones del "
    "catálogo que aparece justo debajo, copiado literalmente. No inventes "
    "otro valor ni propongas un número de horas propio. Cadena vacía si "
    "needed es false.\n"
    "- \"trend_start\": instante en el que, según los datos que tienes, "
    "empieza el cambio de tendencia de las variables que te interesan, en "
    "UTC y formato ISO 8601 (por ejemplo \"2026-08-31T14:00:00Z\"). "
    "**Obligatorio si eliges una ventana más corta que la actual**: se usa "
    "para comprobar que la ventana más corta sigue cubriendo el inicio de la "
    "desviación, y si no lo cubre se te concederá la más corta que sí lo "
    "haga. Si no sabes situarlo, no elijas una ventana más corta. Cadena "
    "vacía si needed es false o si amplías la ventana.\n"
    "- \"reason\": por qué esa ventana cambiaría tu diagnóstico. Cadena "
    "vacía si needed es false.\n"
    "Puede concederse una opción distinta de la que pidas, o ninguna; en ese "
    "caso recibirás los datos igualmente y podrás revisar tu diagnóstico.\n"
    "{window_menu}\n"
    "Responde únicamente con ese JSON, sin bloques de código markdown (```), "
    "sin texto introductorio, resumen ni explicación adicional antes o después."
)

# Unidades de intervalo en castellano. El valor que viaja a aveva-pi-mcp es el
# inglés ("minutes"), que es lo que espera su tool; esto es solo para el texto
# que lee el modelo, donde "cada 15 minutes" en mitad de una frase en español
# chirriaba.
_UNIT_ES = {"minutes": "minutos", "hours": "horas", "days": "días"}


def _describe_interval(value: int, unit: str) -> str:
    """'15 minutos', '1 hora', '2 horas' -- singular incluido."""
    nombre = _UNIT_ES.get(unit, unit)
    if value == 1 and nombre.endswith("s"):
        nombre = nombre[:-1] if nombre != "días" else "día"
    return f"{value} {nombre}"


def _describe_window(hours: int) -> str:
    """'24 horas', '7 días' -- se pasa a días cuando es múltiplo exacto."""
    if hours >= 48 and hours % 24 == 0:
        return f"{hours // 24} días"
    return f"{hours} hora" + ("s" if hours != 1 else "")


def _format_window_menu(options: list[dict], current_hours: int) -> str:
    """Renderiza el catálogo de ventanas para el prompt del Step 6.

    Se coloca junto a la definición de history_request, no en la sección del
    objetivo: es el conjunto de valores legales de un campo, y tenerlo al lado
    del campo evita que el modelo tenga que recordar una tabla leída 50.000
    tokens antes.

    Cada opción se anota con la dirección respecto a la ventana actual, para
    que el intercambio span/detalle sea explícito en vez de tener que
    deducirlo comparando cifras.
    """
    if not options:
        return (
            "\nNo hay otras ventanas disponibles: la que estás analizando es la única "
            "que este sistema puede consultar. Pon needed en false."
        )
    filas = []
    for o in options:
        if o["hours"] > current_hours:
            marca = "más histórico, menos detalle"
        else:
            marca = "más detalle, menos histórico"
        filas.append(
            f"  - \"{o['id']}\": {_describe_window(o['hours'])} de histórico, "
            f"un punto cada {_describe_interval(*o['interval'])} "
            f"({o['points']} puntos por variable) -- {marca}."
        )
    return (
        "\nOpciones disponibles para window_option (elige exactamente una de "
        "estas, o pon needed en false):\n" + "\n".join(filas) + "\n"
        "El número de puntos es aproximadamente constante: la resolución es "
        "proporcional a la ventana, así que ganar histórico cuesta detalle y "
        "al revés. Elige en función de la escala del fenómeno que quieras "
        "descartar, no por defecto la ventana más larga.\n"
        "Si eliges una ventana MÁS CORTA que la actual, la nueva ventana debe "
        "seguir conteniendo el inicio del cambio de tendencia: indícalo en "
        "trend_start. Si la que pides se quedara corta, se te concederá "
        "automáticamente la más corta que sí lo cubra."
    )


_FINAL_INSTRUCTION = (
    "Devuelve tu respuesta como un único objeto JSON válido con exactamente "
    "dos claves:\n"
    "- \"variables\": un array de objetos, cada uno con exactamente dos "
    "claves, \"element\" (nombre/ruta del elemento) y \"piApiPath\" (el "
    "piApiPath exacto tal como aparece en los datos del AF) -- los atributos "
    "cuyos datos requieres consultar para identificar la causa raíz.\n"
    "- \"missing_variables\": un array de strings en lenguaje natural "
    "describiendo qué tipo de variable adicional, que no aparece en los "
    "datos del AF proporcionados, ayudaría a precisar el diagnóstico. Array "
    "vacío si no falta ninguna. Esta clave es solo para informar al usuario "
    "y no se usará para consultar datos, así que no inventes un piApiPath "
    "para ella.\n"
    "Responde únicamente con ese JSON, sin bloques de código markdown (```), "
    "sin texto introductorio, resumen ni explicación adicional antes o después."
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


def _extract_json_payload(text: str) -> str:
    """Extrae el JSON de la respuesta del modelo (Step 4), tolerando que venga
    envuelto en un bloque ```json ... ``` o con texto alrededor, pese a que
    _FINAL_INSTRUCTION pide explícitamente que no sea así (visto en pruebas
    reales: el guardarraíl no es infalible). En vez de exigir que el texto
    completo sea *exactamente* un bloque de fence, busca las llaves/corchetes
    más externos del primer objeto o array JSON que aparezca -- así no
    depende del formato exacto con el que el modelo decida envolver la
    respuesta (con fence, sin fence, o con texto antes/después)."""
    candidates = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not candidates:
        return text
    start = min(candidates)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end == -1 or end < start:
        return text
    return text[start:end + 1]


# =============================================================================
# Puerta de autorización entre el Step 4 y el Step 5
# =============================================================================
# El Step 3 le pone delante al modelo la lista cerrada de atributos que existen
# de verdad en el AF, y _DATA_MODEL_SECTION se lo dice como "regla estricta".
# Eso es una instrucción, no una garantía: nada impide que la respuesta traiga
# un piApiPath retocado, inventado o traído de otro activo. Antes se pasaba tal
# cual a query_by_path, y el fallo aparecía tarde y mal -- aveva-pi-mcp resuelve
# TODOS los paths a WebID antes de consultar nada, así que una sola ruta que no
# existe tumbaba el batch entero (ver _query_with_retry en pi_client.py).
#
# Aquí se invierte la responsabilidad: el modelo propone, el código autoriza.
# Solo se consulta lo que se puede volver a encontrar en el af_context que se le
# entregó.


class VariableSelectionError(Exception):
    """La selección del Step 4 no dejó ninguna variable autorizada que consultar."""


def _normalize_pi_path(path: str) -> str:
    """Clave de comparación tolerante para un piApiPath.

    Se usa SOLO para buscar en la lista blanca; a PI siempre se le manda la
    cadena canónica del AF, nunca esta. Colapsa espacios y compara sin
    distinguir mayúsculas porque el modelo reescribe el path en vez de copiarlo
    literalmente más a menudo de lo que admite el prompt, y un espacio de más
    no debería costar una variable legítima.
    """
    return " ".join(path.split()).casefold()


def _collect_authorized_paths(af_context: dict) -> dict[str, str]:
    """Índice {clave normalizada: piApiPath canónico} con todos los atributos
    que el Step 2 puso delante del modelo. Es la lista blanca del Step 5."""
    index: dict[str, str] = {}
    for block in ("main_asset_context", "nearby_elements_context"):
        for group in af_context.get(block) or []:
            for attr in group.get("attributes") or []:
                path = attr.get("piApiPath") or ""
                if path:
                    index.setdefault(_normalize_pi_path(path), path)
    return index


def _validate_selected_variables(parsed_response, af_context: dict) -> tuple[list[dict], list[str]]:
    """Valida la respuesta del Step 4 contra el af_context del Step 2.

    Comprobaciones, en orden:
      1. Forma de la respuesta (objeto de dos claves; se tolera un array plano).
      2. Cada entrada es un objeto con un piApiPath utilizable.
      3. Ese piApiPath aparece en el af_context -- si no, se descarta.
      4. Se sustituye por la cadena canónica del AF, por si el modelo la
         reescribió con otro espaciado o capitalización.
      5. Se descartan duplicados y se ignoran claves no previstas.
      6. Se recorta a config.MAX_SELECTED_VARIABLES.

    Descartar en vez de abortar es deliberado, y es la misma política que ya
    sigue _query_with_retry con los WebID: una ruta inventada no debería
    invalidar las que sí eran buenas. Todo lo descartado queda en el log con su
    motivo -- es lo que permite detectar si el prompt se está degradando.

    Args:
        parsed_response: el JSON ya parseado de la respuesta del Step 4.
        af_context: el resultado del Step 2 (graph_client.build_af_context).

    Returns:
        (variables autorizadas, missing_variables saneadas)

    Raises:
        VariableSelectionError: si no sobrevive ninguna variable, o si la
                                respuesta no tiene una forma aprovechable.
    """
    if isinstance(parsed_response, dict):
        raw_variables = parsed_response.get("variables")
        raw_missing = parsed_response.get("missing_variables")
    elif isinstance(parsed_response, list):
        # Array plano: esquema anterior a missing_variables. Se tolera por el
        # mismo motivo que _extract_json_payload -- el prompt no es infalible.
        raw_variables = parsed_response
        raw_missing = None
    else:
        raise VariableSelectionError(
            f"la respuesta no es un objeto ni un array JSON (tipo {type(parsed_response).__name__})"
        )

    if not isinstance(raw_variables, list):
        raise VariableSelectionError(
            f"la clave 'variables' no es un array (tipo {type(raw_variables).__name__})"
        )

    authorized = _collect_authorized_paths(af_context)
    if not authorized:
        # Sin lista blanca no hay nada que autorizar. Pasa si el Step 2 no
        # encontró el activo; seguir consultando PI a ciegas no tiene sentido.
        raise VariableSelectionError(
            "el af_context del Step 2 no contiene ningun piApiPath contra el que validar"
        )

    variables: list[dict] = []
    seen: set[str] = set()
    rejected: list = []
    unknown_keys: set[str] = set()
    duplicates = 0

    for entry in raw_variables:
        if not isinstance(entry, dict):
            rejected.append(entry)
            continue

        unknown_keys.update(set(entry) - {"element", "piApiPath"})

        raw_path = entry.get("piApiPath")
        if not isinstance(raw_path, str) or not raw_path.strip():
            rejected.append(entry)
            continue

        canonical = authorized.get(_normalize_pi_path(raw_path))
        if canonical is None:
            # El caso que motiva toda esta función: ruta inventada, retocada
            # más allá de espacios/mayúsculas, o copiada de otro activo.
            rejected.append(raw_path)
            continue

        if canonical != raw_path:
            log.info(
                "Step 4: piApiPath reescrito por el modelo; se usa la forma canonica del AF. "
                "Recibido %r -> AF %r", raw_path, canonical,
            )

        if canonical in seen:
            duplicates += 1
            continue
        seen.add(canonical)

        element = entry.get("element")
        variables.append({
            "element": element if isinstance(element, str) and element else canonical,
            "piApiPath": canonical,
        })

    if rejected:
        log.warning(
            "Step 4: %d variable(s) descartadas por no existir en el af_context del Step 2: %s",
            len(rejected), rejected,
        )
    if unknown_keys:
        log.warning(
            "Step 4: claves no previstas en las variables (se ignoran): %s", sorted(unknown_keys),
        )
    if duplicates:
        log.info("Step 4: %d variable(s) repetidas descartadas.", duplicates)

    if not variables:
        raise VariableSelectionError(
            f"ninguna de las {len(raw_variables)} variables propuestas existe en el af_context"
        )

    if len(variables) > config.MAX_SELECTED_VARIABLES:
        # Se conservan las primeras: _OBJECTIVE_SECTION pide al modelo que
        # priorice (KPI en alerta -> indicadores del activo -> meters físicos ->
        # nivel superior), así que la cabeza de la lista es lo más relevante.
        dropped = [v["piApiPath"] for v in variables[config.MAX_SELECTED_VARIABLES:]]
        log.warning(
            "Step 4: %d variables superan el maximo de %d; se recortan las ultimas: %s",
            len(variables), config.MAX_SELECTED_VARIABLES, dropped,
        )
        variables = variables[:config.MAX_SELECTED_VARIABLES]

    missing_variables: list[str] = []
    if isinstance(raw_missing, list):
        missing_variables = [m for m in raw_missing if isinstance(m, str) and m.strip()]
        if len(missing_variables) != len(raw_missing):
            log.warning("Step 4: se ignoran entradas no textuales en 'missing_variables'.")
    elif raw_missing is not None:
        log.warning("Step 4: 'missing_variables' no es un array (se ignora): %r", raw_missing)

    return variables, missing_variables


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


_EPOCH_MARKER = "1970-01-01T00:00:00Z"


def _label_historical_data(variables: list[dict], raw_data) -> list[dict]:
    """Combina las variables elegidas en el Step 4 con los valores del Step 5
    en una lista etiquetada por elemento, lista para incluir en el prompt del
    Step 6.

    Simplifica los estados digitales (PI los representa como un dict
    {"Name": ..., "Value": <código>, "IsSystem": true}, p.ej. "No Result"
    cuando el cálculo no pudo evaluarse) a solo su nombre, y marca como sin
    historización real las series compuestas enteramente por el timestamp
    1970-01-01T00:00:00Z (visto en atributos "Reference"/estáticos del AF --
    no son mediciones reales, PI simplemente no tiene nada que devolver).
    """
    if not isinstance(raw_data, dict):
        log.warning("Step 6: los datos históricos no son un diccionario parseado; se omite el etiquetado.")
        return []

    labeled = []
    for var in variables:
        pi_path = var.get("piApiPath")
        content = (raw_data.get(pi_path) or {}).get("Content", {})
        items = content.get("Items", [])

        if items and all(it.get("Timestamp") == _EPOCH_MARKER for it in items):
            labeled.append({
                "element": var.get("element"),
                "piApiPath": pi_path,
                "values": "sin historización real (atributo estático/de configuración, no una medición)",
            })
            continue

        uom = ""
        values = []
        for it in items:
            raw_value = it.get("Value")
            if isinstance(raw_value, dict):
                raw_value = raw_value.get("Name", str(raw_value))
            uom = it.get("UnitsAbbreviation") or uom
            values.append({"timestamp": it.get("Timestamp"), "value": raw_value})

        labeled.append({
            "element": var.get("element"),
            "piApiPath": pi_path,
            "uom": uom,
            "values": values,
        })

    return labeled


def build_diagnosis_context(
    context: dict, variables: list[dict], historical_data: dict, missing_variables: list[str],
) -> str:
    """Step 6: construye el mensaje con los datos históricos etiquetados para
    que el modelo produzca el diagnóstico final.

    Args:
        context: resultado de build_analysis_context() (Step 3) -- se
                 reutiliza su "summary" para no repetir la descripción de la
                 alerta.
        variables: la lista {"element", "piApiPath"} que el modelo eligió en
                   el Step 4.
        historical_data: el dict que devuelve pi_client.fetch_historical_data
                          (Step 5); se usa su clave "data".
        missing_variables: las variables que el modelo señaló como no
                            disponibles en el Step 4 (ver _FINAL_INSTRUCTION)
                            -- se incluyen como limitación conocida del
                            diagnóstico, no se inventan datos para ellas.

    Returns:
        El mensaje de usuario completo para la llamada al modelo del Step 6
        (se envía junto con SYSTEM_PROMPT, igual que el Step 4).
    """
    labeled_data = _label_historical_data(variables, historical_data.get("data"))
    data_json = json.dumps(labeled_data, ensure_ascii=False, indent=2)

    limitations_note = ""
    if missing_variables:
        limitations_note = (
            "\n\nLimitaciones conocidas: en la interacción anterior se identificó que "
            "estas variables ayudarían a precisar el diagnóstico pero no estaban "
            "disponibles en el Asset Framework proporcionado: "
            f"{', '.join(missing_variables)}. Ten esto en cuenta al valorar tu "
            "confianza en cada causa raíz -- no las inventes ni asumas su valor."
        )

    # El modelo tiene que saber qué ventana está mirando para poder juzgar si
    # le basta: antes solo recibía los timestamps y tenía que deducirlo.
    hours = historical_data.get("lookback_hours", config.PI_LOOKBACK_HOURS)
    int_value, int_unit = historical_data.get(
        "interval", (config.PI_QUERY_INTERVAL_VALUE, config.PI_QUERY_INTERVAL_UNIT),
    )
    # La invitación a pedir más ventana vive solo en la sección 1: repetirla
    # aquí insistía de más y podía empujar al modelo a pedir ampliación con
    # más frecuencia de la necesaria.
    window_note = (
        f"Ventana consultada: las {_describe_window(hours)} anteriores a la "
        f"detección de la alerta ({historical_data.get('start_date')} a "
        f"{historical_data.get('end_date')} UTC), con un punto cada "
        f"{_describe_interval(int_value, int_unit)}."
    )

    demo_note = _DEMO_MODE_NOTE if config.DEMO_MODE else ""
    final_instruction = _DIAGNOSIS_FINAL_INSTRUCTION.replace(
        "{window_menu}", _format_window_menu(_available_window_options(hours), hours),
    )

    return (
        f"1. Objetivo de la interacción\n\n{_DIAGNOSIS_OBJECTIVE_SECTION}{demo_note}\n\n"
        f"2. Resumen de la alerta\n\n{context['summary']}{limitations_note}\n\n"
        f"3. Datos históricos\n\n{window_note}\n\n{_DIAGNOSIS_DATA_SECTION_INTRO}\n\n"
        f"{data_json}\n\n"
        f"{final_instruction}"
    )


def _available_window_options(current_hours: int) -> list[dict]:
    """Opciones del catálogo distintas de la ventana actual, en ambas direcciones.

    Única fuente para las dos caras del mecanismo: lo que se le ofrece al
    modelo en el prompt y lo que se le acepta al validar su respuesta. Si
    divergieran, se le estaría ofreciendo algo que luego se le rechaza.
    """
    menu = pi_client.window_menu(
        config.PI_WINDOW_LADDER_HOURS, config.PI_TARGET_POINTS_PER_VARIABLE,
    )
    return [
        o for o in menu
        if o["hours"] != current_hours and o["hours"] <= config.PI_MAX_LOOKBACK_HOURS
    ]


def _parse_history_request(
    diagnosis: dict, current_hours: int, detected_at_utc: str,
) -> tuple[int, tuple[int, str], str] | None:
    """Lee history_request del Step 6 y decide qué ventana se concede.

    El modelo elige del catálogo; el código valida la elección -- misma
    filosofía que _validate_selected_variables con los piApiPath. Al ser un
    catálogo cerrado, la ventana concedida es siempre uno de los pares
    ventana/resolución que se le ofrecieron, así que no hace falta recortar
    nada a posteriori: el techo ya está aplicado al construir las opciones.

    El catálogo va en las dos direcciones. Ampliar es seguro (solo añade
    contexto), así que se concede sin más. **Estrechar puede recortar la
    evidencia**, así que exige que el modelo indique en trend_start cuándo
    arranca la desviación, y el código comprueba aritméticamente que la
    ventana pedida lo sigue cubriendo (ver _enforce_trend_coverage).

    Args:
        diagnosis: el JSON ya parseado del Step 6.
        current_hours: la ventana con la que se generó ese diagnóstico.
        detected_at_utc: momento de detección de la alerta (ISO 8601 con 'Z'),
                         extremo final de la ventana. Necesario para comprobar
                         la cobertura de la tendencia al estrechar.

    Returns:
        (horas, (valor, unidad) del intervalo, motivo) si procede repetir los
        Steps 5-6 con otra ventana; None si el modelo no la pide, si no hay
        alternativa, o si el estrechamiento pedido no es admisible.
    """
    if not isinstance(diagnosis, dict):
        return None
    request = diagnosis.get("history_request")
    if not isinstance(request, dict) or not request.get("needed"):
        return None

    options = _available_window_options(current_hours)
    if not options:
        log.info(
            "Step 6: el modelo pide otra ventana, pero el catálogo no ofrece "
            "ninguna alternativa a las %d h actuales.", current_hours,
        )
        return None

    reason = request.get("reason")
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else "no indicado"

    chosen = None
    raw_option = request.get("window_option")
    if isinstance(raw_option, str) and raw_option.strip():
        clave = raw_option.strip().casefold()
        chosen = next((o for o in options if o["id"].casefold() == clave), None)
        if chosen is None:
            log.warning(
                "Step 6: window_option %r no está en el catálogo ofrecido (%s).",
                raw_option, [o["id"] for o in options],
            )

    if chosen is None:
        # Compatibilidad con el esquema anterior, que pedía un número libre de
        # horas: si viene, se ajusta a la opción más pequeña que lo cubra.
        raw_hours = request.get("hours")
        if not isinstance(raw_hours, bool) and isinstance(raw_hours, (int, float)):
            chosen = next((o for o in options if o["hours"] >= raw_hours), options[-1])
            log.info(
                "Step 6: sin window_option válido, pero llega hours=%r; se ajusta "
                "a la opción '%s' (%d h).", raw_hours, chosen["id"], chosen["hours"],
            )

    if chosen is None:
        # Petición sin ninguna referencia utilizable. Se concede la ampliación
        # más pequeña en vez de descartarla: que la cifra sea inservible no
        # invalida la observación de que la ventana se le queda corta. Nunca se
        # estrecha por defecto -- estrechar pierde datos y solo se hace cuando
        # el modelo lo pide explícitamente y justifica la cobertura.
        ampliaciones = [o for o in options if o["hours"] > current_hours]
        if not ampliaciones:
            log.info("Step 6: history_request sin opción utilizable y sin ampliación posible.")
            return None
        chosen = ampliaciones[0]
        log.info(
            "Step 6: history_request sin opción utilizable; se concede la ampliación "
            "mínima '%s' (%d h).", chosen["id"], chosen["hours"],
        )

    # Estrechar la ventana solo vale si sigue conteniendo el inicio de la
    # desviación: si no, se recortaría justo la evidencia que explica la
    # alerta. El modelo aporta el dato (trend_start) y el código comprueba la
    # aritmética -- no se fía de que la comprobación la haya hecho él.
    if chosen["hours"] < current_hours:
        chosen = _enforce_trend_coverage(chosen, options, request, current_hours, detected_at_utc)
        if chosen is None:
            return None

    return chosen["hours"], chosen["interval"], reason


def _enforce_trend_coverage(
    chosen: dict, options: list[dict], request: dict, current_hours: int, detected_at_utc: str,
) -> dict | None:
    """Comprueba que una ventana más corta sigue cubriendo el inicio de la tendencia.

    Devuelve la opción concedida (la pedida, o la más corta que sí cubra el
    inicio de la desviación), o None si no procede estrechar.
    """
    raw = request.get("trend_start")
    if not isinstance(raw, str) or not raw.strip():
        log.warning(
            "Step 6: el modelo pide estrechar a '%s' sin indicar trend_start; no se "
            "estrecha (estrechar sin saber dónde empieza la desviación puede recortarla).",
            chosen["id"],
        )
        return None

    try:
        trend_start = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        detected_at = datetime.fromisoformat(detected_at_utc.replace("Z", "+00:00"))
    except ValueError:
        log.warning(
            "Step 6: trend_start con formato no interpretable (%r); no se estrecha.", raw,
        )
        return None

    if trend_start >= detected_at:
        log.warning(
            "Step 6: trend_start (%s) no es anterior a la detección (%s); no se estrecha.",
            raw, detected_at_utc,
        )
        return None

    horas_tendencia = (detected_at - trend_start).total_seconds() / 3600
    if chosen["hours"] >= horas_tendencia:
        return chosen

    # La ventana pedida cortaría el inicio de la desviación: se sube al escalón
    # más corto que sí lo cubra, sin pasar de la ventana actual (si hiciera
    # falta más que la actual, no es un estrechamiento y no se toca nada).
    cubren = [o for o in options if horas_tendencia <= o["hours"] < current_hours]
    if not cubren:
        log.warning(
            "Step 6: la tendencia arranca %.1f h antes de la alerta; ninguna ventana "
            "más corta que las %d h actuales la cubre. No se estrecha.",
            horas_tendencia, current_hours,
        )
        return None

    log.warning(
        "Step 6: '%s' (%d h) no cubre la tendencia, que arranca %.1f h antes de la "
        "alerta; se concede '%s' (%d h) en su lugar.",
        chosen["id"], chosen["hours"], horas_tendencia, cubren[0]["id"], cubren[0]["hours"],
    )
    return cubren[0]


async def run_rca_analysis(notification_payload: dict, trace: dict | None = None) -> dict | None:
    """Punto de entrada principal del workflow RCA.

    Se llama desde webhook.py cada vez que llega una notificación de PI System.
    Se ejecuta en segundo plano (no bloquea la respuesta al servidor de PI).

    Args:
        notification_payload: los datos de la alerta tal como los envió PI.
        trace: diccionario opcional que esta función rellena con los prompts
               enviados al modelo y sus respuestas. webhook.py lo persiste en el
               fichero del incidente. Los prompts ya no van al log: con el
               logging estructurado se truncan a 200 caracteres, y el fichero
               del incidente es su sitio natural -- es el registro del caso, y
               base/software-spec §1.5 pide preservar el prompt que llevó a un
               artefacto generado por IA.

    Returns:
        El diagnóstico ya parseado ({"root_causes": [...]}) si el análisis llegó
        al final, o None si se cortó de forma controlada en cualquier paso. El
        que llama lo usa para registrar el resultado del incidente; los motivos
        del corte quedan siempre en el log.

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
    log.info("WORKFLOW RCA: Iniciando análisis de causa raíz")
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
    if trace is not None:
        trace["step3_prompt"] = context["claude_prompt"]
        trace["system_prompt"] = SYSTEM_PROMPT
    log.info("Contexto construido para el modelo (Step 3).",
             extra={"promptChars": len(context["claude_prompt"])})

    # -------------------------------------------------------------------------
    # Step 4: el modelo responde con las variables que necesita analizar
    # -------------------------------------------------------------------------
    # Estas variables son rutas (piApiPath) que el MCP Server puede consultar
    # directamente en PI Web API. llm_client.generate() ya reintenta errores
    # transitorios (5xx, rate limit...) internamente; si aun así falla (o el
    # error no es transitorio), cortamos aquí de forma controlada en vez de
    # dejar que el traceback se propague sin control en la background task.
    try:
        variables_response = llm_client.generate(SYSTEM_PROMPT, context["claude_prompt"])
    except llm_client.LLMGenerationError as exc:
        log.error("Step 4 fallido: %s", exc)
        log.info("WORKFLOW RCA: análisis interrumpido en el Step 4. Steps 5 y 6 no ejecutados.")
        return
    if trace is not None:
        trace["step4_response"] = variables_response
    log.info("Respuesta del modelo recibida (Step 4).",
             extra={"responseChars": len(variables_response)})

    # -------------------------------------------------------------------------
    # Step 5: Obtener datos históricos de esas variables vía MCP Server
    # -------------------------------------------------------------------------
    try:
        parsed_response = json.loads(_extract_json_payload(variables_response))
    except json.JSONDecodeError as exc:
        log.error("Step 5: la respuesta del modelo no es JSON válido (%s): %r", exc, variables_response)
        log.info("WORKFLOW RCA: análisis interrumpido en el Step 5. Step 6 no ejecutado.")
        return

    # Puerta de autorización: el modelo propone, el código decide qué se
    # consulta. Nada que no esté en el af_context del Step 2 llega a PI.
    try:
        variables, missing_variables = _validate_selected_variables(parsed_response, af_context)
    except VariableSelectionError as exc:
        log.error("Step 5: seleccion de variables no valida (%s): %r", exc, variables_response)
        log.info("WORKFLOW RCA: análisis interrumpido en el Step 5. Step 6 no ejecutado.")
        return

    log.info("Step 4: %d variables autorizadas para consultar en PI.", len(variables))

    if missing_variables:
        # No se usa para consultar PI, así se evita meter ruido externo
        # (nombres de variable sin piApiPath real) en el bucket del Step 5. Sí
        # se arrastra al Step 6 vía build_diagnosis_context(), como limitación
        # declarada del diagnóstico: el modelo modula su confianza en vez de
        # inventar valores para esas variables.
        log.info(
            "Step 4: el modelo indica que estas variables adicionales (no "
            "disponibles en el AF proporcionado) mejorarian el diagnostico: %s",
            missing_variables,
        )

    # La ventana inicial es un punto de partida, no una decisión definitiva. Si
    # el modelo, ya con los datos delante, concluye que necesita más histórico,
    # lo pide en history_request y se repite el par Step 5 + Step 6 con una
    # ventana mayor. Quién decide cuánto se concede y cuántas veces es el
    # código (_parse_history_request + MAX_HISTORY_ADJUSTMENTS), no el modelo:
    # sigue siendo un workflow acotado, no un bucle agéntico.
    #
    # Al ampliar, la resolución se recalcula con derive_interval() para que el
    # número de puntos por variable se mantenga: ampliar la ventana sin tocar
    # la resolución multiplicaría el prompt del Step 6 por el mismo factor.
    lookback_hours = config.PI_LOOKBACK_HOURS
    interval = (config.PI_QUERY_INTERVAL_VALUE, config.PI_QUERY_INTERVAL_UNIT)
    adjustments = 0
    visitadas = {lookback_hours}

    while True:
        # --- Step 5 ---
        try:
            historical_data = await pi_client.fetch_historical_data(
                variables, context["detected_at_utc"], lookback_hours, interval,
            )
        except pi_client.PIQueryError as exc:
            log.error("Step 5 fallido: %s", exc)
            log.info("WORKFLOW RCA: análisis interrumpido en el Step 5. Step 6 no ejecutado.")
            return

        log.info(
            "Step 5: datos históricos obtenidos para %d piApiPath, ventana %s -> %s "
            "(%d h, un punto cada %d %s, bucket %s)",
            len(historical_data["piApiPaths"]), historical_data["start_date"],
            historical_data["end_date"], lookback_hours, interval[0], interval[1],
            historical_data["bucket_id"],
        )
        if historical_data["excluded_piApiPaths"]:
            log.warning(
                "Step 5: %d piApiPath no resolvieron a WebID y se excluyeron: %s",
                len(historical_data["excluded_piApiPaths"]), historical_data["excluded_piApiPaths"],
            )


        # --- Step 6 ---
        diagnosis_prompt = build_diagnosis_context(context, variables, historical_data, missing_variables)
        if trace is not None:
            trace.setdefault("step6_prompts", []).append(diagnosis_prompt)
        log.info("Contexto construido para el modelo (Step 6).",
                 extra={"promptChars": len(diagnosis_prompt), "lookbackHours": lookback_hours})

        try:
            diagnosis_response = llm_client.generate(SYSTEM_PROMPT, diagnosis_prompt)
        except llm_client.LLMGenerationError as exc:
            log.error("Step 6 fallido: %s", exc)
            log.info("WORKFLOW RCA: análisis interrumpido en el Step 6.")
            return
        if trace is not None:
            trace.setdefault("step6_responses", []).append(diagnosis_response)
        log.info("Respuesta del modelo recibida (Step 6).",
                 extra={"responseChars": len(diagnosis_response)})

        try:
            diagnosis = json.loads(_extract_json_payload(diagnosis_response))
        except json.JSONDecodeError as exc:
            log.error("Step 6: la respuesta del modelo no es JSON válido (%s): %r", exc, diagnosis_response)
            log.info("WORKFLOW RCA: análisis interrumpido en el Step 6.")
            return

        # ¿Pide el modelo más histórico? Se registra siempre, se conceda o no:
        # con qué frecuencia lo pide es una métrica de si la ventana inicial
        # está bien dimensionada para este tipo de alerta.
        granted = _parse_history_request(diagnosis, lookback_hours, context["detected_at_utc"])
        if granted is None:
            break
        if adjustments >= config.MAX_HISTORY_ADJUSTMENTS:
            log.warning(
                "Step 6: el modelo pide reajustar la ventana a %d h pero se ha agotado el "
                "presupuesto (MAX_HISTORY_ADJUSTMENTS=%d). Se conserva el diagnóstico "
                "con %d h. Motivo alegado: %s",
                granted[0], config.MAX_HISTORY_ADJUSTMENTS, lookback_hours, granted[2],
            )
            break
        if granted[0] in visitadas:
            # Con el catálogo en las dos direcciones, un presupuesto mayor que 1
            # permitiría oscilar (24 h -> 8 h -> 24 h) sin converger nunca.
            log.warning(
                "Step 6: el modelo vuelve a pedir una ventana ya analizada (%d h); "
                "se corta para no oscilar.", granted[0],
            )
            break

        # La resolución viene ya fijada por la opción del catálogo: es el par
        # ventana/resolución que el propio modelo vio al elegir.
        anterior = lookback_hours
        lookback_hours, interval, reason = granted
        visitadas.add(lookback_hours)
        adjustments += 1
        log.info(
            "Step 6: el modelo pide %s la ventana (reajuste %d/%d): de %d h a %d h, "
            "un punto cada %d %s. Motivo: %s",
            "ampliar" if lookback_hours > anterior else "estrechar",
            adjustments, config.MAX_HISTORY_ADJUSTMENTS, anterior, lookback_hours,
            interval[0], interval[1], reason,
        )

    # TODO: presentar esto al usuario final (interfaz web, notificación...) en
    # vez de solo dejarlo en el log -- pendiente de decidir el canal de salida.
    log.info("=" * 60)
    log.info("WORKFLOW RCA: DIAGNÓSTICO FINAL")
    for i, cause in enumerate(diagnosis.get("root_causes", []), 1):
        log.info("%d. %s", i, cause.get("cause"))
        log.info("   Explicación: %s", cause.get("explanation"))
        log.info("   Acción recomendada: %s", cause.get("recommended_action"))
    log.info("=" * 60)

    log.info(
        "WORKFLOW RCA: análisis completo (Steps 1-6). Ventana final: %d h "
        "(un punto cada %d %s), reajustes: %d.",
        lookback_hours, interval[0], interval[1], adjustments,
    )
    return diagnosis
