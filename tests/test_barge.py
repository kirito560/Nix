"""Interrupciones sin perder frases: pre-roll de 600 ms, frase en cola con BARGE_IN=0, filtro de eco,
historial con lo que sonó (playback.ack) y barge-in del cliente (BARGE_CLIENTE)."""
import asyncio
import types

import numpy as np

from client.barge import BargeCliente, Envio, Reproduccion
from server.config import ConfigServidor
from server.orquestador import lo_que_sono
from server.pipeline.escucha import Escucha, es_eco
from server.pipeline.stt import STTStub, Transcripcion, _SesionStub
from server.pipeline.vad import DetectorVoz, PuntuadorEnergia, Reagrupador
from shared.protocol import BargeIn, PlaybackAck, PlaybackFlush
from tests.test_orquestador import LLMFijo, orquestador, span
from tests.utiles import silencio, tono, tramas

TURNO = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------- lo que sonó
FRASES = [(0, 1000, "Uno dos tres cuatro."), (1000, 2000, "Cinco seis siete ocho.")]


def test_lo_que_sono():
    assert lo_que_sono(FRASES, 2000) == "Uno dos tres cuatro. Cinco seis siete ocho."
    assert lo_que_sono(FRASES, 1000) == "Uno dos tres cuatro. —"
    assert lo_que_sono(FRASES, 1500) == "Uno dos tres cuatro. Cinco seis —"
    assert lo_que_sono(FRASES, 250) == "Uno —"
    assert lo_que_sono(FRASES, 0) == ""


async def hasta_dos_frases(turno) -> None:
    """Espera a que haya dos frases listas, sin contar con el reloj: un sleep(0.02) dura ~31 ms en Windows."""
    for _ in range(300):
        if len(turno.frases_pts) >= 2:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no llegaron dos frases a tiempo")


async def test_el_historial_guarda_lo_que_sono_segun_el_pc(caplog):
    o, _, _ = orquestador(LLMFijo("Una frase larga de prueba. Y otra más. Y otra.", pausa_s=0.02))
    await o.al_texto_usuario(TURNO, "cuéntame", span())
    await hasta_dos_frases(o.turno)
    await o.interrumpir("prueba")
    assert o.historial.mensajes[-1]["content"].endswith("—")
    inicio, fin, frase = o.turno.frases_pts[0]
    o.oido(TURNO, fin)                                    # el PC dice: sonó justo la primera frase
    assert o.historial.mensajes[-1]["content"] == f"{frase} —"
    o.oido(TURNO, 0)                                      # nada: queda la marca de corte
    assert o.historial.mensajes[-1]["content"] == "—"


async def test_el_ack_llega_cuando_ya_empezo_el_turno_siguiente():
    o, _, _ = orquestador(LLMFijo("Primera frase de la respuesta. Segunda frase. Tercera frase.", pausa_s=0.02))
    await o.al_texto_usuario(TURNO, "hola", span())
    await hasta_dos_frases(o.turno)                       # con la tercera aún por llegar: el turno sigue vivo
    viejo = o.turno
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000002", "otra cosa", span())
    o.oido(TURNO, viejo.frases_pts[0][1])
    assert viejo.en_historial["content"] == viejo.frases_pts[0][2] + " —"
    await o.turno.tarea


# ---------------------------------------------------------------- pre-roll
def test_pre_roll_de_600_ms():
    assert ConfigServidor(token="x" * 40).vad_pre_roll_ms == 600
    assert ConfigServidor.desde_entorno({"NIX_TOKEN": "x" * 40}).vad_pre_roll_ms == 600
    d, r = DetectorVoz(PuntuadorEnergia(), pre_roll_ms=600), Reagrupador()
    eventos = []
    for t in tramas(silencio(1.0) + tono(0.3)):
        for v in r.agregar(t):
            eventos += d.procesar(v, 0.0)
    inicio = next(e for e in eventos if e.tipo == "inicio")
    assert 0.58 <= len(inicio.audio) / 16000 <= 0.65      # 600 ms de antes + la primera ventana


