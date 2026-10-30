"""
Conexiones a edge-tts abiertas de antemano.

Medido el 2026-10-30 desde el servidor: de los ~800 ms que tarda edge-tts en dar el primer audio, ~470 ms
son abrir el WebSocket con Microsoft (TLS + saludo). La síntesis en sí tarda 150-230 ms, y la misma
conexión sirve para varias frases. Por eso PoolEdge deja `tamano` conexiones abiertas y listas: cada
frase toma una, y mientras tanto se abre otra para reponerla. Microsoft cierra las conexiones inactivas
entre 15 y 45 s (con o sin ping, medido el 2026-10-30), así que las que pasan `max_ocio_s` sin usarse se
renuevan antes.

Solo se mantienen mientras hay actividad: `calentar()` (al conectarse un cliente y mientras siga
conectado) y cada frase abren una ventana de `ventana_s`; pasada, se cierran todas. Así no se abren
conexiones con un servicio gratuito cuando nadie está hablando con el personaje.

Si una conexión del pool falla antes de dar audio, se reintenta con una nueva; si eso también falla, se
usa edge_tts.Communicate tal cual (que además corrige el reloj si Microsoft lo rechaza).

Usa funciones internas de edge-tts (versión fijada en requirements-server.txt). Si cambian, `disponible()`
devuelve False y todo sigue con edge_tts.Communicate, solo que más lento.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Awaitable, Callable, Optional

log = logging.getLogger("nix.tts")


def disponible() -> bool:
    try:
        from edge_tts import communicate as c  # noqa: F401
        from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL  # noqa: F401
        from edge_tts.data_classes import TTSConfig  # noqa: F401
        from edge_tts.drm import DRM  # noqa: F401
        return all(hasattr(c, f) for f in ("connect_id", "date_to_string", "mkssml", "ssml_headers_plus_data",
                                          "escape", "get_headers_and_data", "_SSL_CTX"))
    except ImportError:
        return False


class ConexionEdge:
    """Un WebSocket con el servicio de voz de Edge, ya configurado para MP3 de 24 kHz."""

    def __init__(self, voz: str, velocidad: str):
        self.voz, self.velocidad = voz, velocidad
        self.sesion = self.ws = None
        self.ultimo_uso = time.monotonic()

    @property
    def cerrada(self) -> bool:
        return self.ws is None or self.ws.closed

    async def abrir(self) -> "ConexionEdge":
        import aiohttp
        from edge_tts import communicate as c
        from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
        from edge_tts.drm import DRM
        self.sesion = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=10))
        try:
            self.ws = await self.sesion.ws_connect(
                f"{WSS_URL}&ConnectionId={c.connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
                f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
                compress=15, headers=DRM.headers_with_muid(WSS_HEADERS), ssl=c._SSL_CTX, heartbeat=None)
            await self.ws.send_str(
                f"X-Timestamp:{c.date_to_string()}\r\nContent-Type:application/json; charset=utf-8\r\n"
                "Path:speech.config\r\n\r\n"
                '{"context":{"synthesis":{"audio":{"metadataoptions":{"sentenceBoundaryEnabled":"false",'
                '"wordBoundaryEnabled":"false"},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}\r\n')
        except BaseException:
            await self.cerrar()
            raise
        self.ultimo_uso = time.monotonic()
        return self

    async def pedir(self, texto: str) -> AsyncIterator[bytes]:
        """MP3 de una frase. Lanza ConnectionError si la conexión se cierra antes de terminar."""
        import aiohttp
        from edge_tts import communicate as c
        from edge_tts.data_classes import TTSConfig
        limpio = c.remove_incompatible_characters(texto) if hasattr(c, "remove_incompatible_characters") else texto
        config = TTSConfig(self.voz, self.velocidad, "+0%", "+0Hz", "SentenceBoundary")
        await self.ws.send_str(c.ssml_headers_plus_data(c.connect_id(), c.date_to_string(),
                                                        c.mkssml(config, c.escape(limpio))))
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                if len(msg.data) < 2:
                    continue
                cabecera = int.from_bytes(msg.data[:2], "big")
                parametros, datos = c.get_headers_and_data(msg.data, cabecera)
                if parametros.get(b"Path") == b"audio" and parametros.get(b"Content-Type") == b"audio/mpeg" and datos:
                    yield datos
            elif msg.type == aiohttp.WSMsgType.TEXT:
                if b"Path:turn.end" in msg.data.encode("utf-8"):
                    self.ultimo_uso = time.monotonic()
                    return
            else:
                break
        raise ConnectionError("edge-tts cerró la conexión a mitad de la frase")

    async def cerrar(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        if self.sesion is not None:
            await self.sesion.close()


class PoolEdge:
    def __init__(self, voz: str, velocidad: str = "+0%", tamano: int = 1, max_ocio_s: float = 12.0,
                 ventana_s: float = 300.0,
                 conectar: Optional[Callable[[], Awaitable]] = None,
                 respaldo: Optional[Callable[[str], AsyncIterator[bytes]]] = None):
        self.tamano, self.max_ocio_s, self.ventana_s = tamano, max_ocio_s, ventana_s
        self.activo_hasta = 0.0
        self.conectar = conectar or (lambda: ConexionEdge(voz, velocidad).abrir())
        self.respaldo = respaldo
        self.libres: list = []
        self.abriendo = 0
        self.tareas: set[asyncio.Task] = set()
        self.vigilante: Optional[asyncio.Task] = None

    async def mp3(self, texto: str) -> AsyncIterator[bytes]:
        self._arrancar()
        conexion = self._tomar()
        self._reponer()
        for intento in range(2):
            try:
                if conexion is None:
                    conexion = await self.conectar()
            except Exception as e:  # noqa: BLE001 - red, 403 por el reloj...
                log.debug("edge-tts: no pude abrir una conexión: %s", e)
                break
            dio_audio = devuelta = False
            try:
                async for datos in conexion.pedir(texto):
                    dio_audio = True
                    yield datos
                self._devolver(conexion)
                devuelta = True
                return
            except Exception as e:  # noqa: BLE001
                if dio_audio:
                    raise
                log.debug("edge-tts: conexión del pool sin respuesta (%s); intento %d", e, intento + 1)
            finally:
                # Frase cortada (barge-in, error o cancelación): esa conexión quedó a mitad; se cierra.
                if not devuelta:
                    self._lanzar(conexion.cerrar())
            conexion = None
        if self.respaldo is None:
            raise ConnectionError("edge-tts no responde")
        log.info("🔈 edge-tts: uso la conexión normal (sin pool) para esta frase")
        async for datos in self.respaldo(texto):
            yield datos

    def _tomar(self):
        while self.libres:
            conexion = self.libres.pop()
            if not conexion.cerrada and time.monotonic() - conexion.ultimo_uso < self.max_ocio_s:
                return conexion
            self._lanzar(conexion.cerrar())
        return None

    def _devolver(self, conexion) -> None:
        if not conexion.cerrada and len(self.libres) < self.tamano:
            self.libres.append(conexion)
        else:
            self._lanzar(conexion.cerrar())

    def _reponer(self) -> None:
        while len(self.libres) + self.abriendo < self.tamano:
            self.abriendo += 1
            self._lanzar(self._abrir_una())

    async def _abrir_una(self) -> None:
        try:
            conexion = await self.conectar()
            self._devolver(conexion)
        except Exception as e:  # noqa: BLE001 - se reintenta en la siguiente vuelta
            log.debug("edge-tts: no pude abrir una conexión de reserva: %s", e)
        finally:
            self.abriendo -= 1

    def _arrancar(self) -> None:
        self.activo_hasta = time.monotonic() + self.ventana_s
        if self.vigilante is None or self.vigilante.done():
            self.vigilante = asyncio.create_task(self._vigilar(), name="edge-pool")

    async def calentar(self) -> None:
        """Abre (o mantiene) las conexiones de reserva durante `ventana_s`."""
        self._arrancar()

    async def _vigilar(self) -> None:
        """Renueva las conexiones que llevan demasiado sin usarse y repone las que faltan; sin actividad,
        las cierra todas y termina."""
        while True:
            ahora = time.monotonic()
            if ahora > self.activo_hasta:
                for conexion in self.libres:
                    self._lanzar(conexion.cerrar())
                self.libres.clear()
                log.debug("edge-tts: sin actividad; cierro las conexiones de reserva")
                return
            for conexion in list(self.libres):
                if conexion.cerrada or ahora - conexion.ultimo_uso > self.max_ocio_s * 0.8:
                    self.libres.remove(conexion)
                    self._lanzar(conexion.cerrar())
            self._reponer()
            await asyncio.sleep(min(2.0, self.max_ocio_s / 10))

    def _lanzar(self, corrutina) -> None:
        tarea = asyncio.create_task(corrutina)
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def cerrar(self) -> None:
        if self.vigilante is not None:
            self.vigilante.cancel()
        for t in list(self.tareas):
            t.cancel()
        await asyncio.gather(*self.tareas, *(c.cerrar() for c in self.libres), return_exceptions=True)
        self.libres.clear()
