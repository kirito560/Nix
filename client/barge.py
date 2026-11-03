"""
Qué parte de la voz del personaje ya sonó, y el corte al instante cuando hablas encima (opcional).

  - Reproduccion: envuelve la salida de audio. Sabe de qué turno es la voz que suena y hasta qué pts se ha
    encolado; con lo que queda en cola calcula cuánto sonó de verdad. Al callarla (playback.flush del servidor
    o barge-in del cliente) devuelve ese dato, que va al servidor como playback.ack para que el historial
    guarde solo lo que se oyó.
  - BargeCliente (BARGE_CLIENTE=1, apagado por defecto): mira el micrófono ya sin eco (después del AEC).
    Si mientras ella suena hay voz por encima de BARGE_CLIENTE_UMBRAL_DB durante BARGE_CLIENTE_MIN_MS, la calla
    en el PC sin esperar al servidor y le manda barge.in. Tu audio no se toca: sigue yendo entero al servidor,
    que lo junta con el pre-roll y lo transcribe como cualquier frase.
    Es por energía, no un VAD: con bocinas fuertes, el eco que deje pasar el AEC puede dispararlo. Por eso
    está apagado hasta probarlo en el PC.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable, Optional

import numpy as np

from shared.protocol import MS_TRAMA, BargeIn, PlaybackAck

log = logging.getLogger("nix.cliente")


class Envio:
    """La función para mandar mensajes al servidor mientras hay conexión (None si no hay)."""

    def __init__(self):
        self.enviar: Optional[Callable[[object], Awaitable[None]]] = None

    def conectar(self, enviar: Optional[Callable[[object], Awaitable[None]]]) -> None:
        self.enviar = enviar

    async def mandar(self, msg) -> None:
        if self.enviar is None:
            return
        try:
            await self.enviar(msg)
        except Exception as e:  # noqa: BLE001 - conexión caída: no importa, el turno ya terminó
            log.debug("no pude mandar %s: %s", msg.type, e)


class Reproduccion:
    def __init__(self, salida):
        self.salida = salida
        self.turno: Optional[str] = None
        self.fin_ms = 0.0                  # hasta dónde se encoló la voz del turno (pts + duración)
        self.callado = False               # este turno ya se calló (no se repite el ack)

    def al_audio(self, turno: str, pts_ms: int, sample_rate: int, pcm: bytes) -> None:
        if turno != self.turno:
            self.turno, self.fin_ms, self.callado = turno, 0.0, False
        if self.callado:
            return                         # llegó tarde, después de callarla: no suena
        self.fin_ms = max(self.fin_ms, pts_ms + len(pcm) / 2 / sample_rate * 1000)
        self.salida.reproducir(sample_rate, pcm)

    def sonando(self) -> bool:
        return not self.callado and self.salida.pendiente_ms() > 0

    def callar(self) -> Optional[PlaybackAck]:
        """La calla al instante. Devuelve cuánto sonó del turno (o None si ya estaba callada)."""
        pendiente = self.salida.pendiente_ms()
        self.salida.vaciar()
        if self.turno is None or self.callado:
            return None
        self.callado = True
        return PlaybackAck(turn_id=self.turno, pts_played=max(0, round(self.fin_ms - pendiente)))


class BargeCliente:
    def __init__(self, reproduccion: Reproduccion, envio: Envio, umbral_db: float = -35.0, min_ms: float = 250.0):
        self.reproduccion, self.envio = reproduccion, envio
        self.umbral_db, self.min_ms = umbral_db, min_ms
        self.ms_voz = 0.0
        self.disparos = 0

    def procesar(self, pcm16: bytes) -> Optional[BargeIn]:
        """Una trama del micrófono (20 ms, ya sin eco). Devuelve el barge.in si la calló."""
        if not self.reproduccion.sonando():
            self.ms_voz = 0.0
            return None
        x = np.frombuffer(pcm16, "<i2").astype(np.float32) / 32768.0
        nivel = 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9) if len(x) else -120.0
        self.ms_voz = self.ms_voz + MS_TRAMA if nivel > self.umbral_db else 0.0
        if self.ms_voz < self.min_ms:
            return None
        self.ms_voz = 0.0
        ack = self.reproduccion.callar()
        if ack is None:
            return None
        self.disparos += 1
        log.info("✋ te oí hablar encima: la callé (sonaron %d ms de su respuesta)", ack.pts_played)
        msg = BargeIn(turn_id=ack.turn_id, pts_played=ack.pts_played)
        try:
            asyncio.get_running_loop().create_task(self.envio.mandar(msg), name="barge-in")
        except RuntimeError:               # sin bucle (pruebas síncronas)
            pass
        return msg

    async def vigilar(self, tramas: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Deja pasar las tramas tal cual (van enteras al servidor) mirando si hay que callarla."""
        async for trama in tramas:
            self.procesar(trama)
            yield trama
