"""Pantalla: pHash, límites de tamaño, nada en disco, apagada al iniciar, ventanas excluidas, atajos, indicador
y el lado del servidor (descripción, resumen con tope de tokens, contexto del LLM)."""
import asyncio
import base64
import builtins
import io
import os
import sys
import tempfile
import types

import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw

from client import screen_capture as sc
from client.indicador import LINEA_PANTALLA_ACTIVA, LINEA_PANTALLA_APAGADA, Indicador
from client.screen_capture import (TEXTO_PRIVADA, Atajos, CapturadorMss, CapturaPantalla, a_jpeg, distancia,
                                   es_excluida, phash, reducir)
from server.config import ConfigServidor
from server.vision import (SIN_DESCRIPCION, ResumenPantalla, VisionGroq, VisionStub, Vista, crear_vision)
from shared.protocol import ErrorProtocolo, VisionDelta, VisionFrame, parsear_mensaje


# ------------------------------------------------------------------ pantallas falsas
def escritorio(variante: int = 0, ancho: int = 1920, alto: int = 1080) -> Image.Image:
    """Una «pantalla» con ventanas de colores; cada variante cambia la composición."""
    img = Image.new("RGB", (ancho, alto), (30, 30, 40))
    d = ImageDraw.Draw(img)
    rng = np.random.default_rng(variante)
    for _ in range(6):
        x0, y0 = int(rng.integers(0, ancho - 400)), int(rng.integers(0, alto - 300))
        d.rectangle([x0, y0, x0 + int(rng.integers(200, 900)), y0 + int(rng.integers(150, 600))],
                    fill=tuple(int(c) for c in rng.integers(0, 255, 3)))
    return img


def con_ruido(img: Image.Image, semilla: int = 0) -> Image.Image:
    """La misma pantalla con un poco de ruido (compresión, un cursor que parpadea...)."""
    a = np.asarray(img).astype(np.int16)
    a = a + np.random.default_rng(semilla).integers(-6, 7, a.shape)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


class Capturador:
    def __init__(self, imagenes):
        self.imagenes, self.llamadas = list(imagenes), 0

    def __call__(self):
        self.llamadas += 1
        return reducir(self.imagenes[min(self.llamadas - 1, len(self.imagenes) - 1)])


def captura(imagenes, titulo="Minecraft 1.21", **kw):
    enviados = []

    async def enviar(msg):
        enviados.append(msg)

    c = CapturaPantalla(capturador=Capturador(imagenes), titulo=lambda: titulo, min_intervalo_s=0, **kw)
    c.enviar = enviar
    c.activar()
    return c, enviados


# ------------------------------------------------------------------ pHash
def test_phash_misma_pantalla_cerca_y_otra_lejos():
    a = escritorio(1)
    assert distancia(phash(a), phash(con_ruido(a))) < 10
    assert distancia(phash(reducir(a)), phash(a)) < 10              # reducir no cambia el hash
    assert distancia(phash(a), phash(escritorio(2))) >= 10


async def test_solo_avisa_cuando_la_pantalla_cambia():
    a, b = escritorio(1), escritorio(2)
    c, enviados = captura([a, con_ruido(a, 1), con_ruido(a, 2), b])
    for i in range(4):
        await c.paso(ahora=float(i * 10))
    assert len(enviados) == 2                                        # la primera y el cambio a b
    assert all(isinstance(m, VisionDelta) for m in enviados)
    assert enviados[0].caption == "Ventana activa: Minecraft 1.21" and enviados[1].phash_dist >= 10


async def test_no_avisa_mas_de_una_vez_cada_intervalo():
    c, enviados = captura([escritorio(i) for i in range(5)])
    c.min_intervalo_s = 3.0
    for t in (0.0, 1.0, 2.0, 3.5):
        await c.paso(ahora=t)
    assert len(enviados) == 2                                        # en 0 s y en 3,5 s


