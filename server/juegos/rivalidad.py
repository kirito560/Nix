"""
Marcador de rivalidad: cuántos retos le ha ganado y perdido el personaje a cada persona.

Se guarda en SQLite dentro de la carpeta de datos del personaje, así que sobrevive entre conversaciones
y no se mezcla con otros personajes. Cada persona se identifica con "canal:id" (p. ej. "telegram:123",
"twitch:kirito560_", "minecraft:Kirito560"); el nombre visible es el último con el que se le vio.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

RESULTADOS = ("gana_personaje", "gana_persona", "empate")


@dataclass
class Marcador:
    persona: str
    nombre: str
    gana_personaje: int = 0
    gana_persona: int = 0
    empates: int = 0
    por_juego: dict[str, tuple[int, int, int]] = field(default_factory=dict)   # juego -> (personaje, persona, empates)
    racha: int = 0          # >0: victorias seguidas del personaje; <0: de la persona

    @property
    def partidas(self) -> int:
        return self.gana_personaje + self.gana_persona + self.empates


class Rivalidad:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(ruta)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS partidas (
                id INTEGER PRIMARY KEY,
                persona TEXT NOT NULL,
                nombre TEXT NOT NULL,
                juego TEXT NOT NULL,
                resultado TEXT NOT NULL CHECK (resultado IN ('gana_personaje', 'gana_persona', 'empate')),
                ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS partidas_persona ON partidas (persona, id);
        """)

    def registrar(self, persona: str, nombre: str, juego: str, resultado: str, ts: float | None = None) -> Marcador:
        if resultado not in RESULTADOS:
            raise ValueError(f"resultado {resultado!r} no válido; usa uno de {RESULTADOS}")
        with self.db:
            self.db.execute("INSERT INTO partidas (persona, nombre, juego, resultado, ts) VALUES (?, ?, ?, ?, ?)",
                            (persona, nombre, juego.lower(), resultado, time.time() if ts is None else ts))
        return self.marcador(persona)

    def marcador(self, persona: str) -> Marcador:
        filas = self.db.execute("SELECT nombre, juego, resultado FROM partidas WHERE persona = ? ORDER BY id",
                                (persona,)).fetchall()
        m = Marcador(persona=persona, nombre=filas[-1][0] if filas else persona)
        por_juego: dict[str, list[int]] = {}
        for _, juego, resultado in filas:
            i = RESULTADOS.index(resultado)
            por_juego.setdefault(juego, [0, 0, 0])[i] += 1
            if resultado == "gana_personaje":
                m.gana_personaje += 1
                m.racha = m.racha + 1 if m.racha > 0 else 1
            elif resultado == "gana_persona":
                m.gana_persona += 1
                m.racha = m.racha - 1 if m.racha < 0 else -1
            else:
                m.empates += 1
                m.racha = 0
        m.por_juego = {j: tuple(v) for j, v in por_juego.items()}
        return m

    def tabla(self, limite: int = 10) -> list[Marcador]:
        """Las personas con más partidas contra el personaje."""
        personas = [p for (p,) in self.db.execute(
            "SELECT persona FROM partidas GROUP BY persona ORDER BY COUNT(*) DESC, MAX(id) DESC LIMIT ?", (limite,))]
        return [self.marcador(p) for p in personas]

    def texto_para_prompt(self, persona: str, nombre_personaje: str) -> str:
        """Una línea para el contexto del LLM; vacía si nunca han jugado."""
        m = self.marcador(persona)
        if not m.partidas:
            return ""
        if m.gana_personaje > m.gana_persona:
            quien = f"va ganando {nombre_personaje}"
        elif m.gana_persona > m.gana_personaje:
            quien = f"va ganando {m.nombre}"
        else:
            quien = "van empatados"
        texto = (f"Marcador de rivalidad con {m.nombre}: {nombre_personaje} {m.gana_personaje} - "
                 f"{m.gana_persona} {m.nombre}" + (f" ({m.empates} empates)" if m.empates else "") + f"; {quien}.")
        if abs(m.racha) >= 2:
            ganador = nombre_personaje if m.racha > 0 else m.nombre
            texto += f" Racha: {ganador} lleva {abs(m.racha)} seguidas."
        return texto

    def cerrar(self) -> None:
        self.db.close()
