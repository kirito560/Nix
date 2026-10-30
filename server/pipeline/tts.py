"""
Voz (TTS) detrás de una interfaz común: `sintetizar(texto)` devuelve PCM16 mono a `sample_rate` en trozos,
a medida que se genera.

  - TTSEdge: edge-tts (voces neuronales de Microsoft Edge, sin clave). Entrega MP3 de 24 kHz; un proceso
    de ffmpeg lo decodifica a PCM sobre la marcha, así el primer trozo sale sin esperar a la frase entera.
    Las conexiones con Microsoft se abren de antemano (server/pipeline/edge_pool.py): ahorra ~500 ms.
  - TTSStub: un tono suave con la duración aproximada de la frase. Para pruebas y sin internet.
"""
from __future__ import annotations

import abc
import asyncio
import logging
import shutil
from typing import AsyncIterator, Callable, Optional

import numpy as np

from server.config import ConfigServidor

log = logging.getLogger("nix.tts")

SR_SALIDA = 24000
BYTES_TROZO = SR_SALIDA * 2 // 10          # 100 ms de PCM16 mono


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

    async def calentar(self) -> None:
        if self.pool is not None:
            await self.pool.calentar()

    async def cerrar(self) -> None:
        if self.pool is not None:
            await self.pool.cerrar()

    async def sintetizar(self, texto: str) -> AsyncIterator[bytes]:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-probesize", "32", "-analyzeduration", "0",
            "-fflags", "nobuffer", "-f", "mp3", "-i", "pipe:0",
            "-f", "s16le", "-ac", "1", "-ar", str(self.sample_rate), "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

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
