import math

import numpy as np

from server.latencia import Registro, Span, percentil


def test_percentil_igual_que_numpy():
    datos = [12.0, 5.0, 300.0, 41.0, 7.5, 88.0, 61.0]
    for p in (0, 50, 95, 100):
        assert math.isclose(percentil(datos, p), float(np.percentile(datos, p)))
    assert math.isnan(percentil([], 50))


def test_span_y_registro():
    r = Registro()
    for i in range(10):
        s = Span(f"turno{i}")
        s.marcar("voz_fin", 0.0)
        s.marcar("vad_fin", 0.4)
        s.marcar("stt_final", 0.4 + 0.01 * (i + 1))
        r.cerrar(s)
    p50, p95, n = r.estadisticas()["vad_fin→stt_final"]
    assert n == 10 and math.isclose(p50, 55.0) and math.isclose(p95, 95.5)
    assert math.isclose(r.estadisticas()["voz_fin→vad_fin"][0], 400.0)
    assert "p50" in r.resumen()
