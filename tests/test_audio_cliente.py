"""Audio del cliente con un sounddevice falso: canales reales, conversión a mono, remuestreo, fallback sin
dúplex, errores que no tumban el programa, --listar y --probar-audio."""
import asyncio
import sys
import threading
import types

import numpy as np
import pytest

from client import audio_io
from client.audio_io import (Altavoz, AudioDuplex, ColaBytes, ErrorAudio, Microfono, Remuestreador, Tramador,
                             a_canales, a_mono, info_dispositivo, probar_audio)
from shared.protocol import BYTES_TRAMA, MUESTRAS_TRAMA

# Parecidos a los del PC del usuario: un micrófono estéreo a 44,1 kHz, audífonos estéreo a 48 kHz y una
# webcam de otra API.
DISPOSITIVOS = [
    {"name": "Micrófono (BKD-11)", "index": 0, "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0,
     "default_samplerate": 44100.0},
    {"name": "Auriculares (BKD-11)", "index": 1, "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2,
     "default_samplerate": 48000.0},
    {"name": "CABLE Input", "index": 2, "hostapi": 0, "max_input_channels": 0, "max_output_channels": 16,
     "default_samplerate": 48000.0},
    {"name": "Micrófono (WEBCAM)", "index": 3, "hostapi": 1, "max_input_channels": 1, "max_output_channels": 0,
     "default_samplerate": 16000.0},
]
APIS = [{"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}]


class ErrorPortAudio(Exception):
    pass


def sd_falso(duplex_falla=True, mic_falla=False, altavoz_falla=False, nivel_mic=0.3):
    """Módulo sounddevice falso. Registra lo que se abre en `sd.abiertos`."""
    sd = types.SimpleNamespace(abiertos=[], tocado=[], PortAudioError=ErrorPortAudio)

    def query_devices(dispositivo=None, tipo=None):
        if dispositivo is None and tipo is None:
            return DISPOSITIVOS
        if dispositivo is None:
            dispositivo = 0 if tipo == "input" else 1
        if isinstance(dispositivo, str):
            coincide = [d for d in DISPOSITIVOS if dispositivo.lower() in d["name"].lower()]
            if not coincide:
                raise ValueError(f"No input/output device matching {dispositivo!r}")
            return coincide[0]
        if not 0 <= dispositivo < len(DISPOSITIVOS):
            raise ErrorPortAudio(f"Error querying device {dispositivo}")
        return DISPOSITIVOS[dispositivo]

    class FlujoEntrada:
        def __init__(self, samplerate, channels, blocksize, device, callback, **_):
            if mic_falla:
                raise ErrorPortAudio("Invalid number of channels [PaErrorCode -9998]")
            sd.abiertos.append(("entrada", device, channels, samplerate))
            self.sr, self.canales, self.bloque, self.callback = samplerate, channels, blocksize, callback
            self.parar = threading.Event()
            self.t = 0

        def start(self):
            self.hilo = threading.Thread(target=self._correr, daemon=True)
            self.hilo.start()

        def _correr(self):
            while not self.parar.is_set():
                n = np.arange(self.t, self.t + self.bloque)
                self.t += self.bloque
                tono = nivel_mic * np.sin(2 * np.pi * 300 * n / self.sr)
                intercalado = np.repeat((tono * 32767).astype("<i2"), self.canales)
                self.callback(intercalado.tobytes(), self.bloque, None, None)
                self.parar.wait(0.001)

        def stop(self):
            self.parar.set()
            self.hilo.join()

        def close(self):
            pass

    class FlujoSalida:
        def __init__(self, samplerate, channels, device, callback, **_):
            if altavoz_falla:
                raise ErrorPortAudio("Device unavailable")
            sd.abiertos.append(("salida", device, channels, samplerate))
            self.callback, self.canales = callback, channels

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

        def pedir(self, frames):
            """Simula a la tarjeta pidiendo `frames` muestras por canal."""
            buf = bytearray(frames * self.canales * 2)
            self.callback(memoryview(buf), frames, None, None)
            return np.frombuffer(bytes(buf), "<i2").reshape(-1, self.canales)

    def flujo_duplex(**kw):
        if duplex_falla:
            raise ErrorPortAudio("Invalid number of channels [PaErrorCode -9998]")
        sd.abiertos.append(("duplex", kw["device"], kw["channels"], kw["samplerate"]))
        return types.SimpleNamespace(close=lambda: None)

    def play(datos, samplerate, device=None, blocking=False):
        if altavoz_falla:
            raise ErrorPortAudio("Device unavailable")
        sd.tocado.append((device, datos.shape, samplerate))

    def rec(frames, samplerate, channels, dtype, device=None, blocking=False):
        if mic_falla:
            raise ErrorPortAudio("Invalid number of channels")
        t = np.arange(frames) / samplerate
        tono = (nivel_mic * np.sin(2 * np.pi * 300 * t) * 32767).astype("<i2")
        return np.repeat(tono[:, None], channels, axis=1)

    sd.query_devices = query_devices
    sd.query_hostapis = lambda i: APIS[i]
    sd.RawInputStream, sd.RawOutputStream, sd.RawStream = FlujoEntrada, FlujoSalida, flujo_duplex
    sd.play, sd.rec = play, rec
    return sd


@pytest.fixture
def instalar(monkeypatch):
    def _instalar(**kw):
        sd = sd_falso(**kw)
        monkeypatch.setitem(sys.modules, "sounddevice", sd)
        return sd
    monkeypatch.setattr(audio_io, "REINTENTO_AUDIO_S", 0.01)
    return _instalar


# ------------------------------------------------------------------ conversiones
def test_a_mono_promedia_los_canales():
    estereo = np.array([1000, 3000, -2000, -4000], dtype="<i2").tobytes()
    assert np.allclose(a_mono(estereo, 2) * 32768, [2000, -3000])
    mono = np.array([100, -200], dtype="<i2").tobytes()
    assert np.allclose(a_mono(mono, 1) * 32768, [100, -200])


def test_a_canales_duplica_el_mono():
    salida = np.frombuffer(a_canales(np.array([0.5, -0.25]), 2), "<i2")
    assert list(salida) == [16383, 16383, -8191, -8191]
    assert len(a_canales(np.zeros(10), 1)) == 20


def test_remuestreador_por_bloques_sigue_un_tono_continuo():
    sr_in, sr_out = 44100, 16000
    t = np.arange(sr_in) / sr_in
    tono = np.sin(2 * np.pi * 440 * t)
    r = Remuestreador(sr_in, sr_out)
    salida = np.concatenate([r.procesar(tono[i:i + 882]) for i in range(0, len(tono), 882)])
    assert abs(len(salida) - sr_out) <= 2
    # Mismo tono, sin saltos entre bloques: correlación casi perfecta con un tono de 440 Hz a 16 kHz
    # (compensando el retraso del paso bajo).
    ref = np.sin(2 * np.pi * 440 * (np.arange(len(salida)) / sr_out - 31 / sr_in))
    assert np.corrcoef(salida[200:], ref[200:])[0, 1] > 0.999
    # Subir de frecuencia (24 -> 44,1 kHz) da las muestras esperadas.
    assert abs(len(Remuestreador(24000, 44100).procesar(np.zeros(2400))) - 4410) <= 2


def test_tramador_entrega_tramas_exactas():
    tr = Tramador()
    assert tr.agregar(np.zeros(300)) == []
    tramas = tr.agregar(np.zeros(700))
    assert len(tramas) == 3 and all(len(t) == BYTES_TRAMA for t in tramas)
    assert len(tr.pendiente) == 1000 - 3 * MUESTRAS_TRAMA


def test_cola_bytes_lee_y_completa_con_silencio():
    c = ColaBytes()
    c.agregar(b"abcdef")
    destino = bytearray(4)
    c.leer_en(destino, 4)
    assert destino == b"abcd" and len(c) == 2
    assert c.leer(4) == b"ef\x00\x00" and len(c) == 0
    c.agregar(b"xy")
    c.vaciar()
    assert c.leer(2) == b"\x00\x00"


# ------------------------------------------------------------------ selección de dispositivos
def test_info_usa_los_canales_y_la_frecuencia_reales(instalar):
    sd = instalar()
    mic = info_dispositivo(sd, None, "input")
    assert (mic.indice, mic.canales, mic.sr, mic.api) == (0, 2, 44100, "Windows DirectSound")
    assert info_dispositivo(sd, 2, "output").canales == 2          # 16 canales -> se abren 2
    assert info_dispositivo(sd, 3, "input").canales == 1
    assert info_dispositivo(sd, "webcam", "input").indice == 3
    with pytest.raises(ErrorAudio):
        info_dispositivo(sd, 1, "input")                           # unos audífonos no son micrófono
    with pytest.raises(ErrorAudio):
        info_dispositivo(sd, 99, "input")


def test_microfono_abre_con_sus_canales_y_entrega_mono_16k(instalar):
    sd = instalar()

    async def correr():
        fuente = Microfono(0, acondicionar=False).tramas()
        tramas = [await fuente.__anext__() for _ in range(25)]
        await fuente.aclose()
        return tramas

    tramas = asyncio.run(correr())
    assert sd.abiertos[0] == ("entrada", 0, 2, 44100)
    assert all(len(t) == BYTES_TRAMA for t in tramas)
    audio = np.frombuffer(b"".join(tramas[5:]), "<i2") / 32768
    assert 0.25 < np.max(np.abs(audio)) < 0.35                    # el tono llega entero, sin perder volumen


def test_altavoz_lleva_la_voz_a_sus_canales_y_frecuencia(instalar):
    sd = instalar()
    alt = Altavoz(1)
    voz = (0.5 * np.sin(2 * np.pi * 300 * np.arange(2400) / 24000) * 32767).astype("<i2").tobytes()
    alt.reproducir(24000, voz)
    assert sd.abiertos[0] == ("salida", 1, 2, 48000)
    bloque = alt.stream.pedir(4800)
    assert bloque.shape == (4800, 2) and np.array_equal(bloque[:, 0], bloque[:, 1])
    assert np.max(np.abs(bloque)) > 15000                          # suena
    alt.vaciar()
    assert not np.any(alt.stream.pedir(100))                       # vaciar() la calla al instante


def test_altavoz_que_no_abre_no_tumba_el_programa(instalar, caplog):
    instalar(altavoz_falla=True)
    alt = Altavoz(1)
    alt.reproducir(24000, bytes(960))
    alt.reproducir(24000, bytes(960))
    assert alt.stream is None
    assert "no pude abrir el altavoz" in caplog.text


# ------------------------------------------------------------------ fallback sin dúplex
def test_sin_duplex_abre_microfono_y_altavoz_por_separado(instalar, caplog):
    sd = instalar(duplex_falla=True)

    async def correr():
        audio = AudioDuplex(0, 1)
        fuente = audio.tramas()
        tramas = [await fuente.__anext__() for _ in range(10)]
        audio.reproducir(24000, bytes(960))
        await fuente.aclose()
        return audio, tramas

    audio, tramas = asyncio.run(correr())
    assert all(len(t) == BYTES_TRAMA for t in tramas)
    assert ("entrada", 0, 2, 44100) in sd.abiertos and ("salida", 1, 2, 48000) in sd.abiertos
    assert audio.separado is not None
    assert "por separado" in caplog.text


def test_apis_distintas_avisan_antes_de_intentar_el_duplex(instalar, caplog):
    instalar(duplex_falla=True)

    async def correr():
        fuente = AudioDuplex(3, 1).tramas()                         # webcam WASAPI + audífonos DirectSound
        await fuente.__anext__()
        await fuente.aclose()

    asyncio.run(correr())
    assert "Windows WASAPI" in caplog.text and "Windows DirectSound" in caplog.text
    avisos = [r.getMessage() for r in caplog.records]
    assert avisos.index(next(a for a in avisos if "Windows WASAPI" in a)) < \
        avisos.index(next(a for a in avisos if "por separado" in a))


def test_microfono_que_no_abre_reintenta_sin_tumbar_el_programa(instalar, caplog):
    instalar(duplex_falla=True, mic_falla=True)

    async def correr():
        fuente = AudioDuplex(0, 1).tramas()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fuente.__anext__(), 0.2)         # sigue vivo, reintentando
        await fuente.aclose()

    asyncio.run(correr())
    assert caplog.text.count("Reintento en") >= 2


def test_con_duplex_usa_los_canales_de_cada_lado(instalar):
    sd = instalar(duplex_falla=False)
    assert audio_io.probar_duplex(sd, 0, 1, imprimir=lambda _t: None)
    assert sd.abiertos[-1] == ("duplex", (0, 1), (2, 2), 48000)


# ------------------------------------------------------------------ comandos
def test_listar_muestra_dispositivos_y_los_elegidos(instalar, capsys):
    from client.main import main
    instalar()
    main(["--listar", "--dispositivo", "3", "--altavoz", "1"])
    salida = capsys.readouterr().out
    assert "Micrófono (BKD-11)" in salida
    assert "micrófono: [3] Micrófono (WEBCAM) (Windows WASAPI, 1 canal(es), 16000 Hz)" in salida
    assert "altavoz: [1] Auriculares (BKD-11)" in salida


def test_listar_avisa_si_el_dispositivo_no_existe(instalar, capsys):
    from client.main import main
    instalar()
    main(["--listar", "--dispositivo", "99"])
    assert "❌ no encuentro el dispositivo de entrada 99" in capsys.readouterr().out


def test_probar_audio_bien(instalar):
    sd = instalar(duplex_falla=False)
    lineas = []
    assert probar_audio(0, 1, imprimir=lineas.append)
    assert sd.tocado == [(1, (48000, 2), 48000)]                    # 1 s de tono en estéreo a 48 kHz
    texto = "\n".join(lineas)
    assert "✅ abrió y reprodujo" in texto and "✅ funciona" in texto and "cancelación de eco" in texto


def test_probar_audio_detecta_micro_mudo_y_altavoz_roto(instalar):
    instalar(altavoz_falla=True, nivel_mic=0.0)
    lineas = []
    assert not probar_audio(0, 1, imprimir=lineas.append)
    texto = "\n".join(lineas)
    assert "❌ falló: Device unavailable" in texto and "casi en silencio" in texto


def test_probar_audio_todos(instalar):
    sd = instalar()
    lineas = []
    assert probar_audio(todos=True, imprimir=lineas.append)
    assert [t[0] for t in sd.tocado] == [1, 2]
    assert "entradas con sonido: [0, 3]" in lineas[-1]


def test_probar_audio_desde_main_sale_con_codigo(instalar):
    from client.main import main
    instalar(mic_falla=True)
    with pytest.raises(SystemExit) as salida:
        main(["--probar-audio"])
    assert salida.value.code == 1
