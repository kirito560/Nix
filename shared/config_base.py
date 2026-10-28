"""Lectura del .env (python-dotenv) y conversión de tipos, común a servidor y cliente."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent


def cargar_env(ruta: Optional[Path] = None) -> None:
    """Carga el .env de la raíz del proyecto sin pisar variables ya definidas en el entorno."""
    load_dotenv(ruta or RAIZ / ".env", override=False)


class Entorno:
    """Acceso tipado a variables de entorno (o a un dict, en los tests)."""

    def __init__(self, fuente: Optional[Mapping[str, str]] = None):
        self.fuente = os.environ if fuente is None else fuente

    def texto(self, clave: str, defecto: str = "") -> str:
        return (self.fuente.get(clave) or "").strip() or defecto

    def entero(self, clave: str, defecto: int) -> int:
        return int(self.texto(clave) or defecto)

    def decimal(self, clave: str, defecto: float) -> float:
        return float(self.texto(clave) or defecto)

    def booleano(self, clave: str, defecto: bool) -> bool:
        valor = self.texto(clave).lower()
        if not valor:
            return defecto
        return valor in ("1", "si", "sí", "true", "yes", "on")

    def lista(self, clave: str, defecto: str = "") -> list[str]:
        return [x.strip() for x in self.texto(clave, defecto).split(",") if x.strip()]
