"""
Personajes intercambiables. Cada uno es un archivo personajes/<id>.md con una cabecera simple:

    ---
    nombre: Nix
    voz: es-MX-DaliaNeural
    ---
    (ficha del personaje: el carácter estable que va primero en el prompt)

PERSONAJE=<id> en el .env elige cuál se carga. Cada personaje tiene su propia carpeta de datos
(NIX_DATOS_DIR/<id>): memoria, marcadores y retos no se comparten entre personajes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Personaje:
    id: str
    nombre: str
    voz: str
    ficha: str
    datos: Path        # carpeta de datos propia de este personaje

    def crear_carpeta(self) -> Path:
        self.datos.mkdir(parents=True, exist_ok=True)
        return self.datos


def _cabecera(texto: str, ruta: Path) -> tuple[dict, str]:
    lineas = texto.splitlines()
    if not lineas or lineas[0].strip() != "---" or "---" not in [l.strip() for l in lineas[1:]]:
        raise ValueError(f"{ruta.name}: falta la cabecera '---' con nombre y voz")
    fin = [l.strip() for l in lineas[1:]].index("---") + 1
    campos = {}
    for linea in lineas[1:fin]:
        if ":" in linea:
            clave, valor = linea.split(":", 1)
            campos[clave.strip().lower()] = valor.strip()
    return campos, "\n".join(lineas[fin + 1:]).strip()


def cargar_personaje(id_personaje: str, carpeta: Path, datos_dir: Path) -> Personaje:
    ruta = carpeta / f"{id_personaje}.md"
    if not ruta.is_file():
        disponibles = ", ".join(sorted(p.stem for p in carpeta.glob("*.md"))) or "ninguno"
        raise FileNotFoundError(f"No existe el personaje {id_personaje!r} ({ruta}). Disponibles: {disponibles}")
    campos, ficha = _cabecera(ruta.read_text(encoding="utf-8"), ruta)
    for obligatorio in ("nombre", "voz"):
        if not campos.get(obligatorio):
            raise ValueError(f"{ruta.name}: falta '{obligatorio}' en la cabecera")
    return Personaje(id=id_personaje, nombre=campos["nombre"], voz=campos["voz"], ficha=ficha,
                     datos=datos_dir / id_personaje)
