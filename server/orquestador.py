"""
Orquestador de la conversación (fase 2): texto del usuario -> LLM en streaming -> frases -> TTS -> audio.

Un turno corre en tres tareas encadenadas por colas, para que cada etapa empiece en cuanto tiene algo:
  pensar      tokens del LLM -> Segmentador -> frases
  sintetizar  por cada frase lanza su TTS (como mucho 2 a la vez, así la siguiente ya está lista)
  emitir      en orden: LlmText con la frase y después su audio (audio.out en trozos de 100 ms)

Barge-in: `interrumpir()` cancela el turno y manda PlaybackFlush para que el cliente calle al instante.
`hablando` sigue siendo cierto mientras el cliente reproduce lo ya enviado (se estima por la duración
del audio), porque el servidor manda el audio más rápido que el tiempo real.

Latencia de cada turno (además de las etapas de la Escucha):
  llm_1er_token  primer texto del LLM
  frase_1        primera frase completa para el TTS
  tts_1er_audio  primer PCM del TTS
  audio_enviado  primer audio enviado al cliente (fin del span: lo que tarda en empezar a hablar)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from contextlib import aclosing
from typing import Awaitable, Callable, Optional

from server.latencia import Registro, Span
from server.personaje import Personaje
from server.pipeline.frases import Segmentador
from server.pipeline.llm import ProveedorLLM
from server.pipeline.tts import ProveedorTTS
from shared.protocol import LlmText, PlaybackFlush, SttText, codificar_audio_out

log = logging.getLogger("nix.orquestador")

INSTRUCCIONES_VOZ = """
## CÓMO RESPONDES AHORA
Hablas en voz alta en una conversación en tiempo real: todo lo que escribas se lee con tu voz.
- Respuestas cortas: una a tres frases, salvo que te pidan algo largo.
- Sin emojis, sin markdown, sin listas y sin acotaciones entre asteriscos.
- Lo que te dicen viene de un reconocimiento de voz: si algo no tiene sentido, pide que lo repitan.
"""

MARGEN_REPRODUCCION_S = 0.3        # retraso de red y del búfer de audio del cliente
# Si el LLM falla antes de decir nada (sin internet, límite de Groq...), que no se quede callada sin más.
FRASE_SIN_CEREBRO = "Uy, se me trabó el cerebro. ¿Me lo repites?"
TTS_EN_PARALELO = 2


def prompt_sistema(personaje: Personaje) -> str:
    return f"{personaje.ficha}\n{INSTRUCCIONES_VOZ}".strip()


class Historial:
    """Últimos turnos de la conversación (solo en memoria; la memoria de verdad llega en la fase 4).
    Es del servidor, no de la conexión: sobrevive a que el cliente se reconecte."""

    def __init__(self, max_turnos: int):
        self.mensajes: deque[dict] = deque(maxlen=max(2, max_turnos * 2))

    def agregar(self, rol: str, texto: str) -> None:
        self.mensajes.append({"role": rol, "content": texto})

    def para_llm(self, sistema: str, texto_usuario: str) -> list[dict]:
        return [{"role": "system", "content": sistema}, *self.mensajes,
                {"role": "user", "content": texto_usuario}]


class Turno:
    def __init__(self, turn_id: str, span: Span):
        self.id, self.span = turn_id, span
        self.tarea: Optional[asyncio.Task] = None
        self.dicho: list[str] = []
        self.interrumpido = False


class Orquestador:
    def __init__(self, personaje: Personaje, llm: ProveedorLLM, tts: ProveedorTTS,
                 enviar: Callable[[object], Awaitable[None]], registro: Registro, historial: Historial):
        self.personaje, self.llm, self.tts, self.enviar = personaje, llm, tts, enviar
        self.registro, self.historial = registro, historial
        self.sistema = prompt_sistema(personaje)
        self.turno: Optional[Turno] = None
        self.fin_reproduccion = 0.0

    @property
    def hablando(self) -> bool:
        activo = self.turno is not None and self.turno.tarea is not None and not self.turno.tarea.done()
        return activo or time.monotonic() < self.fin_reproduccion

    async def al_texto_usuario(self, turn_id: str, texto: str, span: Span) -> None:
        """Llamado por la Escucha con cada frase reconocida."""
        if self.hablando:
            await self.interrumpir("nueva frase del usuario")
        await self.enviar(SttText(turn_id=turn_id, texto=texto))
        turno = Turno(turn_id, span)
        turno.tarea = asyncio.create_task(self._turno(turno, texto), name=f"turno-{turn_id[:8]}")
        self.turno = turno

    async def interrumpir(self, motivo: str) -> None:
        turno, self.fin_reproduccion = self.turno, 0.0
        if turno is None:
            return
        log.info("✋ interrumpida (%s)", motivo)
        turno.interrumpido = True
        if turno.tarea is not None and not turno.tarea.done():
            turno.tarea.cancel()
            await asyncio.gather(turno.tarea, return_exceptions=True)
        await self.enviar(PlaybackFlush(turn_id=turno.id))

    async def cerrar(self) -> None:
        if self.turno is not None and self.turno.tarea is not None:
            self.turno.tarea.cancel()
            await asyncio.gather(self.turno.tarea, return_exceptions=True)

    # ------------------------------------------------------------------ un turno
    async def _turno(self, turno: Turno, texto: str) -> None:
        mensajes = self.historial.para_llm(self.sistema, texto)
        frases: asyncio.Queue[Optional[str]] = asyncio.Queue()
        audios: asyncio.Queue = asyncio.Queue()
        tareas = [asyncio.create_task(self._pensar(turno, mensajes, frases), name="pensar"),
                  asyncio.create_task(self._sintetizar(turno, frases, audios), name="sintetizar")]
        try:
            await self._emitir(turno, audios)
            for t in tareas:
                await t                                   # propaga errores del LLM o del TTS
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("❌ error en el turno")
        finally:
            for t in tareas:
                t.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)
            dicho = " ".join(turno.dicho)
            if dicho:
                self.historial.agregar("user", texto)
                self.historial.agregar("assistant", dicho + (" —" if turno.interrumpido else ""))
            if not turno.interrumpido:
                await self.enviar(LlmText(turn_id=turno.id, texto="", final=True))

    async def _pensar(self, turno: Turno, mensajes: list[dict], frases: asyncio.Queue) -> None:
        segmentador = Segmentador()
        try:
            async with aclosing(self.llm.responder(mensajes)) as trozos:
                async for trozo in trozos:
                    self._marcar_una_vez(turno.span, "llm_1er_token")
                    for frase in segmentador.agregar(trozo):
                        self._marcar_una_vez(turno.span, "frase_1")
                        await frases.put(frase)
            for frase in segmentador.terminar():
                self._marcar_una_vez(turno.span, "frase_1")
                await frases.put(frase)
        except Exception:
            log.exception("❌ el LLM (%s) falló", self.llm.nombre)
            if segmentador.emitidas == 0:
                await frases.put(FRASE_SIN_CEREBRO)
        finally:
            await frases.put(None)

    async def _sintetizar(self, turno: Turno, frases: asyncio.Queue, audios: asyncio.Queue) -> None:
        limite = asyncio.Semaphore(TTS_EN_PARALELO)
        tareas: list[asyncio.Task] = []

        async def una(frase: str, cola: asyncio.Queue) -> None:
            try:
                async with limite, aclosing(self.tts.sintetizar(frase)) as audio:
                    async for pcm in audio:
                        await cola.put(pcm)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - una frase sin voz no debe tumbar el turno
                log.warning("🔈 TTS falló en %r: %s", frase[:40], e)
            finally:
                cola.put_nowait(None)

        try:
            while (frase := await frases.get()) is not None:
                cola: asyncio.Queue = asyncio.Queue()
                tareas.append(asyncio.create_task(una(frase, cola), name="tts"))
                await audios.put((frase, cola))
        finally:
            await audios.put(None)
            try:
                await asyncio.gather(*tareas)
            finally:
                for t in tareas:
                    t.cancel()

    async def _emitir(self, turno: Turno, audios: asyncio.Queue) -> None:
        pts_ms = 0
        while (item := await audios.get()) is not None:
            frase, cola = item
            await self.enviar(LlmText(turn_id=turno.id, texto=frase))
            turno.dicho.append(frase)
            log.info("🤖 %s: %s", self.personaje.nombre, frase)
            while (pcm := await cola.get()) is not None:
                self._marcar_una_vez(turno.span, "tts_1er_audio")
                await self.enviar(codificar_audio_out(turno.id, pts_ms, self.tts.sample_rate, pcm))
                if "audio_enviado" not in dict(turno.span.marcas):
                    turno.span.marcar("audio_enviado")
                    self.registro.cerrar(turno.span)
                duracion_s = len(pcm) / 2 / self.tts.sample_rate
                pts_ms += round(duracion_s * 1000)
                self.fin_reproduccion = max(self.fin_reproduccion, time.monotonic()) + duracion_s
        self.fin_reproduccion += MARGEN_REPRODUCCION_S if self.fin_reproduccion else 0.0

    @staticmethod
    def _marcar_una_vez(span: Span, etapa: str) -> None:
        if etapa not in dict(span.marcas):
            span.marcar(etapa)
