"""AEC con eco simulado: la voz del altavoz vuelve al micrófono con retraso, atenuada y con reverberación."""
import numpy as np

from client.aec import CanceladorEco, Decimador3, Interpolador2, Retardo

SR = 16000
N = 320


def voz_falsa(segundos: float, semilla: int) -> np.ndarray:
    """Ruido con envolvente de sílabas: espectro y pausas parecidos a la voz."""
    rng = np.random.default_rng(semilla)
    t = np.arange(int(SR * segundos)) / SR
    ruido = np.convolve(rng.standard_normal(len(t)), np.ones(4) / 4, mode="same")
    envolvente = np.clip(np.sin(2 * np.pi * 3.1 * t + semilla), 0, None) ** 2
    return (0.3 * ruido * envolvente).astype(np.float64)


def camino_acustico(retraso_ms: float = 40, ganancia: float = 0.5, semilla: int = 7) -> np.ndarray:
    rng = np.random.default_rng(semilla)
    largo = int(SR * 0.08)                                   # 80 ms de reverberación
    h = rng.standard_normal(largo) * np.exp(-np.arange(largo) / (SR * 0.015))
    h = ganancia * h / np.sqrt(np.sum(h ** 2))
    return np.concatenate([np.zeros(int(SR * retraso_ms / 1000)), h])


def procesar(aec: CanceladorEco, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
    bloques = len(mic) // N
    return np.concatenate([aec.procesar(mic[i * N:(i + 1) * N], ref[i * N:(i + 1) * N]) for i in range(bloques)])


def db(x: np.ndarray) -> float:
    return 10 * np.log10(np.mean(x ** 2) + 1e-12)


def test_cancela_el_eco_cuando_solo_habla_ella():
    ref = voz_falsa(8, 1)
    eco = np.convolve(ref, camino_acustico())[:len(ref)]
    salida = procesar(CanceladorEco(), eco, ref)
    final = slice(len(salida) - 3 * SR, len(salida))          # ya convergido
    erle = db(eco[final]) - db(salida[final])
    assert erle > 20, f"atenuación del eco: {erle:.1f} dB"


def test_respeta_la_voz_del_usuario_mientras_ella_habla():
    ref = voz_falsa(10, 1)
    eco = np.convolve(ref, camino_acustico())[:len(ref)]
    usuario = np.zeros_like(ref)
    usuario[6 * SR:] = 1.5 * voz_falsa(4, 99)                 # habla encima a partir del segundo 6
    salida = procesar(CanceladorEco(), eco + usuario, ref)
    tramo = slice(7 * SR, 10 * SR)
    correlacion = np.corrcoef(salida[tramo], usuario[tramo])[0, 1]
    assert correlacion > 0.95
    # El eco que queda es mucho menor que la voz del usuario.
    assert db(usuario[tramo]) - db(salida[tramo] - usuario[tramo]) > 15


def test_sin_referencia_no_toca_el_microfono():
    mic = voz_falsa(2, 5)
    aec = CanceladorEco()
    salida = procesar(aec, mic, np.zeros_like(mic))
    assert np.allclose(salida, mic[:len(salida)], atol=1e-6) and not aec.adaptando


def test_remuestreo_y_retardo():
    t = np.arange(4800) / 48000
    tono = np.sin(2 * np.pi * 1000 * t)
    d = Decimador3()
    salida = np.concatenate([d.procesar(tono[i:i + 960]) for i in range(0, 4800, 960)])
    assert len(salida) == 1600
    esperado = np.sin(2 * np.pi * 1000 * (np.arange(1600) * 3 - 31) / 48000)   # retraso de grupo del FIR
    assert np.max(np.abs(salida[100:] - esperado[100:])) < 0.02

    alto = np.sin(2 * np.pi * 15000 * t)                       # por encima de 8 kHz: se elimina
    assert np.max(np.abs(Decimador3().procesar(alto)[50:])) < 0.01

    i2 = Interpolador2()
    assert len(i2.procesar(np.ones(480))) == 960

    r = Retardo(3)
    assert list(r.procesar(np.array([1.0, 2, 3, 4]))) == [0, 0, 0, 1]
    assert list(r.procesar(np.array([5.0]))) == [2]


def test_audio_duplex_con_tarjeta_falsa(monkeypatch):
    """La voz que sale por el «altavoz» vuelve al «micrófono» por un camino acústico a 48 kHz."""
    import asyncio
    import sys
    import threading
    import types

    from client.audio_io import AudioDuplex

    rng = np.random.default_rng(3)
    largo = int(48000 * 0.06)
    camino = np.concatenate([np.zeros(int(48000 * 0.03)),
                             0.5 * rng.standard_normal(largo) * np.exp(-np.arange(largo) / (48000 * 0.01))])
    camino *= 0.5 / np.sqrt(np.sum(camino ** 2))

    class FlujoFalso:
        latency = (0.02, 0.02)          # la tarjeta «dice» 40 ms; el retraso real es 40 ms + 30 ms acústicos

        def __init__(self, callback, **_):
            self.callback, self.parar = callback, threading.Event()
            self.tarjeta = np.zeros(int(48000 * 0.04))          # retraso de la tarjeta
            self.historia = np.zeros(len(camino))

        def _correr(self):
            while not self.parar.is_set():
                salida = bytearray(960 * 2)
                entrada = self._microfono()
                self.callback(entrada, memoryview(salida), 960, None, None)
                self.tarjeta = np.concatenate([self.tarjeta, np.frombuffer(bytes(salida), "<i2") / 32768.0])
                self.parar.wait(0.002)

        def _microfono(self) -> bytes:
            sonando, self.tarjeta = self.tarjeta[:960], self.tarjeta[960:]
            datos = np.concatenate([self.historia, sonando])
            eco = np.convolve(datos, camino, mode="valid")[-960:]
            self.historia = datos[-len(camino):]
            return (np.clip(eco, -1, 1) * 32767).astype("<i2").tobytes()

        def __enter__(self):
            self.hilo = threading.Thread(target=self._correr, daemon=True)
            self.hilo.start()
            return self

        def __exit__(self, *_):
            self.parar.set()
            self.hilo.join()

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawStream=FlujoFalso))

    async def correr():
        audio = AudioDuplex()
        voz = voz_falsa(12, 1)
        t = np.arange(int(24000 * 12)) / 24000
        voz24 = np.interp(t, np.arange(len(voz)) / SR, voz)
        audio.reproducir(24000, (voz24 * 32767).astype("<i2").tobytes())
        salida, fuente = [], audio.tramas()
        async for trama in fuente:
            salida.append(np.frombuffer(trama, "<i2") / 32768.0)
            if len(salida) >= 50 * 11:
                break
        await fuente.aclose()
        return np.concatenate(salida), audio

    limpio, audio = asyncio.run(correr())
    assert len(limpio) == 50 * 11 * N
    sin_aec = AudioDuplex(aec=False)
    assert sin_aec.aec is None
    # Tras converger, lo que queda en el micrófono es mucho menor que el eco que entraba.
    assert audio.eco_db and max(audio.eco_db) > 15, audio.eco_db
