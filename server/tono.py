"""
Red de seguridad del tono: aunque el prompt pide bromas con cariño, si el LLM suelta una expresión hostil de esta
lista, esa frase no se dice (se registra en el log). La lista es corta a propósito: insultos y frases que hacen
sentir mal, no groserías ligeras.
"""
from __future__ import annotations

import re

from shared.texto import normalizar

# Normalizadas (minúsculas, sin acentos). Se buscan como palabras completas.
EXPRESIONES_HOSTILES = (
    "skill issue", "no me hagas perder el tiempo", "me haces perder el tiempo", "perdiendo el tiempo contigo",
    "manco", "manca", "mancos", "noob", "idiota", "estupido", "estupida", "pendejo", "pendeja", "imbecil",
    "inutil", "baboso", "babosa", "menso", "mensa", "tonto", "tonta", "patetico", "patetica", "das pena",
    "das risa", "callate", "eres malisimo", "eres malisima", "basura", "perdedor", "perdedora", "lloron",
    "llorona",
)
_PATRON = re.compile(r"\b(" + "|".join(re.escape(e) for e in EXPRESIONES_HOSTILES) + r")\b")


def expresion_hostil(texto: str) -> str | None:
    """La primera expresión hostil que contiene el texto, o None."""
    m = _PATRON.search(normalizar(texto))
    return m.group(1) if m else None
