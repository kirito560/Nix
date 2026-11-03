"""
Emoción de cada respuesta: el LLM empieza con una etiqueta entre corchetes ([neutral], [burla], [animada],
[cansada], [sorprendida] o [cercana]). Aquí se quita del texto antes de que llegue al Segmentador (nunca se
dice en voz alta ni va al historial) y se traduce a la prosodia de edge-tts (rate, pitch y volume de Dalia).

El texto llega en trozos («[bur», «la] Oye...»), así que ExtractorEmocion guarda lo que va desde un «[» hasta
su «]» (o hasta MAX_ETIQUETA caracteres; si no se cierra, era texto normal y se suelta tal cual). Cualquier
acotación corta entre corchetes, también a media respuesta («[risas]»), se quita: la voz no la leería bien.
Solo la primera etiqueta conocida decide la emoción; si no hay, la respuesta es [neutral].
"""
from __future__ import annotations

import re
from dataclasses import dataclass

EMOCIONES = ("neutral", "burla", "animada", "cansada", "sorprendida", "cercana")
NEUTRAL = "neutral"
MAX_ETIQUETA = 16
_ACOTACION = re.compile(r"\[\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]{1,14})\s*\]")


@dataclass(frozen=True)
class Prosodia:
    """Cambios sobre la voz base, en el formato de edge-tts: rate en %, pitch en Hz y volume en %."""
    rate: int = 0
    pitch: int = 0
    volume: int = 0

    def edge(self, velocidad_base: str = "+0%") -> tuple[str, str, str]:
        """(rate, volume, pitch) para edge-tts, sumando la velocidad base del .env (TTS_VELOCIDAD)."""
        rate = _porcentaje(velocidad_base) + self.rate
        return f"{rate:+d}%", f"{self.volume:+d}%", f"{self.pitch:+d}Hz"


PROSODIAS: dict[str, Prosodia] = {
    "neutral": Prosodia(),
    "burla": Prosodia(rate=8, pitch=4),
    "animada": Prosodia(rate=10, pitch=8, volume=8),
    "cansada": Prosodia(rate=-10, pitch=-6, volume=-8),
    "sorprendida": Prosodia(rate=6, pitch=12, volume=5),
    "cercana": Prosodia(rate=-4, pitch=-2, volume=-4),
}


def prosodia(emocion: str) -> Prosodia:
    return PROSODIAS.get(emocion, PROSODIAS[NEUTRAL])


def _porcentaje(texto: str) -> int:
    try:
        return int(texto.strip().rstrip("%") or 0)
    except ValueError:
        return 0


class ExtractorEmocion:
    def __init__(self):
        self.emocion: str | None = None      # None: todavía no se sabe (puede venir la etiqueta)
        self.pendiente = ""                  # desde un «[» sin cerrar
        self.hubo_texto = False
        self.etiquetada = False              # la emoción salió de una etiqueta del LLM

    @property
    def decidida(self) -> str:
        return self.emocion or NEUTRAL

    def agregar(self, trozo: str) -> str:
        """Devuelve el texto limpio que ya se puede pasar al Segmentador."""
        texto, self.pendiente = self.pendiente + trozo, ""
        salida = []
        while texto:
            i = texto.find("[")
            if i < 0:
                salida.append(texto)
                break
            salida.append(texto[:i])
            j = texto.find("]", i)
            if j < 0:
                if len(texto) - i <= MAX_ETIQUETA:
                    self.pendiente = texto[i:]           # puede ser una etiqueta partida en dos trozos
                    break
                salida.append(texto[i])                  # un «[» que no cierra: texto normal
                texto = texto[i + 1:]
                continue
            etiqueta = texto[i:j + 1]
            if (m := _ACOTACION.fullmatch(etiqueta)) is None:
                salida.append(texto[i])
                texto = texto[i + 1:]
                continue
            nombre = m.group(1).strip().lower()
            if self.emocion is None and nombre in EMOCIONES and not self._ya_hablo(salida):
                self.emocion, self.etiquetada = nombre, True
            texto = texto[j + 1:]
        limpio = "".join(salida)
        if not self.hubo_texto:
            limpio = limpio.lstrip()                     # el espacio tras la etiqueta no va a la voz
        if limpio.strip():
            self.hubo_texto = True
            if self.emocion is None:
                self.emocion = NEUTRAL                   # habló sin etiqueta: ya no se espera
        return limpio

    def _ya_hablo(self, salida: list[str]) -> bool:
        return self.hubo_texto or bool("".join(salida).strip())

    def terminar(self) -> str:
        """Al acabar el stream: lo que quedó guardado era texto normal (un «[» sin cerrar)."""
        resto, self.pendiente = self.pendiente, ""
        if self.emocion is None:
            self.emocion = NEUTRAL
        return resto if self.hubo_texto else resto.lstrip()
