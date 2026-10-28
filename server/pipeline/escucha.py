"""
Escucha de una conexión (fase 1): audio del cliente -> VAD -> STT -> eco del texto.

Por cada frase que detecta el VAD se lanza una tarea que abre la sesión STT y le va pasando el audio
por una cola (así el audio no se pierde mientras se abre la conexión con el proveedor). Al cerrar la
frase, la tarea pide el texto final y lo envía al cliente con un SttText. En la fase 2 el Orchestrator
recibirá ese texto en lugar de devolverlo como eco.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Awaitable, Callable

from server.latencia import Registro, Span
from server.pipeline.stt import ProveedorSTT, a_pcm16
from server.pipeline.vad import DetectorVoz, Reagrupador
from shared.protocol import SttText

log = logging.getLogger("nix.escucha")


class Escucha:
    def __init__(self, detector: DetectorVoz, stt: ProveedorSTT, enviar: Callable[[SttText], Awaitable[None]],
                 registro: Registro):
        self.detector, self.stt, self.enviar, self.registro = detector, stt, enviar, registro
        self.reagrupador = Reagrupador()
        self.cola: asyncio.Queue | None = None
        self.tareas: set[asyncio.Task] = set()

    async def recibir_audio(self, pcm16: bytes) -> None:
        for ventana in self.reagrupador.agregar(pcm16):
            for ev in self.detector.procesar(ventana, time.monotonic()):
                self._evento(ev)

    def _evento(self, ev) -> None:
        if ev.tipo == "inicio":
            self.cola = asyncio.Queue()
            self.cola.put_nowait(a_pcm16(ev.audio))
            tarea = asyncio.create_task(self._frase(self.cola, str(uuid.uuid4())), name="frase")
            self.tareas.add(tarea)
            tarea.add_done_callback(self.tareas.discard)
        elif ev.tipo == "voz" and self.cola is not None:
            self.cola.put_nowait(a_pcm16(ev.audio))
        elif ev.tipo in ("fin", "descartada") and self.cola is not None:
            self.cola.put_nowait((ev.tipo, ev.t_ultima_voz, time.monotonic()))
            self.cola = None

    async def _frase(self, cola: asyncio.Queue, turno: str) -> None:
        try:
            sesion = await self.stt.abrir()
        except Exception:
            log.exception("❌ no pude abrir el STT (%s)", self.stt.nombre)
            return
        terminada = False
        try:
            while True:
                item = await cola.get()
                if isinstance(item, bytes):
                    await sesion.enviar(item)
                    continue
                tipo, t_voz_fin, t_vad_fin = item
                if tipo == "descartada":
                    log.debug("frase demasiado corta; descartada")
                    return
                span = Span(turno)
                span.marcar("voz_fin", t_voz_fin)
                span.marcar("vad_fin", t_vad_fin)
                resultado = await sesion.finalizar()
                terminada = True
                span.marcar("stt_final")
                if resultado.texto is None:
                    log.info("🙉 frase descartada: %s", resultado.motivo)
                    return
                log.info("🗣️ usuario: %s", resultado.texto)
                await self.enviar(SttText(turn_id=turno, texto=resultado.texto))
                span.marcar("eco")
                self.registro.cerrar(span)
                return
        except Exception:
            log.exception("❌ error en el STT (%s)", self.stt.nombre)
        finally:
            if not terminada:
                await sesion.cancelar()

    async def cerrar(self) -> None:
        for tarea in list(self.tareas):
            tarea.cancel()
        await asyncio.gather(*self.tareas, return_exceptions=True)

