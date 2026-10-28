import pytest

from server.pipeline.filtros_whisper import es_alucinacion, validar_transcripcion


@pytest.mark.parametrize("texto", ["Gracias.", "¡Muchas gracias!", "[Música]", "Subtítulos realizados por la comunidad de Amara.org", "Mmm", ""])
def test_alucinaciones(texto):
    assert es_alucinacion(texto)


@pytest.mark.parametrize("texto", ["Gracias por ayudarme con el código", "Hola Nix, ¿jugamos?"])
def test_frases_reales(texto):
    assert not es_alucinacion(texto)


def test_validar_por_no_speech_y_por_poca_voz():
    assert validar_transcripcion("Hola", [{"no_speech_prob": 0.9}])[0] is None
    assert validar_transcripcion("Hola, ¿qué tal?", voz_s=0.1)[0] is None
    assert validar_transcripcion("Hola, ¿qué tal?", [{"no_speech_prob": 0.1}], voz_s=1.0) == ("Hola, ¿qué tal?", "")
