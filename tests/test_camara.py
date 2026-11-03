"""Presencia por cámara: arranca apagada, nunca escribe archivos, solo manda un booleano cuando cambia, sin
cámara no falla, Ctrl+Alt+C la apaga; y en el servidor, solo como contexto del LLM."""
import asyncio
import builtins
import os
import sys
import tempfile
import types

import numpy as np
import pytest

from client import camara as cam
from client.camara import CamaraOpenCV, DetectorCaras, Presencia
from client.indicador import LINEA_CAMARA_ACTIVA, LINEA_CAMARA_APAGADA, Indicador
from server.sesion import TEXTO_AUSENTE, TEXTO_PRESENTE, EstadoSesion
from shared.protocol import PresenciaUpdate


# ------------------------------------------------------------------ OpenCV falso
def cv2_falso(caras: list[bool] | None = None, abre: bool = True):
    """Módulo cv2 falso: una cámara que da frames 320×240 y un Haar que «ve» caras según `caras`."""
    secuencia = list(caras or [])
    cv2 = types.SimpleNamespace(CAP_DSHOW=700, CAP_ANY=0, CAP_PROP_FRAME_WIDTH=3, CAP_PROP_FRAME_HEIGHT=4,
                                COLOR_BGR2GRAY=6, INTER_AREA=3, data=types.SimpleNamespace(haarcascades="/haar/"),
                                abiertas=[], liberadas=[], frames=0)

    class VideoCapture:
        def __init__(self, indice, api):
            self.indice, self.abierta = indice, abre
            cv2.abiertas.append(indice)

        def isOpened(self):
            return self.abierta

        def set(self, *_):
            return True

        def read(self):
            cv2.frames += 1
            return True, np.zeros((240, 320, 3), np.uint8)

        def release(self):
            cv2.liberadas.append(self.indice)

    class CascadeClassifier:
        def __init__(self, ruta):
            self.ruta = ruta

        def empty(self):
            return False

        def detectMultiScale(self, gris, **_):
            hay = secuencia.pop(0) if secuencia else False
            return [(10, 10, 50, 50)] if hay else []

    def prohibido(*_a, **_k):
        raise AssertionError("la cámara no debe escribir imágenes ni video")

    cv2.VideoCapture, cv2.CascadeClassifier = VideoCapture, CascadeClassifier
    cv2.cvtColor = lambda frame, _codigo: frame[:, :, 0]
    cv2.resize = lambda frame, tam, interpolation=None: np.zeros((tam[1], tam[0], 3), np.uint8)
    cv2.imwrite = cv2.imencode = cv2.VideoWriter = prohibido
    return cv2


def presencia(caras, abre=True, **kw):
    """Presencia con el OpenCV falso y una función de envío que guarda lo enviado."""
    cv2 = cv2_falso(caras, abre)
    p = Presencia(abrir=lambda: CamaraOpenCV(0, cv2=cv2), detector=DetectorCaras(cv2=cv2), **kw)
    enviados = []

    async def enviar(msg):
        enviados.append(msg)

    p.enviar = enviar
    return p, enviados, cv2


async def pasos(p, n, desde=0.0, cada=0.5):
    for i in range(n):
        await p.paso(ahora=desde + i * cada)


# ------------------------------------------------------------------ arranca apagada
def test_arranca_apagada():
    p = Presencia(abrir=lambda: pytest.fail("no debe abrir la cámara"))
    assert p.activa is False and p.camara is None and p.presente is None


async def test_apagada_no_abre_la_camara_aunque_este_conectada():
    abiertas = []
    p = Presencia(fps=2.0, abrir=lambda: abiertas.append(1), detector=lambda f: True)
    enviados = []

    async def enviar(msg):
        enviados.append(msg)

    p.conectar(enviar)
    tarea = asyncio.create_task(p.correr())
    await asyncio.sleep(1.1)
    tarea.cancel()
    await asyncio.gather(tarea, return_exceptions=True)
    assert abiertas == [] and enviados == []


def test_el_cliente_arranca_con_la_camara_apagada(monkeypatch):
    import client.main as cm
    llamadas = {}

    async def conversar(url, token, fuente, salida, al_audio, captura, encender=False, presencia=None,
                        encender_camara=False, **_):
        llamadas["camara"], llamadas["activa"] = encender_camara, presencia.activa

    monkeypatch.setattr(cm, "conversar_con_pantalla", conversar)
    monkeypatch.setattr(cm, "AudioDuplex", lambda *a, **k: types.SimpleNamespace(cerrar=lambda: None))
    monkeypatch.setenv("NIX_TOKEN", "t" * 40)
    cm.main([])
    assert llamadas == {"camara": False, "activa": False}
    cm.main(["--camara", "on"])
    assert llamadas["camara"] is True


