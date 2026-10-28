"""
Medición de latencia por turno con un registro propio de marcas de tiempo (time.monotonic del servidor).

Cada turno abre un Span y va marcando etapas. Las duraciones se calculan entre etapas consecutivas
(y el total, de la primera a la última); el Registro guarda las últimas N y calcula p50 y p95.

Etapas de la fase 1:
  voz_fin     llegó la última ventana con voz del usuario
  vad_fin     el VAD dio la frase por terminada (tras VAD_SILENCIO_MS de silencio)
  stt_final   el STT devolvió el texto
  eco         se envió el texto al cliente
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

log = logging.getLogger("nix.latencia")


def percentil(valores: list[float], p: float) -> float:
    """Percentil con interpolación lineal (igual que numpy.percentile por defecto)."""
    if not valores:
        return float("nan")
    orden = sorted(valores)
    pos = (len(orden) - 1) * p / 100
    bajo = int(pos)
    alto = min(bajo + 1, len(orden) - 1)
    return orden[bajo] + (orden[alto] - orden[bajo]) * (pos - bajo)


class Span:
    def __init__(self, turno: str):
        self.turno = turno
        self.marcas: list[tuple[str, float]] = []

    def marcar(self, etapa: str, t: Optional[float] = None) -> None:
        self.marcas.append((etapa, time.monotonic() if t is None else t))

    def duraciones_ms(self) -> dict[str, float]:
        """{"a→b": ms, ..., "total": ms}"""
        d = {f"{a}→{b}": (tb - ta) * 1000 for (a, ta), (b, tb) in zip(self.marcas, self.marcas[1:])}
        if len(self.marcas) >= 2:
            d["total"] = (self.marcas[-1][1] - self.marcas[0][1]) * 1000
        return d


class Registro:
    def __init__(self, maximo: int = 200):
        self.datos: dict[str, deque] = defaultdict(lambda: deque(maxlen=maximo))

    def cerrar(self, span: Span) -> dict[str, float]:
        duraciones = span.duraciones_ms()
        for etapa, ms in duraciones.items():
            self.datos[etapa].append(ms)
        log.info("⏱️ turno %s | %s", span.turno[:8],
                 " | ".join(f"{k} {v:.0f} ms" for k, v in duraciones.items()))
        log.info("⏱️ acumulado | %s", self.resumen())
        return duraciones

    def estadisticas(self) -> dict[str, tuple[float, float, int]]:
        """{etapa: (p50, p95, n)}"""
        return {k: (percentil(list(v), 50), percentil(list(v), 95), len(v)) for k, v in self.datos.items()}

    def resumen(self) -> str:
        return " | ".join(f"{k} p50 {p50:.0f} / p95 {p95:.0f} ms (n={n})"
                          for k, (p50, p95, n) in self.estadisticas().items())