# ---------------------------------------------------------------- frase en cola (BARGE_IN=0)
class OrquestadorHablando:
    """Habla durante `segundos` (reloj real) y luego se calla."""

    def __init__(self, segundos: float, dicho: str = "Hoy toca jugar Minecraft con todos."):
        self.hasta = asyncio.get_running_loop().time() + segundos
        self.ultimo_dicho = dicho
        self.recibidas: list[tuple[str, bool, bool]] = []
        self.interrupciones = 0

    @property
    def hablando(self) -> bool:
        return asyncio.get_running_loop().time() < self.hasta

    async def al_texto_usuario(self, turno, texto, span, medir=True):
        self.recibidas.append((texto, medir, self.hablando))

    async def decir(self, turno, frase):
        pass

    async def interrumpir(self, motivo):
        self.interrupciones += 1

    async def cerrar(self):
        pass


def stt_que_dice(texto: str):
    class Sesion(_SesionStub):
        async def finalizar(self, usar_adelanto=False):
            return Transcripcion(texto)

    class STT(STTStub):
        async def abrir(self):
            return Sesion(self)
    return STT()


async def hablar(e: Escucha, pcm: bytes) -> None:
    for i in range(0, len(pcm), 640):
        await e.recibir_audio(pcm[i:i + 640])
        await asyncio.sleep(0)


async def test_con_barge_in_apagado_la_frase_se_responde_al_terminar_ella(caplog):
    import logging
    caplog.set_level(logging.INFO, "nix.escucha")
    o = OrquestadorHablando(0.4)
    e = Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), stt_que_dice("¿Y mañana qué jugamos?"), o,
                barge_in=False, barge_min_voz_ms=400)
    await hablar(e, tono(0.8) + silencio(0.4))
    for _ in range(40):
        if o.recibidas:
            break
        await asyncio.sleep(0.05)
    assert o.recibidas == [("¿Y mañana qué jugamos?", False, False)]   # no cuenta para la latencia, ya calló
    assert o.interrupciones == 0
    assert "se responde cuando termine" in caplog.text
    await e.cerrar()


async def test_su_propio_eco_no_se_responde(caplog):
    import logging
    caplog.set_level(logging.INFO, "nix.escucha")
    o = OrquestadorHablando(0.3)
    e = Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), stt_que_dice("Hoy toca jugar Minecraft."), o,
                barge_in=False, barge_min_voz_ms=400)
    await hablar(e, tono(0.8) + silencio(0.4))
    await asyncio.sleep(0.4)
    assert o.recibidas == [] and "eco" in caplog.text
    await e.cerrar()


async def test_una_frase_corta_durante_su_voz_sigue_siendo_eco():
    o = OrquestadorHablando(5.0)
    e = Escucha(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), stt_que_dice("ajá"), o,
                barge_in=False, barge_min_voz_ms=400)
    await hablar(e, tono(0.3) + silencio(0.4))
    await asyncio.sleep(0.1)
    assert o.recibidas == []
    await e.cerrar()


def test_es_eco():
    dicho = "¡Uy! Hoy toca jugar Minecraft con todos tus amigos."
    assert es_eco("hoy toca jugar minecraft", dicho)
    assert not es_eco("¿Y mañana qué jugamos?", dicho)
    assert not es_eco("Minecraft", dicho)                # una palabra: no se decide por eso
    assert not es_eco("hoy toca jugar", "")


# ---------------------------------------------------------------- gateway
async def test_el_gateway_atiende_barge_in_y_playback_ack():
    from server.gateway.ws_server import Gateway

    o = OrquestadorHablando(5.0)
    vistos = []
    o.oido = lambda turno, pts: vistos.append((turno, pts))
    escucha = types.SimpleNamespace(orquestador=o)
    g = Gateway(ConfigServidor(token="x" * 40), lambda enviar: None, "Nix")
    g._mensaje(BargeIn(turn_id=TURNO, pts_played=1200).a_json(), escucha)
    await asyncio.gather(*g.tareas)
    assert o.interrupciones == 1 and vistos == [(TURNO, 1200)]
    g._mensaje(PlaybackAck(turn_id=TURNO, pts_played=800).a_json(), escucha)
    assert vistos[-1] == (TURNO, 800)


# ---------------------------------------------------------------- cliente
class SalidaFalsa:
    def __init__(self):
        self.pendiente, self.vaciada, self.recibido = 0.0, 0, []

    def reproducir(self, sr, pcm):
        self.recibido.append(pcm)

    def vaciar(self):
        self.vaciada += 1
        self.pendiente = 0.0

    def pendiente_ms(self):
        return self.pendiente


