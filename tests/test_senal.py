"""Señal del micrófono en el cliente: pasa-altas, normalización, guardián del eco y remuestreo."""
import logging

import numpy as np

from client.audio_io import Remuestreador, Tramador
from client.senal import Acondicionador, GuardaEco, Normalizador, PasaAltas
from shared.protocol import BYTES_TRAMA

SR = 16000
N = 320


def seno(hz: float, segundos: float, amplitud: float = 0.1, sr: int = SR) -> np.ndarray:
    return amplitud * np.sin(2 * np.pi * hz * np.arange(int(sr * segundos)) / sr)


def por_tramas(proceso, x: np.ndarray) -> np.ndarray:
    return np.concatenate([proceso(x[i:i + N]) for i in range(0, len(x) - N + 1, N)])


def dbfs(x: np.ndarray) -> float:
    return 10 * np.log10(np.mean(x ** 2) + 1e-12)


# ------------------------------------------------------------------ pasa-altas
def test_pasa_altas_quita_graves_y_continua_y_deja_la_voz():
    def pasar(x):
        return por_tramas(PasaAltas().procesar, x)[SR // 2:]      # sin el transitorio inicial

    assert abs(dbfs(pasar(seno(1000, 2))) - dbfs(seno(1000, 2))) < 0.2       # la voz pasa igual
    assert dbfs(seno(30, 2)) - dbfs(pasar(seno(30, 2))) > 15                  # zumbido / viento
    assert dbfs(seno(50, 2)) - dbfs(pasar(seno(50, 2))) > 7                   # zumbido de la corriente
    assert abs(np.mean(pasar(seno(300, 2) + 0.2))) < 1e-3                     # sin componente continua


def test_pasa_altas_por_tramas_igual_que_de_una_vez():
    x = seno(60, 1) + seno(700, 1)
    assert np.allclose(por_tramas(PasaAltas().procesar, x), PasaAltas().procesar(x))


# ------------------------------------------------------------------ normalización
def normalizar(x: np.ndarray, n: Normalizador | None = None) -> tuple[np.ndarray, Normalizador]:
    n = n or Normalizador()
    return por_tramas(n.procesar, x), n


def voz(segundos: float, nivel_dbfs: float) -> np.ndarray:
    """Tono con sílabas (pausas), escalado para que su RMS sea `nivel_dbfs`."""
    t = np.arange(int(SR * segundos)) / SR
    x = np.sin(2 * np.pi * 220 * t) * np.clip(np.sin(2 * np.pi * 3 * t), 0.2, None)
    return x * 10 ** (nivel_dbfs / 20) / np.sqrt(np.mean(x ** 2))


def test_normalizador_lleva_la_voz_al_objetivo():
    salida, _ = normalizar(voz(5, -30))
    assert abs(dbfs(salida[3 * SR:]) - (-23)) < 1.5
    salida, _ = normalizar(voz(5, -16))
    assert abs(dbfs(salida[3 * SR:]) - (-23)) < 1.5


def test_normalizador_no_sube_mas_que_el_tope():
    salida, n = normalizar(voz(5, -45))                       # haría falta +22 dB; el tope es +12
    assert 20 * np.log10(n.ganancia) <= 12.01
    assert abs(dbfs(salida[3 * SR:]) - (-45 + 12)) < 1.5


def test_normalizador_no_amplifica_el_ruido():
    rng = np.random.default_rng(1)
    ruido = 10 ** (-65 / 20) * rng.standard_normal(5 * SR)
    salida, n = normalizar(ruido)
    assert n.ganancia == 1.0 and np.allclose(salida, ruido[:len(salida)])


def test_normalizador_nunca_satura_y_cambia_sin_saltos():
    x = np.concatenate([voz(2, -40), voz(1, -3)])             # de muy bajo a casi saturado de golpe
    salida, _ = normalizar(x)
    assert np.max(np.abs(salida)) <= 0.9 + 1e-9
    # La ganancia aplicada (salida / entrada, donde el limitador no actúa) cambia de forma continua: ningún
    # escalón entre muestras seguidas, tampoco en los bordes de las tramas.
    x = x[:len(salida)]
    validas = (np.abs(x) > 0.02) & (np.abs(salida) < 0.7)
    seguidas = validas[:-1] & validas[1:]
    ganancia = np.divide(salida, x, out=np.ones_like(x), where=np.abs(x) > 0.02)
    assert np.max(np.abs(np.diff(ganancia))[seguidas] / ganancia[:-1][seguidas]) < 0.01


def test_acondicionador_quita_graves_y_normaliza():
    x = voz(5, -32) + seno(30, 5, amplitud=0.02)

    def graves_sobre_voz(senal):
        espectro = np.abs(np.fft.rfft(senal))
        frecuencias = np.fft.rfftfreq(len(senal), 1 / SR)
        return (espectro[(frecuencias > 25) & (frecuencias < 35)].max()
                / espectro[(frecuencias > 215) & (frecuencias < 225)].max())

    salida = por_tramas(Acondicionador().procesar, x)[3 * SR:]
    mejora_db = 20 * np.log10(graves_sobre_voz(x[3 * SR:len(salida) + 3 * SR]) / graves_sobre_voz(salida))
    assert mejora_db > 12                                     # los 30 Hz bajan ~17 dB respecto a la voz
    assert abs(dbfs(salida) - (-23)) < 2


# ------------------------------------------------------------------ guardián del eco
class AECFalso:
    def __init__(self):
        self.reinicios = 0

    def reiniciar(self):
        self.reinicios += 1


def guardar(guarda: GuardaEco, mic: np.ndarray, limpio: np.ndarray, aec=None, adaptando=True) -> np.ndarray:
    return np.concatenate([guarda.procesar(mic[i:i + N], limpio[i:i + N], aec, adaptando)
                           for i in range(0, len(mic) - N + 1, N)])


def test_guarda_eco_limita_la_salida_a_3_db():
    mic = seno(500, 2)
    salida = guardar(GuardaEco(), mic, 3 * mic)               # el cancelador la sube ~9,5 dB
    assert abs(dbfs(salida[SR:]) - dbfs(mic) - 3.0) < 0.5


def test_guarda_eco_reinicia_el_filtro_desajustado():
    mic, aec = seno(500, 2), AECFalso()
    guardar(GuardaEco(), mic, 3 * mic, aec)
    assert aec.reinicios >= 1
    aec2 = AECFalso()
    guardar(GuardaEco(), mic, 3 * mic, aec2, adaptando=False)  # sin su voz sonando no se reinicia
    assert aec2.reinicios == 0


def test_guarda_eco_respeta_una_buena_cancelacion():
    mic = seno(500, 2)
    salida = guardar(GuardaEco(), mic, 0.1 * mic)             # -20 dB: el cancelador quitó el eco
    assert abs(dbfs(salida[SR:]) - dbfs(0.1 * mic)) < 0.5


def test_guarda_eco_con_solo_ruido_deja_el_microfono():
    rng = np.random.default_rng(2)
    mic = 10 ** (-70 / 20) * rng.standard_normal(3 * SR)
    guarda = GuardaEco()
    salida = guardar(guarda, mic, rng.standard_normal(3 * SR) * 0.001)
    assert guarda.mezcla < 1e-3                                 # ya solo pasa el micrófono
    resto = salida[2 * SR:] - mic[2 * SR:len(salida)]
    assert np.sqrt(np.mean(resto ** 2)) < 0.01 * np.sqrt(np.mean(mic ** 2))


def test_guarda_eco_cambia_sin_cortes():
    mic = seno(500, 2)
    limpio = np.concatenate([0.1 * mic[:SR], 3 * mic[SR:]])  # el filtro se desajusta de golpe
    salida = guardar(GuardaEco(), mic, limpio)
    # Ningún salto entre muestras mayor que la pendiente máxima de lo que entra (un seno de 0,3 a 500 Hz):
    # sin escalones de ganancia en los bordes de las tramas.
    assert np.max(np.abs(np.diff(salida))) <= 0.3 * 2 * np.pi * 500 / SR * 1.01


def test_guarda_eco_informa_cada_5_segundos(caplog):
    caplog.set_level(logging.INFO, logger="nix.audio")
    mic = seno(500, 12)
    guarda = GuardaEco()
    guardar(guarda, mic, 0.1 * mic)
    assert len(guarda.atenuaciones_db) == 2 and all(abs(a - 20) < 1 for a in guarda.atenuaciones_db)
    assert caplog.text.count("eco atenuado") == 2


# ------------------------------------------------------------------ remuestreo y formato al servidor
def test_remuestreo_de_48_y_22_khz_a_16_khz():
    for sr in (48000, 22050):
        x = seno(440, 1, sr=sr)
        r = Remuestreador(sr, SR)
        bloque = sr // 50
        y = np.concatenate([r.procesar(x[i:i + bloque]) for i in range(0, len(x), bloque)])
        assert abs(len(y) - SR) <= 2
        ref = np.sin(2 * np.pi * 440 * (np.arange(len(y)) / SR - 31 / sr))   # retraso del paso bajo
        assert np.corrcoef(y[1000:], ref[1000:])[0, 1] > 0.999


def test_remuestreo_quita_lo_que_no_cabe_en_16_khz():
    x = seno(12000, 1, sr=48000)                               # 12 kHz no existe a 16 kHz: no debe colarse
    y = Remuestreador(48000, SR).procesar(x)
    assert dbfs(y[200:]) < dbfs(x) - 30


def test_al_servidor_van_tramas_pcm16_mono_16k_acondicionadas():
    tr = Tramador(Acondicionador())
    tramas = tr.agregar(voz(1, -30)) + tr.agregar(voz(1, -30))
    assert len(tramas) == 100 and all(isinstance(t, bytes) and len(t) == BYTES_TRAMA for t in tramas)