async def test_mira_ahora_manda_una_imagen_aunque_no_cambie():
    a = escritorio(1)
    c, enviados = captura([a, a, a])
    await c.paso(ahora=0.0)
    c.mirar_ahora()
    await c.paso(ahora=1.0)
    await c.paso(ahora=2.0)
    assert [type(m) for m in enviados] == [VisionDelta, VisionFrame]
    assert enviados[1].caption == "Ventana activa: Minecraft 1.21"


# ------------------------------------------------------------------ tamaño
def test_reducir_a_768_px_sin_deformar():
    r = reducir(escritorio(1, 2560, 1440))
    assert r.size == (768, 432)
    pequena = escritorio(1, 640, 480)
    assert reducir(pequena) is pequena                               # nunca se agranda


def test_jpeg_no_pasa_del_limite():
    ruido = Image.fromarray(np.random.default_rng(0).integers(0, 255, (432, 768, 3), dtype=np.uint8))
    for limite in (200_000, 50_000, 10_000):
        jpeg = a_jpeg(ruido, max_bytes=limite)                       # el ruido es lo que peor se comprime
        assert len(jpeg) <= limite and jpeg[:2] == b"\xff\xd8"


async def test_la_imagen_enviada_es_pequena():
    c, enviados = captura([escritorio(1, 3840, 2160)], enviar_imagen=True)
    await c.paso(ahora=0.0)
    jpeg = base64.b64decode(enviados[0].jpeg_b64)
    img = Image.open(io.BytesIO(jpeg))
    assert img.width <= 768 and len(jpeg) <= sc.MAX_JPEG_BYTES


def test_el_protocolo_rechaza_imagenes_y_textos_enormes():
    with pytest.raises(ErrorProtocolo):
        parsear_mensaje(VisionFrame.model_construct(ts=1, jpeg_b64="A" * 400_004).model_dump_json())
    with pytest.raises(ErrorProtocolo):
        parsear_mensaje('{"type": "vision.delta", "ts": 1, "phash_dist": 3, "caption": "' + "x" * 301 + '"}')
    assert parsear_mensaje('{"type": "vision.delta", "ts": 1, "phash_dist": 0, "activa": false}').activa is False


# ------------------------------------------------------------------ nada en disco
def test_no_se_guarda_nada_en_disco(monkeypatch, tmp_path):
    """Captura real (mss falso) + imagen pedida: ningún archivo nuevo y ninguna escritura de archivo."""
    pantalla = escritorio(3, 2560, 1440)

    class MssFalso:
        monitors = [None, {"left": 0, "top": 0, "width": 2560, "height": 1440}]

        def grab(self, _monitor):
            return types.SimpleNamespace(size=pantalla.size, rgb=pantalla.tobytes())

        def shot(self, *_a, **_k):
            raise AssertionError("shot() escribe un PNG en disco")

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=MssFalso))
    escrituras = []
    abrir_real = builtins.open

    def abrir(archivo, modo="r", *a, **k):
        if any(m in modo for m in "wax+"):
            escrituras.append(archivo)
        return abrir_real(archivo, modo, *a, **k)

    monkeypatch.setattr(builtins, "open", abrir)
    guardar_real = Image.Image.save

    def guardar(self, fp, *a, **k):
        assert isinstance(fp, io.BytesIO), f"Image.save a {fp!r}: solo en memoria"
        return guardar_real(self, fp, *a, **k)

    monkeypatch.setattr(Image.Image, "save", guardar)
    monkeypatch.chdir(tmp_path)
    temporales_antes = set(os.listdir(tempfile.gettempdir()))

    async def correr():
        enviados = []

        async def enviar(msg):
            enviados.append(msg)

        c = CapturaPantalla(capturador=CapturadorMss(), titulo=lambda: "Visual Studio Code", min_intervalo_s=0)
        c.enviar = enviar
        c.activar()
        await c.paso(ahora=0.0)
        c.mirar_ahora()
        await c.paso(ahora=1.0)
        return enviados

    enviados = asyncio.run(correr())
    assert [type(m) for m in enviados] == [VisionDelta, VisionFrame]
    assert escrituras == []
    assert list(tmp_path.iterdir()) == []
    assert set(os.listdir(tempfile.gettempdir())) - temporales_antes == set()


