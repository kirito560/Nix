"""
Captura de pantalla del cliente, para que Nix sepa qué estás haciendo.

Privacidad (reglas del proyecto):
  - Apagada al iniciar, siempre. Se enciende con `--pantalla on` o con el atajo (Ctrl+Alt+P), que también la
    pausa al instante.
  - Nada se guarda en disco: la captura vive en memoria lo justo para reducirla, calcular su pHash y, si se
    pidió, comprimirla a JPEG; después se suelta.
  - Por defecto NO sale ninguna imagen del PC: cuando la pantalla cambia de verdad (pHash), se manda solo una
    descripción breve hecha aquí (el título de la ventana activa). La imagen reducida (≤ 768 px de ancho) solo
    se manda si la pides: atajo «mira ahora» (Ctrl+Alt+M) o PANTALLA_ENVIAR_IMAGEN=1.
  - Si la ventana activa parece de contraseñas o de un banco (PANTALLA_EXCLUIR), no se captura nada.

Todo lo de sistema (mss, el título de la ventana) se inyecta, así que las pruebas corren sin pantalla.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
import time
from typing import Awaitable, Callable, Iterable, Optional

import numpy as np

from shared.protocol import VisionDelta, VisionFrame

log = logging.getLogger("nix.pantalla")

ANCHO_MAXIMO = 768
MAX_JPEG_BYTES = 200_000                 # el servidor acepta mensajes de hasta 1 MiB (en base64 crece 4/3)
EXCLUIDAS_POR_DEFECTO = (
    "contraseña", "password", "1password", "bitwarden", "keepass", "lastpass", "administrador de credenciales",
    "credential manager", "iniciar sesión", "inicio de sesión", "sign in", "login", "banco", "bank", "banca",
    "bbva", "santander", "banorte", "banamex", "hsbc", "scotiabank", "mercado pago", "paypal",
)
TEXTO_PRIVADA = "El usuario está en una ventana privada (contraseñas o banco); no se mira."


# ---------------------------------------------------------------------------------------- imagen
def reducir(imagen, ancho: int = ANCHO_MAXIMO):
    """Imagen de Pillow con como mucho `ancho` px de ancho, sin deformarla."""
    from PIL import Image
    if imagen.width <= ancho:
        return imagen
    alto = max(1, round(imagen.height * ancho / imagen.width))
    return imagen.resize((ancho, alto), Image.Resampling.BILINEAR)


def _dct_matriz(n: int) -> np.ndarray:
    k = np.arange(n)[:, None]
    m = np.cos(np.pi * (2 * np.arange(n)[None, :] + 1) * k / (2 * n))
    m[0] /= np.sqrt(2)
    return m


_DCT32 = _dct_matriz(32)


def phash(imagen) -> int:
    """Hash perceptual de 64 bits: gris 32×32, DCT 2D, las 8×8 frecuencias más bajas (sin la continua)
    comparadas con su mediana. Pantallas parecidas dan hashes a poca distancia de Hamming."""
    from PIL import Image
    gris = np.asarray(imagen.convert("L").resize((32, 32), Image.Resampling.BILINEAR), dtype=np.float64)
    bajas = (_DCT32 @ gris @ _DCT32.T)[:8, :8].flatten()[1:]
    bits = bajas > np.median(bajas)
    return int("".join("1" if b else "0" for b in bits), 2)


def distancia(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def a_jpeg(imagen, max_bytes: int = MAX_JPEG_BYTES) -> bytes:
    """JPEG en memoria (nunca en disco) que no pasa de `max_bytes`: baja la calidad y, si no basta, el tamaño."""
    imagen = imagen.convert("RGB")
    while True:
        for calidad in (70, 55, 40, 30):
            buf = io.BytesIO()
            imagen.save(buf, format="JPEG", quality=calidad)
            if buf.tell() <= max_bytes:
                return buf.getvalue()
        imagen = imagen.resize((max(1, imagen.width * 3 // 4), max(1, imagen.height * 3 // 4)))


# ---------------------------------------------------------------------------------------- sistema
def titulo_ventana_activa() -> Optional[str]:
    """Título de la ventana en primer plano (Windows). En otros sistemas, None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        usuario = ctypes.windll.user32
        ventana = usuario.GetForegroundWindow()
        largo = usuario.GetWindowTextLengthW(ventana)
        buf = ctypes.create_unicode_buffer(largo + 1)
        usuario.GetWindowTextW(ventana, buf, largo + 1)
        return buf.value or None
    except Exception:  # noqa: BLE001
        return None


