import numpy as np

from server.pipeline.vad import VENTANA, DetectorVoz, PuntuadorEnergia, Reagrupador
from tests.utiles import silencio, tono


def eventos(pcm: bytes, **kw) -> list:
    detector, reagrupador, salida = DetectorVoz(PuntuadorEnergia(), **kw), Reagrupador(), []
    for i, ventana in enumerate(reagrupador.agregar(pcm)):
        salida += [e for e in detector.procesar(ventana, ahora=i * 0.032) if e.tipo != "voz"]
    return salida


def test_reagrupador_hace_ventanas_de_512():
    r = Reagrupador()
    assert r.agregar(bytes(640)) == []                      # 320 muestras: aún no hay ventana
    ventanas = r.agregar(bytes(640 * 3))                     # 1280 muestras en total
    assert [len(v) for v in ventanas] == [VENTANA, VENTANA]
    assert all(v.dtype == np.float32 for v in ventanas)


def test_silencio_no_genera_eventos():
    assert eventos(silencio(2)) == []


def test_frase_completa():
    ev = eventos(silencio(0.5) + tono(1.0) + silencio(1.0), silencio_ms=400)
    assert [e.tipo for e in ev] == ["inicio", "fin"]
    # La última voz fue alrededor de 1.5 s; el fin se declara ~400 ms después.
    assert 1.4 < ev[1].t_ultima_voz < 1.55
    assert len(ev[1].audio) >= 16000                          # incluye toda la frase y el pre-roll


def test_ruido_corto_se_descarta():
    ev = eventos(silencio(0.5) + tono(0.1) + silencio(1.0), min_voz_ms=250)
    assert [e.tipo for e in ev] == ["inicio", "descartada"]


def test_dos_frases_separadas():
    ev = eventos(tono(0.6) + silencio(0.8) + tono(0.6) + silencio(0.8), silencio_ms=400)
    assert [e.tipo for e in ev] == ["inicio", "fin", "inicio", "fin"]
