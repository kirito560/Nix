"""Audio sintético para las pruebas (VAD por energía: un tono fuerte cuenta como voz)."""
import numpy as np

from shared.protocol import BYTES_TRAMA


def tono(segundos: float, amplitud: float = 0.3, hz: float = 220.0) -> bytes:
    t = np.arange(int(16000 * segundos)) / 16000
    return (amplitud * np.sin(2 * np.pi * hz * t) * 32767).astype("<i2").tobytes()


def silencio(segundos: float) -> bytes:
    return bytes(int(16000 * segundos) * 2)


def tramas(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + BYTES_TRAMA] for i in range(0, len(pcm) - BYTES_TRAMA + 1, BYTES_TRAMA)]
