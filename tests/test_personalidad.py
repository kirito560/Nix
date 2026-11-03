"""Personalidad y conversación: respuestas cortas, tono sin insultos, aclaraciones, memoria de sesión, estados de
ánimo y presencia que no genera respuestas."""
import asyncio
import json
import random
from pathlib import Path

import httpx
import pytest

from client.ws_client import ejecutar
from server.animo import BASE, MAX_CAMBIO, Animo
from server.config import ConfigServidor
from server.orquestador import (FRASE_NO_ENTENDI, FRASE_SIN_CEREBRO, FRASE_SUAVE, INSTRUCCIONES_VOZ, Historial,
                                prompt_sistema)
from server.personaje import cargar_personaje
from server.pipeline.filtros_whisper import poco_claro
from server.pipeline.llm import MARGEN_RAZONAMIENTO, LLMGroq
from server.pipeline.stt import Transcripcion
from server.sesion import EstadoSesion
from server.tono import EXPRESIONES_HOSTILES, expresion_hostil
from shared.protocol import LlmText, PresenciaUpdate, SttText
from tests.test_gateway import TOKEN, conversar, gateway
from tests.test_orquestador import NIX, LLMFijo, orquestador, span
from tests.test_transcripcion import gateway_con_stt, textos
from tests.utiles import silencio, tono


def dichos(enviados) -> list[str]:
    return [m.texto for m in enviados if isinstance(m, LlmText) and m.texto]


async def responder(llm, max_oraciones=2, texto="hola"):
    o, enviados, _ = orquestador(llm, max_oraciones=max_oraciones)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", texto, span())
    await o.turno.tarea
    return dichos(enviados)


class LLMContador(LLMFijo):
    """Cuenta cuántos trozos llegó a entregar (para ver que el stream se corta)."""

    def __init__(self, texto):
        super().__init__(texto)
        self.entregados = 0

    async def responder(self, mensajes):
        async for trozo in super().responder(mensajes):
            self.entregados += 1
            yield trozo


# ------------------------------------------------------------------ respuestas cortas
def test_el_limite_de_tokens_es_bajo():
    cfg = ConfigServidor(token="x" * 40)
    assert cfg.llm_max_tokens == 90 and cfg.respuesta_max_oraciones == 2
    assert ConfigServidor.desde_entorno({"NIX_TOKEN": "x" * 40}).llm_max_tokens == 90


def test_groq_recibe_el_limite_de_tokens():
    qwen = LLMGroq("k", "qwen/qwen3.8-27b", max_tokens=90, cliente=httpx.AsyncClient())
    assert qwen.cuerpo([])["max_completion_tokens"] == 90
    gpt = LLMGroq("k", "openai/gpt-oss-120b", max_tokens=90, cliente=httpx.AsyncClient())
    # gpt-oss razona dentro del límite: se le suma el margen para que la respuesta no salga vacía.
    assert gpt.cuerpo([])["max_completion_tokens"] == 90 + MARGEN_RAZONAMIENTO


async def test_la_respuesta_se_corta_en_dos_oraciones():
    largo = "Eso fue increíble. Te lo juro. Además hay más cosas. Y otra. Y otra más. Fin."
    llm = LLMContador(largo)
    dicho = await responder(llm)
    assert " ".join(dicho) == "Eso fue increíble. Te lo juro."
    assert llm.entregados < len(largo.split(" "))                     # dejó de leer el stream
    assert len(" ".join(dicho)) <= 90 * 4                             # dentro del límite (~4 caracteres por token)


async def test_las_comas_no_cuentan_como_oracion():
    dicho = await responder(LLMFijo("Uy, eso estuvo cerca, la neta, casi. Va otra. Y otra."))
    assert " ".join(dicho) == "Uy, eso estuvo cerca, la neta, casi. Va otra."


async def test_sin_tope_dice_todo():
    dicho = await responder(LLMFijo("Una. Dos. Tres."), max_oraciones=0)
    assert " ".join(dicho) == "Una. Dos. Tres."


def test_el_prompt_pide_una_o_dos_frases_sin_listas():
    assert "una o dos frases" in INSTRUCCIONES_VOZ and "sin listas" in INSTRUCCIONES_VOZ


