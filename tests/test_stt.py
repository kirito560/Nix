import asyncio
import json

import httpx
import numpy as np
from websockets.asyncio.server import serve

from server.pipeline.stt import STTDeepgram, STTGroqWhisper, STTStub, crear_stt
from server.config import ConfigServidor
from tests.utiles import tono


async def test_stub_devuelve_texto_simulado():
    sesion = await STTStub().abrir()
    await sesion.enviar(tono(1.5))
    r = await sesion.finalizar()
    assert r.texto == "(simulado) dijiste algo de 1.5 segundos"


def test_crear_stt_sin_claves_usa_stub():
    cfg = ConfigServidor(token="x" * 40, stt_proveedor="deepgram")
    assert crear_stt(cfg).nombre == "stub"
    assert crear_stt(ConfigServidor(token="x" * 40, groq_api_key="k")).nombre == "groq"
    assert crear_stt(ConfigServidor(token="x" * 40, deepgram_api_key="k", groq_api_key="k")).nombre == "deepgram"


async def test_deepgram_con_servidor_falso():
    """Imita el WebSocket de Deepgram: resultado final al recibir Finalize."""
    recibido = {"bytes": 0, "auth": None, "mensajes": []}

    async def falso(ws):
        recibido["auth"] = ws.request.headers.get("Authorization")
        recibido["query"] = ws.request.path
        async for m in ws:
            if isinstance(m, bytes):
                recibido["bytes"] += len(m)
                continue
            recibido["mensajes"].append(json.loads(m)["type"])
            if json.loads(m)["type"] == "Finalize":
                await ws.send(json.dumps({"type": "Results", "is_final": True, "from_finalize": False,
                                          "channel": {"alternatives": [{"transcript": "hola Nix"}]}}))
                await ws.send(json.dumps({"type": "Results", "is_final": True, "from_finalize": True,
                                          "channel": {"alternatives": [{"transcript": "¿cómo estás?"}]}}))

    async with serve(falso, "127.0.0.1", 0) as srv:
        puerto = srv.sockets[0].getsockname()[1]
        stt = STTDeepgram("CLAVE", "nova-3", "es-419", url_base=f"ws://127.0.0.1:{puerto}/v1/listen")
        sesion = await stt.abrir()
        await sesion.enviar(tono(0.5))
        r = await sesion.finalizar()
        await asyncio.sleep(0.05)
    assert r.texto == "hola Nix ¿cómo estás?"
    assert recibido["auth"] == "Token CLAVE"
    assert "model=nova-3" in recibido["query"] and "language=es-419" in recibido["query"]
    assert recibido["bytes"] == 16000
    assert recibido["mensajes"][:2] == ["Finalize", "CloseStream"]


def _groq_falso(respuesta: dict):
    def manejar(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer CLAVE"
        return httpx.Response(200, json=respuesta)
    return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


async def test_groq_acepta_frase_valida():
    stt = STTGroqWhisper("CLAVE", "whisper-large-v3-turbo", "es",
                         cliente=_groq_falso({"text": " Hola, ¿qué tal?", "segments": [{"no_speech_prob": 0.01}]}))
    sesion = await stt.abrir()
    await sesion.enviar(tono(1.0))
    assert (await sesion.finalizar()).texto == "Hola, ¿qué tal?"


async def test_groq_descarta_alucinacion():
    stt = STTGroqWhisper("CLAVE", "m", "es", cliente=_groq_falso({"text": "Gracias por ver el video."}))
    sesion = await stt.abrir()
    await sesion.enviar(tono(1.0))
    r = await sesion.finalizar()
    assert r.texto is None and "alucinación" in r.motivo


async def test_groq_no_llama_a_la_api_sin_voz():
    stt = STTGroqWhisper("CLAVE", "m", "es", cliente=_groq_falso({"text": "no debería llegar"}))
    sesion = await stt.abrir()
    await sesion.enviar(np.zeros(16000, dtype="<i2").tobytes())
    assert (await sesion.finalizar()).texto is None


async def test_groq_usa_la_transcripcion_adelantada():
    llamadas = []

    def manejar(request: httpx.Request) -> httpx.Response:
        llamadas.append(len(request.content))
        return httpx.Response(200, json={"text": "Hola, ¿qué tal?", "segments": [{"no_speech_prob": 0.01}]})

    stt = STTGroqWhisper("CLAVE", "m", "es", cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    sesion = await stt.abrir()
    await sesion.enviar(tono(1.0))
    sesion.adelantar()
    await sesion.enviar(np.zeros(3200, dtype="<i2").tobytes())       # 200 ms más de silencio
    assert (await sesion.finalizar(usar_adelanto=True)).texto == "Hola, ¿qué tal?"
    assert len(llamadas) == 1


async def test_groq_descarta_el_adelanto_si_siguio_hablando():
    textos = iter(["Hola", "Hola, ¿qué tal?"])

    def manejar(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": next(textos), "segments": [{"no_speech_prob": 0.01}]})

    stt = STTGroqWhisper("CLAVE", "m", "es", cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    sesion = await stt.abrir()
    await sesion.enviar(tono(1.0))
    sesion.adelantar()
    await asyncio.sleep(0.01)
    await sesion.enviar(tono(1.0))
    assert (await sesion.finalizar(usar_adelanto=False)).texto == "Hola, ¿qué tal?"
