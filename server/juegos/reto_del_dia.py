"""
Reto del día: uno por fecha, el mismo para todos, y con el tipo rotando cada día
(trivia de anime, trivia de juegos, adivinanza, récord, pregunta polémica).

  - Dentro de cada tipo se elige al azar (semilla = fecha) entre los que no salieron en los últimos
    REPETICION_DIAS días; el elegido se guarda en SQLite para que no cambie al reiniciar.
  - Trivias y adivinanzas tienen respuesta: cada persona tiene INTENTOS intentos. Si acierta, gana ella;
    si los agota, gana el personaje. Ambos resultados van al marcador de rivalidad.
  - Récords y polémicas no tienen respuesta automática; el LLM los comenta en la conversación.
"""
from __future__ import annotations

import datetime as dt
import random
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from server.juegos.banco_retos import BANCO, Reto
from server.juegos.rivalidad import Rivalidad
from shared.texto import normalizar

TIPOS = ("trivia_anime", "adivinanza", "trivia_juegos", "record", "polemica")
INTENTOS = 3
REPETICION_DIAS = 30
JUEGO = "reto del día"


@dataclass(frozen=True)
class Respuesta:
    estado: str             # correcta | incorrecta | sin_intentos | ya_jugado | sin_respuesta
    intentos_restantes: int = 0


def es_correcta(reto: Reto, texto: str) -> bool:
    """Acierta si alguna respuesta aceptada aparece como palabras completas en lo que dijo."""
    dicho = f" {normalizar(texto)} "
    return any(re.search(rf"\b{re.escape(normalizar(r))}\b", dicho) for r in reto.respuestas)


class RetoDelDia:
    def __init__(self, ruta: Path, rivalidad: Rivalidad, banco: tuple[Reto, ...] = BANCO):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.rivalidad, self.banco = rivalidad, {r.id: r for r in banco}
        self.db = sqlite3.connect(ruta)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reto_dia (fecha TEXT PRIMARY KEY, reto_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reto_intentos (
                fecha TEXT NOT NULL, persona TEXT NOT NULL, intentos INTEGER NOT NULL DEFAULT 0,
                terminado INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (fecha, persona));
        """)

    def de_hoy(self, fecha: dt.date | None = None) -> Reto:
        fecha = fecha or dt.date.today()
        fila = self.db.execute("SELECT reto_id FROM reto_dia WHERE fecha = ?", (fecha.isoformat(),)).fetchone()
        if fila and fila[0] in self.banco:
            return self.banco[fila[0]]
        reto = self._elegir(fecha)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO reto_dia (fecha, reto_id) VALUES (?, ?)",
                            (fecha.isoformat(), reto.id))
        return reto

    def _elegir(self, fecha: dt.date) -> Reto:
        tipo = TIPOS[fecha.toordinal() % len(TIPOS)]
        candidatos = sorted((r for r in self.banco.values() if r.tipo == tipo), key=lambda r: r.id)
        desde = (fecha - dt.timedelta(days=REPETICION_DIAS)).isoformat()
        recientes = {i for (i,) in self.db.execute(
            "SELECT reto_id FROM reto_dia WHERE fecha >= ? AND fecha < ?", (desde, fecha.isoformat()))}
        libres = [r for r in candidatos if r.id not in recientes] or candidatos
        return random.Random(fecha.isoformat()).choice(libres)

    def responder(self, persona: str, nombre: str, texto: str, fecha: dt.date | None = None) -> Respuesta:
        fecha = fecha or dt.date.today()
        reto = self.de_hoy(fecha)
        if not reto.respuestas:
            return Respuesta("sin_respuesta")
        clave = (fecha.isoformat(), persona)
        fila = self.db.execute("SELECT intentos, terminado FROM reto_intentos WHERE fecha = ? AND persona = ?",
                               clave).fetchone()
        intentos, terminado = fila or (0, 0)
        if terminado:
            return Respuesta("ya_jugado")
        intentos += 1
        acierto = es_correcta(reto, texto)
        terminado = acierto or intentos >= INTENTOS
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO reto_intentos (fecha, persona, intentos, terminado) "
                            "VALUES (?, ?, ?, ?)", (*clave, intentos, int(terminado)))
        if acierto:
            self.rivalidad.registrar(persona, nombre, JUEGO, "gana_persona")
            return Respuesta("correcta")
        if terminado:
            self.rivalidad.registrar(persona, nombre, JUEGO, "gana_personaje")
            return Respuesta("sin_intentos")
        return Respuesta("incorrecta", INTENTOS - intentos)

    def texto_para_prompt(self, fecha: dt.date | None = None) -> str:
        reto = self.de_hoy(fecha)
        texto = f"Reto del día ({reto.tipo.replace('_', ' ')}): {reto.enunciado}"
        if reto.opinion:
            texto += f" Tu opinión (defiéndela, pero acepta buenos argumentos): {reto.opinion}"
        if reto.respuestas:
            texto += f" Respuesta (no la reveles hasta que alguien acierte o se rinda): {reto.respuestas[0]}."
        return texto

    def cerrar(self) -> None:
        self.db.close()
