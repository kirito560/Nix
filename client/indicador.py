"""
Indicador visible de la captura de pantalla, en la ventana del cliente:
  - una línea bien visible cada vez que se enciende o se pausa;
  - el título de la ventana de la consola, que se ve siempre (también en la barra de tareas):
    «🔴 Nix · VIENDO TU PANTALLA» mientras está activa.
"""
from __future__ import annotations

import sys
from typing import Callable

TITULO_ACTIVA = "🔴 Nix · VIENDO TU PANTALLA (Ctrl+Alt+P pausa)"
TITULO_APAGADA = "Nix · pantalla apagada"
LINEA_ACTIVA = "🔴 PANTALLA COMPARTIDA: Nix ve tu pantalla. Ctrl+Alt+P la pausa al instante."
LINEA_APAGADA = "⚫ Pantalla en pausa: Nix no ve nada. Ctrl+Alt+P la enciende."


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


class IndicadorPantalla:
    def __init__(self, escribir: Callable[[str], None] = lambda t: print(t, flush=True),
                 titulo: Callable[[str], None] = poner_titulo):
        self.escribir, self.titulo = escribir, titulo
        self.activa = False

    def __call__(self, activa: bool) -> None:
        self.activa = activa
        self.escribir(LINEA_ACTIVA if activa else LINEA_APAGADA)
        self.titulo(TITULO_ACTIVA if activa else TITULO_APAGADA)
