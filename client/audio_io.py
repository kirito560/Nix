"""
Fuentes de audio del cliente. Ambas entregan tramas PCM16 mono 16 kHz de 20 ms (protocol.BYTES_TRAMA).

  - Microfono: captura con sounddevice (PortAudio). El callback corre en otro hilo; las tramas pasan
    al bucle de asyncio con call_soon_threadsafe.
  - ArchivoWav: lee un WAV (cualquier frecuencia, mono o estéreo, 16 bits) y lo reproduce a ritmo real,
    seguido de silencio para que el VAD cierre la frase. Sirve para probar sin micrófono.
"""
from __future__ import annotations

import asyncio
import time
import wave
from pathlib import Path
from typing import AsyncIterator, Optional

import numpy as np

from shared.protocol import BYTES_TRAMA, MS_TRAMA, MUESTRAS_TRAMA, SR_ENTRADA


def ahora_ms() -> int:
    return int(time.time() * 1000)


class Microfono:
    def __init__(self, dispositivo: Optional[int | str] = None):
        self.dispositivo = dispositivo

    async def tramas(self) -> AsyncIterator[bytes]:
        import sounddevice as sd
        bucle = asyncio.get_running_loop()
        cola: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

        def callback(datos, _frames, _tiempo, estado):
            if estado:
                print(f"⚠️ micrófono: {estado}")
            trama = bytes(datos)
            bucle.call_soon_threadsafe(lambda: cola.full() or cola.put_nowait(trama))

        with sd.RawInputStream(samplerate=SR_ENTRADA, channels=1, dtype="int16", blocksize=MUESTRAS_TRAMA,
                               device=self.dispositivo, callback=callback):
            while True:
                yield await cola.get()


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
        for i, pos in enumerate(range(0, len(self.pcm) - BYTES_TRAMA + 1, BYTES_TRAMA)):
            if self.tiempo_real:
                await asyncio.sleep(max(0.0, inicio + i * MS_TRAMA / 1000 - time.monotonic()))
            yield self.pcm[pos:pos + BYTES_TRAMA]
