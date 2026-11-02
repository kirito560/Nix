"""
Estado de la sesión con el cliente conectado: lo que vale mientras dura esa conexión y se olvida al cerrarla.
No es memoria: nada de esto se guarda en disco ni entra en el historial de la conversación.

  - historial: los últimos `max_turnos` turnos (usuario + Nix) de esta conversación, que el LLM recibe como
    contexto. Al desconectarse el PC se borran; no van a ninguna memoria de largo plazo.
  - presente: si hay alguien frente a la cámara (None = cámara apagada o sin dato). Solo se usa como una línea
    en el prompt de sistema del LLM, turno a turno.
"""
from __future__ import annotations

import logging
from typing import Optional

from server.orquestador import Historial
from shared.protocol import PresenciaUpdate

log = logging.getLogger("nix.sesion")

TEXTO_PRESENTE = "Presencia (cámara): el usuario está frente a la cámara."
TEXTO_AUSENTE = ("Presencia (cámara): el usuario no está frente a la cámara; puede haberse levantado y no oírte "
                 "bien.")


class EstadoSesion:
    def __init__(self, max_turnos: int = 10):
        self.historial = Historial(max_turnos)
        self.presente: Optional[bool] = None

    def actualizar_presencia(self, msg: PresenciaUpdate) -> None:
        anterior = self.presente
        self.presente = msg.presente if msg.activa else None
        if self.presente != anterior:
            log.info("📷 presencia: %s", {True: "frente a la cámara", False: "no está",
                                          None: "sin dato (cámara apagada)"}[self.presente])

    def para_llm(self) -> str:
        if self.presente is None:
            return ""
        return TEXTO_PRESENTE if self.presente else TEXTO_AUSENTE
