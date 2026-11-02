"""
Estado de la sesión con el cliente conectado: lo que vale mientras dura esa conexión y se olvida al cerrarla.
No es memoria: nada de esto se guarda en disco ni entra en el historial de la conversación.

  - historial: los últimos `max_turnos` turnos (usuario + Nix) de esta conversación, que el LLM recibe como
    contexto. Al desconectarse el PC se borran; no van a ninguna memoria de largo plazo.
  - animo: energía, paciencia y cercanía de Nix (server/animo.py); solo cambian el tono del prompt.
  - presente: si hay alguien frente a la cámara (None = cámara apagada o sin dato). Solo se usa como una línea
    en el prompt de sistema del LLM, turno a turno.
"""
from __future__ import annotations

import logging
from typing import Optional

from server.animo import Animo
from server.orquestador import Historial
from shared.protocol import PresenciaUpdate

log = logging.getLogger("nix.sesion")

# Es solo contexto: Nix no lo comenta por su cuenta ni habla porque cambie (ningún código responde a la presencia).
_NO_MENCIONAR = "Es solo contexto: no lo menciones salvo que el usuario pregunte si lo ves o si está ahí."
TEXTO_PRESENTE = f"Presencia (cámara): el usuario está frente a la cámara. {_NO_MENCIONAR}"
TEXTO_AUSENTE = f"Presencia (cámara): el usuario no está frente a la cámara. {_NO_MENCIONAR}"


class EstadoSesion:
    def __init__(self, max_turnos: int = 10):
        self.historial = Historial(max_turnos)
        self.animo = Animo()
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
