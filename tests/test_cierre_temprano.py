"""Cierre temprano: si el texto adelantado ya es una frase completa, el turno no espera el resto del silencio."""
import asyncio
import logging

import pytest

from server.pipeline.escucha import Escucha, frase_completa
from server.pipeline.stt import STTStub, Transcripcion, _SesionStub
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia
from tests.utiles import silencio, tono


class OrquestadorEspia:
    hablando = False

    def __init__(self):
        self.textos: list[str] = []

    async def al_texto_usuario(self, turno, texto, span):
        self.textos.append(texto)

    async def decir(self, turno, frase):
        self.textos.append(f"(fija) {frase}")

    async def interrumpir(self, motivo):
        pass

    async def cerrar(self):
        pass


def stt_con_adelanto(texto: str, usos: list, listo: asyncio.Event | None = None):
    class Sesion(_SesionStub):
        adelanto = None

        def adelantar(self):
            async def transcribir():
                await (listo.wait() if listo is not None else asyncio.sleep(0))
                return Transcripcion(texto)
            usos.append("adelantar")
            self.adelanto = asyncio.create_task(transcribir())

        async def finalizar(self, usar_adelanto=False):
            usos.append(f"finalizar:{usar_adelanto}")
            if usar_adelanto and self.adelanto is not None:
                return await self.adelanto
            return Transcripcion(texto)

    class STT(STTStub):
        async def abrir(self):
            return Sesion(self)

    return STT()


async def hablar(escucha: Escucha, *partes: bytes) -> None:
    """Manda el audio en tramas de 20 ms, dejando correr las tareas entre trama y trama."""
    for pcm in partes:
        for i in range(0, len(pcm), 640):
            await escucha.recibir_audio(pcm[i:i + 640])
            await asyncio.sleep(0)
    for _ in range(20):
        await asyncio.sleep(0)


def escucha(texto: str, usos: list, cierre_temprano: bool = True, listo=None) -> tuple[Escucha, OrquestadorEspia]:
    o = OrquestadorEspia()
    e = Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), stt_con_adelanto(texto, usos, listo), o,
                barge_in=False, adelanto_ms=150, cierre_temprano=cierre_temprano)
    return e, o


async def test_frase_completa_cierra_sin_esperar_el_silencio_del_vad(caplog):
    caplog.set_level(logging.INFO, "nix.escucha")
    usos = []
    e, o = escucha("Hola, ¿qué tal?", usos)
    await hablar(e, tono(0.8), silencio(0.2))            # 200 ms: el VAD todavía no cierra (300)
    assert o.textos == ["Hola, ¿qué tal?"]
    assert usos == ["adelantar", "finalizar:True"]
    assert "cierre temprano" in caplog.text and "(temprano)" in caplog.text
    await hablar(e, silencio(0.4))                        # el silencio que sigue ya no cierra otra frase
    assert o.textos == ["Hola, ¿qué tal?"] and usos.count("finalizar:True") == 1
    await e.cerrar()


async def test_frase_abierta_espera_al_vad():
    usos = []
    e, o = escucha("Creo que...", usos)
    await hablar(e, tono(0.8), silencio(0.2))
    assert o.textos == []                                 # «Creo que...» es una pausa, no un final
    await hablar(e, silencio(0.2))
    assert o.textos == ["Creo que..."] and usos == ["adelantar", "finalizar:True"]
    await e.cerrar()


async def test_si_volvio_a_hablar_antes_del_adelanto_no_se_cierra():
    """El adelanto (frase completa) llega cuando ya volvió a hablar: no corresponde a la última voz."""
    usos, listo = [], asyncio.Event()
    e, o = escucha("Bueno, ya entendí.", usos, listo=listo)
    await hablar(e, tono(0.8), silencio(0.16), tono(0.3))     # adelanta en la pausa y sigue hablando
    listo.set()
    await hablar(e, silencio(0.1))
    assert o.textos == []                                     # no cerró: sigue la frase
    await hablar(e, silencio(0.3))                            # la nueva pausa adelanta otra vez y esa sí vale
    assert o.textos == ["Bueno, ya entendí."] and usos == ["adelantar", "adelantar", "finalizar:True"]
    await e.cerrar()


async def test_apagado_espera_siempre_al_vad():
    usos = []
    e, o = escucha("Hola, ¿qué tal?", usos, cierre_temprano=False)
    await hablar(e, tono(0.8), silencio(0.2))
    assert o.textos == []
    await hablar(e, silencio(0.2))
    assert o.textos == ["Hola, ¿qué tal?"]
    await e.cerrar()


@pytest.mark.parametrize("texto, completa", [
    ("Hola, ¿qué tal?", True),
    ("Ya terminé el examen.", True),
    ("¡No manches!", True),
    ("Creo que...", False),
    ("Creo que…", False),
    ("Bueno, y luego", False),
    ("Sí.", False),                                        # una palabra: puede seguir («Sí... pero»)
    ("", False),
    (None, False),
])
def test_frase_completa(texto, completa):
    assert frase_completa(texto) is completa


def test_cerrar_ya_no_cierra_frases_demasiado_cortas():
    from tests.utiles import tramas
    from server.pipeline.vad import Reagrupador
    d, r = DetectorVoz(PuntuadorEnergia(), min_voz_ms=250), Reagrupador()
    assert d.cerrar_ya() is False                         # sin frase
    for t in tramas(tono(0.1)):
        for v in r.agregar(t):
            d.procesar(v, 0.0)
    assert d.en_frase and d.cerrar_ya() is False          # 100 ms de voz: la decide el silencio
    for t in tramas(tono(0.3)):
        for v in r.agregar(t):
            d.procesar(v, 0.0)
    assert d.cerrar_ya() is True and not d.en_frase