def test_reproduccion_calcula_cuanto_sono():
    s = SalidaFalsa()
    r = Reproduccion(s)
    r.al_audio(TURNO, 0, 24000, bytes(48000))             # 1 s
    r.al_audio(TURNO, 1000, 24000, bytes(48000))          # 2 s encolados
    s.pendiente = 700.0                                   # faltan 700 ms por sonar
    assert r.sonando()
    ack = r.callar()
    assert ack == PlaybackAck(turn_id=TURNO, pts_played=1300) and s.vaciada == 1
    assert r.callar() is None and not r.sonando()          # una sola vez por turno
    r.al_audio(TURNO, 2000, 24000, bytes(4800))           # llegó tarde: ya no suena
    assert len(s.recibido) == 2
    r.al_audio("otro", 0, 24000, bytes(4800))             # el turno siguiente sí
    assert len(s.recibido) == 3


def voz(db: float, ms: int = 20) -> bytes:
    n = 16000 * ms // 1000
    amplitud = 10 ** (db / 20) * np.sqrt(2)
    return (amplitud * np.sin(2 * np.pi * 200 * np.arange(n) / 16000) * 32767).astype("<i2").tobytes()


async def test_barge_cliente_la_calla_al_hablar_encima():
    s, envio, mandados = SalidaFalsa(), Envio(), []

    async def enviar(msg):
        mandados.append(msg)
    envio.conectar(enviar)
    r = Reproduccion(s)
    b = BargeCliente(r, envio, umbral_db=-35, min_ms=100)
    r.al_audio(TURNO, 0, 24000, bytes(96000))             # 2 s de su voz
    s.pendiente = 1500.0
    for _ in range(4):
        assert b.procesar(voz(-50)) is None               # ruido bajo: nada
    msgs = [b.procesar(voz(-20)) for _ in range(5)]
    barge = next(m for m in msgs if m is not None)
    assert barge == BargeIn(turn_id=TURNO, pts_played=500) and s.vaciada == 1 and b.disparos == 1
    await asyncio.sleep(0)
    assert mandados == [barge]
    assert all(b.procesar(voz(-20)) is None for _ in range(10))   # ya está callada


def test_barge_cliente_no_hace_nada_si_ella_no_suena():
    s = SalidaFalsa()
    b = BargeCliente(Reproduccion(s), Envio(), min_ms=40)
    assert all(b.procesar(voz(-10)) is None for _ in range(10)) and s.vaciada == 0


async def test_el_flush_del_servidor_manda_el_ack():
    from client.main import crear_mostrar
    s, envio, mandados = SalidaFalsa(), Envio(), []

    async def enviar(msg):
        mandados.append(msg)
    envio.conectar(enviar)
    r = Reproduccion(s)
    r.al_audio(TURNO, 0, 24000, bytes(48000))
    s.pendiente = 400.0
    await crear_mostrar(s, r, envio)(PlaybackFlush(turn_id=TURNO))
    assert mandados == [PlaybackAck(turn_id=TURNO, pts_played=600)] and s.vaciada == 1


def test_barge_cliente_apagado_por_defecto():
    from client.main import crear_barge
    from shared.config_base import Entorno
    r, envio = Reproduccion(SalidaFalsa()), Envio()
    assert crear_barge(Entorno({}), r, envio) is None
    b = crear_barge(Entorno({"BARGE_CLIENTE": "1", "BARGE_CLIENTE_UMBRAL_DB": "-30"}), r, envio)
    assert b is not None and b.umbral_db == -30 and b.min_ms == 250


async def test_varias_frases_en_cola_se_responden_en_orden():
    """Dos frases mientras ella habla: la segunda espera a que termine la respuesta de la primera."""
    from server.pipeline.escucha import Escucha as E

    class Orq(OrquestadorHablando):
        async def al_texto_usuario(self, turno, texto, span, medir=True):
            self.recibidas.append((texto, medir, self.hablando))
            self.hasta = asyncio.get_running_loop().time() + 0.2      # responder le toma 200 ms

    o = Orq(0.3)
    e = E(DetectorVoz(PuntuadorEnergia(), silencio_ms=300), STTStub(), o, barge_in=False, barge_min_voz_ms=400)
    await asyncio.gather(e._en_cola("t1", "primera frase", span()), e._en_cola("t2", "segunda frase", span()))
    assert [t for t, _, _ in o.recibidas] == ["primera frase", "segunda frase"]
    assert all(not hablando for _, _, hablando in o.recibidas)          # cada una, con ella callada
    await e.cerrar()
