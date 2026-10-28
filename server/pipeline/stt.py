"""
Reconocimiento de voz (STT) detrás de una interfaz común.

Por cada frase detectada por el VAD se abre una sesión: recibe el audio mientras el usuario habla
(enviar) y, cuando el VAD cierra la frase, devuelve el texto (finalizar).

  - STTDeepgram: streaming real por WebSocket (Nova-3). El audio viaja mientras hablas; al cerrar la frase
    se manda {"type": "Finalize"} y se espera la respuesta con from_finalize=true.
  - STTGroqWhisper: Whisper en Groq. No es streaming: junta el audio y lo sube al cerrar la frase.
    Aplica los filtros de alucinaciones de Whisper.
  - STTStub: sin claves. Devuelve un texto simulado con la duración de la frase.
"""
from __future__ import annotations

import abc
import asyncio
import io
import json
import logging
import wave
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import numpy as np

from server.config import ConfigServidor
from server.pipeline.filtros_whisper import duracion_voz_s, validar_transcripcion

log = logging.getLogger("nix.stt")


@dataclass(frozen=True)
class Transcripcion:
    texto: Optional[str]          # None si se descartó
    motivo: str = ""              # por qué se descartó


class SesionSTT(abc.ABC):
    @abc.abstractmethod
    async def enviar(self, pcm16: bytes) -> None: ...

    @abc.abstractmethod
    async def finalizar(self) -> Transcripcion: ...

    @abc.abstractmethod
    async def cancelar(self) -> None: ...


class ProveedorSTT(abc.ABC):
    nombre: str

    @abc.abstractmethod
    async def abrir(self) -> SesionSTT: ...


def a_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()


# --------------------------------------------------------------------------- stub
class _SesionAcumulada(SesionSTT):
    """Junta el audio de la frase; finalizar() lo procesa de una vez."""

    def __init__(self, proveedor: "ProveedorSTT"):
        self.proveedor = proveedor
        self.partes: list[bytes] = []

    async def enviar(self, pcm16: bytes) -> None:
        self.partes.append(pcm16)

    async def cancelar(self) -> None:
        self.partes.clear()

    def audio(self) -> np.ndarray:
        return np.frombuffer(b"".join(self.partes), dtype="<i2").astype(np.float32) / 32768.0


class _SesionStub(_SesionAcumulada):
    async def finalizar(self) -> Transcripcion:
        segundos = len(self.audio()) / 16000
        return Transcripcion(f"(simulado) dijiste algo de {segundos:.1f} segundos")


class STTStub(ProveedorSTT):
    nombre = "stub"

    async def abrir(self) -> SesionSTT:
        return _SesionStub(self)


# --------------------------------------------------------------------------- Groq Whisper
class _SesionGroq(_SesionAcumulada):
    async def finalizar(self) -> Transcripcion:
        audio = self.audio()
        voz = duracion_voz_s(audio)
        if voz < 0.4:
            return Transcripcion(None, f"poca voz ({voz:.2f} s)")
        datos = await self.proveedor.transcribir(audio)
        texto, motivo = validar_transcripcion(datos.get("text", ""), datos.get("segments"), voz)
        return Transcripcion(texto, motivo)


