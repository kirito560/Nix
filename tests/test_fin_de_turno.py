"""Latencia: el VAD cierra el turno con 300 ms de silencio sin cortar pausas normales, la primera frase para la voz
sale con 5 palabras o en la primera coma (las demás por puntuación), y el TTS usa caché y un ffmpeg de reserva."""
import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from server.config import ConfigServidor
from server.pipeline.frases import Segmentador
from server.pipeline.tts import CACHE_MAX_CARACTERES, TTSEdge
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia, Reagrupador
from tests.utiles import silencio, tono


# ------------------------------------------------------------------ fin de turno
def cierres(pcm: bytes, silencio_ms: float = 300) -> list[tuple[str, float, float]]:
    """(tipo, momento del evento, última voz) de cada inicio/fin, con ventanas de 32 ms."""
    detector, reagrupador, salida = DetectorVoz(PuntuadorEnergia(), silencio_ms=silencio_ms), Reagrupador(), []
    for i, ventana in enumerate(reagrupador.agregar(pcm)):
        ahora = (i + 1) * 0.032
        salida += [(e.tipo, ahora, e.t_ultima_voz) for e in detector.procesar(ventana, ahora=ahora) if e.tipo != "voz"]
    return salida


def test_el_turno_se_cierra_con_300_ms_por_defecto():
    cfg = ConfigServidor(token="x" * 40)
    assert cfg.vad_silencio_ms == 300 and 0 < cfg.stt_adelanto_ms < cfg.vad_silencio_ms
    assert ConfigServidor.desde_entorno({"NIX_TOKEN": "x" * 40}).vad_silencio_ms == 300
    assert DetectorVoz(PuntuadorEnergia()).silencio_ms == 300


def test_el_fin_llega_unos_300_ms_despues_de_la_voz():
    ev = cierres(silencio(0.5) + tono(1.0) + silencio(1.0))
    assert [t for t, _, _ in ev] == ["inicio", "fin"]
    _, momento, ultima_voz = ev[1]
    assert 0.27 <= momento - ultima_voz <= 0.34                    # 300 ms (± una ventana de 32 ms)
    antes = cierres(silencio(0.5) + tono(1.0) + silencio(1.0), silencio_ms=400)[1]
    assert (antes[1] - antes[2]) - (momento - ultima_voz) >= 0.09  # 100 ms antes que con 400


@pytest.mark.parametrize("pausa_s", [0.15, 0.2, 0.25])
def test_una_pausa_normal_no_corta_la_frase(pausa_s):
    ev = cierres(tono(0.7) + silencio(pausa_s) + tono(0.7) + silencio(0.8))
    assert [t for t, _, _ in ev] == ["inicio", "fin"]


def test_una_pausa_larga_si_cierra_el_turno():
    ev = cierres(tono(0.7) + silencio(0.5) + tono(0.7) + silencio(0.8))
    assert [t for t, _, _ in ev] == ["inicio", "fin", "inicio", "fin"]


# ------------------------------------------------------------------ primera frase
def segmentar(texto: str, paso: int = 3, **kw) -> list[str]:
    s, frases = Segmentador(**kw), []
    for i in range(0, len(texto), paso):
        frases += s.agregar(texto[i:i + paso])
    return frases + s.terminar()


def test_la_primera_frase_sale_con_5_palabras_si_no_hay_coma():
    frases = segmentar("Ayer estuve jugando Minecraft con mis amigos y construimos una casa enorme. Qué buena onda.")
    assert frases[0] == "Ayer estuve jugando Minecraft con"
    assert 4 <= len(frases[0].split()) <= 6
    assert frases[1:] == ["mis amigos y construimos una casa enorme.", "Qué buena onda."]


def test_la_primera_frase_sale_en_la_coma_si_llega_antes():
    assert segmentar("Uy, eso estuvo muy cerca de verdad. Va.")[0] == "Uy,"
    assert segmentar("Depende de tu humor: si quieres gritar, terror.")[0] == "Depende de tu humor:"


