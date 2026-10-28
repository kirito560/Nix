"""Conexión del cliente con el servidor: autenticación mutua, envío de audio y recepción de mensajes."""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect

from client.audio_io import ahora_ms
from shared import auth
from shared.protocol import (AuthChallenge, AuthOk, AuthResponse, ErrorProtocolo, codificar_audio_in,
                             parsear_mensaje)

log = logging.getLogger("nix.cliente")


class ErrorAutenticacion(RuntimeError):
    pass


async def autenticar(ws: ClientConnection, token: str) -> AuthOk:
    reto = parsear_mensaje(await asyncio.wait_for(ws.recv(), 5))
    if not isinstance(reto, AuthChallenge):
        raise ErrorAutenticacion(f"esperaba auth.challenge y llegó {reto.type}")
    nonce_cliente = auth.nuevo_nonce()
    await ws.send(AuthResponse(mac=auth.firmar(token, "cliente", reto.nonce), nonce_cliente=nonce_cliente).a_json())
    ok = parsear_mensaje(await asyncio.wait_for(ws.recv(), 5))
    if not isinstance(ok, AuthOk) or not auth.verificar(token, "servidor", nonce_cliente, ok.mac_servidor):
        raise ErrorAutenticacion("el servidor no demostró conocer el token")
    return ok


async def ejecutar(url: str, token: str, tramas: AsyncIterator[bytes],
                   al_recibir: Callable[[object], Awaitable[None]], espera_final_s: float = 3.0) -> None:
    """Envía las tramas de audio y llama a `al_recibir` con cada mensaje del servidor.
    Cuando se acaban las tramas (fin del WAV) espera `espera_final_s` las últimas respuestas y cierra."""
    async with connect(url, open_timeout=5, max_size=2 ** 22) as ws:
        ok = await autenticar(ws, token)
        log.info("✅ conectado con %s (personaje: %s)", url, ok.personaje)

        async def recibir():
            async for trama in ws:
                if isinstance(trama, bytes):
                    continue                     # audio.out llega en la fase 2
                try:
                    await al_recibir(parsear_mensaje(trama))
                except ErrorProtocolo as e:
                    log.warning("mensaje inválido del servidor: %s", e)

        receptor = asyncio.create_task(recibir())
        try:
            async for pcm in tramas:
                await ws.send(codificar_audio_in(ahora_ms(), pcm))
            await asyncio.wait_for(asyncio.shield(receptor), espera_final_s)
        except asyncio.TimeoutError:
            pass
        finally:
            receptor.cancel()
            await asyncio.gather(receptor, return_exceptions=True)
