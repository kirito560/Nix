"""
Audio del cliente.

Cada dispositivo se abre con sus canales y su frecuencia reales (sd.query_devices): el micrófono se pasa a
mono y a 16 kHz después, y la voz del personaje se lleva a la frecuencia y los canales del altavoz. Así no
fallan los dispositivos que no aceptan 1 canal o 16 kHz (pasaba con varios de Windows).

Entrada (tramas PCM16 mono 16 kHz de 20 ms, protocol.BYTES_TRAMA):
  - Microfono: captura con sounddevice (PortAudio). El callback corre en otro hilo; las tramas pasan
    al bucle de asyncio con call_soon_threadsafe.
  - ArchivoWav: lee un WAV (cualquier frecuencia, mono o estéreo, 16 bits) y lo reproduce a ritmo real,
    seguido de silencio para que el VAD cierre la frase. Sirve para probar sin micrófono.

Salida (la voz del personaje, audio.out):
  - Altavoz: búfer que el callback de sounddevice va vaciando; vaciar() la calla al instante (barge-in).
    Si no se puede abrir, lo avisa y sigue sin voz (reintenta cada REINTENTO_AUDIO_S).
  - GrabadorWav: guarda la voz en un WAV (para probar sin altavoces).

Micrófono y altavoz a la vez, con cancelación de eco (lo normal en el PC):
  - AudioDuplex: un solo flujo de entrada y salida a 48 kHz. En cada callback, los 20 ms del micrófono
    llegan emparejados con los 20 ms que se mandaron al altavoz: esa es la referencia del AEC (client/aec.py).
    Si la tarjeta no acepta el flujo dúplex, sigue con Microfono + Altavoz por separado, sin AEC. Si tampoco
    abre el micrófono, lo avisa y reintenta cada REINTENTO_AUDIO_S: el programa no se cae.

probar_audio(): para `python -m client.main --probar-audio`.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

import numpy as np

from client.aec import CanceladorEco, Decimador3, Interpolador2, Retardo, _paso_bajo
from client.senal import Acondicionador, GuardaEco
from shared.protocol import BYTES_TRAMA, MS_TRAMA, MUESTRAS_TRAMA, SR_ENTRADA

log = logging.getLogger("nix.audio")

REINTENTO_AUDIO_S = 5.0
MAX_CANALES = 2                     # para voz basta; algunos dispositivos dicen tener 8 o 16
PICO_SILENCIO_DB = -50.0            # por debajo, el micrófono llega prácticamente mudo


def ahora_ms() -> int:
    return int(time.time() * 1000)


class ErrorAudio(RuntimeError):
    """No se pudo consultar o abrir un dispositivo de audio."""


# ---------------------------------------------------------------------------------------- dispositivos
@dataclass(frozen=True)
class InfoDispositivo:
    indice: Optional[int | str]
    nombre: str
    api: str                        # MME, Windows DirectSound, Windows WASAPI, Windows WDM-KS, ALSA...
    canales: int                    # los que se abren (los reales, como mucho MAX_CANALES)
    sr: int                         # frecuencia nativa del dispositivo

    def __str__(self) -> str:
        return f"[{self.indice}] {self.nombre} ({self.api}, {self.canales} canal(es), {self.sr} Hz)"


def _sd():
    try:
        import sounddevice as sd
    except Exception as e:  # noqa: BLE001 - ImportError u OSError si falta PortAudio
        raise ErrorAudio(f"no pude cargar sounddevice/PortAudio: {e}") from e
    return sd


def info_dispositivo(sd, dispositivo: Optional[int | str], tipo: str) -> InfoDispositivo:
    """tipo: "input" o "output". `dispositivo` None = el predeterminado de Windows."""
    que = "entrada" if tipo == "input" else "salida"
    try:
        d = sd.query_devices(dispositivo, tipo)
        api = sd.query_hostapis(d["hostapi"])["name"]
    except Exception as e:  # noqa: BLE001 - ValueError (no existe), PortAudioError...
        raise ErrorAudio(f"no encuentro el dispositivo de {que} {dispositivo!r}: {e}") from e
    maximo = int(d["max_input_channels" if tipo == "input" else "max_output_channels"])
    if maximo < 1:
        raise ErrorAudio(f"«{d['name']}» no es un dispositivo de {que}")
    indice = d.get("index", dispositivo)
    return InfoDispositivo(indice, d["name"], api, min(maximo, MAX_CANALES), int(d["default_samplerate"]))


# ---------------------------------------------------------------------------------------- conversiones
def a_mono(pcm16, canales: int) -> np.ndarray:
    """PCM16 intercalado de `canales` canales -> float64 mono (-1..1), promediando los canales."""
    x = np.frombuffer(pcm16, dtype="<i2")
    if canales > 1:
        x = x[:len(x) - len(x) % canales].reshape(-1, canales).mean(axis=1)
    return x / 32768.0


def a_canales(mono: np.ndarray, canales: int) -> bytes:
    """float mono (-1..1) -> PCM16 intercalado, el mismo sonido en todos los canales."""
    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    return (np.repeat(pcm, canales) if canales > 1 else pcm).tobytes()


def _a_float(pcm16) -> np.ndarray:
    return a_mono(pcm16, 1)


def _a_pcm16(audio: np.ndarray) -> bytes:
    return a_canales(audio, 1)


class Remuestreador:
    """Cambia la frecuencia de muestreo por bloques, con estado entre ellos (interpolación lineal; al bajar,
    antes pasa un paso bajo para no meter alias en la banda de voz)."""

    def __init__(self, sr_entrada: int, sr_salida: int):
        self.paso = sr_entrada / sr_salida
        self.igual = sr_entrada == sr_salida
        self.filtro = _paso_bajo(63, 0.45 * sr_salida / sr_entrada) if sr_salida < sr_entrada else None
        self.cola_filtro = np.zeros(0 if self.filtro is None else len(self.filtro) - 1)
        self.anterior: Optional[float] = None
        self.pos = 0.0              # posición de la próxima muestra de salida (0 = self.anterior)

    def procesar(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self.igual or not len(x):
            return x
        if self.filtro is not None:
            datos = np.concatenate([self.cola_filtro, x])
            x = np.convolve(datos, self.filtro, mode="valid")
            self.cola_filtro = datos[-(len(self.filtro) - 1):]
        buf = x if self.anterior is None else np.concatenate([[self.anterior], x])
        ultimo = len(buf) - 1
        cuantas = int(np.floor((ultimo - self.pos) / self.paso)) + 1 if ultimo >= self.pos else 0
        posiciones = self.pos + self.paso * np.arange(cuantas)
        y = np.interp(posiciones, np.arange(len(buf)), buf)
        self.pos = self.pos + cuantas * self.paso - ultimo
        self.anterior = float(buf[-1])
        return y


class Tramador:
    """Junta muestras de cualquier tamaño y entrega tramas exactas de MUESTRAS_TRAMA en PCM16 (mono, 16 kHz:
    lo que espera el servidor). Si recibe un `acondicionador`, lo aplica a cada trama antes de entregarla."""

    def __init__(self, acondicionador: Optional[Acondicionador] = None):
        self.pendiente = np.zeros(0)
        self.acondicionador = acondicionador

    def agregar(self, x: np.ndarray) -> list[bytes]:
        self.pendiente = np.concatenate([self.pendiente, x]) if len(self.pendiente) else np.asarray(x, np.float64)
        n = len(self.pendiente) // MUESTRAS_TRAMA
        if not n:
            return []
        bloques = self.pendiente[:n * MUESTRAS_TRAMA].reshape(n, MUESTRAS_TRAMA)
        if self.acondicionador is not None:
            bloques = [self.acondicionador.procesar(b) for b in bloques]
        tramas = [_a_pcm16(b) for b in bloques]
        self.pendiente = self.pendiente[n * MUESTRAS_TRAMA:]
        return tramas


class ColaBytes:
    """FIFO de bytes entre el bucle de asyncio (escribe) y el callback de audio (lee). Lee avanzando un índice
    en lugar de recortar el inicio, que con frases largas copiaba todo lo pendiente cada 20 ms."""

    COMPACTAR = 1 << 18

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0
        self.lock = threading.Lock()

    def __len__(self) -> int:
        with self.lock:
            return len(self.buf) - self.pos

    def agregar(self, datos: bytes) -> None:
        with self.lock:
            if self.pos >= self.COMPACTAR:
                del self.buf[:self.pos]
                self.pos = 0
            self.buf += datos

    def vaciar(self) -> None:
        with self.lock:
            self.buf.clear()
            self.pos = 0

    def leer_en(self, destino, n: int) -> None:
        """Escribe n bytes en `destino` (el búfer de la tarjeta); si no hay tantos, completa con silencio."""
        with self.lock:
            k = min(n, len(self.buf) - self.pos)
            if k:
                with memoryview(self.buf) as vista:
                    destino[:k] = vista[self.pos:self.pos + k]
                self.pos += k
                if self.pos == len(self.buf):
                    self.buf.clear()
                    self.pos = 0
        if k < n:
            destino[k:n] = bytes(n - k)

    def leer(self, n: int) -> bytes:
        destino = bytearray(n)
        self.leer_en(destino, n)
        return bytes(destino)


def _meter(cola: asyncio.Queue, item) -> None:
    """Desde el hilo de audio, vía call_soon_threadsafe: si el bucle va atrasado, se pierde la trama."""
    if not cola.full():
        cola.put_nowait(item)


# ---------------------------------------------------------------------------------------- entrada
class Microfono:
    def __init__(self, dispositivo: Optional[int | str] = None, acondicionar: bool = True):
        self.dispositivo, self.acondicionar = dispositivo, acondicionar

    async def tramas(self) -> AsyncIterator[bytes]:
        sd = _sd()
        info = info_dispositivo(sd, self.dispositivo, "input")
        bucle = asyncio.get_running_loop()
        cola: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        def callback(datos, _frames, _tiempo, estado):
            if estado:
                log.warning("⚠️ micrófono: %s", estado)
            bucle.call_soon_threadsafe(_meter, cola, bytes(datos))

        try:
            flujo = sd.RawInputStream(samplerate=info.sr, channels=info.canales, dtype="int16",
                                      blocksize=info.sr * MS_TRAMA // 1000, device=info.indice, callback=callback)
            flujo.start()
        except Exception as e:  # noqa: BLE001 - PortAudioError: canales, frecuencia, dispositivo ocupado...
            raise ErrorAudio(f"no pude abrir el micrófono {info}: {e}") from e
        log.info("🎤 micrófono: %s", info)
        a_16k = Decimador3() if info.sr == 3 * SR_ENTRADA else Remuestreador(info.sr, SR_ENTRADA)
        tramador = Tramador(Acondicionador() if self.acondicionar else None)
        try:
            while True:
                datos = await cola.get()
                for trama in tramador.agregar(a_16k.procesar(a_mono(datos, info.canales))):
                    yield trama
        finally:
            flujo.stop()
            flujo.close()


def leer_wav(ruta: Path) -> np.ndarray:
    """WAV de 16 bits -> PCM16 mono a 16 kHz (re-muestreo lineal si hace falta)."""
    with wave.open(str(ruta), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{ruta.name}: el WAV debe ser de 16 bits")
        sr, canales = w.getframerate(), w.getnchannels()
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    if canales > 1:
        audio = audio.reshape(-1, canales).mean(axis=1)
    if sr != SR_ENTRADA:
        n = int(len(audio) * SR_ENTRADA / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return np.clip(audio, -32768, 32767).astype("<i2")


class ArchivoWav:
    def __init__(self, ruta: Path, silencio_final_ms: int = 1500, tiempo_real: bool = True):
        self.pcm = leer_wav(ruta).tobytes() + bytes(SR_ENTRADA * 2 * silencio_final_ms // 1000)
        self.tiempo_real = tiempo_real

    async def tramas(self) -> AsyncIterator[bytes]:
        inicio = time.monotonic()
        vista = memoryview(self.pcm)
        for i, pos in enumerate(range(0, len(self.pcm) - BYTES_TRAMA + 1, BYTES_TRAMA)):
            if self.tiempo_real:
                await asyncio.sleep(max(0.0, inicio + i * MS_TRAMA / 1000 - time.monotonic()))
            yield bytes(vista[pos:pos + BYTES_TRAMA])


# ---------------------------------------------------------------------------------------- salida
class Altavoz:
    def __init__(self, dispositivo: Optional[int | str] = None):
        self.dispositivo = dispositivo
        self.cola = ColaBytes()
        self.stream = None
        self.info: Optional[InfoDispositivo] = None
        self.sr_voz: Optional[int] = None
        self.remuestreo: Optional[Remuestreador] = None
        self.fallo_en: Optional[float] = None

    def reproducir(self, sample_rate: int, pcm: bytes) -> None:
        if self.stream is None and not self._abrir():
            return                                       # sin altavoz: Nix sigue funcionando, sin voz
        if sample_rate != self.sr_voz:
            self.sr_voz, self.remuestreo = sample_rate, Remuestreador(sample_rate, self.info.sr)
        self.cola.agregar(a_canales(self.remuestreo.procesar(_a_float(pcm)), self.info.canales))

    def vaciar(self) -> None:
        self.cola.vaciar()

    def _abrir(self) -> bool:
        if self.fallo_en is not None and time.monotonic() - self.fallo_en < REINTENTO_AUDIO_S:
            return False
        try:
            sd = _sd()
            self.info = info_dispositivo(sd, self.dispositivo, "output")
            self.stream = sd.RawOutputStream(samplerate=self.info.sr, channels=self.info.canales, dtype="int16",
                                             device=self.info.indice, callback=self._callback)
            self.stream.start()
        except Exception as e:  # noqa: BLE001
            self.stream, self.fallo_en = None, time.monotonic()
            log.error("🔇 no pude abrir el altavoz (%s): sigo sin voz y reintento en %.0f s. "
                      "Revisa ALTAVOZ_DISPOSITIVO con --probar-audio.", e, REINTENTO_AUDIO_S)
            return False
        self.fallo_en = None
        log.info("🔈 altavoz: %s", self.info)
        return True

    def _callback(self, salida, _frames, _tiempo, _estado) -> None:
        self.cola.leer_en(salida, len(salida))

    def cerrar(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class GrabadorWav:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        self.w: Optional[wave.Wave_write] = None

    def reproducir(self, sample_rate: int, pcm: bytes) -> None:
        if self.w is None:
            self.w = wave.open(str(self.ruta), "wb")
            self.w.setnchannels(1)
            self.w.setsampwidth(2)
            self.w.setframerate(sample_rate)
        self.w.writeframes(pcm)

    def vaciar(self) -> None:
        pass

    def cerrar(self) -> None:
        if self.w is not None:
            self.w.close()


# ---------------------------------------------------------------------------------------- dúplex
class AudioDuplex:
    SR = 48000
    BLOQUE = SR * MS_TRAMA // 1000                 # 960 muestras = 320 a 16 kHz = una trama

    def __init__(self, microfono: Optional[int | str] = None, altavoz: Optional[int | str] = None,
                 aec: bool = True, retraso_ms: Optional[float] = None):
        self.microfono, self.altavoz = microfono, altavoz
        self.aec = CanceladorEco() if aec else None
        self.retraso_ms = retraso_ms                 # None = la latencia que informa la tarjeta
        self.cola_salida = ColaBytes()               # voz pendiente, PCM16 mono a 48 kHz
        self.interpolador = Interpolador2()
        self.remuestreo: Optional[Remuestreador] = None
        self.separado: Optional[Altavoz] = None      # si no hubo flujo dúplex
        self.guarda = GuardaEco()

    @property
    def eco_db(self) -> list[float]:
        """Atenuación del eco medida cada 5 s de su voz sonando."""
        return self.guarda.atenuaciones_db

    # ------------------------------------------------------------------ salida
    def reproducir(self, sample_rate: int, pcm: bytes) -> None:
        if self.separado is not None:
            self.separado.reproducir(sample_rate, pcm)
            return
        audio = _a_float(pcm)
        if sample_rate == self.SR // 2:
            audio = self.interpolador.procesar(audio)
        elif sample_rate != self.SR:
            if self.remuestreo is None or self.remuestreo.paso != sample_rate / self.SR:
                self.remuestreo = Remuestreador(sample_rate, self.SR)
            audio = self.remuestreo.procesar(audio)
        self.cola_salida.agregar(_a_pcm16(audio))

    def vaciar(self) -> None:
        if self.separado is not None:
            self.separado.vaciar()
        self.cola_salida.vaciar()

    def cerrar(self) -> None:
        if self.separado is not None:
            self.separado.cerrar()

    # ------------------------------------------------------------------ entrada
    async def tramas(self) -> AsyncIterator[bytes]:
        """Tramas del micrófono (sin eco si hay dúplex). Si no se puede abrir ningún micrófono, lo avisa y
        reintenta cada REINTENTO_AUDIO_S en vez de tumbar el programa."""
        while True:
            try:
                async for trama in self._tramas():
                    yield trama
                return
            except ErrorAudio as e:
                log.error("❌ %s. Reintento en %.0f s (revisa MIC_DISPOSITIVO con --probar-audio).",
                          e, REINTENTO_AUDIO_S)
                await asyncio.sleep(REINTENTO_AUDIO_S)

    async def _tramas(self) -> AsyncIterator[bytes]:
        sd = _sd()
        mic = info_dispositivo(sd, self.microfono, "input")
        alt = info_dispositivo(sd, self.altavoz, "output")
        if mic.api != alt.api:
            log.warning("⚠️ el micrófono es de %s y el altavoz de %s: PortAudio casi nunca los abre juntos. "
                        "Para tener cancelación de eco elige los dos de la misma API (ver --listar).",
                        mic.api, alt.api)
        bucle = asyncio.get_running_loop()
        cola: asyncio.Queue = asyncio.Queue(maxsize=200)
        n_bytes = self.BLOQUE * 2

        def callback(entrada, salida, _frames, _tiempo, estado):
            if estado:
                log.warning("⚠️ audio: %s", estado)
            ref = self.cola_salida.leer(n_bytes)
            if alt.canales == 1:
                salida[:n_bytes] = ref
            else:
                salida[:] = np.repeat(np.frombuffer(ref, "<i2"), alt.canales).tobytes()
            bucle.call_soon_threadsafe(_meter, cola, (bytes(entrada), ref))

        try:
            flujo = sd.RawStream(samplerate=self.SR, blocksize=self.BLOQUE, channels=(mic.canales, alt.canales),
                                 dtype="int16", device=(mic.indice, alt.indice), callback=callback)
        except Exception as e:  # noqa: BLE001 - PortAudioError, APIs distintas, etc.
            log.warning("🔇 no pude abrir micrófono y altavoz juntos (%s): los abro por separado, sin cancelación "
                        "de eco. Con audífonos da igual; con bocinas, Nix puede oírse a sí misma.", e)
            if self.separado is None:
                self.separado = Altavoz(self.altavoz)
            async for trama in Microfono(self.microfono).tramas():
                yield trama
            return

        if self.separado is not None:
            self.separado.cerrar()
            self.separado = None
        with flujo:
            log.info("🎤 micrófono: %s", mic)
            log.info("🔈 altavoz: %s", alt)
            latencia_ms = sum(flujo.latency) * 1000
            retraso_ms = latencia_ms if self.retraso_ms is None else self.retraso_ms
            log.info("🎧 audio dúplex a 48 kHz (latencia de la tarjeta %.0f ms); cancelación de eco: %s",
                     latencia_ms, "sí" if self.aec else "no")
            retardo = Retardo(max(0, round(SR_ENTRADA * (retraso_ms - 10) / 1000)))
            dec_mic, dec_ref = Decimador3(), Decimador3()
            acondicionador = Acondicionador()
            while True:
                mic_pcm, ref_pcm = await cola.get()
                voz = dec_mic.procesar(a_mono(mic_pcm, mic.canales))
                if self.aec is not None:
                    ref = retardo.procesar(dec_ref.procesar(_a_float(ref_pcm)))
                    limpio = self.aec.procesar(voz, ref)
                    voz = self.guarda.procesar(voz, limpio, self.aec, self.aec.adaptando)
                yield _a_pcm16(acondicionador.procesar(voz))


# ---------------------------------------------------------------------------------------- prueba
def _nivel_db(x: np.ndarray) -> tuple[float, float]:
    """(nivel RMS, pico) en dBFS de una señal float -1..1."""
    if not len(x):
        return -120.0, -120.0
    return (20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9), 20 * np.log10(np.max(np.abs(x)) + 1e-9))


def probar_salida(sd, dispositivo, imprimir: Callable[[str], None] = print, segundos: float = 1.0) -> bool:
    try:
        info = info_dispositivo(sd, dispositivo, "output")
        imprimir(f"🔈 Salida {info}: suena un tono de {segundos:.0f} s…")
        t = np.arange(int(info.sr * segundos)) / info.sr
        tono = (0.2 * np.sin(2 * np.pi * 440 * t) * np.minimum(1, np.minimum(t, segundos - t) * 50)).astype(np.float32)
        sd.play(np.repeat(tono[:, None], info.canales, axis=1), info.sr, device=info.indice, blocking=True)
    except Exception as e:  # noqa: BLE001
        imprimir(f"   ❌ falló: {e}")
        return False
    imprimir("   ✅ abrió y reprodujo el tono (si no lo oíste, ese no es tu altavoz)")
    return True


def probar_entrada(sd, dispositivo, imprimir: Callable[[str], None] = print, segundos: float = 2.0) -> bool:
    try:
        info = info_dispositivo(sd, dispositivo, "input")
        imprimir(f"🎤 Entrada {info}: habla durante {segundos:.0f} s…")
        datos = sd.rec(int(info.sr * segundos), samplerate=info.sr, channels=info.canales, dtype="int16",
                       device=info.indice, blocking=True)
    except Exception as e:  # noqa: BLE001
        imprimir(f"   ❌ falló: {e}")
        return False
    nivel, pico = _nivel_db(a_mono(np.ascontiguousarray(datos, dtype="<i2").tobytes(), info.canales))
    if pico < PICO_SILENCIO_DB:
        imprimir(f"   ⚠️ abrió, pero llega casi en silencio (pico {pico:.0f} dBFS): micrófono equivocado, "
                 "silenciado en Windows o muy bajo")
        return False
    imprimir(f"   ✅ funciona: nivel {nivel:.0f} dBFS, pico {pico:.0f} dBFS")
    return True


def probar_duplex(sd, microfono, altavoz, imprimir: Callable[[str], None] = print) -> bool:
    try:
        mic = info_dispositivo(sd, microfono, "input")
        alt = info_dispositivo(sd, altavoz, "output")
        if mic.api != alt.api:
            imprimir(f"⚠️ micrófono ({mic.api}) y altavoz ({alt.api}) son de APIs distintas")
        flujo = sd.RawStream(samplerate=AudioDuplex.SR, blocksize=AudioDuplex.BLOQUE,
                             channels=(mic.canales, alt.canales), dtype="int16", device=(mic.indice, alt.indice),
                             callback=lambda *_: None)
        flujo.close()
    except Exception as e:  # noqa: BLE001
        imprimir(f"🎧 Dúplex: ⚠️ no abre juntos ({e}). Nix funciona igual, pero sin cancelación de eco: "
                 "usa audífonos o elige micrófono y altavoz de la misma API.")
        return False
    imprimir("🎧 Dúplex: ✅ micrófono y altavoz abren juntos: habrá cancelación de eco")
    return True


def probar_audio(microfono=None, altavoz=None, todos: bool = False,
                 imprimir: Callable[[str], None] = print) -> bool:
    """Prueba el micrófono y el altavoz configurados (o, con todos=True, cada dispositivo). Devuelve True si
    los configurados funcionan."""
    try:
        sd = _sd()
    except ErrorAudio as e:
        imprimir(f"❌ {e}")
        return False
    if todos:
        funcionan = {"entrada": [], "salida": []}
        for d in sd.query_devices():
            if d["max_output_channels"] > 0 and probar_salida(sd, d["index"], imprimir):
                funcionan["salida"].append(d["index"])
            if d["max_input_channels"] > 0 and probar_entrada(sd, d["index"], imprimir, segundos=1.0):
                funcionan["entrada"].append(d["index"])
        imprimir(f"\nFuncionan — salidas: {funcionan['salida'] or 'ninguna'} | "
                 f"entradas con sonido: {funcionan['entrada'] or 'ninguna'}")
        return bool(funcionan["salida"] and funcionan["entrada"])
    salida = probar_salida(sd, altavoz, imprimir)
    entrada = probar_entrada(sd, microfono, imprimir)
    probar_duplex(sd, microfono, altavoz, imprimir)
    imprimir("\n" + ("✅ Listo: micrófono y altavoz funcionan." if salida and entrada else
                     "❌ Algo falló: revisa MIC_DISPOSITIVO / ALTAVOZ_DISPOSITIVO en el .env (números de --listar)."))
    return salida and entrada
