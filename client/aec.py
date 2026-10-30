"""
Cancelación de eco acústico (AEC) del cliente: resta del micrófono la voz del personaje que sale por los
altavoces, para que el servidor no la oiga como si fuera el usuario (y el barge-in funcione sin audífonos).

  - CanceladorEco: filtro adaptativo en frecuencia por bloques particionado (PBFDAF, overlap-save) con
    normalización por potencia. Aprende el camino altavoz -> habitación -> micrófono (hasta
    particiones * n muestras, 260 ms por defecto) y resta la estimación del eco. No adapta mientras el
    usuario habla a la vez (detector de doble habla de Geigel) ni cuando no suena nada.
  - Decimador3 / Interpolador2: 48 kHz -> 16 kHz (filtro paso bajo + 1 de cada 3) y 24 kHz -> 48 kHz.
  - Retardo: atrasa la referencia la latencia fija de la tarjeta de sonido, así el filtro solo tiene que
    cubrir el retraso acústico y la reverberación.

Todo es numpy, sin dependencias: corre en Windows igual que en Linux.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class CanceladorEco:
    def __init__(self, n: int = 320, particiones: int = 13, mu: float = 0.5, umbral_doble_habla: float = 0.6,
                 memoria_geigel: int = 25):
        self.n, self.p, self.mu, self.umbral = n, particiones, mu, umbral_doble_habla
        bins = n + 1
        self.w = np.zeros((particiones, bins), dtype=np.complex128)      # filtro, una partición por fila
        self.x = np.zeros((particiones, bins), dtype=np.complex128)      # espectros de referencia (0 = el más nuevo)
        self.ref_prev = np.zeros(n)
        self.potencia = np.full(bins, 1e-3)
        self.picos_ref: deque = deque(maxlen=memoria_geigel)             # máximos de los últimos ~500 ms
        self.adaptando = False

    def procesar(self, mic: np.ndarray, ref: np.ndarray) -> np.ndarray:
        """mic y ref: n muestras float (-1..1), alineadas. Devuelve el micrófono sin el eco."""
        n = self.n
        mic = np.asarray(mic, dtype=np.float64)
        ref = np.asarray(ref, dtype=np.float64)
        self.x = np.roll(self.x, 1, axis=0)
        self.x[0] = np.fft.rfft(np.concatenate([self.ref_prev, ref]))
        self.ref_prev = ref

        eco = np.fft.irfft((self.w * self.x).sum(axis=0), 2 * n)[n:]
        error = mic - eco

        self.picos_ref.append(float(np.max(np.abs(ref))))
        pico_ref = max(self.picos_ref)
        hay_referencia = pico_ref > 1e-3
        habla_el_usuario = float(np.max(np.abs(mic))) > self.umbral * pico_ref
        self.adaptando = hay_referencia and not habla_el_usuario
        self.potencia = 0.9 * self.potencia + 0.1 * np.abs(self.x[0]) ** 2
        if self.adaptando:
            e_f = np.fft.rfft(np.concatenate([np.zeros(n), error]))
            gradiente = self.mu * np.conj(self.x) * e_f / (self.p * self.potencia + 1e-6)
            g = np.fft.irfft(gradiente, 2 * n, axis=1)
            g[:, n:] = 0.0                                               # restricción: filtro causal de n muestras
            self.w += np.fft.rfft(g, axis=1)
        return error.astype(np.float32)


def _paso_bajo(taps: int, corte: float) -> np.ndarray:
    """FIR paso bajo con sinc enventanada (Blackman). `corte` en fracción de la frecuencia de muestreo."""
    m = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * corte * m) * np.blackman(taps)
    return h / h.sum()


class Decimador3:
    """48 kHz -> 16 kHz con estado entre bloques (bloques múltiplos de 3)."""

    FILTRO = _paso_bajo(63, 7400 / 48000)

    def __init__(self):
        self.cola = np.zeros(len(self.FILTRO) - 1)

    def procesar(self, x: np.ndarray) -> np.ndarray:
        datos = np.concatenate([self.cola, np.asarray(x, dtype=np.float64)])
        y = np.convolve(datos, self.FILTRO, mode="valid")
        self.cola = datos[-(len(self.FILTRO) - 1):]
        return y[::3]


class Interpolador2:
    """24 kHz -> 48 kHz por interpolación lineal, con estado entre bloques."""

    def __init__(self):
        self.ultimo = 0.0

    def procesar(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        previos = np.concatenate([[self.ultimo], x[:-1]])
        y = np.empty(2 * len(x))
        y[0::2] = (previos + x) / 2
        y[1::2] = x
        if len(x):
            self.ultimo = x[-1]
        return y


class Retardo:
    """Atrasa una señal un número fijo de muestras."""

    def __init__(self, muestras: int):
        self.buf = np.zeros(max(0, muestras))

    def procesar(self, x: np.ndarray) -> np.ndarray:
        datos = np.concatenate([self.buf, np.asarray(x, dtype=np.float64)])
        salida, self.buf = datos[:len(x)], datos[len(x):]
        return salida
