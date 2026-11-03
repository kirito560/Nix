"""Memoria de largo plazo: SQLite + FTS5, estados que caducan, recuerdos en < 15 ms y extracción en segundo plano."""
import asyncio
import json
import logging
import time

import httpx
import pytest

from server.memoria import (TTL_ESTADO_S, ExtractorGroq, Memoria, SesionMemoria, esperar_pendientes, main,
                            palabras_clave)
from tests.test_orquestador import LLMFijo, orquestador, span

DIA = 86400.0


class Reloj:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def memoria(reloj=None) -> Memoria:
    return Memoria(":memory:", reloj or Reloj())


# ---------------------------------------------------------------- guardar y buscar
def test_recuerda_lo_relevante_al_tema():
    m = memoria()
    m.guardar("Le gusta el anime Frieren", "gusto", 0.9)
    m.guardar("Está aprendiendo a programar en Python", "proyecto", 0.9)
    m.guardar("Su juego favorito es Minecraft", "gusto", 0.9)
    m.guardar("Tiene un gato que se llama Michi", "dato", 0.9)
    assert [h["texto"] for h in m.recordar("¿qué anime me recomiendas?")] == ["Le gusta el anime Frieren"]
    assert m.recordar("ayer estuve programando un bot")[0]["texto"].startswith("Está aprendiendo a programar")
    assert m.recordar("jugando minecraft con mis amigos")[0]["texto"] == "Su juego favorito es Minecraft"
    assert m.recordar("hola") == []                      # sin palabras con contenido: nada


def test_como_maximo_tres_y_sin_acentos():
    m = memoria()
    for texto in ["Escucha música electrónica al programar", "Su música favorita es el techno",
                  "Quiere aprender a hacer música en FL Studio", "Fue a un festival de música en Monterrey",
                  "Odia la música de banda"]:
        m.guardar(texto, "gusto", 0.8)
    recuerdos = m.recordar("¿qué musica pongo?")
    assert len(recuerdos) == 3


def test_el_mismo_hecho_no_se_duplica():
    m = memoria()
    a = m.guardar("Le gusta el anime Frieren", "gusto", 0.6)
    b = m.guardar("Le gusta mucho el anime Frieren", "gusto", 0.9)
    assert a == b and len(m.todos()) == 1
    h = m.todos()[0]
    assert h["veces"] == 2 and h["confianza"] == 0.9 and h["texto"] == "Le gusta mucho el anime Frieren"


def test_los_estados_caducan_a_las_48_horas():
    reloj = Reloj()
    m = memoria(reloj)
    m.guardar("Hoy está cansado por el examen", "estado", 0.9)
    m.guardar("Le gusta estudiar de noche para el examen", "gusto", 0.9)
    assert len(m.recordar("examen")) == 2
    reloj.t += TTL_ESTADO_S + 1
    assert [h["tipo"] for h in m.recordar("examen")] == ["gusto"]   # el estado ya no sale
    assert m.limpiar_caducados() == 1 and len(m.todos()) == 1
    reloj.t += 365 * DIA
    assert len(m.recordar("examen")) == 1                 # los gustos no caducan


def test_un_estado_viejo_pesa_menos_que_uno_nuevo():
    reloj = Reloj()
    m = memoria(reloj)
    m.guardar("Ahorita está jugando Roblox con su primo", "estado", 0.9)
    reloj.t += 30 * 3600
    m.guardar("Ahorita está jugando Fortnite en ranked", "estado", 0.9)
    recuerdos = m.recordar("¿qué estás jugando?")
    assert recuerdos[0]["texto"].endswith("ranked")
    assert "puede que ya no sea así" in m.para_llm("jugando")


def test_buscar_tarda_menos_de_15_ms_con_miles_de_recuerdos(tmp_path):
    m = Memoria(tmp_path / "memoria.sqlite")
    temas = ["anime", "minecraft", "python", "pizza", "examen", "música", "gato", "roblox", "terror", "karaoke"]
    for i in range(3000):
        m.db.execute("INSERT INTO hechos (texto, tipo, confianza, creado, visto) VALUES (?,?,?,?,?)",
                     (f"Hecho {i} sobre {temas[i % 10]} y {temas[(i * 7) % 10]} número {i}", "dato", 0.8,
                      time.time(), time.time()))
    m.db.commit()
    tiempos = []
    for frase in ["¿Te gusta el anime de terror?", "ayer jugué minecraft y comí pizza", "voy a estudiar python"]:
        t = time.perf_counter()
        assert len(m.recordar(frase)) == 3
        tiempos.append((time.perf_counter() - t) * 1000)
    assert max(tiempos) < 15, tiempos


def test_la_base_queda_solo_para_el_dueno(tmp_path):
    ruta = tmp_path / "nix" / "memoria.sqlite"
    Memoria(ruta).guardar("Le gusta el anime", "gusto")
    assert oct(ruta.stat().st_mode & 0o777) == "0o600"


def test_palabras_clave():
    assert palabras_clave("Oye Nix, ¿qué estás jugando ahorita?") == ["juga", "ahor"]
    assert palabras_clave("y de la") == []


