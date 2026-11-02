"""
Visión: qué hay en la pantalla del usuario, como contexto para el LLM.

  - ProveedorVision.describir(jpeg) -> descripción breve en español.
      * VisionGroq: modelo con visión de Groq (qwen/qwen3.8-27b acepta imágenes: ~0,9 s y ~800 tokens por
        captura de 768 px, medido el 2026-11-02).
      * VisionStub: sin clave. Devuelve «pantalla sin descripción».
  - ResumenPantalla: las últimas descripciones (la más nueva primero), recortadas a `max_tokens`, que el
    Orquestador añade al prompt de sistema. Caducan a los `caduca_s`.
  - Vista: recibe vision.delta / vision.frame del cliente. Con una descripción hecha en el cliente
    (vision.delta con caption) actualiza el resumen; con una imagen (vision.frame, solo si el usuario la
    pidió) la describe. Las imágenes viven en memoria lo que tarda la descripción: nunca se guardan.
    Describe como mucho una a la vez; si llegan más mientras tanto, solo se queda con la última.
"""
from __future__ import annotations

import abc
import asyncio
import base64
import binascii
import logging
import time
from collections import deque
from typing import Optional

from server.config import ConfigServidor
from shared.protocol import VisionDelta, VisionFrame

log = logging.getLogger("nix.vision")

SIN_DESCRIPCION = "pantalla sin descripción"
INSTRUCCION = ("Describe en una o dos frases cortas, en español, qué está haciendo el usuario en esta captura de "
               "su pantalla: el programa, juego o página y lo principal que se ve. No copies datos personales, "
               "contraseñas, números de cuenta, correos ni mensajes privados.")
MAX_JPEG_BYTES = 300_000
CARACTERES_POR_TOKEN = 4                 # aproximación para español; basta para poner un tope


class ProveedorVision(abc.ABC):
    nombre: str

    @abc.abstractmethod
    async def describir(self, jpeg: bytes) -> str: ...


class VisionStub(ProveedorVision):
    nombre = "stub"

    async def describir(self, jpeg: bytes) -> str:
        return SIN_DESCRIPCION


