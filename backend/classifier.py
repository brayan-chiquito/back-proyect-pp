"""Respuestas determinísticas sin llamar a la API de IA."""

import logging
import re
from typing import Any

from agents import get_agent

logger = logging.getLogger(__name__)

# Orden: primero intenciones “de negocio”, luego cortesía (evita “hola”
# absorber un mensaje que también agradece o despide).
# Índices alineados con agents/*: 0 saludo … 6 despedida.
_INTENT_REPLY_INDEX: dict[str, int] = {
    "saludo": 0,
    "horario": 1,
    "ubicacion": 2,
    "pago": 3,
    "envio": 4,
    "gracias": 5,
    "despedida": 6,
}

_INTENT_SPECS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "horario",
        re.compile(
            r"(horario|horarios|a\s+qu[eé]\s+hora|hasta\s+qu[eé]\s+hora|"
            r"cu[aá]ndo\s+abren|cu[aá]ndo\s+cierran|qu[eé]\s+d[ií]as\s+abren)",
            re.IGNORECASE,
        ),
    ),
    (
        "ubicacion",
        re.compile(
            r"(ubicaci[oó]n|d[oó]nde\s+(est[aá]n|queda|son)|direcci[oó]n|"
            r"c[oó]mo\s+llegar|mapa|local|sede)",
            re.IGNORECASE,
        ),
    ),
    (
        "pago",
        re.compile(
            r"(pago|pagos|pagar|nequi|daviplata|efectivo|tarjeta|"
            r"transfer(encia)?|pse\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "envio",
        re.compile(
            r"(env[ií]o|domicilio|despacho|entrega|"
            r"me\s+lo\s+env[ií]an|hacen\s+env[ií]os?)",
            re.IGNORECASE,
        ),
    ),
    (
        "gracias",
        re.compile(
            r"(gracias|te\s+lo\s+agradezco|mil\s+gracias|muchas\s+gracias)",
            re.IGNORECASE,
        ),
    ),
    (
        "despedida",
        re.compile(
            r"(chao|chau|adi[oó]s|hasta\s+luego|nos\s+vemos|\bbye\b|"
            r"hasta\s+pronto|hasta\s+ma[nñ]ana)",
            re.IGNORECASE,
        ),
    ),
    (
        "saludo",
        re.compile(
            r"^\s*(\bhola\b|buen[oa]s\s+(d[ií]as|tardes|noches)|"
            r"\bhey\b)\s*[!¡\.]*\s*$",
            re.IGNORECASE,
        ),
    ),
)


def classify_intent(text: str, tenant: str) -> dict[str, Any]:
    """
    Regex por intención; la respuesta sale de agents/{tenant}.QUICK_REPLIES
    (primeros 7 slots: saludo…despedida, ver _INTENT_REPLY_INDEX).
    """
    raw = text.strip()
    if not raw:
        return {"resolved": False, "reply": None}
    norm = raw.lower()
    try:
        agent = get_agent(tenant)
    except ValueError:
        logger.warning("classify_intent: tenant desconocido %r", tenant)
        return {"resolved": False, "reply": None}
    quick = agent.quick_replies
    for name, pattern in _INTENT_SPECS:
        if not pattern.search(norm):
            continue
        idx = _INTENT_REPLY_INDEX[name]
        if idx >= len(quick):
            logger.warning(
                "classify_intent: QUICK_REPLIES corto para %s (falta índice %s)",
                tenant,
                idx,
            )
            return {"resolved": False, "reply": None}
        return {"resolved": True, "reply": quick[idx]}
    return {"resolved": False, "reply": None}
