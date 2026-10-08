"""
llm_client.py — Capa de abstracción sobre el proveedor de LLM (Gemini / Anthropic)

¿Qué hace este fichero?
  Aísla al resto del workflow de qué proveedor de IA se usa para razonar.
  Expone una única función, generate(), que siempre recibe un system prompt
  y un mensaje de usuario en texto plano, y siempre devuelve texto plano.
  Los Steps 4 y 6 de workflow.py llaman a esta función sin saber si por debajo
  hay Gemini o Claude.

  Nota: generate() nunca pasa el parámetro "tools" a ninguno de los dos
  proveedores, y por eso sigue habiendo una sola firma para los dos. El modelo
  solo ve texto y devuelve texto; quien llama a los MCP servers es el código
  Python (graph_client.py, pi_client.py). Esto es un workflow, no un agente --
  ver CLAUDE.md, "Terminología".

  El matiz, desde la Fase 3: en el bucle de búsqueda del reanálisis el texto que
  devuelve el modelo SÍ decide cuál es la siguiente consulta al grafo. No por
  tool use del proveedor -- aquí no lo hay --, sino porque workflow.py lee ese
  texto como una acción en JSON, la valida y la ejecuta. Se hizo así a propósito:
  implementar tool use nativo obligaría a escribir el protocolo de Gemini y el de
  Anthropic por separado, que es exactamente lo que esta capa existe para evitar.

¿Por qué existe esta capa?
  Anthropic y Google no comparten un estándar de API: cada uno define su
  propio SDK, su propio formato de mensajes y su propio formato de
  respuesta (ver CLAUDE.md). Aislar esa diferencia aquí evita que el resto
  del workflow tenga que conocerla, y permite cambiar de proveedor cambiando
  una sola variable de entorno.

¿Cómo se elige el proveedor?
  Variable de entorno LLM_PROVIDER: "gemini" (por defecto), "anthropic" o
  "deepseek". El resto del workflow no sabe cuál hay detrás.

¿Qué pasa si el proveedor falla?
  Visto en producción el 2026-07-28: Gemini devolvió un 503 "high demand"
  (sobrecarga transitoria). _generate_gemini/_generate_anthropic reintentan
  hasta 3 veces con backoff exponencial (2-30s) solo para errores que un
  reintento puede arreglar (5xx, rate limit, timeout, conexión) -- errores
  4xx por auth/payload inválido no se reintentan, porque reintentar no los
  soluciona. Si se agotan los reintentos (o el error no es transitorio),
  generate() lanza LLMGenerationError para que workflow.py lo capture y corte
  el análisis de forma controlada en vez de propagar un traceback crudo.
"""

import logging

import tenacity

import config

log = logging.getLogger(__name__)


class LLMGenerationError(Exception):
    """El proveedor de LLM no pudo generar una respuesta (tras agotar los reintentos si aplica)."""


def generate(system_prompt: str, user_message: str) -> str:
    """Llama al proveedor de LLM configurado y devuelve su respuesta en texto.

    Args:
        system_prompt: rol y dominio fijos del workflow (ver workflow.SYSTEM_PROMPT).
        user_message: mensaje dinámico de la petición concreta (p.ej.
                      context["claude_prompt"] de build_analysis_context()).

    Returns:
        La respuesta del modelo como texto plano.

    Raises:
        ValueError: si LLM_PROVIDER no es "gemini", "anthropic" ni "deepseek".
        LLMGenerationError: si el proveedor falla tras agotar los reintentos
                             (o falla con un error no transitorio).
    """
    provider = config.LLM_PROVIDER
    if provider not in ("gemini", "anthropic", "deepseek"):
        raise ValueError(
            f"LLM_PROVIDER desconocido: '{provider}' "
            f"(valores válidos: 'gemini', 'anthropic', 'deepseek')"
        )
    try:
        if provider == "gemini":
            return _generate_gemini(system_prompt, user_message)
        if provider == "deepseek":
            return _generate_deepseek(system_prompt, user_message)
        return _generate_anthropic(system_prompt, user_message)
    except Exception as exc:
        raise LLMGenerationError(f"El proveedor '{provider}' no respondió: {exc}") from exc


