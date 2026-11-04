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


def sd_falso(duplex_falla=True, mic_falla=False, altavoz_falla=False, nivel_mic=0.3, dispositivos=DISPOSITIVOS,
             apis=APIS, duplex_abre=None, duplex_arranca=None):
    """Módulo sounddevice falso. Registra lo que se abre en `sd.abiertos`. `duplex_abre` y `duplex_arranca`: las
    APIs (índice) donde el dúplex abre y donde además pasa audio; None = en todas."""
    sd = types.SimpleNamespace(abiertos=[], tocado=[], PortAudioError=ErrorPortAudio)

    def query_devices(dispositivo=None, tipo=None):
        if dispositivo is None and tipo is None:
            return dispositivos
        if dispositivo is None:
            dispositivo = 0 if tipo == "input" else 1
        if isinstance(dispositivo, str):
            coincide = [d for d in dispositivos if dispositivo.lower() in d["name"].lower()]
            if not coincide:
                raise ValueError(f"No input/output device matching {dispositivo!r}")
            return coincide[0]
        if not 0 <= dispositivo < len(dispositivos):
            raise ErrorPortAudio(f"Error querying device {dispositivo}")
        d = dispositivos[dispositivo]
        if tipo and not d[f"max_{tipo}_channels"]:                  # como el sounddevice real
            raise ValueError(f"Not an {tipo} device: {d['name']!r}")
        return d

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
        api = dispositivos[kw["device"][0]]["hostapi"]
        if duplex_abre is not None and api not in duplex_abre:
            raise ErrorPortAudio("Invalid device [PaErrorCode -9996]")
        sd.abiertos.append(("duplex", kw["device"], kw["channels"], kw["samplerate"]))

        def arrancar():
            if duplex_arranca is None or api in duplex_arranca:
                salida = bytearray(b"x" * (kw["blocksize"] * kw["channels"][1] * 2))
                kw["callback"](bytes(kw["blocksize"] * kw["channels"][0] * 2), salida, kw["blocksize"], None, None)
                assert not any(salida)                              # la prueba no debe hacer ruido

        return types.SimpleNamespace(start=arrancar, stop=lambda: None, close=lambda: None)

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
    sd.query_hostapis = lambda i: apis[i]
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


# ------------------------------------------------------------------ dispositivos por nombre
# Como en el PC real: el mismo aparato en varias APIs, y MME corta los nombres a 31 caracteres.
def _d(indice, nombre, api, entrada=0, salida=0):
    return {"name": nombre, "index": indice, "hostapi": api, "max_input_channels": entrada,
            "max_output_channels": salida, "default_samplerate": 48000.0}


MME, DSOUND, WASAPI, KS = 0, 1, 2, 3
APIS_PC = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]
MIC, AUR, TV = "Micrófono (BKD-11 Pro Audio Device)", "Auriculares (BKD-11 Pro Audio Device)", \
    "WD50FBR105 (NVIDIA High Definition Audio)"
PC = [_d(0, MIC[:31], MME, entrada=2), _d(1, AUR[:31], MME, salida=2), _d(2, TV[:31], MME, salida=2),
      _d(3, MIC, DSOUND, entrada=2), _d(4, AUR, DSOUND, salida=2), _d(5, TV, DSOUND, salida=2),
      _d(6, TV, WASAPI, salida=2), _d(7, AUR, WASAPI, salida=2), _d(8, MIC, WASAPI, entrada=2),
      _d(9, "CABLE Output (VB-Audio Virtual Cable)", WASAPI, entrada=2),
      _d(10, MIC, KS, entrada=2), _d(11, "Output (NVIDIA High Definition Audio)", KS, salida=2),
      _d(12, "Micrófono (Realtek(R) Audio)", MME, entrada=2), _d(13, "Micrófono (GENERAL WEBCAM)", MME, entrada=1)]


@pytest.fixture
def pc(instalar):
    def _pc(**kw):
        kw.setdefault("duplex_falla", False)
        return instalar(dispositivos=PC, apis=APIS_PC, **kw)
    return _pc


