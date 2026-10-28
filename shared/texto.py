"""Utilidades de texto compartidas."""
from __future__ import annotations

import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, sin acotaciones [Música]/(tos) y sin puntuación."""
    t = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    t = re.sub(r"[\[(][^\])]*[\])]", " ", t)
    t = re.sub(r"[^\w\s]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()
