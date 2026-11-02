"""
Presencia por cámara: si hay alguien frente a la cámara, sí o no. Nada más.

Privacidad (reglas del proyecto):
  - Apagada al iniciar. Solo se enciende con `--camara on`; Ctrl+Alt+C la apaga al instante (y suelta la
    cámara, así se apaga también su luz).
  - Ningún frame sale del PC ni se guarda en disco. Cada frame vive lo que tarda la detección y se suelta;
    no se guarda ni el anterior.
  - Lo único que sale es un booleano (`presencia.update`), y solo cuando cambia.
  - Si no hay cámara o falta OpenCV, no falla: queda apagada y lo avisa en el log.

Detección: clasificador Haar de caras de OpenCV (viene dentro de opencv-python-headless 4.x; OpenCV 5 ya no
lo trae) sobre un frame gris de 320×240, 2 veces por segundo. Una cara que no se detecta un instante (mirar a
un lado, mala luz) no cambia nada: «presente» necesita 2 frames seguidos con cara y «ausente», AUSENTE_TRAS_S
segundos sin ninguna.

Todo lo de sistema (cv2, la cámara) se inyecta, así que las pruebas corren sin cámara.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Awaitable, Callable, Optional

from shared.protocol import PresenciaUpdate

log = logging.getLogger("nix.camara")

ANCHO, ALTO = 320, 240
AUSENTE_TRAS_S = 4.0
PRESENTE_TRAS_FRAMES = 2


class SinCamara(RuntimeError):
    """No hay cámara, está ocupada o falta OpenCV."""


def _cv2():
    try:
        import cv2
    except Exception as e:  # noqa: BLE001 - ImportError o DLL que falta en Windows
        raise SinCamara(f"falta OpenCV ({e}); instala requirements-client.txt") from e
    return cv2


class DetectorCaras:
    """Haar de OpenCV: ¿hay al menos una cara en el frame? (frame BGR o gris, de cualquier tamaño)."""

    def __init__(self, cv2=None):
        self.cv2 = cv2 or _cv2()
        import os
        ruta = os.path.join(self.cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        self.clasificador = self.cv2.CascadeClassifier(ruta)
        if self.clasificador.empty():
            raise SinCamara(f"no pude cargar el detector de caras ({ruta})")

    def __call__(self, frame) -> bool:
        gris = frame if frame.ndim == 2 else self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY)
        caras = self.clasificador.detectMultiScale(gris, scaleFactor=1.15, minNeighbors=5, minSize=(40, 40))
        return len(caras) > 0


class CamaraOpenCV:
    """La cámara `indice` a 320×240. read() devuelve un frame (o None si falló); cerrar() la suelta."""

    def __init__(self, indice: int = 0, cv2=None):
        self.cv2 = cv2 or _cv2()
        api = self.cv2.CAP_DSHOW if sys.platform == "win32" else self.cv2.CAP_ANY  # DSHOW abre más rápido en Windows
        self.cap = self.cv2.VideoCapture(indice, api)
        if not self.cap.isOpened():
            self.cap.release()
            raise SinCamara(f"no hay cámara {indice} o está ocupada por otro programa")
        self.cap.set(self.cv2.CAP_PROP_FRAME_WIDTH, ANCHO)
        self.cap.set(self.cv2.CAP_PROP_FRAME_HEIGHT, ALTO)

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if frame.shape[1] > ANCHO:                       # hay cámaras que ignoran el tamaño pedido
            frame = self.cv2.resize(frame, (ANCHO, ALTO), interpolation=self.cv2.INTER_AREA)
        return frame

    def cerrar(self) -> None:
        self.cap.release()


class Presencia:
    def __init__(self, indice: int = 0, fps: float = 2.0,
                 abrir: Optional[Callable[[], object]] = None, detector: Optional[Callable[[object], bool]] = None,
                 al_cambiar_estado: Optional[Callable[[bool], None]] = None):
        self.indice, self.fps = indice, min(max(fps, 0.5), 2.0)
        self.abrir = abrir or (lambda: CamaraOpenCV(self.indice))
        self.detector = detector
        self.al_cambiar_estado = al_cambiar_estado
        self.camara = None
        self.activa = False                              # apagada al iniciar, siempre
        self.presente: Optional[bool] = None             # None = todavía no se sabe
        self.frames_con_cara = 0
        self.ultima_cara: Optional[float] = None
        self.enviar: Optional[Callable[[object], Awaitable[None]]] = None
        self.enviados = 0

    # ------------------------------------------------------------------ control
    def activar(self) -> bool:
        """Abre la cámara y el detector. Si algo falta, avisa, queda apagada y devuelve False."""
        if self.activa:
            return True
        try:
            if self.detector is None:
                self.detector = DetectorCaras()
            self.camara = self.abrir()
        except Exception as e:  # noqa: BLE001 - SinCamara u otro error de OpenCV / del driver
            self.camara = None
            log.warning("📷 la cámara queda apagada: %s", e)
            return False
        self.activa, self.presente, self.frames_con_cara, self.ultima_cara = True, None, 0, None
        log.info("📷 cámara ENCENDIDA (solo se manda si hay alguien: sí/no)")
        self._avisar_estado()
        return True

    def desactivar(self) -> None:
        """Apaga al instante: suelta la cámara y le dice al servidor que ya no hay dato de presencia."""
        if not self.activa:
            return
        self.activa = False
        camara, self.camara = self.camara, None
        if camara is not None:
            try:
                camara.cerrar()
            except Exception:  # noqa: BLE001
                pass
        self.presente = None
        log.info("📷 cámara APAGADA")
        self._avisar_estado()
        self._mandar_luego(PresenciaUpdate(ts=_ahora_ms(), presente=False, activa=False))

    def conectar(self, enviar: Optional[Callable[[object], Awaitable[None]]]) -> None:
        """Al conectarse (función de envío) y al desconectarse (None). Cada sesión del servidor empieza sin
        dato, así que al reconectar se manda el estado actual si ya se conoce."""
        self.enviar = enviar
        if enviar is not None and self.activa and self.presente is not None:
            self._mandar_luego(PresenciaUpdate(ts=_ahora_ms(), presente=self.presente))

    def _avisar_estado(self) -> None:
        if self.al_cambiar_estado is not None:
            self.al_cambiar_estado(self.activa)

    def _mandar_luego(self, msg) -> None:
        if self.enviar is None:
            return
        try:
            asyncio.get_running_loop().create_task(self._mandar(msg))
        except RuntimeError:                             # sin bucle (pruebas síncronas)
            pass

    async def _mandar(self, msg) -> None:
        if self.enviar is None:
            return
        try:
            await self.enviar(msg)
            self.enviados += 1
        except Exception as e:  # noqa: BLE001 - conexión caída: se manda al reconectar
            log.debug("no pude mandar la presencia: %s", e)

    # ------------------------------------------------------------------ bucle
    async def correr(self) -> None:
        while True:
            await asyncio.sleep(1 / self.fps)
            if self.activa:
                try:
                    await self.paso()
                except Exception:  # noqa: BLE001 - un frame fallido no tumba al cliente
                    log.exception("📷 la detección falló; sigo intentando")

    async def paso(self, ahora: Optional[float] = None) -> Optional[PresenciaUpdate]:
        """Lee un frame, detecta y lo suelta. Devuelve el mensaje si la presencia cambió (y lo manda)."""
        ahora = time.monotonic() if ahora is None else ahora
        camara = self.camara
        if camara is None:
            return None
        frame = await asyncio.to_thread(camara.read)
        if frame is None:
            return None
        try:
            hay_cara = bool(await asyncio.to_thread(self.detector, frame))
        finally:
            del frame                                    # el frame no sobrevive a la detección
        if not self.activa:                              # se apagó mientras detectaba
            return None
        if self.ultima_cara is None:
            self.ultima_cara = ahora                     # se cuenta desde que se encendió
        if hay_cara:
            self.frames_con_cara += 1
            self.ultima_cara = ahora
        else:
            self.frames_con_cara = 0
        if self.presente is not True and self.frames_con_cara >= PRESENTE_TRAS_FRAMES:
            nuevo = True
        elif self.presente is not False and not hay_cara and ahora - self.ultima_cara >= AUSENTE_TRAS_S:
            nuevo = False
        else:
            return None
        self.presente = nuevo
        log.info("📷 %s", "hay alguien frente a la cámara" if nuevo else "no hay nadie frente a la cámara")
        msg = PresenciaUpdate(ts=_ahora_ms(), presente=nuevo)
        await self._mandar(msg)
        return msg


def _ahora_ms() -> int:
    return int(time.time() * 1000)
