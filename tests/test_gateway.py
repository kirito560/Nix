"""Pruebas de punta a punta del gateway: cliente real contra servidor real en 127.0.0.1 (STT, LLM y TTS stub)."""
import asyncio
import ipaddress
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from client.ws_client import ErrorAutenticacion, autenticar, ejecutar
from server.config import ConfigServidor
from server.gateway.ws_server import Gateway
from shared.protocol import CIERRE_NO_AUTORIZADO
from server.orquestador import Historial, Orquestador
from server.pipeline.escucha import Escucha
from server.pipeline.llm import LLMStub
from server.pipeline.stt import STTStub
from server.pipeline.tts import TTSStub
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia
from shared import auth
from shared.protocol import AuthChallenge, AuthOk, LlmText, PlaybackFlush, SttText
from tests.test_orquestador import NIX
from tests.utiles import silencio, tono, tramas

TOKEN = "token-de-prueba-" + "x" * 32


def gateway(redes=("127.0.0.0/8",), tts=None) -> Gateway:
    cfg = ConfigServidor(token=TOKEN, host="127.0.0.1", puerto=0,
                         redes_permitidas=tuple(ipaddress.ip_network(r) for r in redes))
    historial = Historial(10)

    def crear_escucha(enviar):
        orquestador = Orquestador(NIX, LLMStub(), tts or TTSStub(), enviar, g.registro, historial)
        return Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), STTStub(), orquestador,
                       barge_min_voz_ms=400)

    g = Gateway(cfg, crear_escucha, "Nix")
    return g


async def generar(pcm: bytes):
    for t in tramas(pcm):
        yield t
        await asyncio.sleep(0)


async def conversar(g: Gateway, pcm: bytes, tiempo_real: bool = False):
    recibidos, audio = [], []

    async def al_recibir(msg):
        recibidos.append(msg)

    async def fuente():
        for t in tramas(pcm):
            yield t
            await asyncio.sleep(0.02 if tiempo_real else 0)

    async with g.servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        await ejecutar(url, TOKEN, fuente(), al_recibir, espera_final_s=1.0,
                       al_audio=lambda turno, pts, sr, pcm: audio.append((turno, sr, len(pcm))))
    return recibidos, audio


async def test_conversacion_de_punta_a_punta():
    g = gateway()
    recibidos, audio = await conversar(g, silencio(0.3) + tono(1.2) + silencio(0.8))
    oido = [m.texto for m in recibidos if isinstance(m, SttText)]
    respuesta = " ".join(m.texto for m in recibidos if isinstance(m, LlmText) and m.texto)
    assert len(oido) == 1 and oido[0].startswith("(simulado)")
    assert respuesta.startswith("(simulado) Me dijiste") and recibidos[-1].final
    turno = next(m.turn_id for m in recibidos if isinstance(m, LlmText))
    assert audio and all(t == turno and sr == 24000 for t, sr, _ in audio)
    stats = g.registro.estadisticas()
    assert {"voz_fin→vad_fin", "vad_fin→stt_final", "tts_1er_audio→audio_enviado", "total"} <= stats.keys()


async def test_barge_in_la_interrumpe():
    """Mientras su voz (larga) se reproduce, el usuario habla más de BARGE_MIN_VOZ_MS: ella se calla."""
    g = gateway(tts=TTSStub(s_por_caracter=0.2, maximo_s=8))
    recibidos, _ = await conversar(g, tono(0.8) + silencio(0.6) + silencio(0.5) + tono(1.0) + silencio(0.6),
                                   tiempo_real=True)
    assert any(isinstance(m, PlaybackFlush) for m in recibidos)
    assert len([m for m in recibidos if isinstance(m, SttText)]) == 2


async def test_ruido_corto_mientras_habla_no_la_interrumpe():
    g = gateway(tts=TTSStub(s_por_caracter=0.2, maximo_s=8))
    recibidos, _ = await conversar(g, tono(0.8) + silencio(0.6) + silencio(0.5) + tono(0.3) + silencio(0.6),
                                   tiempo_real=True)
    assert not any(isinstance(m, PlaybackFlush) for m in recibidos)
    assert len([m for m in recibidos if isinstance(m, SttText)]) == 1


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


async def test_la_escucha_adelanta_la_transcripcion_en_las_pausas():
    """Una pausa corta a media frase adelanta y luego se descarta; la pausa final se usa."""
    from server.pipeline.stt import STTStub, _SesionStub

    usos = []

    class SesionEspia(_SesionStub):
        def adelantar(self):
            usos.append("adelantar")

        async def finalizar(self, usar_adelanto=False):
            usos.append(f"finalizar:{usar_adelanto}")
            return await super().finalizar()

    class STTEspia(STTStub):
        async def abrir(self):
            return SesionEspia(self)

    g = gateway()

    def crear_escucha(enviar):
        orquestador = Orquestador(NIX, LLMStub(), TTSStub(), enviar, g.registro, Historial(10))
        return Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=400), STTEspia(), orquestador, adelanto_ms=200)

    g.crear_escucha = crear_escucha
    await conversar(g, tono(0.8) + silencio(0.25) + tono(0.8) + silencio(0.8))
    assert usos == ["adelantar", "adelantar", "finalizar:True"]


async def test_el_cliente_se_reconecta_si_el_servidor_reinicia():
    from client.main import conversar_siempre

    class FuenteSilencio:
        async def tramas(self):
            while True:
                await asyncio.sleep(0.02)
                yield bytes(640)

    class SalidaMuda:
        def reproducir(self, sr, pcm):
            pass

        def vaciar(self):
            pass

    g = gateway()
    async with g.servir() as srv:
        puerto = srv.sockets[0].getsockname()[1]
        cliente = asyncio.create_task(conversar_siempre(f"ws://127.0.0.1:{puerto}", TOKEN, FuenteSilencio(),
                                                        SalidaMuda(), lambda *a: None))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if g.actual is not None:
                break
        assert g.actual is not None
    # Servidor apagado: el cliente sigue vivo, reintentando.
    await asyncio.sleep(0.3)
    assert not cliente.done()
    g2 = gateway()
    g2.cfg = ConfigServidor(token=TOKEN, host="127.0.0.1", puerto=puerto,
                            redes_permitidas=g.cfg.redes_permitidas)
    async with g2.servir():
        for _ in range(250):
            await asyncio.sleep(0.02)
            if g2.actual is not None:
                break
        assert g2.actual is not None, "no se reconectó"
        cliente.cancel()
        await asyncio.gather(cliente, return_exceptions=True)
