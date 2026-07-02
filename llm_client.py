"""
llm_client.py — Capa de abstracción sobre el proveedor de LLM (Gemini / Anthropic)

¿Qué hace este fichero?
  Aísla al resto del agente de qué proveedor de IA se usa para razonar.
  Expone una única función, generate(), que siempre recibe un system prompt
  y un mensaje de usuario en texto plano, y siempre devuelve texto plano.
  Los Steps 3 y 5 de agent.py llaman a esta función sin saber si por debajo
  hay Gemini o Claude.

¿Por qué existe esta capa?
  Anthropic y Google no comparten un estándar de API: cada uno define su
  propio SDK, su propio formato de mensajes y su propio formato de
  respuesta (ver CLAUDE.md). Aislar esa diferencia aquí evita que el resto
  del agente tenga que conocerla, y permite cambiar de proveedor cambiando
  una sola variable de entorno.

¿Cómo se elige el proveedor?
  Variable de entorno LLM_PROVIDER ("gemini" por defecto, o "anthropic").
"""

import logging

import config

log = logging.getLogger(__name__)


def generate(system_prompt: str, user_message: str) -> str:
    """Llama al proveedor de LLM configurado y devuelve su respuesta en texto.

    Args:
        system_prompt: rol y dominio fijos del agente (ver agent.SYSTEM_PROMPT).
        user_message: mensaje dinámico de la petición concreta (p.ej.
                      context["claude_prompt"] de build_analysis_context()).

    Returns:
        La respuesta del modelo como texto plano.

    Raises:
        ValueError: si LLM_PROVIDER no es "gemini" ni "anthropic".
    """
    provider = config.LLM_PROVIDER
    if provider == "gemini":
        return _generate_gemini(system_prompt, user_message)
    if provider == "anthropic":
        return _generate_anthropic(system_prompt, user_message)
    raise ValueError(
        f"LLM_PROVIDER desconocido: '{provider}' (valores válidos: 'gemini', 'anthropic')"
    )


def _generate_gemini(system_prompt: str, user_message: str) -> str:
    """Llama a Gemini vía el SDK google-genai."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return response.text


def _generate_anthropic(system_prompt: str, user_message: str) -> str:
    """Llama a Claude vía el SDK anthropic."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text