# ------------------------------------------------------------------ solo al cambiar
async def test_solo_manda_cuando_cambia():
    # 6 frames con cara, uno sin cara (parpadeo), 3 con cara, y luego 10 sin cara (5 s).
    caras = [True] * 6 + [False] + [True] * 3 + [False] * 10
    p, enviados, _ = presencia(caras)
    assert p.activar()
    await pasos(p, len(caras))
    assert [m.presente for m in enviados] == [True, False]           # el parpadeo no cuenta
    assert all(isinstance(m, PresenciaUpdate) for m in enviados)


async def test_ausente_solo_tras_4_segundos_sin_cara():
    p, enviados, _ = presencia([False] * 20)
    p.activar()
    await pasos(p, 8)                                                # 3,5 s: todavía nada
    assert enviados == []
    await pasos(p, 1, desde=4.0)
    assert [m.presente for m in enviados] == [False]
    await pasos(p, 5, desde=4.5)
    assert len(enviados) == 1                                        # sigue ausente: no repite


async def test_solo_sale_un_booleano():
    p, enviados, _ = presencia([True, True])
    p.activar()
    await pasos(p, 2)
    datos = enviados[0].model_dump()
    assert set(datos) == {"type", "ts", "presente", "activa"}
    assert isinstance(datos["presente"], bool) and isinstance(datos["activa"], bool)


async def test_al_reconectar_manda_el_estado_actual():
    p, enviados, _ = presencia([True, True])
    p.activar()
    await pasos(p, 2)
    p.conectar(None)
    nuevos = []

    async def enviar(msg):
        nuevos.append(msg)

    p.conectar(enviar)
    await asyncio.sleep(0)
    assert [m.presente for m in nuevos] == [True]


# ------------------------------------------------------------------ nada en disco
def test_nunca_escribe_archivos(monkeypatch, tmp_path):
    escrituras = []
    abrir_real = builtins.open

    def abrir(archivo, modo="r", *a, **k):
        if any(m in modo for m in "wax+"):
            escrituras.append(archivo)
        return abrir_real(archivo, modo, *a, **k)

    monkeypatch.setattr(builtins, "open", abrir)
    monkeypatch.chdir(tmp_path)
    temporales_antes = set(os.listdir(tempfile.gettempdir()))

    async def correr():
        p, enviados, cv2 = presencia([True] * 4 + [False] * 12)    # imwrite / VideoWriter lanzan error
        p.activar()
        await pasos(p, 16)
        p.desactivar()
        await asyncio.sleep(0)
        return enviados, cv2

    enviados, cv2 = asyncio.run(correr())
    assert cv2.frames == 16 and [m.presente for m in enviados][:2] == [True, False]
    assert escrituras == [] and list(tmp_path.iterdir()) == []
    assert set(os.listdir(tempfile.gettempdir())) - temporales_antes == set()


async def test_el_frame_no_sobrevive_a_la_deteccion():
    """El detector recibe el frame; después de cada paso, Presencia no guarda ninguna referencia a frames."""
    p, _, _ = presencia([True, True])
    p.activar()
    await pasos(p, 2)
    assert not any(isinstance(v, np.ndarray) for v in vars(p).values())
    assert not any(isinstance(v, np.ndarray) for v in vars(p.camara).values())


# ------------------------------------------------------------------ sin cámara
def test_sin_camara_queda_apagada_y_avisa(caplog):
    estados = []
    p, _, cv2 = presencia([], abre=False, al_cambiar_estado=estados.append)
    assert p.activar() is False
    assert p.activa is False and estados == []
    assert cv2.liberadas == [0]                                      # suelta lo que intentó abrir
    assert "la cámara queda apagada" in caplog.text and "no hay cámara" in caplog.text


def test_sin_opencv_queda_apagada_y_avisa(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "cv2", None)                    # import cv2 -> ImportError
    p = Presencia()
    assert p.activar() is False and p.activa is False
    assert "falta OpenCV" in caplog.text


async def test_sin_camara_el_bucle_no_falla():
    p, enviados, _ = presencia([], abre=False)
    p.activar()
    tarea = asyncio.create_task(p.correr())
    await asyncio.sleep(0.7)
    assert not tarea.done()
    tarea.cancel()
    await asyncio.gather(tarea, return_exceptions=True)
    assert enviados == []


async def test_con_camara_on_y_sin_camara_el_cliente_sigue(monkeypatch):
    """--camara on sin cámara: avisa, la deja apagada y la conversación sigue."""
    import client.main as cm
    from client.screen_capture import CapturaPantalla

    conversando = []

    async def conversar_siempre(*a, **k):
        conversando.append(True)

    monkeypatch.setattr(cm, "conversar_siempre", conversar_siempre)
    lineas = []
    ind = Indicador(escribir=lineas.append, titulo=lambda _t: None)
    p, _, _ = presencia([], abre=False, al_cambiar_estado=ind.camara)
    await cm.conversar_con_pantalla("ws://x", "t", None, None, None, CapturaPantalla(capturador=lambda: None),
                                    presencia=p, encender_camara=True)
    assert conversando == [True] and p.activa is False and LINEA_CAMARA_APAGADA in lineas


