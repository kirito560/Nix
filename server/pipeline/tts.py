"""
Voz (TTS) detrás de una interfaz común: `sintetizar(texto)` devuelve PCM16 mono a `sample_rate` en trozos,
a medida que se genera.

  - TTSEdge: edge-tts (voces neuronales de Microsoft Edge, sin clave). Entrega MP3 de 24 kHz en trozos
    mientras sintetiza; un proceso de ffmpeg los decodifica a PCM sobre la marcha, así el primer audio sale sin
    esperar a la frase entera (Microsoft solo da MP3: probado el 2026-11-02, los formatos PCM no devuelven
    audio). Las conexiones con Microsoft se abren de antemano (server/pipeline/edge_pool.py): ahorra ~500 ms.
    Y siempre hay un ffmpeg ya arrancado esperando la siguiente frase: arrancarlo en el momento costaba ~35 ms
    (primer PCM a 42 ms contra 7 ms, medido el 2026-11-02).
    Las frases cortas (≤ CACHE_MAX_CARACTERES) se guardan ya sintetizadas en memoria: Nix empieza muchas
    respuestas igual («Uy,», «Jaja,», «¡Obvio!») y una frase nueva tarda 300-500 ms en Microsoft, mientras que
    de la caché sale al instante. Solo en memoria; se pierde al reiniciar.
  - TTSStub: un tono suave con la duración aproximada de la frase. Para pruebas y sin internet.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import shutil
from collections import OrderedDict
from typing import AsyncIterator, Callable, Optional

import numpy as np

from server.config import ConfigServidor

log = logging.getLogger("nix.tts")

SR_SALIDA = 24000
BYTES_TROZO = SR_SALIDA * 2 // 10          # 100 ms de PCM16 mono
CACHE_MAX_CARACTERES = 50              # caben las frases fijas («Perdón, no te entendí bien, ¿me lo repites?»)
CACHE_MAX_FRASES = 300


class ProveedorTTS(abc.ABC):
    nombre: str
    sample_rate: int = SR_SALIDA

    @abc.abstractmethod
    def sintetizar(self, texto: str) -> AsyncIterator[bytes]: ...

    async def calentar(self) -> None:
        """Deja listo lo que se pueda antes del primer turno (conexiones)."""

    async def cerrar(self) -> None:
        pass


class TTSStub(ProveedorTTS):
    nombre = "stub"

    def __init__(self, s_por_caracter: float = 0.05, maximo_s: float = 4.0):
        self.s_por_caracter, self.maximo_s = s_por_caracter, maximo_s

    async def sintetizar(self, texto: str) -> AsyncIterator[bytes]:
        segundos = min(self.maximo_s, max(0.2, len(texto) * self.s_por_caracter))
        t = np.arange(int(self.sample_rate * segundos)) / self.sample_rate
        pcm = (0.05 * np.sin(2 * np.pi * 330 * t) * 32767).astype("<i2").tobytes()
        for i in range(0, len(pcm), BYTES_TROZO):
            await asyncio.sleep(0)
            yield pcm[i:i + BYTES_TROZO]


def _mp3_de_edge(voz: str, velocidad: str) -> Callable[[str], AsyncIterator[bytes]]:
    async def fuente(texto: str) -> AsyncIterator[bytes]:
        import edge_tts
        async for parte in edge_tts.Communicate(texto, voz, rate=velocidad).stream():
            if parte["type"] == "audio":
                yield parte["data"]
    return fuente


class TTSEdge(ProveedorTTS):
    nombre = "edge"

    def __init__(self, voz: str, velocidad: str = "+0%",
                 fuente_mp3: Optional[Callable[[str], AsyncIterator[bytes]]] = None, pool=None):
        self.voz, self.pool = voz, pool
        self.fuente_mp3 = fuente_mp3 or (pool.mp3 if pool is not None else _mp3_de_edge(voz, velocidad))
        self.reserva: Optional[asyncio.subprocess.Process] = None      # un ffmpeg ya arrancado
        self.reponiendo: set[asyncio.Task] = set()
        self.cache: OrderedDict[str, bytes] = OrderedDict()          # frase corta -> PCM (LRU)
        self.aciertos = 0

    async def calentar(self) -> None:
        if self.pool is not None:
            await self.pool.calentar()
        if self.reserva is None or self.reserva.returncode is not None:
            self.reserva = await self._arrancar_ffmpeg()

    async def cerrar(self) -> None:
        if self.pool is not None:
            await self.pool.cerrar()
        await asyncio.gather(*self.reponiendo, return_exceptions=True)
        reserva, self.reserva = self.reserva, None
        if reserva is not None and reserva.returncode is None:
            reserva.kill()
            await reserva.wait()

    async def _arrancar_ffmpeg(self) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-probesize", "32", "-analyzeduration", "0",
            "-fflags", "nobuffer", "-f", "mp3", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self.sample_rate), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

    async def _tomar_ffmpeg(self) -> asyncio.subprocess.Process:
        """El ffmpeg de reserva si sigue vivo (y se arranca otro para la próxima frase); si no, uno nuevo."""
        proc, self.reserva = self.reserva, None
        if proc is None or proc.returncode is not None:
            proc = await self._arrancar_ffmpeg()
        self._reponer_reserva()
        return proc

    def _reponer_reserva(self) -> None:
        async def reponer() -> None:
            nuevo = await self._arrancar_ffmpeg()
            if self.reserva is None:
                self.reserva = nuevo
            else:                                      # otra frase ya repuso la reserva
                nuevo.kill()
                await nuevo.wait()
        tarea = asyncio.get_running_loop().create_task(reponer(), name="ffmpeg-reserva")
        self.reponiendo.add(tarea)
        tarea.add_done_callback(self.reponiendo.discard)

    async def sintetizar(self, texto: str) -> AsyncIterator[bytes]:
        clave = " ".join(texto.split())
        if (pcm := self.cache.get(clave)) is not None:
            self.cache.move_to_end(clave)
            self.aciertos += 1
            for i in range(0, len(pcm), BYTES_TROZO):
                yield pcm[i:i + BYTES_TROZO]
            return
        guardar = [] if len(clave) <= CACHE_MAX_CARACTERES else None
        async for datos in self._sintetizar(texto):
            if guardar is not None:
                guardar.append(datos)
            yield datos
        if guardar:                                    # solo frases completas (si se cortó, no se llega aquí)
            self.cache[clave] = b"".join(guardar)
            while len(self.cache) > CACHE_MAX_FRASES:
                self.cache.popitem(last=False)

    async def precargar(self, frases: list[str]) -> None:
        """Sintetiza de antemano frases que se dicen a menudo, para que salgan de la caché al instante."""
        for frase in frases:
            if " ".join(frase.split()) in self.cache:
                continue
            try:
                async for _ in self.sintetizar(frase):
                    pass
            except Exception as e:  # noqa: BLE001 - sin red: ya se sintetizarán en su momento
                log.debug("🔈 no pude precargar %r: %s", frase, e)
                return
        log.info("🔈 %d frases frecuentes listas en la caché de voz", len(self.cache))

    async def _sintetizar(self, texto: str) -> AsyncIterator[bytes]:
        proc = await self._tomar_ffmpeg()

        async def alimentar() -> None:
            try:
                async for mp3 in self.fuente_mp3(texto):
                    proc.stdin.write(mp3)
                    await proc.stdin.drain()
            finally:
                proc.stdin.close()

        alimentador = asyncio.create_task(alimentar(), name="tts-mp3")
        resto = b""
        try:
            while datos := await proc.stdout.read(BYTES_TROZO):
                datos, resto = resto + datos, b""
                if len(datos) % 2:
                    datos, resto = datos[:-1], datos[-1:]
                yield datos
            await alimentador                      # propaga errores de edge-tts (red, voz inexistente...)
        finally:
            alimentador.cancel()
            await asyncio.gather(alimentador, return_exceptions=True)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()


def crear_tts(cfg: ConfigServidor, voz_personaje: str) -> ProveedorTTS:
    """TTS_PROVEEDOR=auto usa edge-tts si están el paquete y ffmpeg; si no, el simulado (con aviso)."""
    if cfg.tts_proveedor in ("auto", "edge"):
        try:
            import edge_tts  # noqa: F401
            disponible = shutil.which("ffmpeg") is not None
        except ImportError:
            disponible = False
        if disponible:
            from server.pipeline import edge_pool
            voz = cfg.tts_voz or voz_personaje
            pool = None
            if cfg.tts_conexiones > 0 and edge_pool.disponible():
                pool = edge_pool.PoolEdge(voz, cfg.tts_velocidad, tamano=cfg.tts_conexiones,
                                          max_ocio_s=cfg.tts_ocio_s, respaldo=_mp3_de_edge(voz, cfg.tts_velocidad))
            elif cfg.tts_conexiones > 0:
                log.warning("🔈 esta versión de edge-tts no permite reusar conexiones: cada frase abre una (+~500 ms)")
            return TTSEdge(voz, cfg.tts_velocidad, pool=pool)
        log.warning("🔈 edge-tts necesita el paquete edge-tts y ffmpeg; uso la voz simulada (stub)")
    else:
        log.warning("🔈 voz simulada (stub)")
    return TTSStub()
