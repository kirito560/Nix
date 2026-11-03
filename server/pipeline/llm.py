"""
Modelo de lenguaje (LLM) en streaming detrás de una interfaz común: `responder(mensajes)` devuelve los
trozos de texto a medida que llegan, para que el TTS pueda empezar con la primera frase.

  - LLMGroq: API compatible con OpenAI de Groq (https://console.groq.com/docs). Con los modelos gpt-oss se
    manda `reasoning_effort` (low = menos espera antes del primer token); solo se lee `delta.content`, así
    que el razonamiento nunca llega a la voz. Si Groq retira el modelo (404 model_not_found), pasa al
    modelo de respaldo y se queda con él. Si el modelo está en su límite de uso (429) o Groq no tiene
    capacidad (498/503), ESE turno lo responde el modelo de respaldo y el siguiente vuelve a probar el
    principal; en el log sale «RESPALDO». Si el respaldo también falla, el error sube y el Orquestador dice
    la frase de error.
  - LLMRouter: un router local compatible con OpenAI (freellmapi) con model="auto": el router elige entre
    varios proveedores gratuitos. Se usa con LLM_PROVEEDOR=freellmapi (URL y clave en FREELLMAPI_URL y
    FREELLMAPI_CLAVE). Va dentro de LLMConRespaldo: si el router falla antes de decir nada (sin conexión,
    429, 5xx, tiempo agotado), ese turno lo responde Groq directo y en el log sale «RESPALDO».
  - LLMStub: sin clave. Repite lo que dijo el usuario, para probar la tubería completa.
"""
from __future__ import annotations

import abc
import asyncio
import json
import logging
from typing import AsyncIterator

from server.config import ConfigServidor

log = logging.getLogger("nix.llm")

MARGEN_RAZONAMIENTO = 100


class ErrorLLM(RuntimeError):
    pass


class ModeloRetirado(ErrorLLM):
    """El modelo no existe o la cuenta ya no tiene acceso (los modelos en vista previa pueden retirarse)."""


class LimiteDeUso(ErrorLLM):
    """Pasajero: límite de tokens o de peticiones por minuto (429) o Groq sin capacidad (498, 503). En la cuenta
    gratuita, Qwen admite 7.000 tokens de entrada por minuto (~5 turnos); el respaldo tiene su propio límite."""


ESTADOS_PASAJEROS = {429, 498, 503}


class ProveedorLLM(abc.ABC):
    nombre: str

    @abc.abstractmethod
    def responder(self, mensajes: list[dict]) -> AsyncIterator[str]: ...


class LLMStub(ProveedorLLM):
    nombre = "stub"

    async def responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        ultimo = next((m["content"] for m in reversed(mensajes) if m["role"] == "user"), "")
        for palabra in f"(simulado) Me dijiste: {ultimo}. ¡GG!".split(" "):
            await asyncio.sleep(0)
            yield palabra + " "


class LLMGroq(ProveedorLLM):
    nombre = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, modelo: str, max_tokens: int = 300, temperatura: float = 0.8,
                 razonamiento: str = "low", cliente=None, respaldo: str = ""):
        from server.pipeline import groq_http
        self.api_key, self.modelo, self.respaldo = api_key, modelo, respaldo
        self.max_tokens, self.temperatura, self.razonamiento = max_tokens, temperatura, razonamiento
        self.http = cliente or groq_http.cliente()

    def cuerpo(self, mensajes: list[dict], modelo: str | None = None) -> dict:
        modelo = modelo or self.modelo
        cuerpo = {"model": modelo, "messages": mensajes, "stream": True,
                  "max_completion_tokens": self.max_tokens, "temperature": self.temperatura}
        if modelo.startswith("openai/gpt-oss") and self.razonamiento:
            cuerpo["reasoning_effort"] = self.razonamiento
            # gpt-oss gasta tokens razonando antes de responder (~30 con "low", medido el 2026-11-02) y
            # cuentan dentro del límite: se le da ese margen para que la respuesta no salga vacía.
            cuerpo["max_completion_tokens"] = self.max_tokens + MARGEN_RAZONAMIENTO
        return cuerpo

    async def responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        try:
            async for trozo in self._responder(mensajes, self.modelo):
                yield trozo
        except ModeloRetirado:
            if not self.respaldo or self.respaldo == self.modelo:
                raise
            log.warning("🧠 RESPALDO: Groq ya no ofrece %s; cambio al modelo de respaldo %s para siempre",
                        self.modelo, self.respaldo)
            self.modelo = self.respaldo
            async for trozo in self._responder(mensajes, self.modelo):
                yield trozo
        except LimiteDeUso as e:
            # Llega antes de cualquier texto (es la respuesta HTTP), así que el usuario no nota el cambio.
            if not self.respaldo or self.respaldo == self.modelo:
                raise
            log.warning("🧠 RESPALDO: %s no responde ahora (%s); este turno lo responde %s",
                        self.modelo, e, self.respaldo)
            try:
                async for trozo in self._responder(mensajes, self.respaldo):
                    yield trozo
            except Exception as e2:
                log.error("🧠 RESPALDO: %s también falló (%s)", self.respaldo, e2)
                raise

    async def _responder(self, mensajes: list[dict], modelo: str) -> AsyncIterator[str]:
        async with self.http.stream("POST", self.URL, json=self.cuerpo(mensajes, modelo),
                                    headers={"Authorization": f"Bearer {self.api_key}"}) as r:
            if r.status_code >= 400:
                await r.aread()
                if r.status_code == 404 and "model_not_found" in r.text:
                    error = ModeloRetirado
                elif r.status_code in ESTADOS_PASAJEROS:
                    error = LimiteDeUso
                else:
                    error = ErrorLLM
                raise error(f"Groq respondió {r.status_code}: {r.text[:200]}")
            async for linea in r.aiter_lines():
                if not linea.startswith("data:"):
                    continue
                dato = linea[5:].strip()
                if dato == "[DONE]":
                    return
                opciones = json.loads(dato).get("choices") or [{}]
                contenido = (opciones[0].get("delta") or {}).get("content")
                if contenido:
                    yield contenido


