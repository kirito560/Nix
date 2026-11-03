"""
Memoria a largo plazo de Nix: hechos sobre el usuario en SQLite (NIX_DATOS_DIR/<personaje>/memoria.sqlite).

  - Corto plazo: los últimos 10 turnos siguen en memoria (server.orquestador.Historial), como siempre.
  - Largo plazo (esto): hechos con tipo, confianza y fecha. Los gustos, proyectos, datos y bromas no caducan;
    los estados pasajeros («hoy estoy cansado», «estoy jugando X ahorita») caducan a las 48 h y además pesan
    menos cuanto más viejos son, para que Nix no pregunte semanas después como si siguiera pasando.
  - Búsqueda: FTS5 de SQLite (texto completo, sin acentos, con prefijos para que «jugar» encuentre «jugando»),
    ordenada por coincidencia, confianza y antigüedad. Tarda ~1 ms: va en la ruta de cada respuesta.
  - Extracción: fuera de la ruta crítica. Cada MEMORIA_CADA_TURNOS turnos (y al desconectarse) un modelo
    pequeño de Groq (MEMORIA_MODELO, openai/gpt-oss-20b, con su propio límite de uso: no le quita cuota a
    Qwen) lee lo que DIJO EL USUARIO y devuelve hechos en JSON. Nunca lee la pantalla ni la cámara.
  - Al desconectarse, un resumen corto de la sesión; al empezar la siguiente, Nix lo ve en los primeros turnos.

Ver o borrar lo que recuerda:
    .venv/bin/python -m server.memoria --listar
    .venv/bin/python -m server.memoria --buscar "minecraft"
    .venv/bin/python -m server.memoria --borrar 12
    .venv/bin/python -m server.memoria --borrar-todo
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from shared.texto import normalizar

log = logging.getLogger("nix.memoria")

TIPOS = ("gusto", "proyecto", "dato", "broma", "contexto", "estado")
TTL_ESTADO_S = 48 * 3600
VIDA_MEDIA_ESTADO_S = 12 * 3600          # un estado de hace 12 h pesa la mitad
VIDA_MEDIA_HECHO_DIAS = 180.0           # un gusto de hace medio año pesa la mitad
PARECIDO_MISMO_HECHO = 0.7              # palabras en común para tomarlo por el mismo hecho
MAX_RECUERDOS = 3
MAX_TOKENS_MEMORIA = 300               # lo que la memoria puede añadir a cada turno (aprox.)
CARACTERES_POR_TOKEN = 3.3              # español con el tokenizador de Qwen (medido con el uso que da Groq)
TURNOS_CON_RESUMEN = 3                  # el resumen de la sesión anterior solo en los primeros turnos

_NO_BUSCAR = set("""
a al algo alguien ahi ahora aqui asi aun bien cada como con cual cuando de del desde donde el ella ellos en entre
era eres es esa ese eso esta estas este esto estoy fue hay hoy la las le les lo los mas me mi mis mucho muy nada
ni no nos o otra otro para pero poco por porque que quien se ser si sin sobre solo son su sus tambien tan te
tengo ti tiene todo tu tus un una uno unos y ya yo oye nix bueno pues entonces osea neta
""".split())

ESQUEMA = """
CREATE TABLE IF NOT EXISTS hechos (
    id INTEGER PRIMARY KEY,
    texto TEXT NOT NULL,
    tipo TEXT NOT NULL,
    confianza REAL NOT NULL,
    creado REAL NOT NULL,
    visto REAL NOT NULL,
    veces INTEGER NOT NULL DEFAULT 1,
    caduca REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS hechos_fts USING fts5(
    texto, content='hechos', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS hechos_ai AFTER INSERT ON hechos BEGIN
    INSERT INTO hechos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TRIGGER IF NOT EXISTS hechos_ad AFTER DELETE ON hechos BEGIN
    INSERT INTO hechos_fts(hechos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
END;
CREATE TRIGGER IF NOT EXISTS hechos_au AFTER UPDATE OF texto ON hechos BEGIN
    INSERT INTO hechos_fts(hechos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
    INSERT INTO hechos_fts(rowid, texto) VALUES (new.id, new.texto);
END;
CREATE TABLE IF NOT EXISTS sesiones (
    id INTEGER PRIMARY KEY,
    inicio REAL NOT NULL,
    fin REAL NOT NULL,
    resumen TEXT NOT NULL
);
"""


def _raiz(palabra: str) -> str:
    """Prefijo para buscar variantes: «jugando» y «jugar» -> «juga»."""
    return palabra[:max(4, len(palabra) - 3)] if len(palabra) > 4 else palabra


def palabras_clave(texto: str) -> list[str]:
    vistas, salida = set(), []
    for p in normalizar(texto).split():
        if len(p) < 3 or p in _NO_BUSCAR or p.isdigit():
            continue
        raiz = _raiz(p)
        if raiz not in vistas:
            vistas.add(raiz)
            salida.append(raiz)
    return salida


def _parecido(a: str, b: str) -> float:
    pa, pb = set(normalizar(a).split()), set(normalizar(b).split())
    return len(pa & pb) / len(pa | pb) if pa and pb else 0.0


def _hace(segundos: float) -> str:
    if segundos < 3600:
        return "hace un rato"
    if segundos < 86400:
        return f"hace {segundos / 3600:.0f} h"
    dias = segundos / 86400
    return "ayer" if dias < 2 else f"hace {dias:.0f} días"


class Memoria:
    def __init__(self, ruta: Path | str, reloj: Callable[[], float] = time.time):
        self.ruta, self.reloj = ruta, reloj
        if ruta != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(ruta))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(ESQUEMA)
        if ruta != ":memory:":
            Path(ruta).chmod(0o600)                  # hechos personales: solo el dueño los lee

    def cerrar(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------ escribir
    def guardar(self, texto: str, tipo: str = "dato", confianza: float = 0.7) -> int:
        """Guarda un hecho; si ya había uno casi igual, lo refuerza en vez de duplicarlo. Devuelve su id."""
        texto = " ".join(texto.split()).strip()
        tipo = tipo if tipo in TIPOS else "dato"
        confianza = min(1.0, max(0.0, float(confianza)))
        ahora = self.reloj()
        caduca = ahora + TTL_ESTADO_S if tipo == "estado" else None
        for previo in self._parecidos(texto):
            if _parecido(previo["texto"], texto) >= PARECIDO_MISMO_HECHO and (previo["tipo"] == "estado") == (tipo == "estado"):
                self.db.execute("UPDATE hechos SET texto=?, confianza=MAX(confianza, ?), visto=?, veces=veces+1, "
                                "caduca=? WHERE id=?", (texto, confianza, ahora, caduca, previo["id"]))
                self.db.commit()
                return previo["id"]
        cur = self.db.execute("INSERT INTO hechos (texto, tipo, confianza, creado, visto, caduca) VALUES (?,?,?,?,?,?)",
                              (texto, tipo, confianza, ahora, ahora, caduca))
        self.db.commit()
        return cur.lastrowid

    def _parecidos(self, texto: str) -> list[sqlite3.Row]:
        claves = palabras_clave(texto)
        if not claves:
            return []
        return self.db.execute("SELECT h.* FROM hechos_fts f JOIN hechos h ON h.id = f.rowid "
                               "WHERE hechos_fts MATCH ? LIMIT 20", (_consulta(claves),)).fetchall()

    def guardar_resumen(self, inicio: float, resumen: str) -> None:
        self.db.execute("INSERT INTO sesiones (inicio, fin, resumen) VALUES (?,?,?)",
                        (inicio, self.reloj(), resumen.strip()))
        self.db.commit()

    def borrar(self, id_hecho: int) -> bool:
        cur = self.db.execute("DELETE FROM hechos WHERE id=?", (id_hecho,))
        self.db.commit()
        return cur.rowcount > 0

    def borrar_todo(self) -> None:
        self.db.execute("DELETE FROM hechos")
        self.db.execute("DELETE FROM sesiones")
        self.db.commit()

    def limpiar_caducados(self) -> int:
        cur = self.db.execute("DELETE FROM hechos WHERE caduca IS NOT NULL AND caduca < ?", (self.reloj(),))
        self.db.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ leer
    def recordar(self, texto: str, n: int = MAX_RECUERDOS) -> list[dict]:
        """Los n hechos más relevantes para `texto` (vigentes), del más al menos relevante."""
        claves = palabras_clave(texto)
        if not claves:
            return []
        ahora = self.reloj()
        filas = self.db.execute(
            "SELECT h.*, bm25(hechos_fts) AS rango FROM hechos_fts f JOIN hechos h ON h.id = f.rowid "
            "WHERE hechos_fts MATCH ? AND (h.caduca IS NULL OR h.caduca > ?) ORDER BY rango LIMIT 30",
            (_consulta(claves), ahora)).fetchall()
        puntuados = []
        for f in filas:
            edad = max(0.0, ahora - f["visto"])
            if f["tipo"] == "estado":
                peso = 0.5 ** (edad / VIDA_MEDIA_ESTADO_S)
            else:
                peso = 0.5 ** (edad / 86400 / VIDA_MEDIA_HECHO_DIAS)
            puntuados.append((-f["rango"] * f["confianza"] * peso, f))
        puntuados.sort(key=lambda x: x[0], reverse=True)
        return [dict(f) | {"edad_s": max(0.0, ahora - f["visto"])} for _, f in puntuados[:n]]

    def ultimo_resumen(self) -> Optional[dict]:
        fila = self.db.execute("SELECT * FROM sesiones ORDER BY fin DESC LIMIT 1").fetchone()
        return dict(fila) if fila else None

    def todos(self) -> list[dict]:
        return [dict(f) for f in self.db.execute("SELECT * FROM hechos ORDER BY visto DESC")]

    def para_llm(self, texto: str, con_resumen: bool = False, max_tokens: int = MAX_TOKENS_MEMORIA) -> str:
        """Recuerdos para el prompt, sin pasar de ~max_tokens: los más relevantes primero; lo que no cabe,
        fuera (el resumen de la sesión anterior se recorta)."""
        quedan = int(max_tokens * CARACTERES_POR_TOKEN)
        lineas = []
        cabecera = "Memoria relevante sobre el usuario (no la saques en cada respuesta):"
        for h in self.recordar(texto):
            cuando = f" ({_hace(h['edad_s'])}; puede que ya no sea así)" if h["tipo"] == "estado" else ""
            linea = f"- {h['texto']}{cuando}"
            if len(linea) + 1 + (0 if lineas else len(cabecera)) > quedan:
                break
            quedan -= len(linea) + 1 + (0 if lineas else len(cabecera))
            lineas.append(linea)
        partes = [cabecera + "\n" + "\n".join(lineas)] if lineas else []
        if con_resumen and (r := self.ultimo_resumen()) is not None:
            inicio = f"La última vez que hablaron ({_hace(self.reloj() - r['fin'])}): "
            if (cabe := quedan - len(inicio) - 1) >= 40:
                partes.append(inicio + _recortar(r["resumen"], cabe))
        return "\n".join(partes)


def tokens_aprox(texto: str) -> int:
    return round(len(texto) / CARACTERES_POR_TOKEN)


def _recortar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    return texto[:maximo - 1].rsplit(" ", 1)[0].rstrip(",;: ") + "…"


def _consulta(claves: list[str]) -> str:
    return " OR ".join(f'"{c}"*' for c in claves)


# ---------------------------------------------------------------------------- extracción con el LLM
PROMPT_HECHOS = f"""Extraes recuerdos para una compañera virtual llamada Nix. Te paso un trozo de conversación \
entre el usuario y Nix. Devuelve SOLO JSON: {{"hechos": [{{"texto": "...", "tipo": "...", "confianza": 0.0}}]}}.
- Solo hechos sobre el USUARIO que él mismo dijo: gustos, proyectos, datos personales que compartió, bromas \
internas y contexto de su vida. Nada de lo que dijo Nix, ni suposiciones.
- texto: una frase corta en tercera persona, en español («Le gusta el anime Frieren», «Está aprendiendo Python»).
- tipo: uno de {", ".join(TIPOS)}. Usa "estado" para lo pasajero de hoy o de ahora («Hoy está cansado», \
«Ahorita está jugando Roblox»).
- confianza: 0.9 si lo dijo claro, 0.6 si es una deducción razonable, menos si es dudoso.
- Nada de contraseñas, datos bancarios, direcciones exactas ni temas íntimos o de salud: esos no se guardan.
- Si no hay nada que valga la pena recordar, devuelve {{"hechos": []}}."""

PROMPT_RESUMEN = """Resume en una o dos frases cortas, en español y en tercera persona, de qué habló el usuario \
con Nix en esta sesión (temas, planes, cómo estaba). Solo el resumen, sin introducción."""


class ExtractorGroq:
    """Llamadas a un modelo pequeño de Groq, sin streaming (no hay prisa: va en segundo plano)."""
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, modelo: str = "openai/gpt-oss-20b", cliente=None):
        from server.pipeline import groq_http
        self.api_key, self.modelo = api_key, modelo
        self.http = cliente or groq_http.cliente()

    async def _pedir(self, sistema: str, texto: str, json_: bool) -> str:
        cuerpo = {"model": self.modelo, "temperature": 0.2, "max_completion_tokens": 800,
                  "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": texto}]}
        if self.modelo.startswith("openai/gpt-oss"):
            cuerpo["reasoning_effort"] = "low"           # razona poco (sus tokens cuentan dentro del tope)
        if json_:
            cuerpo["response_format"] = {"type": "json_object"}
        r = await self.http.post(self.URL, json=cuerpo, headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            raise RuntimeError(f"Groq respondió {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"] or ""

    async def hechos(self, conversacion: str) -> list[dict]:
        datos = json.loads(await self._pedir(PROMPT_HECHOS, conversacion, json_=True))
        return [h for h in datos.get("hechos", []) if isinstance(h, dict) and str(h.get("texto", "")).strip()]

    async def resumen(self, conversacion: str) -> str:
        return (await self._pedir(PROMPT_RESUMEN, conversacion, json_=False)).strip()


_PROHIBIDO = re.compile(r"contraseñ|password|clave|tarjeta|cuenta bancaria|\bcvv\b|\bnip\b|\bpin\b", re.I)


# Las tareas de fondo siguen aunque la conexión ya se haya cerrado (el resumen se hace al desconectarse):
# se guardan aquí para que no se pierdan (asyncio solo guarda referencias débiles a las tareas).
_TAREAS: set[asyncio.Task] = set()


def _en_segundo_plano(corrutina: Awaitable) -> None:
    tarea = asyncio.ensure_future(corrutina)
    _TAREAS.add(tarea)
    tarea.add_done_callback(_TAREAS.discard)


async def esperar_pendientes() -> None:
    """Al apagar el servidor: que terminen las extracciones y el último resumen."""
    await asyncio.gather(*_TAREAS, return_exceptions=True)


class SesionMemoria:
    """La memoria durante una conexión: recuerdos para cada respuesta y extracción en segundo plano."""

    def __init__(self, memoria: Memoria, extractor, cada_turnos: int = 3,
                 lanzar: Optional[Callable[[Awaitable], None]] = None):
        self.memoria, self.extractor, self.cada_turnos = memoria, extractor, max(1, cada_turnos)
        self.pendientes: list[tuple[str, str]] = []      # (usuario, nix) aún sin extraer
        self.todos: list[tuple[str, str]] = []           # la sesión entera (para el resumen)
        self.inicio = memoria.reloj()
        self.turnos = 0
        self.lanzar = lanzar or _en_segundo_plano

    def recuerdos(self, texto_usuario: str) -> str:
        t = time.perf_counter()
        texto = self.memoria.para_llm(texto_usuario, con_resumen=self.turnos < TURNOS_CON_RESUMEN)
        ms = (time.perf_counter() - t) * 1000
        if ms > 15:
            log.warning("🧠 buscar recuerdos tardó %.1f ms (objetivo < 15)", ms)
        return texto

    def turno(self, usuario: str, nix: str) -> None:
        """Después de cada respuesta (fuera de la ruta crítica)."""
        self.turnos += 1
        self.pendientes.append((usuario, nix))
        self.todos.append((usuario, nix))
        if len(self.pendientes) >= self.cada_turnos:
            lote, self.pendientes = self.pendientes, []
            self.lanzar(self._extraer(lote))

    async def cerrar(self) -> None:
        """Al desconectarse: lo que falte por extraer y el resumen de la sesión, en segundo plano."""
        lote, self.pendientes = self.pendientes, []
        if lote:
            self.lanzar(self._extraer(lote))
        if len(self.todos) >= 2:
            self.lanzar(self._resumir(list(self.todos)))


    @staticmethod
    def _texto(lote: list[tuple[str, str]]) -> str:
        return "\n".join(f"Usuario: {u}\nNix: {n}" for u, n in lote)

    async def _extraer(self, lote: list[tuple[str, str]]) -> None:
        if self.extractor is None:
            return
        try:
            hechos = await self.extractor.hechos(self._texto(lote))
        except Exception as e:  # noqa: BLE001 - sin red o sin cuota: no pasa nada, se pierde ese lote
            log.warning("🧠 no pude extraer recuerdos: %s", e)
            return
        guardados = 0
        for h in hechos:
            texto = str(h.get("texto", ""))
            if _PROHIBIDO.search(texto):
                continue
            try:
                confianza = float(h.get("confianza", 0.7))
            except (TypeError, ValueError):
                confianza = 0.7
            if confianza < 0.4:
                continue
            self.memoria.guardar(texto, str(h.get("tipo", "dato")), confianza)
            guardados += 1
        if guardados:
            log.info("🧠 %d recuerdo(s) guardado(s)", guardados)

    async def _resumir(self, todos: list[tuple[str, str]]) -> None:
        if self.extractor is None:
            return
        try:
            resumen = await self.extractor.resumen(self._texto(todos[-30:]))
        except Exception as e:  # noqa: BLE001
            log.warning("🧠 no pude resumir la sesión: %s", e)
            return
        if resumen:
            self.memoria.guardar_resumen(self.inicio, resumen)
            log.info("🧠 resumen de la sesión guardado")


# ---------------------------------------------------------------------------- línea de comandos
def _ruta_por_defecto() -> Path:
    from shared.config_base import cargar_env
    cargar_env()
    datos = Path(os.environ.get("NIX_DATOS_DIR", "/datos/nix"))
    return datos / os.environ.get("PERSONAJE", "nix").lower() / "memoria.sqlite"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Ver o borrar lo que Nix recuerda de ti")
    ap.add_argument("--db", type=Path, help="base de datos (por defecto, la del personaje del .env)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--listar", action="store_true", help="todos los recuerdos y el último resumen")
    g.add_argument("--buscar", metavar="TEXTO", help="los recuerdos que Nix usaría para ese tema")
    g.add_argument("--borrar", type=int, metavar="ID", help="borrar un recuerdo")
    g.add_argument("--borrar-todo", action="store_true", help="borrar todos los recuerdos y resúmenes")
    args = ap.parse_args(argv)
    m = Memoria(args.db or _ruta_por_defecto())
    try:
        if args.listar:
            ahora = m.reloj()
            for h in m.todos():
                caduca = f", caduca en {(h['caduca'] - ahora) / 3600:.0f} h" if h["caduca"] else ""
                print(f"[{h['id']}] ({h['tipo']}, confianza {h['confianza']:.1f}, visto {_hace(ahora - h['visto'])}"
                      f"{caduca}) {h['texto']}")
            if (r := m.ultimo_resumen()) is not None:
                print(f"\nÚltima sesión ({_hace(ahora - r['fin'])}): {r['resumen']}")
        elif args.buscar:
            for h in m.recordar(args.buscar):
                print(f"[{h['id']}] ({h['tipo']}) {h['texto']}")
        elif args.borrar is not None:
            print("borrado" if m.borrar(args.borrar) else "no existe ese id")
        elif args.borrar_todo:
            m.borrar_todo()
            print("memoria borrada")
    finally:
        m.cerrar()


if __name__ == "__main__":
    main()
