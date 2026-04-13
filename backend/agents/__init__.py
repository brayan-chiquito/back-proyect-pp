"""Resolución de agente por tenant: prompts en agents/{tenant}.py."""

from __future__ import annotations

from dataclasses import dataclass

from . import comida, pasteleria, pijamas


@dataclass(frozen=True, slots=True)
class Agent:
    """Definición mínima usada por el motor de IA y la UI de replies."""

    tenant: str
    system_prompt: str
    quick_replies: tuple[str, ...]


def get_agent(tenant: str) -> Agent:
    """Devuelve el agente del tenant o falla si el slug no existe."""
    key = tenant.strip().lower()
    if key == "pasteleria":
        return Agent(
            "pasteleria",
            pasteleria.SYSTEM_PROMPT,
            pasteleria.QUICK_REPLIES,
        )
    if key == "pijamas":
        return Agent(
            "pijamas",
            pijamas.SYSTEM_PROMPT,
            pijamas.QUICK_REPLIES,
        )
    if key == "comida":
        return Agent("comida", comida.SYSTEM_PROMPT, comida.QUICK_REPLIES)
    msg = f"tenant desconocido: {tenant!r}"
    raise ValueError(msg)


__all__ = ["Agent", "get_agent"]
