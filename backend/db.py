"""Persistencia dev en JSON; intercambiable por Supabase."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _REPO_ROOT / "db" / "data.json"

_instance: DataStore | None = None


def _user_key(phone: str, tenant: str) -> str:
    return f"{phone.strip()}:{tenant.strip().lower()}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _read_file_sync(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip() or "{}"
    return json.loads(raw)


def _write_file_sync(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _migrate_legacy(data: dict[str, Any]) -> None:
    """Importa abuse_log (T04) hacia abuse_events."""
    if "abuse_log" not in data:
        return
    events = data.setdefault("abuse_events", [])
    for row in data.get("abuse_log", []):
        try:
            events.append(
                {
                    "phone": row["phone"],
                    "tenant": row["tenant"],
                    "text": "",
                    "ts": row["ts"],
                },
            )
        except (KeyError, TypeError):
            continue
    del data["abuse_log"]


def _prune_abuse_events(events: list[dict[str, Any]], *, hours: int) -> None:
    cutoff = _now_utc() - timedelta(hours=hours)
    keep: list[dict[str, Any]] = []
    for e in events:
        try:
            if _parse_ts(e["ts"]) >= cutoff:
                keep.append(e)
        except (KeyError, TypeError, ValueError):
            continue
    events.clear()
    events.extend(keep)


def _prune_expired_blocks(blocks: dict[str, str]) -> None:
    now = _now_utc()
    expired = [k for k, v in blocks.items() if _parse_ts(v) <= now]
    for k in expired:
        del blocks[k]


class DataStore:
    """Capa de datos JSON; sustituir por cliente Supabase en un solo módulo."""

    def __init__(self, data_path: Path | None = None) -> None:
        self._path = data_path or _DEFAULT_PATH
        self._lock = asyncio.Lock()

    async def _load(self) -> dict[str, Any]:
        data = await asyncio.to_thread(_read_file_sync, self._path)
        _migrate_legacy(data)
        return data

    async def _save(self, data: dict[str, Any]) -> None:
        await asyncio.to_thread(_write_file_sync, self._path, data)
        logger.debug("db guardada en %s", self._path)

    async def get_conversation(
        self,
        phone: str,
        tenant: str,
    ) -> list[dict[str, Any]]:
        # SUPABASE: SELECT role, content, ts FROM messages
        #   WHERE phone=? AND tenant=? ORDER BY ts
        async with self._lock:
            data = await self._load()
            key = _user_key(phone, tenant)
            rows = data.get("conversations", {}).get(key, [])
            return [dict(r) for r in rows]

    async def save_message(
        self,
        phone: str,
        tenant: str,
        role: str,
        content: str,
    ) -> None:
        # SUPABASE: INSERT INTO messages (phone, tenant, role, content, ts)
        async with self._lock:
            data = await self._load()
            conv = data.setdefault("conversations", {})
            key = _user_key(phone, tenant)
            conv.setdefault(key, []).append(
                {
                    "role": role,
                    "content": content,
                    "ts": _iso(_now_utc()),
                },
            )
            await self._save(data)

    async def replace_conversation(
        self,
        phone: str,
        tenant: str,
        messages: list[dict[str, Any]],
    ) -> None:
        # SUPABASE: DELETE FROM messages WHERE phone=? AND tenant=?;
        #   luego INSERT masivo de la lista truncada.
        async with self._lock:
            data = await self._load()
            key = _user_key(phone, tenant)
            data.setdefault("conversations", {})[key] = [dict(m) for m in messages]
            await self._save(data)

    async def get_abuse_count(
        self,
        phone: str,
        tenant: str,
        minutes: int,
    ) -> int:
        # SUPABASE: SELECT COUNT(*) FROM abuse_events
        #   WHERE phone=? AND tenant=? AND ts > now()-interval
        async with self._lock:
            data = await self._load()
            since = _now_utc() - timedelta(minutes=minutes)
            n = 0
            for e in data.get("abuse_events", []):
                if (
                    e.get("phone") == phone.strip()
                    and e.get("tenant") == tenant.strip().lower()
                ):
                    try:
                        if _parse_ts(e["ts"]) >= since:
                            n += 1
                    except (KeyError, TypeError, ValueError):
                        continue
            return n

    async def log_abuse(
        self,
        phone: str,
        tenant: str,
        text: str,
    ) -> None:
        # SUPABASE: INSERT INTO abuse_events (phone, tenant, text, ts)
        async with self._lock:
            data = await self._load()
            events = data.setdefault("abuse_events", [])
            events.append(
                {
                    "phone": phone.strip(),
                    "tenant": tenant.strip().lower(),
                    "text": text,
                    "ts": _iso(_now_utc()),
                },
            )
            _prune_abuse_events(events, hours=48)
            await self._save(data)

    async def is_blocked(self, phone: str, tenant: str) -> bool:
        # SUPABASE: SELECT 1 FROM abuse_blocks
        #   WHERE phone=? AND tenant=? AND until > now()
        async with self._lock:
            data = await self._load()
            blocks = data.setdefault("abuse_blocks", {})
            n_before = len(blocks)
            _prune_expired_blocks(blocks)
            need_save = len(blocks) < n_before
            key = _user_key(phone, tenant)
            raw = blocks.get(key)
            if not raw:
                if need_save:
                    await self._save(data)
                return False
            try:
                ok = _parse_ts(raw) > _now_utc()
            except (TypeError, ValueError):
                del blocks[key]
                need_save = True
                ok = False
            if need_save:
                await self._save(data)
            return ok

    async def set_blocked(
        self,
        phone: str,
        tenant: str,
        until: datetime,
    ) -> None:
        # SUPABASE: UPSERT abuse_blocks (phone, tenant, until)
        async with self._lock:
            data = await self._load()
            blocks = data.setdefault("abuse_blocks", {})
            blocks[_user_key(phone, tenant)] = _iso(until)
            await self._save(data)

    async def log_escalation(
        self,
        phone: str,
        tenant: str,
        text: str,
    ) -> None:
        # SUPABASE: INSERT INTO escalations (phone, tenant, text, ts)
        async with self._lock:
            data = await self._load()
            data.setdefault("escalations", []).append(
                {
                    "phone": phone.strip(),
                    "tenant": tenant.strip().lower(),
                    "text": text,
                    "ts": _iso(_now_utc()),
                },
            )
            await self._save(data)


def get_db() -> DataStore:
    """Punto único de acceso a datos; swap Supabase solo aquí."""
    global _instance
    if _instance is None:
        _instance = DataStore()
    return _instance
