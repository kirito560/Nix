"""
Indicador visible de lo que Nix puede ver, en la ventana del cliente:
  - una línea bien visible cada vez que la pantalla o la cámara se encienden o se apagan;
  - el título de la ventana de la consola, que se ve siempre (también en la barra de tareas) y dice qué
    está encendido ahora mismo.
"""
from __future__ import annotations

import sys
from typing import Callable

TITULO_BASE = "Nix"
TITULO_PANTALLA = "VIENDO TU PANTALLA (Ctrl+Alt+P pausa)"
TITULO_CAMARA = "CÁMARA ENCENDIDA (Ctrl+Alt+C apaga)"
TITULO_NADA = "pantalla y cámara apagadas"
LINEA_PANTALLA_ACTIVA = "🔴 PANTALLA COMPARTIDA: Nix ve tu pantalla. Ctrl+Alt+P la pausa al instante."
LINEA_PANTALLA_APAGADA = "⚫ Pantalla en pausa: Nix no ve nada. Ctrl+Alt+P la enciende."
LINEA_CAMARA_ACTIVA = ("🔴 CÁMARA ENCENDIDA: Nix solo sabe si estás frente a ella (sí/no); ninguna imagen sale "
                       "del PC. Ctrl+Alt+C la apaga.")
LINEA_CAMARA_APAGADA = "⚫ Cámara apagada."


def poner_titulo(texto: str) -> None:
    """Título de la ventana de la consola (Windows: SetConsoleTitleW; otras terminales: secuencia OSC)."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(texto)
        elif sys.stdout.isatty():
            sys.stdout.write(f"\x1b]0;{texto}\x07")
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 - sin consola: queda la línea impresa
        pass


class Indicador:
    def __init__(self, escribir: Callable[[str], None] = lambda t: print(t, flush=True),
                 titulo: Callable[[str], None] = poner_titulo):
        self.escribir, self.poner_titulo = escribir, titulo
        self.pantalla_activa = False
        self.camara_activa = False

    def pantalla(self, activa: bool) -> None:
        self.pantalla_activa = activa
        self.escribir(LINEA_PANTALLA_ACTIVA if activa else LINEA_PANTALLA_APAGADA)
        self.poner_titulo(self.titulo())

    def camara(self, activa: bool) -> None:
        self.camara_activa = activa
        self.escribir(LINEA_CAMARA_ACTIVA if activa else LINEA_CAMARA_APAGADA)
        self.poner_titulo(self.titulo())

    def titulo(self) -> str:
        partes = [p for p, activa in ((TITULO_PANTALLA, self.pantalla_activa), (TITULO_CAMARA, self.camara_activa))
                  if activa]
        if not partes:
            return f"{TITULO_BASE} · {TITULO_NADA}"
        return "🔴 " + " · ".join([TITULO_BASE, *partes])
