"""
Filtros contra las "alucinaciones" de Whisper: frases que el modelo inventa en silencios, toses o ruido
porque las aprendió de subtítulos de vídeos ("Gracias.", "Subtítulos realizados por…").
Copiados de Narwell (narwell_oido.py).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from shared.texto import normalizar

ALUCINACIONES_EXACTAS = {
    "gracias", "muchas gracias", "gracias a todos", "gracias por ver", "gracias por ver el video",
    "gracias por su atencion", "gracias por mirar", "cough", "tos", "you", "thank you", "thanks for watching",
    "bye", "adios", "hasta la proxima", "chao", "suscribete", "musica", "aplausos", "risas", "silencio",
    "mmm", "hmm", "eh", "ah", "oh", "uh", "um",
}
ALUCINACIONES_PARCIALES = (
    "subtitulos realizados por", "subtitulos por la comunidad", "subtitulado por", "amara org",
    "gracias por ver el video", "suscribete al canal", "no olvides suscribirte", "dale like", "优优独播剧场",
)


def es_alucinacion(texto: str) -> bool:
    t = normalizar(texto)
    if len(t.replace(" ", "")) < 2:
        return True
    return t in ALUCINACIONES_EXACTAS or any(p in t for p in ALUCINACIONES_PARCIALES)


def duracion_voz_s(audio: np.ndarray, sr: int = 16000, relativo: float = 0.25, absoluto: float = 0.02) -> float:
    """Segundos con voz de un audio float32: ventanas de 32 ms con energía alta respecto a la más fuerte."""
    n = max(1, int(sr * 0.032))
    if len(audio) < n:
        return 0.0
    rms = np.sqrt(np.mean(np.square(audio[: len(audio) // n * n].reshape(-1, n)), axis=1))
    umbral = max(absoluto, float(rms.max()) * relativo)
    return float(np.count_nonzero(rms >= umbral)) * n / sr


def validar_transcripcion(texto: str, segmentos=None, voz_s: Optional[float] = None,
                          min_voz_s: float = 0.4, max_no_speech: float = 0.6) -> tuple[Optional[str], str]:
    """(texto, "") si vale; (None, motivo) si hay que descartarla. `segmentos`: los de Whisper con
    response_format="verbose_json"; se descarta si todos tienen no_speech_prob >= max_no_speech."""
    texto = (texto or "").strip()
    if voz_s is not None and voz_s < min_voz_s:
        return None, f"poca voz ({voz_s:.2f} s)"
    if es_alucinacion(texto):
        return None, "alucinación típica de Whisper"
    probs = [s.get("no_speech_prob") for s in (segmentos or []) if s.get("no_speech_prob") is not None]
    if probs and min(probs) >= max_no_speech:
        return None, f"no_speech_prob {min(probs):.2f}"
    return texto, ""
