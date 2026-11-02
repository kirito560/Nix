"""
Tratamiento de la señal del micrófono en el cliente, después del cancelador de eco y antes de mandarla.
Todo trabaja con tramas float mono a 16 kHz (-1..1) y guarda estado entre tramas.

  - GuardaEco: vigila al cancelador de eco. Si su salida sale más fuerte que el micrófono (el filtro se
    desajustó y mete ruido en vez de quitar eco), la limita a +3 dB y, si dura, reinicia el filtro. Cuando
    el micrófono solo trae ruido de fondo, deja pasar el micrófono tal cual. Los cambios son graduales
    (baja rápido, sube lento) para que no se oigan cortes. Registra la atenuación cada 5 s de audio.
  - PasaAltas: Butterworth de 2.º orden a 80 Hz; quita zumbido de la corriente, golpes y viento.
  - Normalizador: lleva el volumen al objetivo midiendo ventanas de 1 s, con tope de ganancia para no
    amplificar el ruido, y sin pasar de la escala (limitador).
  - Acondicionador: PasaAltas + Normalizador, en ese orden.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger("nix.audio")

SR = 16000


def db(potencia: float) -> float:
    return 10 * math.log10(max(potencia, 1e-12))


def _suavizar(actual: float, objetivo: float, tau_bajada_s: float, tau_subida_s: float, dt_s: float) -> float:
    """Filtro de un polo con constantes distintas para bajar y para subir."""
    tau = tau_bajada_s if objetivo < actual else tau_subida_s
    return objetivo + (actual - objetivo) * math.exp(-dt_s / tau)


def _rampa(x: np.ndarray, desde: float, hasta: float) -> np.ndarray:
    """Aplica una ganancia que cambia linealmente a lo largo de la trama (sin escalones audibles)."""
    if desde == hasta:
        return x * hasta
    return x * np.linspace(desde, hasta, len(x), endpoint=False)


# ---------------------------------------------------------------------------------------- eco
class GuardaEco:
    def __init__(self, max_db: float = 3.0, umbral_ruido_dbfs: float = -55.0, tau_bajada_s: float = 0.01,
                 tau_subida_s: float = 0.2, reiniciar_tras_s: float = 0.5, informe_cada_s: float = 5.0,
                 sr: int = SR):
        self.max_db, self.umbral = max_db, umbral_ruido_dbfs
        self.tau_bajada, self.tau_subida = tau_bajada_s, tau_subida_s
        self.reiniciar_tras_s, self.informe_cada_s, self.sr = reiniciar_tras_s, informe_cada_s, sr
        self.ganancia = 1.0              # corrección aplicada a la salida del cancelador
        self.mezcla = 1.0                # 1 = salida del cancelador, 0 = micrófono tal cual (solo ruido)
        self.desajuste_s = 0.0           # tiempo seguido con la salida por encima de max_db
        self.reinicios = 0
        self._informe = [0.0, 0.0, 0.0]  # energía del micrófono, energía de la salida, segundos medidos
        self.atenuaciones_db: list[float] = []

    def procesar(self, mic: np.ndarray, limpio: np.ndarray, aec=None, adaptando: bool = False) -> np.ndarray:
        """`mic`: la trama antes del cancelador; `limpio`: la que devolvió. `aec`: el cancelador, para
        reiniciarlo si se desajusta. `adaptando`: si sonaba la voz de Nix (solo entonces se mide el eco)."""
        dt = len(mic) / self.sr
        e_mic = float(np.dot(mic, mic)) / max(len(mic), 1)
        e_out = float(np.dot(limpio, limpio)) / max(len(limpio), 1)
        solo_ruido = db(e_mic) < self.umbral
        exceso_db = db(e_out) - db(e_mic) - self.max_db

        objetivo = 10 ** (-exceso_db / 20) if exceso_db > 0 and not solo_ruido else 1.0
        anterior_g, anterior_m = self.ganancia, self.mezcla
        self.ganancia = _suavizar(self.ganancia, objetivo, self.tau_bajada, self.tau_subida, dt)
        self.mezcla = _suavizar(self.mezcla, 0.0 if solo_ruido else 1.0, self.tau_subida, self.tau_bajada, dt)

        if exceso_db > 0 and not solo_ruido and adaptando:
            self.desajuste_s += dt
            if aec is not None and self.desajuste_s >= self.reiniciar_tras_s:
                aec.reiniciar()
                self.reinicios += 1
                self.desajuste_s = 0.0
                log.warning("🎧 el cancelador de eco se desajustó (salía %.0f dB más fuerte que el micrófono): "
                            "lo reinicio", exceso_db + self.max_db)
        else:
            self.desajuste_s = 0.0

        salida = (_rampa(limpio, anterior_g, self.ganancia) * np.linspace(anterior_m, self.mezcla, len(mic),
                                                                          endpoint=False)
                  + mic * (1 - np.linspace(anterior_m, self.mezcla, len(mic), endpoint=False)))
        if adaptando and not solo_ruido:
            self._medir(e_mic * len(mic), float(np.dot(salida, salida)), dt)
        return salida

    def _medir(self, e_mic: float, e_out: float, dt: float) -> None:
        self._informe[0] += e_mic
        self._informe[1] += e_out
        self._informe[2] += dt
        if self._informe[2] >= self.informe_cada_s:
            atenuacion = db(self._informe[0]) - db(self._informe[1])
            self.atenuaciones_db.append(atenuacion)
            log.info("🎧 eco atenuado %.0f dB (últimos %.0f s con su voz sonando)", atenuacion, self._informe[2])
            self._informe = [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------------------- señal
class PasaAltas:
    """Biquad Butterworth (Q = 0,707) a `corte_hz`; coeficientes del «Audio EQ Cookbook»."""

    def __init__(self, corte_hz: float = 80.0, sr: int = SR):
        w0 = 2 * math.pi * corte_hz / sr
        alfa = math.sin(w0) / (2 * math.sqrt(0.5))
        c = math.cos(w0)
        a0 = 1 + alfa
        self.b = ((1 + c) / 2 / a0, -(1 + c) / a0, (1 + c) / 2 / a0)
        self.a = (-2 * c / a0, (1 - alfa) / a0)
        self.estado = [0.0, 0.0, 0.0, 0.0]          # x[n-1], x[n-2], y[n-1], y[n-2]

    def procesar(self, x: np.ndarray) -> np.ndarray:
        b0, b1, b2 = self.b
        a1, a2 = self.a
        x1, x2, y1, y2 = self.estado
        y = np.empty(len(x))
        for i, xn in enumerate(np.asarray(x, dtype=np.float64).tolist()):
            yn = b0 * xn + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xn, y1, yn
            y[i] = yn
        self.estado = [x1, x2, y1, y2]
        return y


class Normalizador:
    """Ganancia automática por ventanas de 1 s.

    Mide el RMS del último segundo y busca la ganancia que lo deja en `objetivo_dbfs`, limitada entre
    `min_ganancia_db` y `max_ganancia_db` (el tope evita convertir el ruido en «voz»). Si el último segundo es
    solo ruido (por debajo de `umbral_ruido_dbfs`), la ganancia se queda donde estaba. Baja rápido y sube
    lento, y nunca deja que una trama pase de `techo` (limitador de picos)."""

    def __init__(self, objetivo_dbfs: float = -23.0, max_ganancia_db: float = 12.0, min_ganancia_db: float = -12.0,
                 umbral_ruido_dbfs: float = -50.0, ventana_s: float = 1.0, tau_bajada_s: float = 0.1,
                 tau_subida_s: float = 1.0, techo: float = 0.9, sr: int = SR):
        self.objetivo, self.umbral = objetivo_dbfs, umbral_ruido_dbfs
        self.max_g, self.min_g = 10 ** (max_ganancia_db / 20), 10 ** (min_ganancia_db / 20)
        self.tau_bajada, self.tau_subida, self.techo, self.sr = tau_bajada_s, tau_subida_s, techo, sr
        self.ventana_s = ventana_s
        self.energias: deque = deque()             # (energía, muestras) de las tramas del último segundo
        self.muestras = 0
        self.ganancia = 1.0

    def rms_ventana_dbfs(self) -> float:
        return db(sum(e for e, _ in self.energias) / max(self.muestras, 1))

    def procesar(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        self.energias.append((float(np.dot(x, x)), len(x)))
        self.muestras += len(x)
        while self.muestras - self.energias[0][1] >= self.ventana_s * self.sr:
            self.muestras -= self.energias.popleft()[1]

        anterior = self.ganancia
        nivel = self.rms_ventana_dbfs()
        if nivel >= self.umbral:
            objetivo = min(self.max_g, max(self.min_g, 10 ** ((self.objetivo - nivel) / 20)))
            self.ganancia = _suavizar(self.ganancia, objetivo, self.tau_bajada, self.tau_subida, len(x) / self.sr)
        pico = float(np.max(np.abs(x))) if len(x) else 0.0
        if pico * self.ganancia > self.techo:                 # limitador: esta trama no se satura
            self.ganancia = self.techo / pico
            anterior = min(anterior, self.ganancia)
        return np.clip(_rampa(x, anterior, self.ganancia), -1.0, 1.0)


class Acondicionador:
    """Lo que se le hace a la voz antes de mandarla al servidor: pasa-altas y luego normalización."""

    def __init__(self, pasa_altas: Optional[PasaAltas] = None, normalizador: Optional[Normalizador] = None):
        self.pasa_altas = pasa_altas or PasaAltas()
        self.normalizador = normalizador or Normalizador()

    def procesar(self, x: np.ndarray) -> np.ndarray:
        return self.normalizador.procesar(self.pasa_altas.procesar(x))