def es_excluida(titulo: Optional[str], patrones: Iterable[str]) -> bool:
    t = (titulo or "").lower()
    return bool(t) and any(p.strip().lower() in t for p in patrones if p.strip())


class CapturadorMss:
    """Pantalla principal con mss, reducida a `ancho` px. Solo en memoria (grab, nunca shot/to_png)."""

    def __init__(self, ancho: int = ANCHO_MAXIMO):
        self.ancho = ancho
        self.sct = None

    def __call__(self):
        from PIL import Image
        if self.sct is None:
            import mss
            self.sct = mss.mss()
        crudo = self.sct.grab(self.sct.monitors[1])
        imagen = Image.frombytes("RGB", crudo.size, crudo.rgb)
        del crudo
        return reducir(imagen, self.ancho)


# ---------------------------------------------------------------------------------------- captura
class CapturaPantalla:
    def __init__(self, fps: float = 1.0, umbral: int = 10, min_intervalo_s: float = 3.0, enviar_imagen: bool = False,
                 excluidas: Iterable[str] = EXCLUIDAS_POR_DEFECTO, ancho: int = ANCHO_MAXIMO,
                 capturador: Optional[Callable[[], object]] = None,
                 titulo: Callable[[], Optional[str]] = titulo_ventana_activa,
                 al_cambiar_estado: Optional[Callable[[bool], None]] = None):
        self.fps = min(max(fps, 0.2), 2.0)                 # 1 o 2 fps; nunca más
        self.umbral, self.min_intervalo_s, self.enviar_imagen = umbral, min_intervalo_s, enviar_imagen
        self.excluidas = tuple(excluidas)
        self.capturador = capturador or CapturadorMss(ancho)
        self.titulo = titulo
        self.al_cambiar_estado = al_cambiar_estado
        self.activa = False                                # apagada al iniciar, siempre
        self.pedida = False                                # «mira ahora»: la próxima va con imagen
        self.enviar: Optional[Callable[[object], Awaitable[None]]] = None
        self.ultimo_hash: Optional[int] = None
        self.ultimo_envio = float("-inf")              # nunca se ha enviado nada
        self.en_privada = False
        self.enviados = 0

    # ------------------------------------------------------------------ control
    def activar(self) -> None:
        if not self.activa:
            self.activa, self.ultimo_hash, self.en_privada = True, None, False
            log.info("👁️ captura de pantalla ENCENDIDA")
            self._avisar_estado()

    def desactivar(self) -> None:
        if self.activa:
            self.activa, self.pedida, self.ultimo_hash = False, False, None
            log.info("👁️ captura de pantalla en PAUSA")
            self._avisar_estado()
            self._mandar_luego(VisionDelta(ts=_ahora_ms(), phash_dist=0, activa=False))

    def alternar(self) -> None:
        self.desactivar() if self.activa else self.activar()

    def mirar_ahora(self) -> None:
        """La próxima captura se manda con imagen aunque la pantalla no haya cambiado (si está encendida)."""
        if not self.activa:
            log.info("👁️ la captura está apagada: enciéndela primero (Ctrl+Alt+P)")
            return
        self.pedida = True
        log.info("👁️ mandaré la pantalla a Nix en el próximo segundo")

    def conectar(self, enviar: Optional[Callable[[object], Awaitable[None]]]) -> None:
        """Lo llama el cliente al conectarse (con la función de envío) y al desconectarse (con None)."""
        self.enviar = enviar
        self.ultimo_hash = None                            # al reconectar, el servidor recibe el estado actual
        if enviar is not None and not self.activa:
            self._mandar_luego(VisionDelta(ts=_ahora_ms(), phash_dist=0, activa=False))

    def _avisar_estado(self) -> None:
        if self.al_cambiar_estado is not None:
            self.al_cambiar_estado(self.activa)

    def _mandar_luego(self, msg) -> None:
        if self.enviar is None:
            return
        try:
            asyncio.get_running_loop().create_task(self._mandar(msg))
        except RuntimeError:                               # sin bucle (pruebas síncronas)
            pass

    async def _mandar(self, msg) -> None:
        if self.enviar is None:
            return
        try:
            await self.enviar(msg)
            self.enviados += 1
        except Exception as e:  # noqa: BLE001 - conexión caída: se reintenta con la próxima captura
            log.debug("no pude mandar %s: %s", msg.type, e)

    # ------------------------------------------------------------------ bucle
    async def correr(self) -> None:
        while True:
            await asyncio.sleep(1 / self.fps)
            if self.activa and self.enviar is not None:
                try:
                    await self.paso()
                except Exception:  # noqa: BLE001 - una captura fallida no tumba al cliente
                    log.exception("👁️ la captura falló; sigo intentando")

    async def paso(self, ahora: Optional[float] = None) -> Optional[object]:
        """Una vuelta: captura, decide si vale la pena mandar algo y lo manda. Devuelve lo enviado (o None)."""
        ahora = time.monotonic() if ahora is None else ahora
        titulo = self.titulo()
        if es_excluida(titulo, self.excluidas):
            if self.en_privada:
                return None
            self.en_privada, self.ultimo_hash = True, None
            log.info("🙈 ventana privada: no capturo")
            msg = VisionDelta(ts=_ahora_ms(), phash_dist=64, caption=TEXTO_PRIVADA)
            await self._mandar(msg)
            return msg
        self.en_privada = False

        imagen = await asyncio.to_thread(self.capturador)
        try:
            h = phash(imagen)
            dist = 64 if self.ultimo_hash is None else distancia(h, self.ultimo_hash)
            cambio = dist >= self.umbral and ahora - self.ultimo_envio >= self.min_intervalo_s
            if not (cambio or self.pedida):
                return None
            descripcion = f"Ventana activa: {titulo}" if titulo else None
            if self.pedida or self.enviar_imagen:
                jpeg = await asyncio.to_thread(a_jpeg, imagen)
                msg = VisionFrame(ts=_ahora_ms(), jpeg_b64=base64.b64encode(jpeg).decode("ascii"),
                                  caption=descripcion)
                del jpeg
            else:
                msg = VisionDelta(ts=_ahora_ms(), phash_dist=dist, caption=descripcion)
            self.ultimo_hash, self.ultimo_envio, self.pedida = h, ahora, False
        finally:
            del imagen
        await self._mandar(msg)
        return msg


