"""
llm_client.py — Capa de abstracción sobre el proveedor de LLM (Gemini / Anthropic)

¿Qué hace este fichero?
  Aísla al resto del workflow de qué proveedor de IA se usa para razonar.
  Expone una única función, generate(), que siempre recibe un system prompt
  y un mensaje de usuario en texto plano, y siempre devuelve texto plano.
  Los Steps 4 y 6 de workflow.py llaman a esta función sin saber si por debajo
  hay Gemini o Claude.

  Nota: generate() nunca pasa el parámetro "tools" a ninguno de los dos
  proveedores. El modelo solo ve texto y devuelve texto; quien llama a los
  MCP servers es el código Python (graph_client.py, pi_client.py). Esto es
  un workflow, no un agente -- ver CLAUDE.md, "Terminología".

¿Por qué existe esta capa?
  Anthropic y Google no comparten un estándar de API: cada uno define su
  propio SDK, su propio formato de mensajes y su propio formato de
  respuesta (ver CLAUDE.md). Aislar esa diferencia aquí evita que el resto
  del workflow tenga que conocerla, y permite cambiar de proveedor cambiando
  una sola variable de entorno.

¿Cómo se elige el proveedor?
  Variable de entorno LLM_PROVIDER ("gemini" por defecto, o "anthropic").

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
        ValueError: si LLM_PROVIDER no es "gemini" ni "anthropic".
        LLMGenerationError: si el proveedor falla tras agotar los reintentos
                             (o falla con un error no transitorio).
    """
    provider = config.LLM_PROVIDER
    if provider not in ("gemini", "anthropic"):
        raise ValueError(
            f"LLM_PROVIDER desconocido: '{provider}' (valores válidos: 'gemini', 'anthropic')"
        )
    try:
        if provider == "gemini":
            return _generate_gemini(system_prompt, user_message)
        return _generate_anthropic(system_prompt, user_message)
    except Exception as exc:
        raise LLMGenerationError(f"El proveedor '{provider}' no respondió: {exc}") from exc


def _is_transient_gemini_error(exc: BaseException) -> bool:
    """5xx (sobrecarga, mantenimiento...) es reintentable; 4xx (auth, payload
    inválido) no lo es -- reintentar no lo arregla."""
    from google.genai import errors
    return isinstance(exc, errors.ServerError)


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
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=config.LLM_MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