def test_el_nombre_del_microfono_busca_solo_entre_entradas(pc):
    sd = pc()
    assert info_dispositivo(sd, "BKD-11 Pro Audio Device", "input").nombre == MIC
    assert info_dispositivo(sd, "BKD-11 Pro Audio Device", "output").nombre == AUR  # el mismo nombre, otra dirección


def test_el_nombre_del_altavoz_busca_solo_entre_salidas(pc):
    sd = pc()
    assert info_dispositivo(sd, "wd50fbr105", "output").indice == 6
    with pytest.raises(ErrorAudio):
        info_dispositivo(sd, "WD50FBR105", "input")


def test_el_nombre_encuentra_el_cortado_de_mme_y_sin_acento(pc):
    sd = pc()
    assert info_dispositivo(sd, "BKD-11 Pro Audio Device MME", "input").indice == 0
    assert info_dispositivo(sd, MIC + ", MME", "input").indice == 0
    assert info_dispositivo(sd, TV + " MME", "output").indice == 2
    assert info_dispositivo(sd, "microfono (general", "input").indice == 13


def test_el_nombre_de_varios_no_adivina(pc):
    with pytest.raises(ErrorAudio, match="coincide con varias"):
        info_dispositivo(pc(), "Micrófono", "input")


def test_la_api_escrita_en_el_nombre_se_respeta(pc):
    sd = pc()
    mic, alt = audio_io.elegir_par(sd, "BKD-11 Pro Audio Device DirectSound", "WD50FBR105 DirectSound")
    assert (mic.indice, alt.indice) == (3, 5)
    with pytest.raises(ErrorAudio, match="no está en wdm-ks"):
        info_dispositivo(sd, "WD50FBR105 WDM-KS", "output")


def test_con_nombres_elige_la_api_preferida_donde_abren(pc):
    sd = pc()
    mic, alt = audio_io.elegir_par(sd, "BKD-11 Pro Audio Device", "WD50FBR105")
    assert (mic.indice, alt.indice, mic.api) == (8, 6, "Windows WASAPI")
    assert sd.abiertos == [("duplex", (8, 6), (2, 2), 48000)]


def test_si_wasapi_no_abre_prueba_la_siguiente_api(pc):
    sd = pc(duplex_abre={DSOUND, MME})
    mic, alt = audio_io.elegir_par(sd, "BKD-11 Pro Audio Device", "WD50FBR105")
    assert (mic.indice, alt.indice) == (3, 5)


def test_una_api_que_abre_pero_no_pasa_audio_no_sirve(pc, monkeypatch):
    real = audio_io._duplex_arranca
    monkeypatch.setattr(audio_io, "_duplex_arranca", lambda sd, m, a: real(sd, m, a, espera_s=0.01))
    sd = pc(duplex_arranca={MME})
    mic, alt = audio_io.elegir_par(sd, "BKD-11 Pro Audio Device", "WD50FBR105")
    assert (mic.indice, alt.indice) == (0, 2)                         # MME, con los nombres cortados
    assert [a[1] for a in sd.abiertos] == [(8, 6), (3, 5), (0, 2)]


def test_si_ninguna_api_abre_avisa_y_no_revienta(pc, caplog):
    sd = pc(duplex_abre=set())
    mic, alt = audio_io.elegir_par(sd, "BKD-11 Pro Audio Device", "WD50FBR105")
    assert (mic.indice, alt.indice) == (8, 6)
    assert "no abren en dúplex" in caplog.text


def test_un_indice_fija_la_api_del_nombre(pc):
    sd = pc()
    mic, alt = audio_io.elegir_par(sd, 3, "WD50FBR105")
    assert (mic.indice, alt.indice) == (3, 5)


def test_solo_con_indices_no_se_abre_nada_para_elegir(pc):
    sd = pc()
    mic, alt = audio_io.elegir_par(sd, 0, 5)
    assert (mic.indice, alt.indice) == (0, 5) and sd.abiertos == []


