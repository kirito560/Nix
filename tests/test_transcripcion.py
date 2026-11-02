"""Transcripción: textos vacíos, prompt de contexto, confianza baja, fallos del STT; medidor del servidor;
espera de reconexión del cliente."""
import asyncio

import httpx
import pytest
from websockets.exceptions import ConnectionClosed

from server.gateway.ws_server import MedidorNivel
from server.orquestador import FRASE_NO_ENTENDI, FRASE_SIN_CEREBRO, Historial, Orquestador
from server.pipeline.escucha import Escucha
from server.pipeline.filtros_whisper import confianza_baja, es_texto_vacio, repite_el_prompt
from server.pipeline.llm import LLMStub
from server.pipeline.stt import STTGroqWhisper, STTStub, Transcripcion, _SesionStub
from server.pipeline.tts import TTSStub
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia
from shared.protocol import LlmText, SttText
from tests.test_gateway import conversar, gateway
from tests.test_orquestador import NIX, LLMFijo, orquestador
from tests.utiles import silencio, tono

PROMPT = "Nix, Minecraft, PvP, Roblox, Visual Studio, VTube Studio."


# ------------------------------------------------------------------ filtros
@pytest.mark.parametrize("texto", ["", "   ", ".", "a", "¿?", "¡A!", None])
def test_textos_vacios_o_de_un_caracter(texto):
    assert es_texto_vacio(texto)


@pytest.mark.parametrize("texto", ["sí", "Hola", "no, Nix"])
def test_textos_validos(texto):
    assert not es_texto_vacio(texto)


def test_repite_el_prompt():
    assert repite_el_prompt("Minecraft, PvP, Roblox.", PROMPT)
    assert repite_el_prompt(PROMPT, PROMPT)
    assert not repite_el_prompt("Juguemos Minecraft hoy", PROMPT)
    assert not repite_el_prompt("Nix", PROMPT)                   # una sola palabra sí puede ser real


def test_confianza_baja():
    assert confianza_baja([{"avg_logprob": -0.3, "start": 0, "end": 2}]) is None
    assert "confianza baja" in confianza_baja([{"avg_logprob": -1.4, "start": 0, "end": 2}])
    # Ponderada por la duración: un trozo corto malo no tumba una frase larga buena.
    assert confianza_baja([{"avg_logprob": -0.2, "start": 0, "end": 3},
                           {"avg_logprob": -2.0, "start": 3, "end": 3.3}]) is None
    assert confianza_baja([{"no_speech_prob": 0.1}]) is None     # sin dato, no se juzga
    assert confianza_baja(None) is None


# ------------------------------------------------------------------ Groq Whisper
def groq(respuesta: dict, enviado: list | None = None, **kw) -> STTGroqWhisper:
    def manejar(request: httpx.Request) -> httpx.Response:
        if enviado is not None:
            enviado.append(request.content)
        return httpx.Response(200, json=respuesta)
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(manejar))
    return STTGroqWhisper("CLAVE", "whisper-large-v3", "es", cliente=cliente, prompt=PROMPT, **kw)


async def transcribir(stt) -> Transcripcion:
    sesion = await stt.abrir()
    await sesion.enviar(tono(1.0))
    return await sesion.finalizar()


async def test_groq_manda_espanol_y_el_prompt_de_contexto():
    enviado = []
    r = await transcribir(groq({"text": "Juguemos PvP", "segments": [{"avg_logprob": -0.2}]}, enviado))
    assert r.texto == "Juguemos PvP"
    cuerpo = enviado[0].decode("utf-8", "replace")
    assert 'name="language"\r\n\r\nes\r\n' in cuerpo
    assert f'name="prompt"\r\n\r\n{PROMPT}\r\n' in cuerpo


async def test_groq_sin_prompt_no_lo_manda():
    enviado = []
    stt = groq({"text": "hola"}, enviado)
    stt.prompt = ""
    await transcribir(stt)
    assert b'name="prompt"' not in enviado[0]


async def test_groq_con_confianza_baja_pide_repetir():
    r = await transcribir(groq({"text": "pinte micropulero", "segments": [{"avg_logprob": -1.3, "start": 0,
                                                                            "end": 1}]}))
    assert r.texto is None and r.repetir and "pinte micropulero" in r.motivo


async def test_groq_umbral_de_confianza_configurable():
    r = await transcribir(groq({"text": "pinte micropulero", "segments": [{"avg_logprob": -1.3}]},
                               min_logprob=-1.5))
    assert r.texto == "pinte micropulero" and not r.repetir


async def test_groq_descarta_el_prompt_repetido():
    r = await transcribir(groq({"text": "Minecraft, PvP, Roblox", "segments": [{"avg_logprob": -0.1}]}))
    assert r.texto is None and not r.repetir and "prompt" in r.motivo


async def test_groq_descarta_un_solo_caracter():
    r = await transcribir(groq({"text": " a.", "segments": [{"avg_logprob": -0.1}]}))
    assert r.texto is None and not r.repetir


