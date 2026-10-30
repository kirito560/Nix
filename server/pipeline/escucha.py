"""
Escucha de una conexión: audio del cliente -> VAD -> STT -> Orquestador.

Por cada frase que detecta el VAD se lanza una tarea que abre la sesión STT y le va pasando el audio
por una cola (así el audio no se pierde mientras se abre la conexión con el proveedor). Al cerrar la
frase, la tarea pide el texto final y se lo pasa al Orquestador, que responde.

Transcripción adelantada: con STT que no es streaming (Groq), en cuanto el usuario lleva STT_ADELANTO_MS
callado se empieza a transcribir lo que hay. Si la frase termina sin más voz, ese resultado se usa y se
ahorra la diferencia con VAD_SILENCIO_MS (~200 ms); si sigue hablando, se descarta.

Barge-in: si la frase empieza mientras el personaje habla, solo cuenta cuando junta BARGE_MIN_VOZ_MS de
voz; entonces se interrumpe al personaje y la frase sigue como cualquier otra. Si termina antes, se toma
por eco de su propia voz (o ruido) y se descarta sin transcribirla.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from server.latencia import Span
from server.pipeline.stt import ProveedorSTT, a_pcm16
from server.pipeline.vad import DetectorVoz, Reagrupador

if TYPE_CHECKING:
    from server.orquestador import Orquestador

log = logging.getLogger("nix.escucha")


class Escucha:
    def __init__(self, detector: DetectorVoz, stt: ProveedorSTT, orquestador: "Orquestador",
                 barge_in: bool = True, barge_min_voz_ms: float = 400.0, adelanto_ms: float = 0.0):
        self.detector, self.stt, self.orquestador = detector, stt, orquestador
        self.barge_in, self.barge_min_voz_ms = barge_in, barge_min_voz_ms
        self.adelanto_ms = adelanto_ms
        self.adelanto_de: float | None = None    # t_ultima_voz de la pausa ya adelantada
        self.reagrupador = Reagrupador()
        self.cola: asyncio.Queue | None = None
        self.tareas: set[asyncio.Task] = set()
        self.pendiente_de_barge = False      # la frase en curso empezó mientras el personaje hablaba

    async def recibir_audio(self, pcm16: bytes) -> None:
        for ventana in self.reagrupador.agregar(pcm16):
            for ev in self.detector.procesar(ventana, time.monotonic()):
                self._evento(ev)

    def _evento(self, ev) -> None:
        if ev.tipo == "inicio":
            self.pendiente_de_barge = self.orquestador.hablando
            self.cola = asyncio.Queue()
            self.cola.put_nowait(a_pcm16(ev.audio))
            self._lanzar(self._frase(self.cola, str(uuid.uuid4())), "frase")
        elif ev.tipo == "voz" and self.cola is not None:
            self.cola.put_nowait(a_pcm16(ev.audio))
            d = self.detector
            if (self.adelanto_ms and d.en_frase and d.ms_silencio >= self.adelanto_ms
                    and self.adelanto_de != d.t_ultima_voz and not self.pendiente_de_barge):
                self.adelanto_de = d.t_ultima_voz
                self.cola.put_nowait(("adelantar", d.t_ultima_voz))
            if self.pendiente_de_barge and self.detector.ms_voz >= self.barge_min_voz_ms:
                self.pendiente_de_barge = False
                if self.barge_in:
                    self._lanzar(self.orquestador.interrumpir("barge-in"), "barge-in")
                else:
                    self.cola.put_nowait(("descartada", 0.0, 0.0))      # sin barge-in: no se le interrumpe
                    self.cola = None
        elif ev.tipo in ("fin", "descartada") and self.cola is not None:
            if self.pendiente_de_barge:
                log.info("🔇 frase durante su voz, demasiado corta para interrumpir: la tomo por eco")
                ev.tipo = "descartada"
            self.pendiente_de_barge = False
            self.cola.put_nowait((ev.tipo, ev.t_ultima_voz, time.monotonic()))
            self.cola = None

    def _lanzar(self, corrutina, nombre: str) -> None:
        tarea = asyncio.create_task(corrutina, name=nombre)
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def _frase(self, cola: asyncio.Queue, turno: str) -> None:
        try:
            sesion = await self.stt.abrir()
        except Exception:
            log.exception("❌ no pude abrir el STT (%s)", self.stt.nombre)
            return
        terminada = False
        adelantado_de = None
        try:
            while True:
                item = await cola.get()
                if isinstance(item, bytes):
                    await sesion.enviar(item)
                    continue
                if item[0] == "adelantar":
                    adelantado_de = item[1]
                    sesion.adelantar()
                    continue
                tipo, t_voz_fin, t_vad_fin = item
                if tipo == "descartada":
                    log.debug("frase descartada")
                    return
                span = Span(turno)
                span.marcar("voz_fin", t_voz_fin)
                span.marcar("vad_fin", t_vad_fin)
                resultado = await sesion.finalizar(usar_adelanto=adelantado_de == t_voz_fin)
                terminada = True
                span.marcar("stt_final")
                if resultado.texto is None:
                    log.info("🙉 frase descartada: %s", resultado.motivo)
                    return
                log.info("🗣️ usuario: %s", resultado.texto)
                await self.orquestador.al_texto_usuario(turno, resultado.texto, span)
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
        await self.orquestador.cerrar()
