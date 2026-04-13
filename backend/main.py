"""Aplicación FastAPI: rutas y arranque."""

import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import ai_engine
import excel_loader
from classifier import classify_intent
from config import get_settings
from conversation import get_context, save_turn
from db import DataStore, get_db
from escalation import HttpxBridgeClient, notify_human, should_escalate
from guard import abuse_guard, injection_guard, sanitize_user_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="WhatsApp Bots Backend", version="0.1.0")

TENANTS = ("pasteleria", "pijamas", "comida")

_ESCALATION_USER_MSG = (
    "Derivamos tu consulta a un asesor humano; "
    "te contactarán en breve."
)


def get_store() -> DataStore:
    """Inyección de DataStore (singleton)."""
    return get_db()


async def require_bridge_secret(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authorization: Bearer <BRIDGE_SECRET> (mismo secreto que usa el bridge)."""
    secret = get_settings().bridge_secret.strip()
    if not secret:
        raise HTTPException(status_code=503, detail="BRIDGE_SECRET no configurado")
    expected = f"Bearer {secret}"
    if (authorization or "").strip() != expected:
        raise HTTPException(status_code=401, detail="no autorizado")


class MessageBody(BaseModel):
    """Payload POST /message desde el bridge."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    tenant: str
    from_jid: str = Field(alias="from")
    text: str


async def _send_via_bridge(tenant: str, to: str, text: str) -> None:
    """POST /send al bridge (Baileys)."""
    client = HttpxBridgeClient()
    await client.send_whatsapp(tenant=tenant, to=to, text=text)


def _require_tenant(tenant: str) -> str:
    key = tenant.strip().lower()
    if key not in TENANTS:
        raise HTTPException(status_code=400, detail="tenant inválido")
    return key


@app.get("/health")
async def health() -> dict[str, Any]:
    """Estado del servicio y tenants soportados."""
    return {"status": "ok", "tenants": list(TENANTS)}


@app.get("/products/{tenant}")
async def products_debug(tenant: str) -> list[dict[str, Any]]:
    """Catálogo Excel en memoria/caché (solo depuración)."""
    t = _require_tenant(tenant)
    return await excel_loader.load_products(t)


@app.post("/message")
async def message_webhook(
    body: MessageBody,
    _auth: None = Depends(require_bridge_secret),
    store: DataStore = Depends(get_store),
) -> dict[str, bool]:
    """Pipeline inbound: guards → clasificador → escalamiento → IA."""
    tenant = _require_tenant(body.tenant)
    wa_from = body.from_jid.strip()
    text_raw = body.text or ""
    text = sanitize_user_text(text_raw)

    inj = injection_guard(text)
    if inj.get("blocked"):
        await _send_via_bridge(tenant, wa_from, str(inj["reply"]))
        return {"ok": True}

    abuse = await abuse_guard(wa_from, text, tenant)
    if abuse.get("blocked"):
        await _send_via_bridge(tenant, wa_from, str(abuse["reply"]))
        return {"ok": True}
    if abuse.get("reply"):
        await _send_via_bridge(tenant, wa_from, str(abuse["reply"]))

    cl = classify_intent(text, tenant)
    if cl.get("resolved") and cl.get("reply"):
        await _send_via_bridge(tenant, wa_from, str(cl["reply"]))
        return {"ok": True}

    ctx = await get_context(wa_from, tenant, store)
    hist = list(ctx.get("history", []))
    if should_escalate(text, hist, tenant):
        await notify_human(wa_from, text, tenant, HttpxBridgeClient())
        await _send_via_bridge(tenant, wa_from, _ESCALATION_USER_MSG)
        return {"ok": True}

    products = await excel_loader.load_products(tenant)
    products_ctx = excel_loader.format_products_for_context(products)
    reply = await ai_engine.get_ai_response(text, hist, tenant, products_ctx)
    await save_turn(wa_from, tenant, text, reply, store)
    await _send_via_bridge(tenant, wa_from, reply)
    return {"ok": True}