# ------------------------------------------------------------------ escucha + orquestador
def gateway_con_stt(resultado):
    """Gateway cuyo STT devuelve siempre `resultado` (una Transcripcion o una excepción)."""
    class Sesion(_SesionStub):
        async def finalizar(self, usar_adelanto=False):
            if isinstance(resultado, Exception):
                raise resultado
            return resultado

    class STT(STTStub):
        async def abrir(self):
            return Sesion(self)

    g = gateway()

    def crear_escucha(enviar):
        o = Orquestador(NIX, LLMStub(), TTSStub(), enviar, g.registro, Historial(10))
        return Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), STT(), o)

    g.crear_escucha = crear_escucha
    return g


def textos(recibidos) -> list[str]:
    return [m.texto for m in recibidos if isinstance(m, LlmText) and m.texto]


async def test_texto_vacio_no_llega_al_llm():
    recibidos, audio = await conversar(gateway_con_stt(Transcripcion(".")), tono(1.0) + silencio(0.8))
    assert not any(isinstance(m, SttText) for m in recibidos) and not textos(recibidos) and not audio


async def test_confianza_baja_nix_pide_que_lo_repitas():
    g = gateway_con_stt(Transcripcion(None, "confianza baja", repetir=True))
    recibidos, audio = await conversar(g, tono(1.0) + silencio(0.8))
    assert textos(recibidos) == [FRASE_NO_ENTENDI] and audio
    assert not any(isinstance(m, SttText) for m in recibidos)
    assert "total" not in g.registro.estadisticas()             # las frases fijas no cuentan en la latencia


async def test_si_falla_el_stt_avisa_y_sigue_escuchando():
    g = gateway_con_stt(httpx.ConnectError("Groq no responde"))
    pcm = tono(1.0) + silencio(0.8) + tono(1.0) + silencio(0.8)
    recibidos, _ = await conversar(g, pcm)
    assert textos(recibidos) == [FRASE_SIN_CEREBRO, FRASE_SIN_CEREBRO]   # avisó las dos veces


async def test_si_falla_el_llm_dice_la_frase_de_error_y_sigue():
    class Roto(LLMFijo):
        async def responder(self, mensajes):
            raise RuntimeError("Groq 503")
            yield ""

    o, enviados, _ = orquestador(Roto(""))
    from tests.test_orquestador import span
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    o.llm = LLMFijo("Ya volví.")
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000002", "¿sigues?", span())
    await o.turno.tarea
    assert textos(enviados) == [FRASE_SIN_CEREBRO, "Ya volví."]


async def test_decir_no_se_guarda_en_el_historial():
    o, enviados, _ = orquestador(LLMFijo("x"))
    await o.decir("00000000-0000-0000-0000-000000000003", FRASE_NO_ENTENDI)
    await o.turno.tarea
    assert textos(enviados) == [FRASE_NO_ENTENDI] and not o.historial.mensajes


# ------------------------------------------------------------------ medidor del servidor
def test_medidor_cada_10_s_con_rms_duracion_y_voces():
    m = MedidorNivel(ahora=0.0)
    trama = tono(0.02, amplitud=0.1)
    assert m.agregar(trama, ahora=5.0, voces=1) is None
    texto = m.agregar(trama, ahora=10.0, voces=3)
    assert "0.0 s de audio en 10 s" in texto and "RMS -23 dBFS" in texto and "voz detectada 3 veces" in texto
    assert "voz detectada 1 vez" in m.agregar(trama, ahora=20.0, voces=4)          # solo las nuevas
    assert "casi silencio" in m.agregar(bytes(640), ahora=30.0, voces=4)


# ------------------------------------------------------------------ reconexión del cliente
def test_espera_de_reconexion_crece_hasta_10_s():
    from client.main import siguiente_espera
    esperas, e = [], 1.0
    for _ in range(7):
        esperas.append(e)
        e = siguiente_espera(e)
    assert esperas == [1, 2, 4, 8, 10, 10, 10]


async def test_conversar_siempre_espera_entre_reintentos(monkeypatch):
    import client.main as cm

    intentos, esperas = [], []

    async def ejecutar_falla(*_a, **_k):
        intentos.append(1)
        if len(intentos) >= 6:
            raise asyncio.CancelledError
        raise ConnectionRefusedError("servidor apagado")

    real_sleep = asyncio.sleep

    async def dormir(segundos):
        esperas.append(segundos)
        await real_sleep(0)

    class Fuente:
        async def tramas(self):
            yield bytes(640)

    class Salida:
        def vaciar(self):
            pass

    monkeypatch.setattr(cm, "ejecutar", ejecutar_falla)
    monkeypatch.setattr(cm.asyncio, "sleep", dormir)
    with pytest.raises(asyncio.CancelledError):
        await cm.conversar_siempre("ws://x", "t", Fuente(), Salida(), None)
    assert esperas == [1, 2, 4, 8, 10]                            # nunca reintenta sin pausa


async def test_no_reintenta_si_el_token_es_incorrecto(monkeypatch):
    import client.main as cm
    from websockets.frames import Close
    from shared.protocol import CIERRE_NO_AUTORIZADO

    async def ejecutar_rechazado(*_a, **_k):
        raise ConnectionClosed(Close(CIERRE_NO_AUTORIZADO, "token incorrecto"), None)

    class Fuente:
        async def tramas(self):
            yield bytes(640)

    monkeypatch.setattr(cm, "ejecutar", ejecutar_rechazado)
    with pytest.raises(ConnectionClosed):
        await cm.conversar_siempre("ws://x", "t", Fuente(), None, None)