class LLMRouter(ProveedorLLM):
    """Router local compatible con OpenAI (freellmapi). Mismo formato que LLMGroq: trozos de `delta.content`.

    Sin reintentos de la librería (cada uno sumaría latencia: para eso está el respaldo con Groq) y con un
    tope de espera hasta el primer texto, porque el router puede tardar probando proveedores."""
    nombre = "freellmapi"
    MODELO = "auto"

    def __init__(self, url: str, clave: str, max_tokens: int = 300, temperatura: float = 0.8,
                 espera_s: float = 4.0, http_client=None):
        from openai import AsyncOpenAI
        self.max_tokens, self.temperatura, self.espera_s = max_tokens, temperatura, espera_s
        self.cliente = AsyncOpenAI(base_url=url, api_key=clave or "sin-clave", max_retries=0,
                                   timeout=espera_s, http_client=http_client)
        self.ultimo_modelo = ""

    async def responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        flujo = await asyncio.wait_for(self.cliente.chat.completions.create(
            model=self.MODELO, messages=mensajes, stream=True,
            max_tokens=self.max_tokens, temperature=self.temperatura), self.espera_s)
        try:
            iterador = aiter(flujo)
            primero, modelo = True, ""
            while True:
                try:
                    # Hasta el primer texto rige el tope de espera; después, el timeout del cliente.
                    trozo = await (asyncio.wait_for(anext(iterador), self.espera_s) if primero else anext(iterador))
                except StopAsyncIteration:
                    return
                if primero and trozo.model and trozo.model != modelo:
                    modelo = self.ultimo_modelo = trozo.model
                    log.info("🧠 el router eligió %s", modelo)
                contenido = trozo.choices[0].delta.content if trozo.choices else None
                if contenido:
                    primero = False
                    yield contenido
        finally:
            await flujo.close()


class LLMConRespaldo(ProveedorLLM):
    """Usa `principal`; si falla antes de dar texto, ese turno lo responde `respaldo` (el siguiente vuelve a
    probar el principal). Si ya dijo algo, el error sube: no se puede empezar otra respuesta a medias."""

    def __init__(self, principal: ProveedorLLM, respaldo: ProveedorLLM):
        self.principal, self.respaldo = principal, respaldo
        self.nombre = principal.nombre

    async def responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        dijo_algo = False
        try:
            async for trozo in self.principal.responder(mensajes):
                dijo_algo = True
                yield trozo
            return
        except Exception as e:  # noqa: BLE001 - conexión, 429, 5xx, tiempo agotado o cualquier otro fallo
            if dijo_algo:
                raise
            log.warning("🧠 RESPALDO: el router %s falló (%s); este turno lo responde %s",
                        self.principal.nombre, _describir(e), self.respaldo.nombre)
        async for trozo in self.respaldo.responder(mensajes):
            yield trozo


def _describir(e: Exception) -> str:
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "tiempo agotado"
    estado = getattr(e, "status_code", None)
    return f"{type(e).__name__} {estado}" if estado else f"{type(e).__name__}: {e}"[:200]


def _crear_groq(cfg: ConfigServidor) -> LLMGroq:
    return LLMGroq(cfg.groq_api_key, cfg.llm_modelo, cfg.llm_max_tokens, cfg.llm_temperatura,
                   cfg.llm_razonamiento, respaldo=cfg.llm_modelo_respaldo)


def crear_llm(cfg: ConfigServidor) -> ProveedorLLM:
    """LLM_PROVEEDOR=auto (o sin la variable) usa Groq si hay GROQ_API_KEY; si no, el simulado (con aviso).
    LLM_PROVEEDOR=freellmapi usa el router, con Groq directo de respaldo."""
    if cfg.llm_proveedor == "freellmapi":
        if not cfg.freellmapi_url:
            log.warning("🧠 LLM_PROVEEDOR=freellmapi pero falta FREELLMAPI_URL en .env: uso Groq directo")
        else:
            router = LLMRouter(cfg.freellmapi_url, cfg.freellmapi_clave, cfg.llm_max_tokens, cfg.llm_temperatura,
                               cfg.freellmapi_espera_s)
            if cfg.groq_api_key:
                return LLMConRespaldo(router, _crear_groq(cfg))
            log.warning("🧠 router sin respaldo: falta GROQ_API_KEY")
            return router
    if cfg.llm_proveedor in ("auto", "groq", "freellmapi") and cfg.groq_api_key:
        return _crear_groq(cfg)
    log.warning("🧠 LLM simulado (stub): %s", "sin GROQ_API_KEY" if cfg.llm_proveedor != "stub" else "elegido")
    return LLMStub()
