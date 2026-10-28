import asyncio
from pathlib import Path

from server.latencia import Registro, Span
from server.orquestador import Historial, Orquestador
from server.personaje import Personaje
from server.pipeline.llm import ProveedorLLM
from server.pipeline.tts import TTSStub
from shared.protocol import LlmText, PlaybackFlush, SttText, decodificar_audio_out

NIX = Personaje(id="nix", nombre="Nix", voz="es-MX-DaliaNeural", ficha="Eres Nix.", datos=Path("/tmp/nix-x"))


class LLMFijo(ProveedorLLM):
    nombre = "fijo"

    def __init__(self, texto: str, pausa_s: float = 0.0):
        self.texto, self.pausa_s, self.recibido = texto, pausa_s, None

    async def responder(self, mensajes):
        self.recibido = mensajes
        for palabra in self.texto.split(" "):
            await asyncio.sleep(self.pausa_s)
            yield palabra + " "


def orquestador(llm, historial=None):
    enviados, registro = [], Registro()

    async def enviar(msg):
        enviados.append(msg)

    o = Orquestador(NIX, llm, TTSStub(), enviar, registro, historial or Historial(10))
    return o, enviados, registro


def span() -> Span:
    s = Span("t")
    s.marcar("voz_fin")
    s.marcar("vad_fin")
    s.marcar("stt_final")
    return s


async def test_turno_completo():
    llm = LLMFijo("¡GG! Eso estuvo roto, la neta. Otra vez.")
    o, enviados, registro = orquestador(llm)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola Nix", span())
    await o.turno.tarea
    assert isinstance(enviados[0], SttText) and enviados[0].texto == "hola Nix"
    textos = [m.texto for m in enviados if isinstance(m, LlmText) and m.texto]
    assert textos == ["¡GG!", "Eso estuvo roto, la neta.", "Otra vez."]
    assert isinstance(enviados[-1], LlmText) and enviados[-1].final
    audios = [decodificar_audio_out(m) for m in enviados if isinstance(m, bytes)]
    assert audios and all(sr == 24000 for _, _, sr, _ in audios)
    assert [pts for _, pts, _, _ in audios] == sorted(pts for _, pts, _, _ in audios)
    # Cada frase va antes que su audio.
    i_frase = enviados.index(next(m for m in enviados if isinstance(m, LlmText)))
    assert isinstance(enviados[i_frase + 1], bytes)
    etapas = registro.estadisticas().keys()
    assert {"stt_final→llm_1er_token", "frase_1→tts_1er_audio", "total", "tras_vad"} <= etapas
    assert llm.recibido[0]["role"] == "system" and "Eres Nix." in llm.recibido[0]["content"]
    assert o.hablando                         # el cliente aún reproduce lo enviado


async def test_historial_entre_turnos():
    historial = Historial(10)
    o, _, _ = orquestador(LLMFijo("Hola, Kirito."), historial)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    llm2 = LLMFijo("Sí.")
    o2, _, _ = orquestador(llm2, historial)
    await o2.al_texto_usuario("00000000-0000-0000-0000-000000000002", "¿te acuerdas?", span())
    await o2.turno.tarea
    roles = [(m["role"], m["content"]) for m in llm2.recibido[1:]]
    assert roles == [("user", "hola"), ("assistant", "Hola, Kirito."), ("user", "¿te acuerdas?")]


async def test_interrumpir_corta_y_manda_flush():
    o, enviados, _ = orquestador(LLMFijo("Una. Dos. Tres. Cuatro. Cinco. Seis.", pausa_s=0.05))
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "cuéntame", span())
    await asyncio.sleep(0.12)
    await o.interrumpir("prueba")
    assert o.turno.tarea.done() and not o.hablando
    assert isinstance(enviados[-1], PlaybackFlush)
    dichas = [m.texto for m in enviados if isinstance(m, LlmText) and m.texto]
    assert 0 < len(dichas) < 6
    assert o.historial.mensajes[-1]["content"].endswith("—")


async def test_si_falla_el_llm_no_se_cae():
    class Roto(ProveedorLLM):
        nombre = "roto"

        async def responder(self, mensajes):
            raise RuntimeError("sin red")
            yield ""

    o, enviados, _ = orquestador(Roto())
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    assert isinstance(enviados[-1], LlmText) and enviados[-1].final
    assert not any(isinstance(m, bytes) for m in enviados)
