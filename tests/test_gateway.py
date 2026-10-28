"""Pruebas de punta a punta del gateway: cliente real contra servidor real en 127.0.0.1 (STT stub)."""
import asyncio
import ipaddress
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from client.ws_client import ErrorAutenticacion, autenticar, ejecutar
from server.config import ConfigServidor
from server.gateway.ws_server import CIERRE_NO_AUTORIZADO, Gateway
from server.pipeline.escucha import Escucha
from server.pipeline.stt import STTStub
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia
from shared import auth
from shared.protocol import AuthChallenge, AuthOk, SttText
from tests.utiles import silencio, tono, tramas

TOKEN = "token-de-prueba-" + "x" * 32


def gateway(redes=("127.0.0.0/8",)) -> Gateway:
    cfg = ConfigServidor(token=TOKEN, host="127.0.0.1", puerto=0,
                         redes_permitidas=tuple(ipaddress.ip_network(r) for r in redes))

    def crear_escucha(enviar):
        return Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), STTStub(), enviar, g.registro)

    g = Gateway(cfg, crear_escucha, "Nix")
    return g


async def generar(pcm: bytes):
    for t in tramas(pcm):
        yield t
        await asyncio.sleep(0)


async def test_eco_de_punta_a_punta():
    g, recibidos = gateway(), []

    async def al_recibir(msg):
        recibidos.append(msg)

    async with g.servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        await ejecutar(url, TOKEN, generar(silencio(0.3) + tono(1.2) + silencio(0.8) + tono(0.8) + silencio(0.8)),
                       al_recibir, espera_final_s=1.0)
    textos = [m.texto for m in recibidos if isinstance(m, SttText)]
    assert len(textos) == 2 and all(t.startswith("(simulado)") for t in textos)
    stats = g.registro.estadisticas()
    assert stats["total"][2] == 2 and {"voz_fin→vad_fin", "vad_fin→stt_final", "stt_final→eco"} <= stats.keys()


async def test_token_incorrecto_cierra_la_conexion():
    async with gateway().servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        async with connect(url) as ws:
            with pytest.raises(ConnectionClosed) as e:
                await autenticar(ws, "otro-token-" + "y" * 32)
    assert e.value.rcvd.code == CIERRE_NO_AUTORIZADO


async def test_sin_autenticar_no_se_acepta_audio():
    async with gateway().servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        async with connect(url) as ws:
            json.loads(await ws.recv())                      # auth.challenge
            await ws.send(b"\x00" * 648)                     # audio sin autenticar
            with pytest.raises(ConnectionClosed) as e:
                await ws.recv()
    assert e.value.rcvd.code == CIERRE_NO_AUTORIZADO


async def test_ip_fuera_de_la_red_permitida():
    async with gateway(redes=("10.0.0.0/8",)).servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        async with connect(url) as ws:
            with pytest.raises(ConnectionClosed) as e:
                await ws.recv()
    assert e.value.rcvd.code == CIERRE_NO_AUTORIZADO


async def test_el_cliente_rechaza_un_servidor_falso():
    """Un servidor que no conoce el token no puede firmar el nonce del cliente."""
    async def impostor(ws):
        await ws.send(AuthChallenge(nonce=auth.nuevo_nonce()).a_json())
        await ws.recv()
        await ws.send(AuthOk(mac_servidor="0" * 64, sesion="s", personaje="Nix").a_json())
        await ws.wait_closed()

    async with serve(impostor, "127.0.0.1", 0) as srv:
        async with connect(f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}") as ws:
            with pytest.raises(ErrorAutenticacion):
                await autenticar(ws, TOKEN)


def test_redes_permitidas_por_defecto():
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": TOKEN})
    assert cfg.ip_permitida("192.168.1.80") and cfg.ip_permitida("127.0.0.1")
    assert not cfg.ip_permitida("192.168.2.10") and not cfg.ip_permitida("8.8.8.8")
    assert cfg.ip_permitida("::ffff:192.168.1.80") and not cfg.ip_permitida("basura")
    assert cfg.vision_habilitada is False