# ------------------------------------------------------------------ tono
RESPUESTAS_DE_PRUEBA = [
    "¡GG! Eso estuvo roto, la neta.",
    "Skill issue, la neta. Pero va otra.",
    "No me hagas perder el tiempo. ¿Jugamos?",
    "Eres un manco jaja.",
    "Ni modo, a la próxima. Te toca elegir juego.",
    "Qué tonto fue eso, jaja.",
]


@pytest.mark.parametrize("respuesta", RESPUESTAS_DE_PRUEBA)
async def test_ninguna_respuesta_dicha_usa_insultos(respuesta):
    dicho = await responder(LLMFijo(respuesta))
    assert dicho, "algo debe decir"
    for frase in dicho:
        assert expresion_hostil(frase) is None, frase


async def test_si_todo_era_hostil_dice_algo_amable():
    assert await responder(LLMFijo("Skill issue. Eres un manco.")) == [FRASE_SUAVE]


def test_frases_fijas_y_ficha_sin_insultos():
    for frase in (FRASE_NO_ENTENDI, FRASE_SIN_CEREBRO, FRASE_SUAVE):
        assert expresion_hostil(frase) is None
    ficha = cargar_personaje("nix", ConfigServidor(token="x" * 40).carpeta_personajes, Path("/tmp/nix-x")).ficha
    como_habla = ficha.split("## CÓMO HABLA")[1].split("##")[0]
    assert expresion_hostil(como_habla) is None                      # su jerga ya no incluye «skill issue»
    assert "burlona" not in ficha and "insulta" in ficha


def test_el_filtro_reconoce_la_lista_y_no_palabras_parecidas():
    for e in EXPRESIONES_HOSTILES:
        assert expresion_hostil(f"oye, {e}, ¿no?") == e
    for ok in ("Mancomunidad", "Qué buena jugada", "Perdón, no te entendí bien, ¿me lo repites?", "Ni modo"):
        assert expresion_hostil(ok) is None


# ------------------------------------------------------------------ aclaraciones
@pytest.mark.parametrize("texto", ["ble", "Tor.", "ñe", "zas"])
def test_una_palabra_corta_y_rara_pide_repetir(texto):
    assert poco_claro(texto)


@pytest.mark.parametrize("texto", ["sí", "No.", "ok", "Hola", "GG", "Minecraft", "pinchada de la lora", "va"])
def test_respuestas_cortas_validas_no(texto):
    assert poco_claro(texto) is None


async def test_lo_poco_claro_no_llega_al_llm():
    recibidos, _ = await conversar(gateway_con_stt(Transcripcion("ble")), tono(1.0) + silencio(0.8))
    assert textos(recibidos) == [FRASE_NO_ENTENDI]
    assert not any(isinstance(m, SttText) for m in recibidos)


def test_las_frases_de_repetir_son_de_una_oracion():
    for frase in (FRASE_NO_ENTENDI, FRASE_SIN_CEREBRO):
        assert sum(frase.count(c) for c in ".!?") == 1 and frase.endswith("?")
    assert "no inventes" in INSTRUCCIONES_VOZ


# ------------------------------------------------------------------ memoria de sesión
async def test_guarda_los_ultimos_10_turnos():
    historial = Historial(10)
    o, _, _ = orquestador(LLMFijo("Va."), historial=historial)
    for i in range(12):
        await o.al_texto_usuario(f"00000000-0000-0000-0000-0000000000{i:02d}", f"mensaje {i}", span())
        await o.turno.tarea
    assert len(historial.mensajes) == 20                              # 10 turnos: usuario + Nix
    assert historial.mensajes[0]["content"] == "mensaje 2"


async def test_el_historial_se_borra_al_desconectarse():
    g = gateway()
    historiales = []
    original = g.crear_escucha

    def crear_escucha(enviar):
        historiales.append(g.sesion.historial)
        return original(enviar)

    g.crear_escucha = crear_escucha
    await conversar(g, silencio(0.1))
    g.sesion.historial.agregar("user", "algo")                        # (sesión ya cerrada: una nueva)
    await conversar(g, silencio(0.1))
    assert historiales[0] is not historiales[1]
    assert not historiales[1].mensajes
    assert g.sesion.historial is not historiales[1]                  # al cerrarse, también se tiró


def test_la_sesion_usa_historial_turnos():
    g = gateway()
    g.cfg = ConfigServidor(token="x" * 40, historial_turnos=3)
    assert g._sesion_nueva().historial.mensajes.maxlen == 6


