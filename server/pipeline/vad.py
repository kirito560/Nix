"""
Detección de voz (VAD) sobre el audio que llega del cliente en tramas de 20 ms.

Silero VAD trabaja con ventanas de 32 ms (512 muestras a 16 kHz), así que las tramas se re-agrupan.
Cada ventana recibe una probabilidad de voz:
  - PuntuadorSilero: Silero VAD en ONNX (onnxruntime, sin torch). Modelo en SILERO_ONNX
    (https://github.com/snakers4/silero-vad, src/silero_vad/data/silero_vad.onnx).
  - PuntuadorEnergia: por volumen (RMS), sin dependencias. Respaldo si no hay modelo; también en tests.
DetectorVoz convierte esas probabilidades en eventos: inicio de frase, ventana de voz y fin de frase.

Copiado y adaptado de Narwell (narwell_oido.py), cuya máquina de estados es una traducción de AIRI
(https://github.com/moeru-ai/airi, packages/stage-ui/src/libs/voice/voice-activity-plugin.ts):
umbral, 30 % para mantener la frase, silencio de cierre, mínimo de voz y pre-roll.

MIT License - Copyright (c) 2024-PRESENT Neko Ayaka
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions: The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

log = logging.getLogger("nix.vad")

SR = 16000
VENTANA = 512                    # 32 ms a 16 kHz (lo que pide Silero)
MS_VENTANA = VENTANA * 1000 / SR


class PuntuadorEnergia:
    """Probabilidad de voz por volumen: 0 por debajo de `umbral` (RMS de 0 a 1), 1 a partir de `pleno`."""

    def __init__(self, umbral: float = 0.010, pleno: float = 0.040):
        self.umbral, self.pleno = umbral, pleno

    def puntuar(self, ventana: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(ventana))))
        return float(np.clip((rms - self.umbral) / max(self.pleno - self.umbral, 1e-6), 0.0, 1.0))

    def reiniciar(self) -> None:
        pass


class PuntuadorSilero:
    """Silero VAD v5 en ONNX. Igual que el envoltorio oficial: estado (2,1,128) que se arrastra entre
    ventanas y 64 muestras de contexto de la ventana anterior delante de cada una."""

    CONTEXTO = 64

    def __init__(self, ruta_modelo: str):
        import onnxruntime as ort
        opciones = ort.SessionOptions()
        opciones.inter_op_num_threads = opciones.intra_op_num_threads = 1
        self.sesion = ort.InferenceSession(ruta_modelo, sess_options=opciones, providers=["CPUExecutionProvider"])
        self.sr = np.array(SR, dtype=np.int64)
        self.reiniciar()

    def reiniciar(self) -> None:
        self.estado = np.zeros((2, 1, 128), dtype=np.float32)
        self.contexto = np.zeros(self.CONTEXTO, dtype=np.float32)

    def puntuar(self, ventana: np.ndarray) -> float:
        x = np.concatenate([self.contexto, ventana])[None, :]
        salida, self.estado = self.sesion.run(None, {"input": x, "state": self.estado, "sr": self.sr})
        self.contexto = ventana[-self.CONTEXTO:]
        return float(np.ravel(salida)[0])


def crear_puntuador(ruta_silero: str):
    """Silero si existe el modelo; si no, energía (con aviso)."""
    if ruta_silero and os.path.isfile(ruta_silero):
        try:
            p = PuntuadorSilero(ruta_silero)
            log.info("👂 VAD: Silero (%s)", os.path.basename(ruta_silero))
            return p
        except Exception as e:  # noqa: BLE001
            log.warning("👂 No pude cargar Silero (%s); uso VAD por energía", e)
    else:
        log.warning("👂 No encuentro el modelo Silero en %r; uso VAD por energía", ruta_silero)
    return PuntuadorEnergia()


@dataclass
class EventoVoz:
    """tipo: "inicio" (audio = pre-roll + primera ventana), "voz" (una ventana), "fin" (frase válida)
    o "descartada" (demasiado corta). t_ultima_voz: reloj del servidor al recibir la última ventana con voz."""
    tipo: str
    audio: Optional[np.ndarray] = None
    t_ultima_voz: float = 0.0


class Reagrupador:
    """Junta tramas PCM16 de cualquier tamaño en ventanas float32 de VENTANA muestras."""

    def __init__(self):
        self._resto = np.zeros(0, dtype=np.float32)

    def agregar(self, pcm16: bytes) -> list[np.ndarray]:
        nuevo = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        datos = np.concatenate([self._resto, nuevo])
        n = len(datos) // VENTANA
        self._resto = datos[n * VENTANA:]
        return [datos[i * VENTANA:(i + 1) * VENTANA] for i in range(n)]


class DetectorVoz:
    def __init__(self, puntuador, umbral: float = 0.5, silencio_ms: float = 400, min_voz_ms: float = 250,
                 pre_roll_ms: float = 360, max_frase_s: float = 18.0):
        self.p = puntuador
        self.umbral, self.mantener = umbral, umbral * 0.3       # AIRI: una frase en curso sigue con el 30 %
        self.silencio_ms, self.min_voz_ms, self.max_frase_s = silencio_ms, min_voz_ms, max_frase_s
        self.pre: deque = deque(maxlen=max(1, int(pre_roll_ms / MS_VENTANA)))
        self._reset()

    def _reset(self) -> None:
        self.en_frase = False
        self.audio: list = []
        self.ms_voz = self.ms_silencio = 0.0
        self.t_ultima_voz = 0.0

    def procesar(self, ventana: np.ndarray, ahora: float) -> list[EventoVoz]:
        score = self.p.puntuar(ventana)
        if not self.en_frase:
            if score < self.umbral:
                self.pre.append(ventana)
                return []
            self.en_frase = True
            self.audio = list(self.pre) + [ventana]          # pre-roll: no cortar el arranque
            self.pre.clear()
            self.ms_voz, self.ms_silencio, self.t_ultima_voz = MS_VENTANA, 0.0, ahora
            return [EventoVoz("inicio", np.concatenate(self.audio), ahora)]
        self.audio.append(ventana)
        eventos = [EventoVoz("voz", ventana)]
        if score >= self.mantener:
            self.ms_voz += MS_VENTANA
            self.ms_silencio, self.t_ultima_voz = 0.0, ahora
        else:
            self.ms_silencio += MS_VENTANA
        largo_s = len(self.audio) * MS_VENTANA / 1000
        if self.ms_silencio >= self.silencio_ms or largo_s >= self.max_frase_s:
            corta = self.ms_voz < self.min_voz_ms
            evento = EventoVoz("descartada" if corta else "fin", np.concatenate(self.audio), self.t_ultima_voz)
            self._reset()
            self.p.reiniciar()
            eventos.append(evento)
        return eventos
