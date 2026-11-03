"""Puente con VTube Studio contra un VTube Studio falso (WebSocket local que imita su API pública)."""
import asyncio
import json
import logging

import numpy as np
import pytest
from websockets.asyncio.server import serve

from client.vtube import PuenteVTube, apertura, leer_hotkeys
from shared.protocol import AvatarCmd

HOTKEYS_MODELO = [
    {"name": "Sonrisa", "type": "ToggleExpression", "hotkeyID": "h1"},
    {"name": "Feliz", "type": "ToggleExpression", "hotkeyID": "h2"},
    {"name": "Brillo", "type": "TriggerAnimation", "hotkeyID": "h3"},
]


class VTubeFalso:
    def __init__(self):
        self.pulsadas: list[str] = []
        self.boca: list[float] = []
        self.pidieron_token = 0
        self.conexiones = 0
        self.cerrar_tras: int | None = None       # cerrar la conexión tras N mensajes

    async def manejar(self, ws):
        self.conexiones += 1
        n = 0
        async for texto in ws:
            n += 1
            msg = json.loads(texto)
            tipo, datos = msg["messageType"], msg.get("data", {})
            respuesta: dict = {}
            if tipo == "AuthenticationTokenRequest":
                self.pidieron_token += 1
                respuesta = {"authenticationToken": "token-de-prueba"}
            elif tipo == "AuthenticationRequest":
                respuesta = {"authenticated": datos.get("authenticationToken") == "token-de-prueba"}
            elif tipo == "HotkeysInCurrentModelRequest":
                respuesta = {"availableHotkeys": HOTKEYS_MODELO}
            elif tipo == "HotkeyTriggerRequest":
                self.pulsadas.append(datos["hotkeyID"])
            elif tipo == "InjectParameterDataRequest":
                self.boca.append(datos["parameterValues"][0]["value"])
            await ws.send(json.dumps({"apiName": "VTubeStudioPublicAPI", "requestID": msg["requestID"],
                                      "messageType": tipo.replace("Request", "Response"), "data": respuesta}))
            if self.cerrar_tras is not None and n >= self.cerrar_tras:
                await ws.close()
                return


@pytest.fixture
async def vtube_falso():
    falso = VTubeFalso()
    async with serve(falso.manejar, "127.0.0.1", 0) as servidor:
        falso.url = f"ws://127.0.0.1:{servidor.sockets[0].getsockname()[1]}"
        yield falso


async def conectado(puente: PuenteVTube) -> asyncio.Task:
    tarea = asyncio.create_task(puente.correr())
    await asyncio.wait_for(puente.conectado.wait(), 3)
    return tarea


async def parar(tarea: asyncio.Task) -> None:
    tarea.cancel()
    await asyncio.gather(tarea, return_exceptions=True)


async def esperar(condicion, segundos: float = 2.0) -> None:
    for _ in range(int(segundos / 0.02)):
        if condicion():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("no pasó a tiempo")


# ---------------------------------------------------------------- permiso
async def test_pide_permiso_una_vez_y_guarda_el_token(vtube_falso, tmp_path):
    token = tmp_path / "Nix" / "vtube_token"
    p = PuenteVTube({}, vtube_falso.url, token_en=token)
    tarea = await conectado(p)
    await parar(tarea)
    assert vtube_falso.pidieron_token == 1 and token.read_text() == "token-de-prueba"
    assert oct(token.stat().st_mode & 0o777) == "0o600"
    tarea = await conectado(PuenteVTube({}, vtube_falso.url, token_en=token))     # la segunda vez ya no pregunta
    await parar(tarea)
    assert vtube_falso.pidieron_token == 1


async def test_un_token_viejo_se_renueva(vtube_falso, tmp_path):
    token = tmp_path / "vtube_token"
    token.write_text("token-revocado")
    tarea = await conectado(PuenteVTube({}, vtube_falso.url, token_en=token))
    await parar(tarea)
    assert vtube_falso.pidieron_token == 1 and token.read_text() == "token-de-prueba"


# ---------------------------------------------------------------- expresiones
async def test_emociones_a_hotkeys_sin_apagar_por_accidente(vtube_falso, tmp_path):
    hotkeys = {"burla": "Sonrisa", "animada": "Feliz", "sorprendida": "Brillo", "cansada": "NoExiste"}
    p = PuenteVTube(hotkeys, vtube_falso.url, token_en=tmp_path / "t")
    tarea = await conectado(p)
    for emocion, esperado in [("burla", ["h1"]),
                              ("burla", ["h1"]),                       # ya puesta: no se pulsa otra vez
                              ("animada", ["h1", "h1", "h2"]),         # apaga Sonrisa y enciende Feliz
                              ("neutral", ["h1", "h1", "h2", "h2"]),   # sin hotkey: solo apaga la anterior
                              ("sorprendida", ["h1", "h1", "h2", "h2", "h3"]),
                              ("cansada", ["h1", "h1", "h2", "h2", "h3"])]:  # hotkey que el modelo no tiene
        p.emocion(emocion)
        await asyncio.sleep(0.1)
        assert vtube_falso.pulsadas == esperado, emocion
    assert p.encendida is None                     # Brillo es una animación, no queda «encendida»
    await parar(tarea)