# ------------------------------------------------------------------ estados de ánimo
def test_los_estados_quedan_entre_0_y_1_y_cambian_poco():
    rng = random.Random(7)
    frases = ["jaja gg", "gracias, eres la mejor", "no sirves, idiota", "tengo sueño, estoy cansado", "hola",
              "ya basta, que fastidio", "te quiero jaja xd genial"]
    a = Animo(reloj=lambda: 0.0)
    for _ in range(500):
        antes = dict(a.valores)
        cambios = a.tras_turno(rng.choice(frases), ahora=0.0)
        for k, v in a.valores.items():
            assert 0.0 <= v <= 1.0
            assert abs(v - antes[k]) <= MAX_CAMBIO + 1e-9
            assert abs(cambios[k]) <= MAX_CAMBIO + 1e-9


def test_un_solo_turno_no_lo_cambia_todo():
    a = Animo(reloj=lambda: 0.0)
    a.tras_turno("no sirves, idiota, ya basta, que fastidio, me desesperas", ahora=0.0)
    assert a.valores["paciencia"] == pytest.approx(BASE["paciencia"] - MAX_CAMBIO)


def test_vuelven_despacio_a_la_base():
    a = Animo(reloj=lambda: 0.0)
    for _ in range(5):
        a.tras_turno("no sirves", ahora=0.0)
    bajo = a.valores["paciencia"]
    assert bajo < BASE["paciencia"] - 0.3
    assert a.actuales(ahora=60)["paciencia"] - bajo < 0.05           # en un minuto apenas se mueve
    diez_min = a.actuales(ahora=600)["paciencia"]
    assert diez_min == pytest.approx(BASE["paciencia"] - (BASE["paciencia"] - bajo) / 2, abs=1e-6)
    assert a.actuales(ahora=6000)["paciencia"] == pytest.approx(BASE["paciencia"], abs=0.01)


def test_el_animo_solo_cambia_el_texto_del_tono():
    a = Animo(reloj=lambda: 0.0)
    assert "con mucha energía" in a.para_llm(0) and "paciente" in a.para_llm(0)
    for _ in range(6):
        a.tras_turno("no sirves", ahora=0.0)
    texto = a.para_llm(0)
    assert "poca paciencia" in texto and "amable" in texto and "no los menciones" in texto
    assert "Paciencia: 22/100" in texto and "Energía: 70/100" in texto


async def test_cada_respuesta_ajusta_el_animo():
    animo = Animo(reloj=lambda: 0.0)
    o, _, _ = orquestador(LLMFijo("Jaja, va."))
    o.animo = animo
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "jaja gg, gracias", span())
    await o.turno.tarea
    await o.decir("00000000-0000-0000-0000-000000000002", FRASE_NO_ENTENDI)
    await o.turno.tarea
    # risas (+0,03) + gracias (+0,08) + platicar (+0,01) = +0,12, topado en +0,1; las frases fijas no lo mueven.
    assert animo.valores["cercania"] == pytest.approx(BASE["cercania"] + MAX_CAMBIO)


# ------------------------------------------------------------------ presencia
async def test_la_presencia_no_genera_respuestas():
    """El PC manda cambios de presencia (y nada de voz): Nix no dice nada."""
    g = gateway()
    recibidos, audio = [], []

    async def al_recibir(msg):
        recibidos.append(msg)

    async def fuente():
        for _ in range(60):                                           # 1,2 s de silencio
            yield bytes(640)
            await asyncio.sleep(0.02)

    async def al_conectar_async(enviar):
        for i, presente in enumerate([True, False, True, False]):
            await enviar(PresenciaUpdate(ts=i, presente=presente))
            await asyncio.sleep(0.05)

    tareas = []

    def al_conectar(enviar):
        if enviar is not None:
            tareas.append(asyncio.create_task(al_conectar_async(enviar)))

    async with g.servir() as srv:
        url = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
        await ejecutar(url, TOKEN, fuente(), al_recibir, espera_final_s=0.5,
                       al_audio=lambda *a: audio.append(a), al_conectar=al_conectar)
    await asyncio.gather(*tareas)
    assert recibidos == [] and audio == []


def test_la_presencia_es_solo_contexto():
    s = EstadoSesion()
    s.actualizar_presencia(PresenciaUpdate(ts=1, presente=False))
    texto = s.para_llm()
    assert "no lo menciones salvo que el usuario pregunte" in texto
    assert "levantado" not in texto and "oírte" not in texto