def _is_transient_gemini_error(exc: BaseException) -> bool:
    """5xx (sobrecarga, mantenimiento...) es reintentable, y el 429 también.

    El resto de 4xx no: auth, payload inválido o modelo inexistente no se
    arreglan repitiendo la misma petición.

    El 429 es la excepción, y es EL error reintentable por definición: dice
    "ahora mismo no, vuelve en un momento". Estaba fuera porque la regla era
    "4xx no se reintenta" -- cierta para todos los demás --, y eso dejaba a los
    dos proveedores comportándose distinto ante el mismo error: la rama de
    Anthropic sí reintenta su RateLimitError.

    Costó un análisis real (2026-09-29): el paso de búsqueda subió las llamadas
    por reanálisis de 2-3 a 8, ocho en 45 s agotaron el límite por minuto de la
    cuenta, y el 429 llegó justo en el Step 6 -- con el diagnóstico a una
    llamada de distancia. La ventana de reintentos son 90 s, de sobra para un
    límite por minuto.

    Si la cuota agotada fuera diaria, reintentar 90 s se pierde y el análisis
    falla igual. Es el mismo precio que ya se paga con cualquier corte largo.
    """
    from google.genai import errors
    if isinstance(exc, errors.ServerError):
        return True
    return isinstance(exc, errors.ClientError) and getattr(exc, "code", None) == 429


def _is_transient_anthropic_error(exc: BaseException) -> bool:
    import anthropic
    return isinstance(exc, (
        anthropic.InternalServerError,
        anthropic.OverloadedError,
        anthropic.RateLimitError,
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
    ))


_RETRY_COMMON = dict(
    wait=tenacity.wait_exponential(multiplier=2, min=2, max=30),
    # Se para por TIEMPO, no por número de intentos. Antes era
    # stop_after_attempt(3), que con esta espera exponencial daba 2 + 4 = SEIS
    # SEGUNDOS de aguante total: ante un corte de tres minutos del proveedor, el
    # análisis se rendía cuando faltaban 174 segundos para que volviera.
    #
    # Contar tiempo dice lo que de verdad importa -- cuánto corte se aguanta --
    # y no depende de lo rápido que falle cada intento. Con la espera
    # exponencial de arriba (2, 4, 8, 16, 30, 30...) caben unos ocho intentos en
    # la ventana.
    #
    # El valor sale del presupuesto de ANALYSIS_TIMEOUT_SECONDS, no del aire:
    # ver la nota en config.LLM_RETRY_WINDOW_SECONDS.
    stop=tenacity.stop_after_delay(config.LLM_RETRY_WINDOW_SECONDS),
    before_sleep=tenacity.before_sleep_log(log, logging.WARNING),
    reraise=True,
)


@tenacity.retry(retry=tenacity.retry_if_exception(_is_transient_gemini_error), **_RETRY_COMMON)
def _generate_gemini(system_prompt: str, user_message: str) -> str:
    """Llama a Gemini vía el SDK google-genai."""
    from google import genai
    from google.genai import types

    # Mismo tope que en la ruta de Anthropic, para que las dos se comporten
    # igual. google-genai lo toma en milisegundos.
    client = genai.Client(
        api_key=config.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=config.LLM_TIMEOUT_SECONDS * 1000),
    )
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            max_output_tokens=config.LLM_MAX_TOKENS,
        ),
    )
    return response.text


@tenacity.retry(retry=tenacity.retry_if_exception(_is_transient_anthropic_error), **_RETRY_COMMON)
def _generate_anthropic(system_prompt: str, user_message: str) -> str:
    """Llama a Claude vía el SDK anthropic."""
    import anthropic

    # max_retries=0 a propósito: el SDK reintenta dos veces por su cuenta y
    # tenacity otras tres, así que los dos juntos daban NUEVE intentos de hasta
    # diez minutos cada uno. Hora y media para una sola llamada, y nadie decidió
    # eso -- salió de encajar dos políticas de reintento sin mirar. La política
    # de reintentos la tiene tenacity, que es donde está escrita y donde se lee.
    client = anthropic.Anthropic(
        api_key=config.ANTHROPIC_API_KEY,
        timeout=config.LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )
    extra = {}
    if config.ANTHROPIC_THINKING:
        # "adaptive" y no {"type": "enabled", "budget_tokens": N}: el modelo
        # decide cuánto razona. En claude-opus-4-8 budget_tokens además ya no se
        # acepta -- la API responde 400 --, así que la forma vieja no es solo
        # peor, es un error.
        #
        # No se pasa 'temperature' en ninguno de los dos casos, y con
        # razonamiento extendido tampoco se puede: la API solo admite el valor
        # por defecto.
        extra["thinking"] = {"type": "adaptive"}

    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=config.LLM_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        **extra,
    )
    return _texto_de(response)


