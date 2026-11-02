"""
Corta el texto del LLM en frases para el TTS a medida que llega.

La primera frase sale en cuanto hay un final de oración, una coma (o punto y coma, dos puntos) o
`palabras_primera` palabras completas (5), lo que llegue antes: cuanto antes empiece a sintetizarse, antes suena
la voz. Las demás esperan al final de oración; si una se alarga más de `max_largo`, se corta en el último espacio.
"""
from __future__ import annotations

import re

_FIN_ORACION = re.compile(r"[.!?…]+[\"'»)\]]*\s")
_PAUSA = re.compile(r"[,;:]\s")
_NEGRITA = re.compile(r"\*\*(.+?)\*\*")
_ACOTACION = re.compile(r"\*[^*]{1,80}\*")          # *se ríe*: nunca se lee en voz alta
_MARCAS = re.compile(r"[*_#`~]+")
_FUERA_BMP = re.compile(r"[\U00010000-\U0010FFFF]")   # emojis


def limpiar_para_voz(texto: str) -> str:
    texto = _MARCAS.sub("", _ACOTACION.sub(" ", _NEGRITA.sub(r"\1", texto)))
    texto = _FUERA_BMP.sub("", texto)
    return re.sub(r"\s+", " ", texto).strip()


def tiene_palabras(texto: str) -> bool:
    return any(c.isalnum() for c in texto)


_PALABRA_COMPLETA = re.compile(r"\S+\s+")


class Segmentador:
    def __init__(self, palabras_primera: int = 5, max_largo: int = 160):
        self.palabras_primera, self.max_largo = palabras_primera, max_largo
        self.buf = ""
        self.emitidas = 0

    def agregar(self, trozo: str) -> list[str]:
        self.buf += trozo
        frases = []
        while True:
            corte = self._corte()
            if corte is None:
                break
            frases += self._emitir(self.buf[:corte])
            self.buf = self.buf[corte:]
        return frases

    def terminar(self) -> list[str]:
        resto, self.buf = self.buf, ""
        return self._emitir(resto)

    def _corte(self) -> int | None:
        m = _FIN_ORACION.search(self.buf)
        if m:
            return m.end()
        if self.emitidas == 0:
            pausa = _PAUSA.search(self.buf)
            palabras = list(_PALABRA_COMPLETA.finditer(self.buf))
            enesima = palabras[self.palabras_primera - 1].end() if len(palabras) >= self.palabras_primera else None
            cortes = [c for c in (pausa.end() if pausa else None, enesima) if c is not None]
            if cortes:
                return min(cortes)
        if len(self.buf) > self.max_largo:
            espacio = self.buf.rfind(" ", 0, self.max_largo)
            return espacio + 1 if espacio > 0 else self.max_largo
        return None

    def _emitir(self, texto: str) -> list[str]:
        limpio = limpiar_para_voz(texto)
        if not tiene_palabras(limpio):
            return []
        self.emitidas += 1
        return [limpio]
