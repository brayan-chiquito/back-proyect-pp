"""Validación de entrada usuario: anti-inyección y anti-abuso."""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from config import get_settings
from db import get_db

logger = logging.getLogger(__name__)

_INJECTION = re.compile(
    r"(ignora\s+(las\s+)?instrucciones|actúa\s+como|eres\s+ahora|"
    r"olvida\s+todo|modo\s+sin\s+restricciones|system\s+prompt|jailbreak|"
    r"\bDAN\b|\[INST\]|prompt\s+anterior|revela\s+(tus\s+)?instrucciones)",
    re.IGNORECASE,
)

_ABUSE = re.compile(
    r"(hijueputa|jueputa|hdp\b|h\.?\s*d\.?\s*p\.?|malparid[oa]|gonorrea|"
    r"carechimba|caremond[aá]|mamaguev[oa]|mamahuevo|pirob[oa]|"
    r"pendej[oa]|imb[eé]cil|est[uú]pid[oa]|mierda|verg[aá]|cag[oó]n|"
    r"careverga|hijueputas|puta\b)",
    re.IGNORECASE,
)


def injection_guard(text: str) -> dict[str, Any]:
    """Detecta patrones típicos de prompt injection (regex, sin IA)."""
    if _INJECTION.search(text):
        logger.info("injection_guard: coincidencia")
        return {
            "blocked": True,
            "reply": (
                "Por seguridad no puedo seguir con pedidos que alteren "
                "cómo debo responder. Reformula tu mensaje, por favor."
            ),
        }
    return {"blocked": False}


def sanitize_user_text(text: str, *, max_len: int = 4000) -> str:
    """Recorta longitud y deja listo para clasificador / IA."""
    cleaned = text.strip()
    if len(cleaned) > max_len:
        logger.warning("mensaje truncado por longitud")
        return cleaned[:max_len]
    return cleaned


async def abuse_guard(phone: str, text: str, tenant: str) -> dict[str, Any]:
    """
    Groserías (regex, tono CO); escala con eventos en abuse_events
    (última hora) y bloqueo temporal vía DataStore.
    """
    store = get_db()
    phone_n = phone.strip()
    tenant_n = tenant.strip().lower()

    if await store.is_blocked(phone_n, tenant_n):
        logger.info("abuse_guard: usuario bloqueado %s:%s", phone_n, tenant_n)
        return {
            "blocked": True,
            "reply": (
                "Superaste el límite de advertencias por el tono "
                "del chat. No podremos responder durante un tiempo."
            ),
            "level": 3,
        }

    if not _ABUSE.search(text):
        return {"blocked": False, "reply": None, "level": 0}

    await store.log_abuse(phone_n, tenant_n, text)
    count = await store.get_abuse_count(phone_n, tenant_n, 60)
    minutes = get_settings().abuse_block_minutes
    now = datetime.now(timezone.utc)

    if count >= 3:
        until = now + timedelta(minutes=minutes)
        await store.set_blocked(phone_n, tenant_n, until)
        logger.warning("abuse_guard: bloqueo nivel 3 %s:%s", phone_n, tenant_n)
        return {
            "blocked": True,
            "reply": (
                "Por políticas de respeto debemos pausar esta conversación "
                f"unos {minutes} minutos. Gracias por tu comprensión."
            ),
            "level": 3,
        }

    if count == 2:
        logger.info("abuse_guard: nivel 2 %s:%s", phone_n, tenant_n)
        return {
            "blocked": False,
            "reply": (
                "Es necesario un tono respetuoso. Si continúas con "
                "insultos, tendremos que dejar de responder por un rato."
            ),
            "level": 2,
        }

    logger.info("abuse_guard: nivel 1 %s:%s", phone_n, tenant_n)
    return {
        "blocked": False,
        "reply": (
            "Estamos para ayudarte; por favor evita insultos para "
            "seguir la conversación."
        ),
        "level": 1,
    }