# ---------------------------------------------------------------- sesión: extracción en segundo plano
class ExtractorFalso:
    def __init__(self, hechos=None, falla=False):
        self.lotes, self.resumenes = [], []
        self.respuesta, self.falla = hechos or [], falla

    async def hechos(self, conversacion):
        self.lotes.append(conversacion)
        if self.falla:
            raise RuntimeError("sin red")
        return self.respuesta

    async def resumen(self, conversacion):
        self.resumenes.append(conversacion)
        return "Habló de Minecraft y de su examen."


async def test_extrae_cada_tres_turnos_y_resume_al_cerrar():
    m = memoria()
    ex = ExtractorFalso([{"texto": "Tiene examen de mate el lunes", "tipo": "estado", "confianza": 0.9},
                         {"texto": "Su contraseña es 1234", "tipo": "dato", "confianza": 0.9},     # no se guarda
                         {"texto": "Quizá le gusta el jazz", "tipo": "gusto", "confianza": 0.2}])  # muy dudoso
    s = SesionMemoria(m, ex, cada_turnos=3)
    s.turno("hola", "¡Qué onda!")
    s.turno("tengo examen de mate el lunes", "Uy, suerte.")
    assert ex.lotes == []
    s.turno("y no he estudiado", "Pues a darle.")
    await esperar_pendientes()
    assert len(ex.lotes) == 1 and "Usuario: tengo examen" in ex.lotes[0] and "Nix: Uy, suerte." in ex.lotes[0]
    assert [h["texto"] for h in m.todos()] == ["Tiene examen de mate el lunes"]
    assert m.todos()[0]["caduca"] is not None
    s.turno("me voy", "Va, bye.")
    await s.cerrar()
    await esperar_pendientes()
    assert len(ex.lotes) == 2 and len(ex.resumenes) == 1
    assert m.ultimo_resumen()["resumen"] == "Habló de Minecraft y de su examen."


async def test_si_falla_la_extraccion_no_pasa_nada(caplog):
    s = SesionMemoria(memoria(), ExtractorFalso(falla=True), cada_turnos=1)
    s.turno("hola", "qué onda")
    await esperar_pendientes()
    assert "no pude extraer" in caplog.text


async def test_el_resumen_anterior_solo_en_los_primeros_turnos():
    reloj = Reloj()
    m = memoria(reloj)
    m.guardar_resumen(reloj.t - 3600, "Habló de su examen de mate.")
    reloj.t += DIA
    s = SesionMemoria(m, None, cada_turnos=99)
    assert "La última vez que hablaron (ayer): Habló de su examen" in s.recuerdos("hola")
    for _ in range(3):
        s.turno("hola", "qué onda")
    assert "La última vez" not in s.recuerdos("hola")


async def test_el_orquestador_inyecta_recuerdos_y_avisa_cada_turno():
    m = memoria()
    m.guardar("Su anime favorito es Frieren", "gusto", 0.9)
    ex = ExtractorFalso()
    llm = LLMFijo("[animada] ¡Frieren, obvio!")
    o, _, _ = orquestador(llm)
    o.memoria = SesionMemoria(m, ex, cada_turnos=1)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "¿qué anime vemos hoy?", span())
    await o.turno.tarea
    sistema = llm.recibido[0]["content"]
    assert "Memoria relevante" in sistema and "Su anime favorito es Frieren" in sistema
    await esperar_pendientes()
    assert ex.lotes == ["Usuario: ¿qué anime vemos hoy?\nNix: ¡Frieren, obvio!"]   # sin la etiqueta
    await o.cerrar()


# ---------------------------------------------------------------- Groq (falso) y línea de comandos
async def test_extractor_groq_pide_json_al_modelo_pequeno():
    pedido = {}

    def manejar(request):
        pedido.update(json.loads(request.content))
        contenido = json.dumps({"hechos": [{"texto": "Le gusta el karaoke", "tipo": "gusto", "confianza": 0.9}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": contenido}}]})

    ex = ExtractorGroq("k", cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    assert await ex.hechos("Usuario: me encanta el karaoke") == [
        {"texto": "Le gusta el karaoke", "tipo": "gusto", "confianza": 0.9}]
    assert pedido["model"] == "openai/gpt-oss-20b" and pedido["reasoning_effort"] == "low" and pedido["response_format"] == {"type": "json_object"}
    assert "estado" in pedido["messages"][0]["content"]


def test_linea_de_comandos(tmp_path, capsys):
    db = tmp_path / "m.sqlite"
    m = Memoria(db)
    i = m.guardar("Le gusta el anime Frieren", "gusto", 0.9)
    m.guardar("Hoy está cansado", "estado", 0.9)
    m.cerrar()
    main(["--db", str(db), "--listar"])
    salida = capsys.readouterr().out
    assert "Le gusta el anime Frieren" in salida and "caduca en 48 h" in salida
    main(["--db", str(db), "--borrar", str(i)])
    main(["--db", str(db), "--buscar", "anime"])
    assert "Frieren" not in capsys.readouterr().out.split("borrado")[-1]
    main(["--db", str(db), "--borrar-todo"])
    assert Memoria(db).todos() == []
