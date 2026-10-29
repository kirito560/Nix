"""
Modelo de lenguaje (LLM) en streaming detrás de una interfaz común: `responder(mensajes)` devuelve los
trozos de texto a medida que llegan, para que el TTS pueda empezar con la primera frase.

  - LLMGroq: API compatible con OpenAI de Groq (https://console.groq.com/docs). Con los modelos gpt-oss se
    manda `reasoning_effort` (low = menos espera antes del primer token); solo se lee `delta.content`, así
    que el razonamiento nunca llega a la voz. Si Groq retira el modelo (404 model_not_found), pasa al
    modelo de respaldo y se queda con él.
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


class ErrorLLM(RuntimeError):
    pass


class ModeloRetirado(ErrorLLM):
    """El modelo no existe o la cuenta ya no tiene acceso (los modelos en vista previa pueden retirarse)."""


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
        import httpx
        self.api_key, self.modelo, self.respaldo = api_key, modelo, respaldo
        self.max_tokens, self.temperatura, self.razonamiento = max_tokens, temperatura, razonamiento
        self.http = cliente or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))

    def cuerpo(self, mensajes: list[dict]) -> dict:
        cuerpo = {"model": self.modelo, "messages": mensajes, "stream": True,
                  "max_completion_tokens": self.max_tokens, "temperature": self.temperatura}
        if self.modelo.startswith("openai/gpt-oss") and self.razonamiento:
            cuerpo["reasoning_effort"] = self.razonamiento
        return cuerpo

    async def responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        try:
            async for trozo in self._responder(mensajes):
                yield trozo
        except ModeloRetirado:
            if not self.respaldo or self.respaldo == self.modelo:
                raise
            log.warning("🧠 Groq ya no ofrece %s; cambio al modelo de respaldo %s", self.modelo, self.respaldo)
            self.modelo = self.respaldo
            async for trozo in self._responder(mensajes):
                yield trozo

    async def _responder(self, mensajes: list[dict]) -> AsyncIterator[str]:
        async with self.http.stream("POST", self.URL, json=self.cuerpo(mensajes),
                                    headers={"Authorization": f"Bearer {self.api_key}"}) as r:
            if r.status_code >= 400:
                await r.aread()
                error = ModeloRetirado if r.status_code == 404 and "model_not_found" in r.text else ErrorLLM
                raise error(f"Groq respondió {r.status_code}: {r.text[:300]}")
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


def crear_llm(cfg: ConfigServidor) -> ProveedorLLM:
    """LLM_PROVEEDOR=auto usa Groq si hay GROQ_API_KEY; si no, el simulado (con aviso)."""
    if cfg.llm_proveedor in ("auto", "groq") and cfg.groq_api_key:
        return LLMGroq(cfg.groq_api_key, cfg.llm_modelo, cfg.llm_max_tokens, cfg.llm_temperatura,
                       cfg.llm_razonamiento, respaldo=cfg.llm_modelo_respaldo)
    log.warning("🧠 LLM simulado (stub): %s", "sin GROQ_API_KEY" if cfg.llm_proveedor != "stub" else "elegido")
    return LLMStub()
