import datetime as dt

import pytest

from server.juegos.banco_retos import BANCO
from server.juegos.reto_del_dia import INTENTOS, JUEGO, TIPOS, RetoDelDia, es_correcta
from server.juegos.rivalidad import Rivalidad

HOY = dt.date(2026, 10, 28)


@pytest.fixture
def juegos(tmp_path):
    rivalidad = Rivalidad(tmp_path / "juegos.sqlite")
    yield rivalidad, RetoDelDia(tmp_path / "juegos.sqlite", rivalidad)


def test_marcador_por_persona(juegos):
    r, _ = juegos
    r.registrar("telegram:1", "Kirito", "ppt", "gana_personaje")
    r.registrar("telegram:1", "Kirito", "ppt", "gana_personaje")
    r.registrar("telegram:1", "Kirito", "trivia", "gana_persona")
    r.registrar("twitch:amigo", "Amigo", "dado", "empate")
    m = r.marcador("telegram:1")
    assert (m.gana_personaje, m.gana_persona, m.empates) == (2, 1, 0)
    assert m.por_juego == {"ppt": (2, 0, 0), "trivia": (0, 1, 0)} and m.racha == -1
    assert r.marcador("twitch:amigo").empates == 1
    assert r.texto_para_prompt("telegram:1", "Nix") == "Marcador de rivalidad con Kirito: Nix 2 - 1 Kirito; va ganando Nix."
    assert r.texto_para_prompt("nadie", "Nix") == ""


def test_racha_en_el_texto(juegos):
    r, _ = juegos
    for _ in range(3):
        r.registrar("discord:9", "Pao", "adivina", "gana_persona")
    assert "Pao lleva 3 seguidas" in r.texto_para_prompt("discord:9", "Nix")


def test_marcador_persiste_entre_conversaciones(tmp_path):
    Rivalidad(tmp_path / "j.sqlite").registrar("telegram:1", "Kirito", "ppt", "gana_persona")
    assert Rivalidad(tmp_path / "j.sqlite").marcador("telegram:1").gana_persona == 1


def test_resultado_invalido(juegos):
    with pytest.raises(ValueError):
        juegos[0].registrar("p", "P", "ppt", "gana_nadie")


def test_banco_tiene_ids_unicos_y_todos_los_tipos():
    assert len({r.id for r in BANCO}) == len(BANCO)
    assert {r.tipo for r in BANCO} == set(TIPOS)
    assert all(r.respuestas for r in BANCO if r.tipo in ("trivia_anime", "trivia_juegos", "adivinanza"))
    assert all(r.opinion for r in BANCO if r.tipo == "polemica")


def test_reto_estable_en_el_dia_y_variado(juegos, tmp_path):
    _, reto = juegos
    assert reto.de_hoy(HOY) == reto.de_hoy(HOY)
    # Tras reiniciar sigue siendo el mismo.
    assert RetoDelDia(tmp_path / "juegos.sqlite", juegos[0]).de_hoy(HOY) == reto.de_hoy(HOY)
    semana = [reto.de_hoy(HOY + dt.timedelta(days=i)) for i in range(len(TIPOS))]
    assert {r.tipo for r in semana} == set(TIPOS)                        # rota por todos los tipos
    dias = [reto.de_hoy(HOY + dt.timedelta(days=i)) for i in range(25)]
    assert len({r.id for r in dias}) == 25                               # sin repetir en 25 días


def test_es_correcta_ignora_acentos_y_mayusculas():
    reto = next(r for r in BANCO if r.id == "adiv-platano")
    assert es_correcta(reto, "¡Ya sé! Es un PLATANO")
    assert not es_correcta(reto, "una manzana")
    golem = next(r for r in BANCO if r.id == "juego-golem")
    assert es_correcta(golem, "son 4") and not es_correcta(golem, "son 40")


def _dia_con_respuesta(reto_dia) -> dt.date:
    return next(HOY + dt.timedelta(days=i) for i in range(10)
                if reto_dia.de_hoy(HOY + dt.timedelta(days=i)).respuestas)


def test_acertar_el_reto_suma_al_marcador(juegos):
    rivalidad, reto = juegos
    dia = _dia_con_respuesta(reto)
    correcta = reto.de_hoy(dia).respuestas[0]
    assert reto.responder("telegram:1", "Kirito", "ni idea", dia).estado == "incorrecta"
    assert reto.responder("telegram:1", "Kirito", f"es {correcta}", dia).estado == "correcta"
    assert reto.responder("telegram:1", "Kirito", correcta, dia).estado == "ya_jugado"
    assert rivalidad.marcador("telegram:1").por_juego == {JUEGO: (0, 1, 0)}


def test_agotar_intentos_gana_el_personaje(juegos):
    rivalidad, reto = juegos
    dia = _dia_con_respuesta(reto)
    estados = [reto.responder("twitch:x", "X", "zzz", dia).estado for _ in range(INTENTOS)]
    assert estados == ["incorrecta"] * (INTENTOS - 1) + ["sin_intentos"]
    assert rivalidad.marcador("twitch:x").gana_personaje == 1


def test_polemica_no_se_califica(juegos):
    _, reto = juegos
    dia = next(HOY + dt.timedelta(days=i) for i in range(10)
               if reto.de_hoy(HOY + dt.timedelta(days=i)).tipo == "polemica")
    assert reto.responder("p", "P", "sí", dia).estado == "sin_respuesta"
    assert "Tu opinión" in reto.texto_para_prompt(dia)