@pytest.mark.parametrize("valor", [1, 7, "Auriculares (BKD-11"])
def test_salida_como_microfono_dice_la_entrada_mas_parecida(pc, valor):
    with pytest.raises(ErrorAudio) as e:
        audio_io.elegir_par(pc(), valor, "WD50FBR105")
    texto = str(e.value)
    assert "MIC_DISPOSITIVO" in texto and "es una salida, no una entrada" in texto
    assert f"pon MIC_DISPOSITIVO={MIC}" in texto


@pytest.mark.parametrize("valor", [8, "Micrófono (BKD-11"])
def test_entrada_como_altavoz_dice_la_salida_mas_parecida(pc, valor):
    with pytest.raises(ErrorAudio) as e:
        audio_io.elegir_par(pc(), "BKD-11", valor)
    texto = str(e.value)
    assert "ALTAVOZ_DISPOSITIVO" in texto and "es una entrada, no una salida" in texto
    assert f"pon ALTAVOZ_DISPOSITIVO={AUR}" in texto


def test_nombre_mal_escrito_dice_el_mas_parecido(pc):
    with pytest.raises(ErrorAudio) as e:
        info_dispositivo(pc(), "BKD-12 Pro Audio Devise", "input")
    assert "no encuentro" in str(e.value) and f"pon MIC_DISPOSITIVO={MIC}" in str(e.value)


@pytest.mark.parametrize("valor", [2, "WD50FBR105"])
def test_sin_nombre_parecido_la_sugerencia_se_da_como_dudosa(pc, valor):
    with pytest.raises(ErrorAudio) as e:                            # una TV no tiene micrófono
        info_dispositivo(pc(), valor, "input")
    texto = str(e.value)
    assert "es una salida, no una entrada" in texto and "Ninguna entrada tiene un nombre parecido" in texto
    assert "pon MIC_DISPOSITIVO=" in texto and "--listar" in texto


def test_la_recomendacion_no_es_un_cable_virtual(pc):
    with pytest.raises(ErrorAudio) as e:
        info_dispositivo(pc(), "CABLE Input (VB-Audio Virtual Cable)", "input")
    assert "VB-Audio" not in str(e.value).split("pon MIC_DISPOSITIVO=")[1]


def test_duplex_con_nombres_y_sin_flujo_abre_por_separado_en_la_misma_api(pc):
    sd = pc(duplex_falla=True)

    async def correr():
        audio = AudioDuplex("BKD-11 Pro Audio Device", "WD50FBR105")
        fuente = audio.tramas()
        await fuente.__anext__()
        audio.reproducir(24000, bytes(960))
        await fuente.aclose()

    asyncio.run(correr())
    assert ("entrada", 8, 2, 48000) in sd.abiertos and ("salida", 6, 2, 48000) in sd.abiertos


def test_probar_audio_con_nombres(pc):
    sd = pc()
    lineas = []
    assert probar_audio("BKD-11 Pro Audio Device", "WD50FBR105", imprimir=lineas.append)
    texto = "\n".join(lineas)
    assert sd.tocado[0][0] == 6 and "[8]" in texto and "🎧 Dúplex: ✅" in texto


def test_probar_audio_con_una_salida_de_microfono_lo_explica(pc):
    pc()
    lineas = []
    assert not probar_audio(2, "WD50FBR105", imprimir=lineas.append)
    assert f"pon MIC_DISPOSITIVO={MIC}" in "\n".join(lineas)


def test_probar_duplex_avisa_si_abre_pero_no_arranca(pc, monkeypatch):
    real = audio_io._duplex_arranca
    monkeypatch.setattr(audio_io, "_duplex_arranca", lambda sd, m, a: real(sd, m, a, espera_s=0.01))
    lineas = []
    assert not audio_io.probar_duplex(pc(duplex_arranca=set()), 8, 6, imprimir=lineas.append)
    assert "no pasó audio" in lineas[-1]


def test_listar_con_nombres_dice_lo_que_usara(pc, capsys):
    from client.main import main
    pc()
    main(["--listar", "--dispositivo", "BKD-11", "--altavoz", "WD50FBR105"])
    salida = capsys.readouterr().out
    assert f"micrófono: [8] {MIC} (Windows WASAPI" in salida and f"altavoz: [6] {TV} (Windows WASAPI" in salida


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
