from pathlib import Path

import pytest

from server.config import ConfigServidor
from server.personaje import cargar_personaje
from shared.config_base import RAIZ

PERSONAJES = RAIZ / "personajes"


def test_nix_por_defecto(tmp_path):
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": "x" * 40, "NIX_DATOS_DIR": str(tmp_path)})
    assert cfg.personaje == "nix"
    nix = cargar_personaje(cfg.personaje, cfg.carpeta_personajes, cfg.datos_dir)
    assert nix.nombre == "Nix" and nix.voz == "es-MX-DaliaNeural"
    assert "Nunca" in nix.ficha or "NUNCA" in nix.ficha
    assert "Marcador de rivalidad" in nix.ficha and "---" not in nix.ficha.splitlines()[0]


def test_personaje_elegido_por_entorno(tmp_path):
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": "x" * 40, "PERSONAJE": "Narwell",
                                        "NIX_DATOS_DIR": str(tmp_path)})
    narwell = cargar_personaje(cfg.personaje, PERSONAJES, tmp_path)
    assert narwell.nombre == "Narwell"


def test_cada_personaje_tiene_su_carpeta_de_datos(tmp_path):
    nix = cargar_personaje("nix", PERSONAJES, tmp_path)
    narwell = cargar_personaje("narwell", PERSONAJES, tmp_path)
    assert nix.datos != narwell.datos
    assert nix.crear_carpeta() == tmp_path / "nix"
    assert list(nix.datos.iterdir()) == []                   # Nix empieza sin recuerdos


def test_personaje_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError, match="nix"):
        cargar_personaje("nadie", PERSONAJES, tmp_path)


def test_cabecera_obligatoria(tmp_path):
    (tmp_path / "malo.md").write_text("# sin cabecera", encoding="utf-8")
    with pytest.raises(ValueError):
        cargar_personaje("malo", tmp_path, tmp_path)
