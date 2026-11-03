"""Emoción por etiqueta: se quita del texto, cambia la prosodia de Dalia y llega al avatar del cliente."""
import shutil
import sys
import types

import pytest

from server.pipeline.emocion import EMOCIONES, ExtractorEmocion, Prosodia, prosodia
from shared.protocol import AvatarCmd, LlmText
from tests.test_orquestador import LLMFijo, orquestador, span


def extraer(*trozos: str) -> tuple[str, str, bool]:
    e = ExtractorEmocion()
    texto = "".join(e.agregar(t) for t in trozos) + e.terminar()
    return texto, e.decidida, e.etiquetada


# ---------------------------------------------------------------- extractor
def test_la_etiqueta_se_quita_y_decide_la_emocion():
    assert extraer("[burla] ¿Otra vez compilando a ciegas?") == ("¿Otra vez compilando a ciegas?", "burla", True)


def test_la_etiqueta_partida_en_varios_trozos():
    assert extraer("[", "sorpren", "dida]", " ¡No ", "manches!") == ("¡No manches!", "sorprendida", True)


@pytest.mark.parametrize("emocion", EMOCIONES)
def test_todas_las_emociones(emocion):
    assert extraer(f"[{emocion}] Hola.")[1:] == (emocion, True)


def test_mayusculas_y_espacios_dentro_de_la_etiqueta():
    assert extraer("[ Animada ]¡Vamos!") == ("¡Vamos!", "animada", True)


def test_sin_etiqueta_es_neutral_y_el_texto_no_cambia():
    assert extraer("Va, ", "yo te ayudo.") == ("Va, yo te ayudo.", "neutral", False)


def test_acotaciones_a_media_respuesta_no_se_leen():
    texto, emocion, _ = extraer("[animada] Jaja [risas] ¡eso ", "[burla]", " estuvo roto!")
    assert texto == "Jaja  ¡eso  estuvo roto!" and emocion == "animada"     # solo cuenta la primera


def test_una_etiqueta_que_llega_tarde_no_cambia_la_emocion():
    assert extraer("Hola, ", "[burla] qué tal.") == ("Hola,  qué tal.", "neutral", False)


def test_corchetes_que_no_son_etiqueta_se_quedan():
    assert extraer("El índice [0] y la lista [1, 2].")[0] == "El índice [0] y la lista [1, 2]."
    assert extraer("Un [ que nunca cierra y sigue y sigue.")[0] == "Un [ que nunca cierra y sigue y sigue."
    assert extraer("Termina en [")[0] == "Termina en ["


def test_la_etiqueta_desconocida_se_quita_pero_es_neutral():
    assert extraer("[feliz] ¡Hola!") == ("¡Hola!", "neutral", False)


# ---------------------------------------------------------------- prosodia
def test_prosodia_de_dalia():
    assert prosodia("burla").edge() == ("+8%", "+0%", "+4Hz")
    assert prosodia("cansada").edge() == ("-10%", "-8%", "-6Hz")
    assert prosodia("neutral").edge() == ("+0%", "+0%", "+0Hz")
    assert prosodia("inventada") == Prosodia()


def test_la_prosodia_suma_la_velocidad_base():
    assert prosodia("burla").edge("+10%")[0] == "+18%"
    assert prosodia("cansada").edge("+5%")[0] == "-5%"
    assert prosodia("neutral").edge("raro")[0] == "+0%"


async def test_edge_tts_recibe_rate_pitch_y_volume(monkeypatch):
    from server.pipeline.tts import _mp3_de_edge
    recibido = {}

    class Communicate:
        def __init__(self, texto, voz, **kw):
            recibido.update(kw, texto=texto, voz=voz)

        async def stream(self):
            yield {"type": "audio", "data": b"mp3"}

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Communicate))
    datos = [d async for d in _mp3_de_edge("es-MX-DaliaNeural", "+0%")("hola", "burla")]
    assert datos == [b"mp3"]
    assert recibido == {"texto": "hola", "voz": "es-MX-DaliaNeural", "rate": "+8%", "volume": "+0%", "pitch": "+4Hz"}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="falta ffmpeg")
async def test_la_cache_de_voz_separa_las_emociones():
    from tests.test_fin_de_turno import tts_falso
    tts, pedidas = tts_falso()
    [t async for t in tts.sintetizar("Uy,", "burla")]
    [t async for t in tts.sintetizar("Uy,", "cansada")]
    [t async for t in tts.sintetizar("Uy,", "burla")]
    assert pedidas == ["Uy,", "Uy,"] and tts.aciertos == 1
    await tts.precargar(["Va,"], ("neutral", "animada"))
    assert ("animada", "Va,") in tts.cache and ("neutral", "Va,") in tts.cache
    await tts.cerrar()


# ---------------------------------------------------------------- orquestador
class TTSQueAnota:
    nombre, sample_rate = "anota", 24000

    def __init__(self):
        self.pedidas = []

    async def sintetizar(self, texto, emocion="neutral"):
        self.pedidas.append((emocion, texto))
        yield b"\x00\x00" * 240


async def test_el_turno_usa_la_emocion_y_la_etiqueta_no_se_dice():
    o, enviados, _ = orquestador(LLMFijo("[burla] ¿Otra vez compilando a ciegas? Lee el error."))
    o.tts = TTSQueAnota()
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "ya compila", span())
    await o.turno.tarea
    dicho = " ".join(m.texto for m in enviados if isinstance(m, LlmText) and m.texto)
    assert "[" not in dicho and dicho.startswith("¿Otra vez")
    assert {e for e, _ in o.tts.pedidas} == {"burla"}
    avatar = [m for m in enviados if isinstance(m, AvatarCmd)]
    assert len(avatar) == 1 and avatar[0].expression == "burla"
    assert enviados.index(avatar[0]) < next(i for i, m in enumerate(enviados) if isinstance(m, LlmText))
    # En el historial va con su etiqueta, para que el LLM siga el formato.
    assert o.historial.mensajes[-1]["content"].startswith("[burla] ¿Otra vez")


async def test_sin_etiqueta_la_voz_es_neutral_y_el_historial_queda_igual():
    o, enviados, _ = orquestador(LLMFijo("Va, yo te ayudo."))
    o.tts = TTSQueAnota()
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    assert {e for e, _ in o.tts.pedidas} == {"neutral"}
    assert o.historial.mensajes[-1]["content"] == "Va, yo te ayudo."


def test_el_prompt_pide_la_etiqueta_y_brevedad():
    from server.orquestador import INSTRUCCIONES_VOZ
    for emocion in EMOCIONES:
        assert f"[{emocion}]" in INSTRUCCIONES_VOZ
    assert "25 palabras" in INSTRUCCIONES_VOZ and "markdown" in INSTRUCCIONES_VOZ