# ------------------------------------------------------------------ apagar al instante
async def test_desactivar_suelta_la_camara_y_avisa_al_servidor():
    estados = []
    p, enviados, cv2 = presencia([True, True], al_cambiar_estado=estados.append)
    p.activar()
    await pasos(p, 2)
    p.desactivar()
    await asyncio.sleep(0)
    assert p.activa is False and p.camara is None and cv2.liberadas == [0]
    assert estados == [True, False]
    assert enviados[-1].activa is False
    await p.paso(ahora=10.0)                                         # apagada: no lee más frames
    assert cv2.frames == 2


def test_ctrl_alt_c_apaga_la_camara_y_no_la_enciende(monkeypatch):
    from client.screen_capture import Atajos, CapturaPantalla
    registrados = {}

    class GlobalHotKeys:
        def __init__(self, mapa):
            registrados.update(mapa)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setitem(sys.modules, "pynput", types.SimpleNamespace(
        keyboard=types.SimpleNamespace(GlobalHotKeys=GlobalHotKeys)))

    async def correr():
        p, _, _ = presencia([])
        Atajos(CapturaPantalla(capturador=lambda: None), p).iniciar(asyncio.get_running_loop())
        registrados["<ctrl>+<alt>+c"]()                              # apagada: sigue apagada
        await asyncio.sleep(0)
        assert not p.activa
        p.activar()
        registrados["<ctrl>+<alt>+c"]()                              # encendida: se apaga al instante
        await asyncio.sleep(0)
        assert not p.activa and p.camara is None

    asyncio.run(correr())


def test_indicador_de_camara():
    lineas, titulos = [], []
    ind = Indicador(escribir=lineas.append, titulo=titulos.append)
    ind.camara(True)
    ind.camara(False)
    assert lineas == [LINEA_CAMARA_ACTIVA, LINEA_CAMARA_APAGADA]
    assert "CAMARA ENCENDIDA" in titulos[0] and "Ctrl+Alt+C" in titulos[0]
    assert titulos[1] == "Nix - pantalla y camara apagadas"


def test_haar_real_de_opencv():
    """Con el OpenCV de verdad (si está instalado): el clasificador carga y no ve caras en negro."""
    pytest.importorskip("cv2")
    detector = DetectorCaras()
    assert detector(np.zeros((240, 320, 3), np.uint8)) is False


# ------------------------------------------------------------------ servidor
def test_estado_de_sesion():
    s = EstadoSesion()
    assert s.para_llm() == ""
    s.actualizar_presencia(PresenciaUpdate(ts=1, presente=True))
    assert s.para_llm() == TEXTO_PRESENTE
    s.actualizar_presencia(PresenciaUpdate(ts=2, presente=False))
    assert s.para_llm() == TEXTO_AUSENTE
    s.actualizar_presencia(PresenciaUpdate(ts=3, presente=False, activa=False))
    assert s.para_llm() == "" and s.presente is None


async def test_el_gateway_guarda_la_presencia_en_la_sesion_y_la_olvida_al_desconectar():
    from tests.test_gateway import conversar, gateway
    from tests.utiles import silencio

    g = gateway()
    vistos = []
    escucha_original = g.crear_escucha

    def crear_escucha(enviar):
        vistos.append(g.sesion)
        return escucha_original(enviar)

    g.crear_escucha = crear_escucha
    g._mensaje(PresenciaUpdate(ts=1, presente=True).a_json())
    assert g.sesion.presente is True
    await conversar(g, silencio(0.1))                                # una conexión nueva empieza sin dato
    assert vistos[0].presente is None
    assert g.sesion.presente is None                                 # y al cerrarse se olvida


async def test_la_presencia_va_al_prompt_y_no_al_historial():
    from server.latencia import Registro
    from server.orquestador import Historial, Orquestador
    from server.pipeline.tts import TTSStub
    from tests.test_orquestador import NIX, LLMFijo, span

    sesion = EstadoSesion()
    sesion.actualizar_presencia(PresenciaUpdate(ts=1, presente=False))
    llm, historial = LLMFijo("¿Sigues ahí?"), Historial(10)

    async def enviar(_msg):
        pass

    o = Orquestador(NIX, llm, TTSStub(), enviar, Registro(), historial, contexto=sesion.para_llm)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    assert TEXTO_AUSENTE in llm.recibido[0]["content"]
    assert all("cámara" not in m["content"] for m in historial.mensajes)
