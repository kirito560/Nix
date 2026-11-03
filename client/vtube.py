"""
Puente con VTube Studio (opcional: `--vtube on` o VTUBE=1). Usa su API pública por WebSocket local
(VTUBE_URL, por defecto ws://127.0.0.1:8001; en VTube Studio: Ajustes > «Start API»).

  - Emoción -> expresión: el servidor manda avatar.cmd con la emoción de cada respuesta ([burla], [animada]...)
    y aquí se pulsa la hotkey de tu modelo Live2D que le corresponde (VTS_HOTKEYS=burla:Sonrisa,animada:Feliz...;
    los nombres son los de tus hotkeys en VTube Studio). Las expresiones de tipo «ToggleExpression» se apagan si
    se pulsan dos veces, así que se lleva la cuenta de cuál está encendida y se apaga antes de encender otra.
  - Boca: el volumen (RMS) de la voz de Nix EN EL MOMENTO EN QUE SUENA (lo mide el callback de la tarjeta de
    audio, no cuando llega, que llega adelantada) mueve el parámetro MouthOpen ~30 veces por segundo. No hace
    falta un cable de audio virtual.
  - Permiso: la primera vez VTube Studio muestra un aviso para permitir el plugin «Nix»; el token que da se
    guarda en %APPDATA%\\Nix\\vtube_token (en Linux, ~/.config/nix/vtube_token), fuera del repositorio.
  - Si VTube Studio no está abierto, o se cierra, no pasa nada: Nix sigue igual y el puente se reconecta solo
    (1, 2, 4... hasta 10 s entre intentos).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("nix.vtube")

URL_POR_DEFECTO = "ws://127.0.0.1:8001"
PLUGIN = {"pluginName": "Nix", "pluginDeveloper": "Proyecto Nix"}
FPS_BOCA = 30
ESPERA_MAX_S = 10.0
DB_CERRADA, DB_ABIERTA = -50.0, -15.0      # voz más baja que esto: boca cerrada; más alta: boca abierta del todo


class ErrorVTube(RuntimeError):
    pass


def ruta_token() -> Path:
    base = Path(os.environ["APPDATA"]) / "Nix" if sys.platform == "win32" and os.environ.get("APPDATA") \
        else Path.home() / ".config" / "nix"
    return base / "vtube_token"


def leer_hotkeys(texto: str) -> dict[str, str]:
    """«burla:Sonrisa, animada:Feliz» -> {"burla": "Sonrisa", "animada": "Feliz"}."""
    mapa = {}
    for parte in texto.split(","):
        if ":" in parte:
            emocion, hotkey = parte.split(":", 1)
            if emocion.strip() and hotkey.strip():
                mapa[emocion.strip().lower()] = hotkey.strip()
    return mapa


def apertura(rms: float) -> float:
    """Volumen RMS (0..1) -> MouthOpen (0..1)."""
    if rms <= 0:
        return 0.0
    db = 20 * np.log10(rms)
    return float(min(1.0, max(0.0, (db - DB_CERRADA) / (DB_ABIERTA - DB_CERRADA))))


class PuenteVTube:
    def __init__(self, hotkeys: dict[str, str], url: str = URL_POR_DEFECTO, nivel: Callable[[], float] = lambda: 0.0,
                 token_en: Optional[Path] = None, conectar=None):
        self.hotkeys, self.url, self.nivel = hotkeys, url, nivel
        self.token_en = token_en or ruta_token()
        self.conectar = conectar
        self.ws = None
        self.pendientes: dict[str, asyncio.Future] = {}
        self.ids: dict[str, str] = {}               # nombre de hotkey -> {hotkeyID, tipo}
        self.tipos: dict[str, str] = {}
        self.encendida: Optional[str] = None        # hotkey de expresión (toggle) que está encendida
        self.cola: asyncio.Queue[str] = asyncio.Queue()
        self.boca = 0.0
        self.conectado = asyncio.Event()

    # ------------------------------------------------------------------ desde el cliente
    def emocion(self, nombre: Optional[str]) -> None:
        if nombre:
            self.cola.put_nowait(nombre.lower())

    # ------------------------------------------------------------------ bucle
    async def correr(self) -> None:
        espera = 1.0
        while True:
            try:
                await self._sesion()
                espera = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - VTube Studio cerrado, API apagada, permiso negado...
                log.info("🎭 VTube Studio no disponible (%s); reintento en %.0f s", e, espera)
            finally:
                self.conectado.clear()
                self.ws = None
                for f in self.pendientes.values():
                    if not f.done():
                        f.set_exception(ErrorVTube("conexión cerrada"))
                self.pendientes.clear()
            await asyncio.sleep(espera)
            espera = min(espera * 2, ESPERA_MAX_S)

    async def _sesion(self) -> None:
        conectar = self.conectar
        if conectar is None:
            from websockets.asyncio.client import connect as conectar
        async with conectar(self.url, open_timeout=3) as ws:
            self.ws = ws
            lector = asyncio.create_task(self._leer(ws), name="vtube-leer")
            try:
                await self._autenticar()
                await self._cargar_hotkeys()
                self.encendida = None
                self.conectado.set()
                log.info("🎭 VTube Studio conectado (%d hotkeys en el modelo; %d emociones con hotkey)",
                         len(self.ids), sum(h in self.ids for h in self.hotkeys.values()))
                tareas = [asyncio.create_task(self._emociones(), name="vtube-emociones"),
                          asyncio.create_task(self._boca(), name="vtube-boca"), lector]
                hecho, _ = await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
                for t in tareas:
                    t.cancel()
                await asyncio.gather(*tareas, return_exceptions=True)
                for t in hecho:
                    if t.exception() is not None:
                        raise t.exception()
                raise ErrorVTube("se cerró la conexión")
            finally:
                lector.cancel()
                await asyncio.gather(lector, return_exceptions=True)

    async def _leer(self, ws) -> None:
        async for texto in ws:
            try:
                msg = json.loads(texto)
            except ValueError:
                continue
            futuro = self.pendientes.pop(msg.get("requestID", ""), None)
            if futuro is not None and not futuro.done():
                futuro.set_result(msg)

    async def pedir(self, tipo: str, datos: Optional[dict] = None, esperar: bool = True) -> dict:
        id_ = uuid.uuid4().hex[:12]
        msg = {"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0", "requestID": id_, "messageType": tipo}
        if datos is not None:
            msg["data"] = datos
        futuro = asyncio.get_running_loop().create_future() if esperar else None
        if futuro is not None:
            self.pendientes[id_] = futuro
        await self.ws.send(json.dumps(msg))
        if futuro is None:
            return {}
        # El permiso del plugin espera a que hagas clic en VTube Studio: hasta 60 s.
        respuesta = await asyncio.wait_for(futuro, 60 if tipo == "AuthenticationTokenRequest" else 5)
        if respuesta.get("messageType") == "APIError":
            d = respuesta.get("data", {})
            raise ErrorVTube(f"{tipo}: {d.get('message', 'error')} ({d.get('errorID')})")
        return respuesta.get("data", {})

    # ------------------------------------------------------------------ permiso
    async def _autenticar(self) -> None:
        token = self._token_guardado()
        if token and await self._con_token(token):
            return
        log.info("🎭 Pidiendo permiso a VTube Studio: acepta el plugin «Nix» en la ventana de VTube Studio")
        token = (await self.pedir("AuthenticationTokenRequest", dict(PLUGIN))).get("authenticationToken")
        if not token or not await self._con_token(token):
            raise ErrorVTube("VTube Studio no dio permiso al plugin")
        self._guardar_token(token)

    async def _con_token(self, token: str) -> bool:
        datos = await self.pedir("AuthenticationRequest", dict(PLUGIN, authenticationToken=token))
        return bool(datos.get("authenticated"))

    def _token_guardado(self) -> Optional[str]:
        try:
            return self.token_en.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _guardar_token(self, token: str) -> None:
        try:
            self.token_en.parent.mkdir(parents=True, exist_ok=True)
            self.token_en.write_text(token, encoding="utf-8")
            self.token_en.chmod(0o600)
        except OSError as e:
            log.warning("🎭 no pude guardar el permiso de VTube Studio (%s): lo pedirá otra vez", e)

    # ------------------------------------------------------------------ expresiones
    async def _cargar_hotkeys(self) -> None:
        datos = await self.pedir("HotkeysInCurrentModelRequest")
        self.ids = {h["name"]: h["hotkeyID"] for h in datos.get("availableHotkeys", []) if h.get("name")}
        self.tipos = {h["name"]: h.get("type", "") for h in datos.get("availableHotkeys", []) if h.get("name")}
        faltan = [h for h in self.hotkeys.values() if h not in self.ids]
        if faltan:
            log.warning("🎭 tu modelo no tiene estas hotkeys de VTS_HOTKEYS: %s", ", ".join(faltan))

    async def _pulsar(self, nombre: str) -> None:
        await self.pedir("HotkeyTriggerRequest", {"hotkeyID": self.ids[nombre]})

    async def _emociones(self) -> None:
        while True:
            emocion = await self.cola.get()
            await self.poner_emocion(emocion)

    async def poner_emocion(self, emocion: str) -> None:
        hotkey = self.hotkeys.get(emocion)
        if hotkey is not None and hotkey not in self.ids:
            hotkey = None
        if hotkey is not None and hotkey == self.encendida:
            return                                   # ya está puesta: pulsarla otra vez la apagaría
        if self.encendida is not None:               # apagar la expresión anterior
            await self._pulsar(self.encendida)
            self.encendida = None
        if hotkey is None:
            return                                   # sin hotkey (p. ej. neutral): cara normal
        await self._pulsar(hotkey)
        if self.tipos.get(hotkey) == "ToggleExpression":
            self.encendida = hotkey
        log.debug("🎭 expresión: %s -> %s", emocion, hotkey)

    # ------------------------------------------------------------------ boca
    async def _boca(self) -> None:
        """MouthOpen al ritmo de lo que suena. Mientras está callada solo se manda de vez en cuando (VTube Studio
        devuelve el control a la cámara si pasa 1 s sin datos, y eso está bien cuando no habla)."""
        callada = 0
        while True:
            await asyncio.sleep(1 / FPS_BOCA)
            objetivo = apertura(self.nivel())
            # Abre rápido y cierra un poco más despacio: se ve más natural que seguir cada pico.
            self.boca = objetivo if objetivo > self.boca else self.boca * 0.6 + objetivo * 0.4
            if self.boca < 0.02:
                callada += 1
                if callada > FPS_BOCA // 2:          # medio segundo callada: deja de mandar
                    continue
            else:
                callada = 0
            await self.pedir("InjectParameterDataRequest",
                             {"faceFound": False, "mode": "set",
                              "parameterValues": [{"id": "MouthOpen", "value": round(self.boca, 3)}]},
                             esperar=False)