class STTGroqWhisper(ProveedorSTT):
    nombre = "groq"
    URL = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, api_key: str, modelo: str, idioma: str, cliente=None):
        import httpx
        self.api_key, self.modelo, self.idioma = api_key, modelo, idioma
        self.http = cliente or httpx.AsyncClient(timeout=10.0)

    async def abrir(self) -> SesionSTT:
        return _SesionGroq(self)

    async def transcribir(self, audio: np.ndarray) -> dict:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(a_pcm16(audio))
        r = await self.http.post(
            self.URL, headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("frase.wav", buf.getvalue(), "audio/wav")},
            data={"model": self.modelo, "language": self.idioma, "response_format": "verbose_json",
                  "temperature": "0"})
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- Deepgram (streaming)
class _SesionDeepgram(SesionSTT):
    def __init__(self, ws, espera_final_s: float):
        self.ws = ws
        self.espera_final_s = espera_final_s
        self.finales: list[str] = []
        self.finalizado = asyncio.Event()
        self.lector = asyncio.create_task(self._leer(), name="deepgram-lector")

    async def _leer(self) -> None:
        try:
            async for crudo in self.ws:
                msg = json.loads(crudo)
                if msg.get("type") != "Results":
                    continue
                if msg.get("is_final"):
                    texto = msg["channel"]["alternatives"][0].get("transcript", "").strip()
                    if texto:
                        self.finales.append(texto)
                if msg.get("from_finalize"):
                    self.finalizado.set()
        except Exception as e:  # noqa: BLE001 - la conexión puede cerrarse en cualquier momento
            log.debug("Deepgram cerró la lectura: %s", e)
        finally:
            self.finalizado.set()

    async def enviar(self, pcm16: bytes) -> None:
        await self.ws.send(pcm16)

    async def finalizar(self) -> Transcripcion:
        try:
            await self.ws.send(json.dumps({"type": "Finalize"}))
            # Con muy poco audio Deepgram puede no responder con from_finalize: se espera un máximo.
            await asyncio.wait_for(self.finalizado.wait(), self.espera_final_s)
        except asyncio.TimeoutError:
            log.debug("Deepgram no confirmó Finalize en %.1f s", self.espera_final_s)
        finally:
            await self._cerrar()
        texto = " ".join(self.finales).strip()
        return Transcripcion(texto) if texto else Transcripcion(None, "Deepgram no reconoció palabras")

    async def cancelar(self) -> None:
        await self._cerrar()

    async def _cerrar(self) -> None:
        try:
            await self.ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:  # noqa: BLE001 - ya estaba cerrada
            pass
        await self.ws.close()
        self.lector.cancel()
        await asyncio.gather(self.lector, return_exceptions=True)


class STTDeepgram(ProveedorSTT):
    nombre = "deepgram"

    def __init__(self, api_key: str, modelo: str, idioma: str,
                 url_base: str = "wss://api.deepgram.com/v1/listen", espera_final_s: float = 1.5):
        self.api_key, self.espera_final_s = api_key, espera_final_s
        parametros = {"model": modelo, "language": idioma, "encoding": "linear16", "sample_rate": 16000,
                      "channels": 1, "interim_results": "true", "punctuate": "true", "smart_format": "true"}
        self.url = f"{url_base}?{urlencode(parametros)}"

    async def abrir(self) -> SesionSTT:
        from websockets.asyncio.client import connect
        ws = await connect(self.url, additional_headers={"Authorization": f"Token {self.api_key}"},
                           open_timeout=5)
        return _SesionDeepgram(ws, self.espera_final_s)


def crear_stt(cfg: ConfigServidor) -> ProveedorSTT:
    """STT_PROVEEDOR=auto elige Deepgram si hay clave, luego Groq, y si no, el stub (con aviso)."""
    eleccion = cfg.stt_proveedor
    if eleccion == "auto":
        eleccion = "deepgram" if cfg.deepgram_api_key else "groq" if cfg.groq_api_key else "stub"
    if eleccion == "deepgram" and cfg.deepgram_api_key:
        return STTDeepgram(cfg.deepgram_api_key, cfg.deepgram_modelo, cfg.deepgram_idioma)
    if eleccion == "groq" and cfg.groq_api_key:
        return STTGroqWhisper(cfg.groq_api_key, cfg.groq_modelo_stt, cfg.idioma)
    if eleccion != "stub":
        log.warning("🎙️ STT %r sin API key; uso el STT simulado (stub)", eleccion)
    else:
        log.warning("🎙️ STT simulado (stub): no transcribe de verdad")
    return STTStub()
