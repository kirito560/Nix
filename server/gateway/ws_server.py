"""
Gateway WebSocket del servidor.

Seguridad:
  - Escucha solo en NIX_HOST (la IP de la red local, nunca 0.0.0.0 por defecto).
  - Rechaza conexiones de IPs fuera de NIX_REDES_PERMITIDAS (por defecto 127.0.0.0/8 y 192.168.1.0/24).
  - Exige autenticación mutua por HMAC (shared/auth.py) antes de aceptar audio: el token nunca viaja.
  - Solo un cliente a la vez: una conexión nueva y autenticada reemplaza a la anterior.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Awaitable, Callable, Optional

import numpy as np

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from server.config import ConfigServidor
from server.latencia import Registro
from server.pipeline.escucha import Escucha
from server.sesion import EstadoSesion
from shared import auth
from shared.protocol import (CIERRE_NO_AUTORIZADO, CIERRE_REEMPLAZADO, AuthChallenge, AuthOk, AuthResponse,
                             ErrorProtocolo, PlaybackAck, PresenciaUpdate, UserState, VisionDelta, VisionFrame,
                             decodificar_audio_in, parsear_mensaje)

log = logging.getLogger("nix.gateway")

TIEMPO_AUTH_S = 5.0
CADA_S_NIVEL = 10.0


class MedidorNivel:
    """Cada `cada_s` resume lo que llegó del micrófono del cliente: segundos de audio, volumen RMS y pico en
    dBFS, y cuántas veces detectó voz el VAD. Por debajo de ~-50 dBFS de pico es prácticamente silencio
    (micrófono equivocado o silenciado)."""

    def __init__(self, cada_s: float = CADA_S_NIVEL, ahora: float | None = None):
        self.cada_s = cada_s
        self.voces_al_inicio = 0
        self._reiniciar(time.monotonic() if ahora is None else ahora)

    def _reiniciar(self, ahora: float) -> None:
        self.desde, self.muestras, self.energia, self.pico = ahora, 0, 0.0, 0

    def agregar(self, pcm16: bytes, ahora: float | None = None, voces: int = 0) -> Optional[str]:
        """`voces`: el total de voces detectadas hasta ahora (se resta el del inicio de la ventana)."""
        ahora = time.monotonic() if ahora is None else ahora
        x = np.frombuffer(pcm16, "<i2")
        if len(x):
            self.muestras += len(x)
            self.energia += float(np.dot(x.astype(np.float64), x.astype(np.float64)))
            self.pico = max(self.pico, int(np.max(np.abs(x.astype(np.int32)))))
        if ahora - self.desde < self.cada_s:
            return None
        segundos = self.muestras / 16000
        rms = np.sqrt(self.energia / max(self.muestras, 1)) / 32768
        db = 20 * np.log10(rms + 1e-9)
        pico_db = 20 * np.log10(self.pico / 32768 + 1e-9)
        nuevas = voces - self.voces_al_inicio
        self.voces_al_inicio = voces
        aviso = "  ⚠️ casi silencio: ¿micrófono equivocado o silenciado?" if pico_db < -50 else ""
        texto = (f"🎚️ micrófono: {segundos:.1f} s de audio en {ahora - self.desde:.0f} s | RMS {db:.0f} dBFS, "
                 f"pico {pico_db:.0f} dBFS | voz detectada {nuevas} {'vez' if nuevas == 1 else 'veces'}{aviso}")
        self._reiniciar(ahora)
        return texto


class Gateway:
    def __init__(self, cfg: ConfigServidor, crear_escucha: Callable[[Callable], Escucha], nombre_personaje: str,
                 al_conectar: Optional[Callable[[], Awaitable[None]]] = None, vista=None):
        self.cfg, self.crear_escucha, self.nombre_personaje = cfg, crear_escucha, nombre_personaje
        self.al_conectar = al_conectar
        self.vista = vista                       # server.vision.Vista: recibe lo que el cliente ve de la pantalla
        self.sesion = EstadoSesion()             # estado de la conexión actual (presencia...); se olvida al cerrar
        self.registro = Registro()
        self.actual: ServerConnection | None = None

    def servir(self):
        """Devuelve el servidor de websockets (usar con `async with`)."""
        return serve(self._atender, self.cfg.host, self.cfg.puerto, max_size=2 ** 20)

    async def _atender(self, ws: ServerConnection) -> None:
        ip = ws.remote_address[0] if ws.remote_address else ""
        if not self.cfg.ip_permitida(ip):
            log.warning("🚫 conexión rechazada desde %s (fuera de la red permitida)", ip)
            await ws.close(CIERRE_NO_AUTORIZADO, "red no permitida")
            return
        if not await self._autenticar(ws, ip):
            return
        if self.actual is not None:
            log.info("🔁 nueva conexión; cierro la anterior")
            await self.actual.close(CIERRE_REEMPLAZADO, "reemplazada")
        self.actual = ws
        self.sesion = EstadoSesion()
        if self.al_conectar is not None:
            asyncio.create_task(self.al_conectar(), name="al-conectar")

        async def enviar(msg) -> None:
            """Un mensaje del protocolo o una trama binaria de audio.out."""
            await ws.send(msg if isinstance(msg, bytes) else msg.a_json())

        escucha = self.crear_escucha(enviar)
        medidor = MedidorNivel()
        try:
            async for trama in ws:
                if isinstance(trama, bytes):
                    try:
                        _, pcm = decodificar_audio_in(trama)
                    except ErrorProtocolo as e:
                        log.warning("audio.in inválido: %s", e)
                        continue
                    if (resumen := medidor.agregar(pcm, voces=escucha.voces_detectadas)) is not None:
                        log.info(resumen)
                    await escucha.recibir_audio(pcm)
                else:
                    self._mensaje(trama)
        except ConnectionClosed:
            pass
        finally:
            await escucha.cerrar()
            if self.actual is ws:
                self.actual = None
                self.sesion = EstadoSesion()
            log.info("🔌 cliente %s desconectado", ip)

    async def _autenticar(self, ws: ServerConnection, ip: str) -> bool:
        nonce = auth.nuevo_nonce()
        try:
            await ws.send(AuthChallenge(nonce=nonce).a_json())
            respuesta = parsear_mensaje(await asyncio.wait_for(ws.recv(), TIEMPO_AUTH_S))
        except (asyncio.TimeoutError, ErrorProtocolo, ConnectionClosed) as e:
            log.warning("🚫 %s no completó la autenticación: %s", ip, type(e).__name__)
            await ws.close(CIERRE_NO_AUTORIZADO, "autenticación requerida")
            return False
        if not isinstance(respuesta, AuthResponse) or not auth.verificar(self.cfg.token, "cliente", nonce,
                                                                         respuesta.mac):
            log.warning("🚫 %s falló la autenticación (token incorrecto)", ip)
            await ws.close(CIERRE_NO_AUTORIZADO, "token incorrecto")
            return False
        await ws.send(AuthOk(mac_servidor=auth.firmar(self.cfg.token, "servidor", respuesta.nonce_cliente),
                             sesion=str(uuid.uuid4()), personaje=self.nombre_personaje).a_json())
        log.info("✅ cliente %s autenticado", ip)
        return True

    def _mensaje(self, texto: str) -> None:
        try:
            msg = parsear_mensaje(texto)
        except ErrorProtocolo as e:
            log.warning("mensaje inválido del cliente: %s", e)
            return
        if isinstance(msg, PresenciaUpdate):
            self.sesion.actualizar_presencia(msg)
        elif isinstance(msg, (VisionDelta, VisionFrame)) and not self.cfg.vision_habilitada:
            log.debug("visión apagada (VISION_HABILITADA=0); ignoro %s", msg.type)
        elif isinstance(msg, (VisionDelta, VisionFrame)) and self.vista is not None:
            self.vista.recibir(msg)
        elif isinstance(msg, (VisionDelta, VisionFrame, UserState, PlaybackAck)):
            log.debug("recibido %s (se usará en fases posteriores)", msg.type)
        else:
            log.warning("el cliente no debe enviar %s", json.loads(texto).get("type"))
