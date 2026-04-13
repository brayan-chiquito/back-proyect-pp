"""Cliente Anthropic y generación de respuestas con Claude."""

import logging
from typing import Any

import anthropic

from agents import get_agent
from config import get_settings

logger = logging.getLogger(__name__)

_FALLBACK = (
    "Ahora mismo no puedo generar una respuesta. "
    "Intenta de nuevo en unos minutos, por favor."
)


def _build_messages(
    history: list[dict[str, Any]],
    text: str,
) -> list[dict[str, str]]:
    """Últimos turnos válidos + mensaje actual; máximo 5 entradas."""
    out: list[dict[str, str]] = []
    for m in history:
        role = str(m.get("role", "")).strip().lower()
        if role not in ("user", "assistant"):
            continue
        raw = m.get("content")
        if raw is None:
            continue
        content = str(raw).strip()
        if not content:
            continue
        out.append({"role": role, "content": content})
    out.append({"role": "user", "content": text.strip()})
    return out[-10:]


def _extract_text(resp: anthropic.types.Message) -> str:
    for block in resp.content:
        if block.type == "text":
            return block.text.strip()
    return ""


async def get_ai_response(
    text: str,
    history: list[dict[str, Any]],
    tenant: str,
    products_ctx: str,
) -> str:
    """
    Claude con system + catálogo; historial recortado a 5 mensajes.
    Modelo desde AI_MODEL; max_tokens fijo 800 (reglas del proyecto).
    """
    if not text.strip():
        return _FALLBACK

    try:
        agent = get_agent(tenant)
    except ValueError:
        logger.warning("get_ai_response: tenant inválido %r", tenant)
        return _FALLBACK

    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        logger.error("get_ai_response: falta ANTHROPIC_API_KEY")
        return _FALLBACK

    system = (
        f"{agent.system_prompt.strip()}\n\n{products_ctx.strip()}"
    )
    messages = _build_messages(history, text)
    model = settings.ai_model.strip() or "claude-haiku-4-5-20251001"

    try:
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
        )
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            system=system,
            messages=messages,
        )
    except Exception as exc:
        logger.error(
            "get_ai_response: fallo API Anthropic (%s)",
            exc,
            exc_info=True,
        )
        return _FALLBACK

    reply = _extract_text(resp)
    if not reply:
        logger.warning("get_ai_response: respuesta vacía del modelo")
        return _FALLBACK
    return reply