def test_la_primera_frase_sale_en_el_punto_si_llega_antes():
    assert segmentar("¡GG! Eso estuvo roto, la neta. Otra vez.") == ["¡GG!", "Eso estuvo roto, la neta.", "Otra vez."]


def test_las_demas_frases_se_cortan_solo_por_puntuacion():
    frases = segmentar("Mira. Uno, dos, tres, cuatro, cinco, seis, siete y ocho palabras aquí. Y otra frase más larga "
                       "que cinco palabras sin ninguna coma en medio.")
    assert frases == ["Mira.", "Uno, dos, tres, cuatro, cinco, seis, siete y ocho palabras aquí.",
                      "Y otra frase más larga que cinco palabras sin ninguna coma en medio."]


def test_no_cuenta_una_palabra_a_medias():
    s = Segmentador()
    assert s.agregar("Ayer estuve jugando Minecraft co") == []        # «co…» puede seguir creciendo
    assert s.agregar("n mis") == ["Ayer estuve jugando Minecraft con"]


def test_palabras_primera_configurable():
    assert segmentar("uno dos tres cuatro cinco seis siete.", palabras_primera=4)[0] == "uno dos tres cuatro"


# ------------------------------------------------------------------ TTS: caché y ffmpeg de reserva
def mp3_de_prueba(segundos: float = 0.5) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={segundos}",
                           "-ar", "24000", "-ac", "1", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout


def tts_falso():
    mp3, pedidas = mp3_de_prueba(), []

    async def fuente(texto):
        pedidas.append(texto)
        for i in range(0, len(mp3), 1500):
            await asyncio.sleep(0)
            yield mp3[i:i + 1500]

    return TTSEdge("es-MX-DaliaNeural", fuente_mp3=fuente), pedidas


sin_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="falta ffmpeg")


@sin_ffmpeg
async def test_las_frases_cortas_repetidas_salen_de_la_cache():
    tts, pedidas = tts_falso()
    primera = b"".join([t async for t in tts.sintetizar("Uy,")])
    segunda = b"".join([t async for t in tts.sintetizar(" Uy, ")])
    assert primera == segunda and len(primera) > 0
    assert pedidas == ["Uy,"] and tts.aciertos == 1                  # Microsoft solo se usó una vez
    await tts.cerrar()


@sin_ffmpeg
async def test_frases_largas_o_cortadas_no_se_guardan():
    tts, pedidas = tts_falso()
    larga = "x" * (CACHE_MAX_CARACTERES + 1)
    [t async for t in tts.sintetizar(larga)]
    [t async for t in tts.sintetizar(larga)]
    async for _ in tts.sintetizar("Mira,"):                         # se corta (barge-in) tras el primer trozo
        break
    [t async for t in tts.sintetizar("Mira,")]
    assert pedidas == [larga, larga, "Mira,", "Mira,"] and tts.aciertos == 0
    await tts.cerrar()


@sin_ffmpeg
async def test_precargar_llena_la_cache():
    tts, pedidas = tts_falso()
    await tts.precargar(["¡Claro!", "Jaja,", "¡Claro!"])
    assert pedidas == ["¡Claro!", "Jaja,"]
    [t async for t in tts.sintetizar("Jaja,")]
    assert tts.aciertos == 1
    await tts.cerrar()


@sin_ffmpeg
async def test_usa_el_ffmpeg_de_reserva_y_repone_otro():
    tts, _ = tts_falso()
    await tts.calentar()
    reserva = tts.reserva
    assert reserva is not None and reserva.returncode is None
    pcm = b"".join([t async for t in tts.sintetizar("una frase nueva")])
    assert len(pcm) > 0 and reserva.returncode is not None         # la usó (y terminó)
    for _ in range(50):
        if tts.reserva is not None:
            break
        await asyncio.sleep(0.01)
    assert tts.reserva is not None and tts.reserva is not reserva   # ya hay otra lista
    nueva = tts.reserva
    await tts.cerrar()
    assert nueva.returncode is not None and tts.reserva is None     # al cerrar no queda ningún ffmpeg
