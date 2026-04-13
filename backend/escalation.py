"""Escalamiento a operador humano según reglas de negocio."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

import httpx

from config import get_settings

logger = logging.getLogger(__name__)

_HUMAN_REQUEST = re.compile(
    r"(quiero\s+hablar\s+con\s+(una\s+)?persona|hablar\s+con\s+(una\s+)?persona|"
    r"con\s+un\s+humano|humano\s+real|operador|asesor(\s+humano)?|"
    r"agente\s+humano|quiero\s+hablar\s+con\s+alguien|"
    r"comunica(r)?(me)?\s+con|ponme\s+con|atenci[oó]n\s+humana)",
    re.IGNORECASE,
)

_BOT_DEFLECT = re.compile(
    r"(no\s+puedo|no\s+tengo\s+info|no\s+tengo\s+informaci[oó]n|"
    r"no\s+tengo\s+los\s+datos|no\s+tenemos\s+(esa\s+)?info)",
    re.IGNORECASE,
)

_PAX_EVENT = re.compile(
    r"(evento|boda|celebraci[oó]n|fiesta|reuni[oó]n)[^\n]{0,50}?"
    r"(\d{1,3})\s*(pax|personas|invitad)",
    re.IGNORECASE,
)

_MAYORISTA = re.compile(
    r"(mayorista|por\s+mayor|al\s+por\s+mayor|venta\s+mayorista)",
    re.IGNORECASE,
)

_MONEY_CHUNK = re.compile(
    r"(?<![\d])(\d{1,3}(?:[.\s]\d{3})+|\d{4,})(?![\d])",
)


class BridgeClient(Protocol):
    """Inyectable: el backend llama al bridge (p. ej. POST /send)."""

    async def send_whatsapp(
        self,
        *,
        tenant: str,
        to: str,
        text: str,
    ) -> None:
        ...


class HttpxBridgeClient:
    """Implementación por defecto con httpx hacia `BRIDGE_URL/send`."""

    def __init__(self, base_url: str | None = None) -> None:
        url = base_url or get_settings().bridge_url
        self._base = str(url).strip().rstrip("/")

    async def send_whatsapp(
        self,
        *,
        tenant: str,
        to: str,
        text: str,
    ) -> None:
        secret = get_settings().bridge_secret.strip()
        if not secret:
            logger.error("HttpxBridgeClient: BRIDGE_SECRET vacío")
            return
        payload = {"tenant": tenant, "to": to, "text": text}
        headers = {"Authorization": f"Bearer {secret}"}
        url = f"{self._base}/send"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            logger.error(
                "bridge /send falló: %s %s",
                resp.status_code,
                resp.text[:200],
            )


def should_escalate_price(amount: int) -> bool:
    """True si el monto supera el umbral configurado."""
    limit = get_settings().escalation_price_limit
    return amount >= limit


def operator_wa_id(tenant: str) -> str | None:
    """Número configurado del operador para el tenant."""
    s = get_settings()
    mapping = {
        "pasteleria": s.operator_pasteleria,
        "pijamas": s.operator_pijamas,
        "comida": s.operator_comida,
    }
    raw = mapping.get(tenant.strip().lower(), "")
    cleaned = str(raw).strip()
    if not cleaned:
        logger.warning("sin operador para tenant %s", tenant)
        return None
    return cleaned


def _looks_like_co_cell(n: int) -> bool:
    """Evita tomar un celular CO (10 dígitos, empieza en 3) como monto."""
    s = str(n)
    return len(s) == 10 and s[0] == "3"


def _max_amount_in_text(text: str) -> int:
    best = 0
    for m in _MONEY_CHUNK.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) < 4:
            continue
        try:
            val = int(digits)
        except ValueError:
            continue
        if _looks_like_co_cell(val):
            continue
        if val > best:
            best = val
    return best


def _last_assistant_text(history: list[dict[str, Any]]) -> str:
    for msg in reversed(history):
        if str(msg.get("role", "")).strip().lower() != "assistant":
            continue
        raw = msg.get("content")
        return str(raw or "")
    return ""


def should_escalate(
    text: str,
    history: list[dict[str, Any]],
    tenant: str,
) -> bool:
    """Reglas heurísticas para pasar el caso a un humano."""
    t = text.strip()
    key = tenant.strip().lower()
    if not t:
        return False

    if _HUMAN_REQUEST.search(t):
        return True

    limit = get_settings().escalation_price_limit
    if _max_amount_in_text(t) >= limit:
        return True

    if len(history) >= 4 and _BOT_DEFLECT.search(_last_assistant_text(history)):
        return True

    if key == "comida":
        m = _PAX_EVENT.search(t)
        if m:
            try:
                if int(m.group(2)) > 30:
                    return True
            except (TypeError, ValueError):
                pass

    if key == "pijamas" and _MAYORISTA.search(t):
        return True

    return False


def _format_client_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"+{digits}" if digits else phone.strip()


async def notify_human(
    phone: str,
    text: str,
    tenant: str,
    bridge_client: BridgeClient,
) -> None:
    """Avisa al operador por WhatsApp vía bridge POST /send."""
    op = operator_wa_id(tenant)
    if not op:
        return
    display = _format_client_phone(phone)
    body = (
        f"🔔 {tenant} | Cliente: {display} | Mensaje: {text[:150]}"
    )
    await bridge_client.send_whatsapp(tenant=tenant, to=op, text=body)
