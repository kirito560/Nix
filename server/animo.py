"""
Estado de ánimo simple de Nix: tres valores entre 0 y 1 que solo cambian el TONO del texto (una línea en el prompt
de sistema). No cambian la voz todavía ni se guardan: viven en la sesión y se olvidan al desconectarse.

  - energía: qué tan animada habla.
  - paciencia: baja si la tratan mal o se frustran con ella; con poca, responde más breve pero siempre amable.
  - cercanía: sube con risas, cariño y agradecimientos.

Cada respuesta los mueve poco (como mucho MAX_CAMBIO por valor) según señales sencillas de lo que dijo el
usuario, y con el tiempo vuelven despacio a su valor base (la distancia a la base se reduce a la mitad cada
VIDA_MEDIA_S segundos).
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from server.tono import expresion_hostil
from shared.texto import normalizar

BASE = {"energia": 0.7, "paciencia": 0.7, "cercania": 0.5}
MAX_CAMBIO = 0.1
VIDA_MEDIA_S = 600.0

# (palabras o frases normalizadas, cambios). Se suman todas las que aparezcan; luego se limita a ±MAX_CAMBIO.
SENALES: tuple[tuple[tuple[str, ...], dict[str, float]], ...] = (
    (("jaja", "jeje", "xd", "gg", "genial", "chido", "increible", "que buena", "bien hecho", "vamos"),
     {"energia": 0.05, "cercania": 0.03}),
    (("gracias", "te quiero", "eres la mejor", "buena onda", "me caes bien", "te extrane"),
     {"cercania": 0.08}),
    (("cansado", "cansada", "sueno", "me voy a dormir", "agotado", "agotada", "aburrido", "aburrida"),
     {"energia": -0.06}),
    (("no sirves", "no entiendes nada", "que fastidio", "ya basta", "eres aburrida", "me desesperas"),
     {"paciencia": -0.08, "cercania": -0.03}),
)


def _contiene(texto: str, frase: str) -> bool:
    return f" {frase} " in f" {texto} "


class Animo:
    def __init__(self, base: Optional[dict[str, float]] = None, max_cambio: float = MAX_CAMBIO,
                 vida_media_s: float = VIDA_MEDIA_S, reloj: Callable[[], float] = time.monotonic):
        self.base = dict(base or BASE)
        self.valores = dict(self.base)
        self.max_cambio, self.vida_media_s, self.reloj = max_cambio, vida_media_s, reloj
        self.t = reloj()

    def _relajar(self, ahora: float) -> None:
        """Acerca cada valor a su base según el tiempo pasado."""
        factor = 0.5 ** (max(0.0, ahora - self.t) / self.vida_media_s)
        for k, base in self.base.items():
            self.valores[k] = base + (self.valores[k] - base) * factor
        self.t = ahora

    def tras_turno(self, texto_usuario: str, ahora: Optional[float] = None) -> dict[str, float]:
        """Ajusta el ánimo después de una respuesta. Devuelve el cambio aplicado a cada valor."""
        self._relajar(self.reloj() if ahora is None else ahora)
        texto = normalizar(texto_usuario or "")
        cambios = {k: 0.01 if k == "cercania" else 0.0 for k in self.valores}   # platicar acerca un poquito
        for frases, delta in SENALES:
            if any(_contiene(texto, f) for f in frases):
                for k, d in delta.items():
                    cambios[k] += d
        if expresion_hostil(texto_usuario or ""):
            cambios["paciencia"] -= 0.08
            cambios["cercania"] -= 0.03
        aplicados = {}
        for k, d in cambios.items():
            d = max(-self.max_cambio, min(self.max_cambio, d))
            nuevo = max(0.0, min(1.0, self.valores[k] + d))
            aplicados[k] = nuevo - self.valores[k]
            self.valores[k] = nuevo
        return aplicados

    def actuales(self, ahora: Optional[float] = None) -> dict[str, float]:
        self._relajar(self.reloj() if ahora is None else ahora)
        return dict(self.valores)

    def para_llm(self, ahora: Optional[float] = None) -> str:
        v = self.actuales(ahora)
        energia, paciencia, cercania = (round(v[k] * 100) for k in ("energia", "paciencia", "cercania"))
        tono = []
        if v["energia"] <= 0.33:
            tono.append("energía baja: más tranquila y pausada")
        elif v["energia"] >= 0.66:
            tono.append("con mucha energía")
        if v["paciencia"] < 0.5:
            tono.append("poca paciencia: más seca o sarcástica y más breve, pero siempre amable, nunca pesada")
        else:
            tono.append("paciente")
        if v["cercania"] >= 0.66:
            tono.append("cercanía alta: más cálida y cómplice")
        elif v["cercania"] <= 0.33:
            tono.append("un poco más reservada")
        return (f"Estado actual -> Energía: {energia}/100 | Paciencia: {paciencia}/100 | Cercanía: {cercania}/100. "
                f"Deja que estos números tiñan tu tono ({', '.join(tono)}); no los menciones.")