# ------------------------------------------------------------------ apagada al iniciar
def test_apagada_al_iniciar():
    c = CapturaPantalla(capturador=lambda: pytest.fail("no debe capturar"))
    assert c.activa is False and c.pedida is False


async def test_apagada_no_captura_aunque_este_conectada():
    llamadas = []
    c = CapturaPantalla(fps=2.0, capturador=lambda: llamadas.append(1) or escritorio(1), titulo=lambda: "x")
    enviados = []

    async def enviar(msg):
        enviados.append(msg)

    c.conectar(enviar)
    tarea = asyncio.create_task(c.correr())
    await asyncio.sleep(1.2)
    tarea.cancel()
    await asyncio.gather(tarea, return_exceptions=True)
    assert llamadas == []
    # Al conectarse le dice al servidor que está apagada (por si quedaba algo de una sesión anterior).
    assert len(enviados) == 1 and enviados[0].activa is False


def test_el_cliente_arranca_con_la_pantalla_apagada(monkeypatch):
    import client.main as cm
    llamadas = {}

    async def conversar(url, token, fuente, salida, al_audio, captura, encender=False, presencia=None,
                        encender_camara=False, **_):
        llamadas["encender"], llamadas["activa"] = encender, captura.activa

    monkeypatch.setattr(cm, "conversar_con_pantalla", conversar)
    monkeypatch.setattr(cm, "AudioDuplex", lambda *a, **k: types.SimpleNamespace(cerrar=lambda: None))
    monkeypatch.setenv("NIX_TOKEN", "t" * 40)
    cm.main([])
    assert llamadas == {"encender": False, "activa": False}
    cm.main(["--pantalla", "on"])
    assert llamadas["encender"] is True


async def test_pausar_avisa_al_servidor_y_al_indicador():
    estados = []
    c, enviados = captura([escritorio(1)])
    c.al_cambiar_estado = estados.append
    c.desactivar()
    await asyncio.sleep(0)
    assert c.activa is False and estados == [False]
    assert enviados[-1].activa is False
    c.alternar()
    assert c.activa is True and estados == [False, True]


# ------------------------------------------------------------------ ventanas excluidas
@pytest.mark.parametrize("titulo", ["BBVA México - Banca en línea", "Bitwarden", "Iniciar sesión - Google",
                                    "Cambiar CONTRASEÑA"])
def test_ventanas_excluidas(titulo):
    assert es_excluida(titulo, sc.EXCLUIDAS_POR_DEFECTO)


def test_ventanas_normales_no_excluidas():
    for titulo in ("Minecraft 1.21", "Visual Studio Code - main.py", "Roblox", None, ""):
        assert not es_excluida(titulo, sc.EXCLUIDAS_POR_DEFECTO)


async def test_en_una_ventana_privada_no_se_captura():
    c, enviados = captura([escritorio(1)], titulo="Santander - Mi cuenta")
    await c.paso(ahora=0.0)
    await c.paso(ahora=10.0)
    assert c.capturador.llamadas == 0
    assert len(enviados) == 1 and enviados[0].caption == TEXTO_PRIVADA


# ------------------------------------------------------------------ atajos e indicador
def test_atajos_de_teclado(monkeypatch):
    registrados = {}

    class GlobalHotKeys:
        def __init__(self, mapa):
            registrados.update(mapa)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setitem(sys.modules, "pynput", types.SimpleNamespace(keyboard=types.SimpleNamespace(
        GlobalHotKeys=GlobalHotKeys)))

    async def correr():
        c = CapturaPantalla(capturador=lambda: None)
        assert Atajos(c).iniciar(asyncio.get_running_loop())
        registrados["<ctrl>+<alt>+p"]()                              # enciende
        await asyncio.sleep(0)
        assert c.activa
        registrados["<ctrl>+<alt>+m"]()
        await asyncio.sleep(0)
        assert c.pedida
        registrados["<ctrl>+<alt>+p"]()                              # pausa al instante
        await asyncio.sleep(0)
        assert not c.activa and not c.pedida

    asyncio.run(correr())