@tenacity.retry(retry=tenacity.retry_if_exception(_is_transient_anthropic_error), **_RETRY_COMMON)
def _generate_deepseek(system_prompt: str, user_message: str) -> str:
    """Llama a DeepSeek a través de su endpoint compatible con Anthropic.

    No hay SDK propio ni hace falta: DeepSeek publica un endpoint que habla el
    protocolo de Anthropic, y la llamada tiene exactamente la misma forma --
    model, max_tokens, system, messages. Basta con apuntar el cliente que ya
    tenemos a otra base_url.

    Se hace así y no con el SDK de OpenAI -- que DeepSeek también admite --
    porque añadir una dependencia en este proyecto no es gratis: cada una lleva
    revisión de mantenimiento, adopción y licencia (izSpecs §1.3,
    AI-TRACEABILITY.md §6). Reutilizar `anthropic`, que ya está declarado y
    revisado, evita ese trabajo sin perder nada.

    Lo que NO se comparte con la ruta de Anthropic, a propósito:

      - La clave, el modelo y la URL son suyos (DEEPSEEK_*). Que un proveedor
        herede la configuración de otro es el tipo de atajo que luego hace que
        nadie sepa con qué se generó un diagnóstico.
      - No se pide razonamiento, porque NO HACE FALTA: `deepseek-flash` razona
        siempre. Comprobado contra la API el 2026-09-30 -- la respuesta trae
        bloque 'thinking' aunque no se mande nada. Acepta `{"type": "adaptive"}`
        y `{"type": "enabled"}` sin protestar, pero no cambian el resultado, así
        que mandar uno sería ruido.

        Eso deja a DeepSeek en la misma categoría que `gemini-2.5-flash` --
        razona por defecto, no se configura -- y no en un tercer estado.

    Reintentos: los mismos que Anthropic, y por el mismo predicado -- las
    excepciones las levanta ese SDK, así que son sus clases. Incluye el 429.

    OJO con _texto_de(): aquí es imprescindible desde el primer día. Como la
    respuesta siempre trae razonamiento, el primer bloque NO es el texto, así
    que `response.content[0].text` devolvería el pensamiento en lugar del JSON
    y rompería el parseo de todos los pasos.

    VERIFICADO CONTRA LA API REAL el 2026-09-30: una llamada de humo y el prompt
    de la puerta de la búsqueda (3.650 caracteres, 43 variables), que exige JSON
    estricto. Respondió en 3,6 s con JSON válido y un razonamiento correcto --
    distinguió que PH705 es del vertido y no de la salida del primario.
    """
    import anthropic

    # max_retries=0 por lo mismo que en la ruta de Anthropic: la política de
    # reintentos la tiene tenacity, y dos políticas encajadas dan nueve intentos.
    client = anthropic.Anthropic(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=config.LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )
    response = client.messages.create(
        model=config.DEEPSEEK_MODEL,
        # Techo propio, no LLM_MAX_TOKENS: ver DEEPSEEK_MAX_TOKENS en config.py.
        # Razona siempre y lo que razona varía mucho; dos ejecuciones reales se
        # quedaron sin espacio pensando antes de escribir el diagnóstico.
        max_tokens=config.DEEPSEEK_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return _texto_de(response)


def _texto_de(response) -> str:
    """El texto de la respuesta, saltándose los bloques de razonamiento.

    Con razonamiento extendido la respuesta llega en VARIOS bloques y el primero
    NO es el texto: es el pensamiento. `response.content[0].text` -- que es lo
    que había aquí -- devolvería entonces el razonamiento en lugar de la
    respuesta, o reventaría con AttributeError si el bloque no tiene 'text'.

    Y no es un detalle cosmético: lo que sale de aquí va directo a
    _extract_json_payload() en workflow.py. Devolver el razonamiento en vez del
    JSON haría fallar el parseo de TODOS los pasos, y el mensaje de error no
    apuntaría ni de lejos a esta línea.
    """
    for bloque in response.content:
        if getattr(bloque, "type", None) == "text":
            return bloque.text
    # Sin bloque de texto no hay respuesta que devolver. Pasa, por ejemplo, si
    # max_tokens se agota razonando. Se avisa con el motivo de parada, que es lo
    # que lo explica.
    raise LLMGenerationError(
        "El modelo no devolvió texto (stop_reason=%s, bloques=%s). Si es "
        "'max_tokens' y solo hay un bloque 'thinking', se quedó sin espacio "
        "RAZONANDO: en este protocolo LLM_MAX_TOKENS cubre el razonamiento y la "
        "respuesta juntos. Subirlo es la salida; apagar el razonamiento solo "
        "sirve en la ruta de Anthropic (ANTHROPIC_THINKING), porque "
        "deepseek-flash razona siempre." % (
            getattr(response, "stop_reason", "?"),
            [getattr(b, "type", "?") for b in response.content]),
    )
