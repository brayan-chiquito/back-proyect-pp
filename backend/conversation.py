"""Contexto de conversación por sesión (persistido vía DataStore)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import get_settings
from db import DataStore

logger = logging.getLogger(__name__)


def _parse_ts(ts: object) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _history_slice(msgs: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Últimos `limit` turnos con role/content (y ts si existe)."""
    out: list[dict[str, Any]] = []
    for m in msgs[-limit:]:
        role = str(m.get("role", "")).strip()
        content = m.get("content")
        if not role or content is None:
            continue
        row: dict[str, Any] = {"role": role, "content": str(content)}
        if "ts" in m:
            row["ts"] = m["ts"]
        out.append(row)
    return out


async def get_context(
    phone: str,
    tenant: str,
    db: DataStore,
) -> dict[str, Any]:
    """
    Últimos 5 mensajes si la sesión sigue activa (SESSION_TIMEOUT_MINUTES).
    `db` es la instancia de DataStore (p. ej. get_db()).
    """
    msgs = await db.get_conversation(phone, tenant)
    if not msgs:
        return {"history": [], "session_active": False}
    last_ts = _parse_ts(msgs[-1].get("ts"))
    if last_ts is None:
        logger.warning("get_context: sin ts válido en último mensaje")
        return {"history": [], "session_active": False}
    timeout = max(1, get_settings().session_timeout_minutes)
    idle = datetime.now(timezone.utc) - last_ts
    if idle > timedelta(minutes=timeout):
        return {"history": [], "session_active": False}
    return {
        "history": _history_slice(msgs, limit=10),
        "session_active": True,
    }


async def save_turn(
    phone: str,
    tenant: str,
    user_text: str,
    bot_reply: str,
    db: DataStore,
) -> None:
    """Persiste user+assistant y recorta el historial a 20 mensajes."""
    await db.save_message(phone, tenant, "user", user_text)
    await db.save_message(phone, tenant, "assistant", bot_reply)
    all_msgs = await db.get_conversation(phone, tenant)
    if len(all_msgs) > 20:
        await db.replace_conversation(phone, tenant, all_msgs[-20:])
        logger.debug(
            "save_turn: historial truncado a 20 (%s/%s)",
            tenant,
            phone,
        )