async def test_el_cliente_pasa_la_emocion_del_servidor():
    from client.main import crear_mostrar
    p = PuenteVTube({}, "ws://127.0.0.1:1")
    await crear_mostrar(object(), vtube=p)(AvatarCmd(turn_id="t", pts=0, expression="burla"))
    assert p.cola.get_nowait() == "burla"


# ---------------------------------------------------------------- boca
def test_apertura_de_la_boca():
    assert apertura(0.0) == 0.0
    assert apertura(10 ** (-60 / 20)) == 0.0                 # casi silencio: cerrada
    assert apertura(10 ** (-10 / 20)) == 1.0                 # voz fuerte: abierta
    assert 0.3 < apertura(10 ** (-35 / 20)) < 0.6


async def test_la_boca_sigue_lo_que_suena(vtube_falso, tmp_path):
    nivel = {"rms": 10 ** (-15 / 20)}
    p = PuenteVTube({}, vtube_falso.url, nivel=lambda: nivel["rms"], token_en=tmp_path / "t")
    tarea = await conectado(p)
    await esperar(lambda: len(vtube_falso.boca) >= 5)
    assert vtube_falso.boca[-1] == pytest.approx(1.0)
    nivel["rms"] = 0.0
    await esperar(lambda: vtube_falso.boca[-1] < 0.02)       # cierra (un poco más despacio de lo que abre)
    await asyncio.sleep(0.7)                                 # medio segundo más mandando la boca cerrada...
    cuantos = len(vtube_falso.boca)
    await asyncio.sleep(0.5)
    assert len(vtube_falso.boca) == cuantos                  # ...y luego, callada, deja de mandar
    await parar(tarea)


def test_el_audio_mide_lo_que_suena():
    from client.audio_io import _rms
    tono = (0.5 * np.sin(np.arange(960) / 5) * 32767).astype("<i2")
    assert _rms(tono) == pytest.approx(0.5 / np.sqrt(2), rel=0.05)
    assert _rms(np.zeros(0, "<i2")) == 0.0


# ---------------------------------------------------------------- reconexión
async def test_se_reconecta_si_vtube_studio_se_cierra(vtube_falso, tmp_path, monkeypatch):
    import client.vtube as v
    monkeypatch.setattr(v, "ESPERA_MAX_S", 0.05)
    vtube_falso.cerrar_tras = 3                               # se cae justo después de conectar
    p = PuenteVTube({}, vtube_falso.url, token_en=tmp_path / "t")
    real_sleep = asyncio.sleep

    async def rapido(s):
        await real_sleep(min(s, 0.05))
    monkeypatch.setattr(v.asyncio, "sleep", rapido)
    tarea = asyncio.create_task(p.correr())
    await esperar(lambda: vtube_falso.conexiones >= 3)
    await parar(tarea)


async def test_sin_vtube_studio_no_falla(caplog):
    caplog.set_level(logging.INFO, "nix.vtube")
    p = PuenteVTube({}, "ws://127.0.0.1:1")
    tarea = asyncio.create_task(p.correr())
    await asyncio.sleep(0.3)
    assert not tarea.done() and "no disponible" in caplog.text
    await parar(tarea)


def test_leer_hotkeys():
    assert leer_hotkeys("burla:Sonrisa, animada : Feliz,raro,:x,cansada:") == {"burla": "Sonrisa", "animada": "Feliz"}
    assert leer_hotkeys("") == {}


def test_vtube_apagado_por_defecto(monkeypatch):
    import client.main as cm
    llamadas = {}

    async def conversar(*a, vtube=None, **k):
        llamadas["vtube"] = vtube

    monkeypatch.setattr(cm, "conversar_con_pantalla", conversar)
    monkeypatch.setattr(cm, "AudioDuplex", lambda *a, **k: type("S", (), {"cerrar": lambda self: None,
                                                                            "nivel_salida": 0.0})())
    monkeypatch.setenv("NIX_TOKEN", "t" * 40)
    monkeypatch.delenv("VTUBE", raising=False)
    cm.main([])
    assert llamadas["vtube"] is None
    cm.main(["--vtube", "on"])
    assert isinstance(llamadas["vtube"], PuenteVTube)