class Atajos:
    """Atajos globales (pynput): Ctrl+Alt+P enciende o pausa la pantalla al instante; Ctrl+Alt+M manda la
    pantalla ahora; Ctrl+Alt+C apaga la cámara al instante (solo apaga: encenderla es con --camara on).
    Los callbacks llegan desde el hilo de pynput y se pasan al bucle de asyncio."""

    PAUSA = "<ctrl>+<alt>+p"
    MIRAR = "<ctrl>+<alt>+m"
    CAMARA = "<ctrl>+<alt>+c"

    def __init__(self, captura: CapturaPantalla, camara=None):
        self.captura, self.camara = captura, camara
        self.oyente = None

    def iniciar(self, bucle: asyncio.AbstractEventLoop) -> bool:
        mapa = {
            self.PAUSA: lambda: bucle.call_soon_threadsafe(self.captura.alternar),
            self.MIRAR: lambda: bucle.call_soon_threadsafe(self.captura.mirar_ahora),
        }
        if self.camara is not None:
            mapa[self.CAMARA] = lambda: bucle.call_soon_threadsafe(self.camara.desactivar)
        try:
            from pynput import keyboard
            self.oyente = keyboard.GlobalHotKeys(mapa)
            self.oyente.start()
        except Exception as e:  # noqa: BLE001 - sin pynput o sin teclado accesible
            log.warning("⌨️ no pude registrar los atajos (%s): pantalla y cámara solo se controlan al arrancar "
                        "(--pantalla, --camara) o cerrando el cliente", e)
            return False
        log.info("⌨️ atajos: Ctrl+Alt+P pantalla sí/no · Ctrl+Alt+M manda la pantalla a Nix · Ctrl+Alt+C apaga la cámara")
        return True

    def detener(self) -> None:
        if self.oyente is not None:
            self.oyente.stop()


def _ahora_ms() -> int:
    return int(time.time() * 1000)