class VisionGroq(ProveedorVision):
    nombre = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, modelo: str, max_tokens: int = 80, cliente=None):
        from server.pipeline import groq_http
        self.api_key, self.modelo, self.max_tokens = api_key, modelo, max_tokens
        self.http = cliente or groq_http.cliente()

    async def describir(self, jpeg: bytes) -> str:
        imagen = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        cuerpo = {"model": self.modelo, "max_tokens": self.max_tokens, "temperature": 0.2,
                  "messages": [{"role": "user", "content": [
                      {"type": "text", "text": INSTRUCCION},
                      {"type": "image_url", "image_url": {"url": imagen}}]}]}
        if self.modelo.startswith("qwen/"):
            cuerpo["reasoning_effort"] = "none"            # sin «pensar»: más rápido y más barato
        r = await self.http.post(self.URL, json=cuerpo, headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        texto = (r.json()["choices"][0]["message"].get("content") or "").strip()
        return texto or SIN_DESCRIPCION


def crear_vision(cfg: ConfigServidor) -> ProveedorVision:
    """VISION_PROVEEDOR=auto usa Groq si hay GROQ_API_KEY; si no, el stub."""
    if cfg.vision_proveedor in ("auto", "groq") and cfg.groq_api_key:
        return VisionGroq(cfg.groq_api_key, cfg.vision_modelo)
    if cfg.vision_proveedor not in ("auto", "stub"):
        log.warning("👁️ visión %r sin API key; uso el stub", cfg.vision_proveedor)
    return VisionStub()


class ResumenPantalla:
    def __init__(self, max_tokens: int = 120, max_entradas: int = 3, caduca_s: float = 600.0):
        self.max_caracteres = max(40, max_tokens * CARACTERES_POR_TOKEN)
        self.caduca_s = caduca_s
        self.entradas: deque[tuple[float, str]] = deque(maxlen=max_entradas)

    def actualizar(self, texto: str, ahora: Optional[float] = None) -> None:
        texto = " ".join((texto or "").split())
        if not texto:
            return
        ahora = time.monotonic() if ahora is None else ahora
        if self.entradas and self.entradas[-1][1] == texto:
            self.entradas[-1] = (ahora, texto)             # misma pantalla: solo se refresca la hora
            return
        self.entradas.append((ahora, texto))

    def borrar(self) -> None:
        self.entradas.clear()

    def para_llm(self, ahora: Optional[float] = None) -> str:
        """Texto para el prompt de sistema (vacío si no hay nada reciente). Nunca pasa de max_tokens."""
        ahora = time.monotonic() if ahora is None else ahora
        lineas = [f"- {_hace(ahora - t)}: {texto}" for t, texto in reversed(self.entradas)
                  if ahora - t <= self.caduca_s]
        if not lineas:
            return ""
        cabecera = ("Lo que hay en la pantalla del usuario (lo más nuevo primero). Úsalo solo si viene al caso; "
                    "no lo comentes en cada respuesta:")
        texto = cabecera
        for linea in lineas:
            if len(texto) + 1 + len(linea) > self.max_caracteres:
                restante = self.max_caracteres - len(texto) - 2
                if texto == cabecera and restante > 20:      # la más nueva siempre entra, aunque recortada
                    texto += "\n" + linea[:restante].rsplit(" ", 1)[0] + "…"
                break
            texto += "\n" + linea
        return texto if texto != cabecera else ""


def _hace(segundos: float) -> str:
    if segundos < 60:
        return f"hace {max(0, round(segundos))} s"
    return f"hace {round(segundos / 60)} min"


class Vista:
    def __init__(self, proveedor: ProveedorVision, resumen: Optional[ResumenPantalla] = None):
        self.proveedor = proveedor
        self.resumen = resumen or ResumenPantalla()
        self.pendiente: Optional[VisionFrame] = None
        self.describiendo: Optional[asyncio.Task] = None
        self.descripciones = 0

    def recibir(self, msg) -> None:
        """Llamado por el gateway con cada mensaje de visión (ya autenticado y con la visión habilitada)."""
        if isinstance(msg, VisionDelta):
            if not msg.activa:
                self.resumen.borrar()
                self.pendiente = None
                log.info("👁️ el usuario pausó la captura de pantalla: olvido lo que había")
            elif msg.caption:
                self.resumen.actualizar(msg.caption)
                log.info("👁️ pantalla: %s", msg.caption)
        elif isinstance(msg, VisionFrame):
            self.pendiente = msg                           # si ya hay una describiéndose, gana la última
            if self.describiendo is None or self.describiendo.done():
                self.describiendo = asyncio.create_task(self._describir_pendientes(), name="vision")

    async def _describir_pendientes(self) -> None:
        while (msg := self.pendiente) is not None:
            self.pendiente = None
            try:
                jpeg = base64.b64decode(msg.jpeg_b64, validate=True)
            except (binascii.Error, ValueError):
                log.warning("👁️ imagen de pantalla inválida (base64)")
                continue
            if len(jpeg) > MAX_JPEG_BYTES:
                log.warning("👁️ imagen de pantalla demasiado grande (%d bytes); la ignoro", len(jpeg))
                continue
            inicio = time.monotonic()
            try:
                descripcion = await self.proveedor.describir(jpeg)
            except Exception as e:  # noqa: BLE001 - sin internet, límite de Groq...: queda la descripción del PC
                log.warning("👁️ no pude describir la pantalla (%s)", e)
                descripcion = SIN_DESCRIPCION
            finally:
                del jpeg
            self.descripciones += 1
            texto = descripcion if not msg.caption else f"{descripcion} ({msg.caption})"
            self.resumen.actualizar(texto)
            log.info("👁️ pantalla descrita en %.0f ms: %s", (time.monotonic() - inicio) * 1000, descripcion)

    async def cerrar(self) -> None:
        if self.describiendo is not None:
            self.describiendo.cancel()
            await asyncio.gather(self.describiendo, return_exceptions=True)