def test_sin_pynput_los_atajos_no_tumban_el_cliente(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynput", None)

    async def correr():
        return Atajos(CapturaPantalla(capturador=lambda: None)).iniciar(asyncio.get_running_loop())

    assert asyncio.run(correr()) is False


def test_indicador_visible():
    lineas, titulos = [], []
    ind = Indicador(escribir=lineas.append, titulo=titulos.append)
    ind.pantalla(True)
    ind.pantalla(False)
    assert lineas == [LINEA_PANTALLA_ACTIVA, LINEA_PANTALLA_APAGADA]
    assert "PANTALLA" in titulos[0] and "Ctrl+Alt+P" in titulos[0] and "PANTALLA" not in titulos[1]
    assert "🔴" in LINEA_PANTALLA_ACTIVA


def test_el_titulo_dice_nix_y_es_ascii():
    """En Windows el título salía como «Nx»: el emoji del título confundía a la consola. Ahora es ASCII puro."""
    titulos = []
    ind = Indicador(escribir=lambda _t: None, titulo=titulos.append)
    ind.pantalla(True)
    ind.camara(True)
    ind.pantalla(False)
    ind.camara(False)
    assert titulos == [
        "Nix [EN VIVO] - VIENDO TU PANTALLA (Ctrl+Alt+P pausa)",
        "Nix [EN VIVO] - VIENDO TU PANTALLA (Ctrl+Alt+P pausa) - CAMARA ENCENDIDA (Ctrl+Alt+C apaga)",
        "Nix [EN VIVO] - CAMARA ENCENDIDA (Ctrl+Alt+C apaga)",
        "Nix - pantalla y camara apagadas",
    ]
    for t in titulos:
        assert t.startswith("Nix ") and "Nx" not in t and t.isascii()


# ------------------------------------------------------------------ servidor
def jpeg_b64(img=None) -> str:
    return base64.b64encode(a_jpeg(reducir(img or escritorio(1)))).decode()


def test_crear_vision_sin_clave_usa_stub():
    assert crear_vision(ConfigServidor(token="x" * 40)).nombre == "stub"
    assert crear_vision(ConfigServidor(token="x" * 40, groq_api_key="k")).nombre == "groq"
    assert crear_vision(ConfigServidor(token="x" * 40, groq_api_key="k", vision_proveedor="stub")).nombre == "stub"


async def test_stub_da_pantalla_sin_descripcion():
    vista = Vista(VisionStub())
    vista.recibir(VisionFrame(ts=1, jpeg_b64=jpeg_b64()))
    await vista.describiendo
    assert SIN_DESCRIPCION in vista.resumen.para_llm()


async def test_vision_groq_manda_la_imagen_y_devuelve_la_descripcion():
    pedidos = []

    def manejar(request: httpx.Request) -> httpx.Response:
        import json
        pedidos.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": " Juega Minecraft en una cueva. "}}]})

    v = VisionGroq("CLAVE", "qwen/qwen3.8-27b", cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    assert await v.describir(b"\xff\xd8jpeg") == "Juega Minecraft en una cueva."
    contenido = pedidos[0]["messages"][0]["content"]
    assert contenido[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert pedidos[0]["reasoning_effort"] == "none" and pedidos[0]["max_tokens"] <= 100


async def test_vista_describe_solo_la_ultima_si_llegan_varias():
    vistos = []

    class Lento(VisionStub):
        async def describir(self, jpeg):
            vistos.append(jpeg)
            await asyncio.sleep(0.05)
            return f"imagen {len(vistos)}"

    vista = Vista(Lento())
    imagenes = [jpeg_b64(escritorio(i)) for i in range(4)]
    for i, img in enumerate(imagenes):                               # llegan 4 seguidas
        vista.recibir(VisionFrame(ts=i, jpeg_b64=img))
    await vista.describiendo
    assert vistos == [base64.b64decode(imagenes[-1])]               # solo se describe la última
    vista.recibir(VisionFrame(ts=5, jpeg_b64=imagenes[0]))           # mientras describe, llegan 2 más
    await asyncio.sleep(0.01)
    vista.recibir(VisionFrame(ts=6, jpeg_b64=imagenes[1]))
    vista.recibir(VisionFrame(ts=7, jpeg_b64=imagenes[2]))
    await vista.describiendo
    assert vistos[1:] == [base64.b64decode(imagenes[0]), base64.b64decode(imagenes[2])]


async def test_vista_rechaza_imagenes_grandes_o_invalidas():
    vista = Vista(VisionStub())
    # El protocolo ya rechaza algo así; la Vista lo comprueba otra vez por si llega por otro camino.
    vista.recibir(VisionFrame.model_construct(ts=1, jpeg_b64=base64.b64encode(b"x" * 300_001).decode()))
    await vista.describiendo
    vista.recibir(VisionFrame(ts=2, jpeg_b64="esto no es base64!"))
    await vista.describiendo
    assert vista.descripciones == 0 and vista.resumen.para_llm() == ""


def test_vista_descripcion_del_cliente_y_pausa():
    vista = Vista(VisionStub())
    vista.recibir(VisionDelta(ts=1, phash_dist=20, caption="Ventana activa: Roblox"))
    assert "Ventana activa: Roblox" in vista.resumen.para_llm()
    vista.recibir(VisionDelta(ts=2, phash_dist=0, activa=False))
    assert vista.resumen.para_llm() == ""


def test_resumen_respeta_el_limite_de_tokens():
    r = ResumenPantalla(max_tokens=60)
    for i in range(3):
        r.actualizar(f"Descripción número {i} " + "con muchísimo detalle " * 20, ahora=float(i))
    texto = r.para_llm(ahora=3.0)
    assert len(texto) <= 60 * 4 and "número 2" in texto              # la más nueva siempre entra
    assert "número 0" not in texto


def test_resumen_caduca_y_no_repite():
    r = ResumenPantalla(caduca_s=600)
    r.actualizar("Minecraft", ahora=0.0)
    r.actualizar("Minecraft", ahora=5.0)
    assert len(r.entradas) == 1 and "hace 1 min" in r.para_llm(ahora=65.0)
    assert r.para_llm(ahora=700.0) == ""


async def test_el_resumen_llega_al_prompt_del_llm():
    from tests.test_orquestador import LLMFijo, span
    from server.latencia import Registro
    from server.orquestador import Historial, Orquestador
    from server.pipeline.tts import TTSStub
    from tests.test_orquestador import NIX

    resumen = ResumenPantalla()
    resumen.actualizar("Ventana activa: Minecraft 1.21")
    llm = LLMFijo("Qué buena cueva.")

    async def enviar(_msg):
        pass

    o = Orquestador(NIX, llm, TTSStub(), enviar, Registro(), Historial(10), contexto=resumen.para_llm)
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "¿qué ves?", span())
    await o.turno.tarea
    sistema = llm.recibido[0]["content"]
    assert sistema.startswith("Eres Nix.") and "Ventana activa: Minecraft 1.21" in sistema


async def test_el_gateway_ignora_la_pantalla_si_esta_deshabilitada():
    from server.gateway.ws_server import Gateway
    recibidos = []
    vista = types.SimpleNamespace(recibir=recibidos.append)
    apagado = Gateway(ConfigServidor(token="x" * 40), lambda e: None, "Nix", vista=vista)
    apagado._mensaje(VisionDelta(ts=1, phash_dist=20, caption="hola").a_json())
    assert recibidos == []
    encendido = Gateway(ConfigServidor(token="x" * 40, vision_habilitada=True), lambda e: None, "Nix", vista=vista)
    encendido._mensaje(VisionDelta(ts=1, phash_dist=20, caption="hola").a_json())
    assert len(recibidos) == 1
