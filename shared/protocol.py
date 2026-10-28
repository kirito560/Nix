"""
Contrato de mensajes WebSocket entre el cliente ("cuerpo", Windows) y el servidor ("cerebro", Ubuntu).

Hay dos clases de tramas:
  - Binarias: audio. Del cliente al servidor siempre son `audio.in`; del servidor al cliente, `audio.out`.
  - Texto: JSON con un campo `type`. Todos los tipos están definidos aquí como modelos pydantic; ningún
    mensaje se construye con diccionarios sueltos. `parsear_mensaje` valida y devuelve el modelo correcto.
"""
from __future__ import annotations

import struct
import uuid
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

VERSION_PROTOCOLO = 1

# --------------------------------------------------------------------------- audio
SR_ENTRADA = 16000                       # PCM16 mono 16 kHz
MS_TRAMA = 20
MUESTRAS_TRAMA = SR_ENTRADA * MS_TRAMA // 1000   # 320
BYTES_TRAMA = MUESTRAS_TRAMA * 2                 # 640

_CABECERA_IN = struct.Struct("<Q")          # timestamp del cliente en ms
_CABECERA_OUT = struct.Struct("<16sQI")     # turn_id (UUID), pts en ms, sample_rate


class ErrorProtocolo(ValueError):
    """Trama o mensaje que no cumple el contrato."""


def codificar_audio_in(ts_ms: int, pcm: bytes) -> bytes:
    if len(pcm) % 2:
        raise ErrorProtocolo("el PCM16 debe tener un número par de bytes")
    return _CABECERA_IN.pack(ts_ms) + pcm


def decodificar_audio_in(trama: bytes) -> tuple[int, bytes]:
    if len(trama) < _CABECERA_IN.size or (len(trama) - _CABECERA_IN.size) % 2:
        raise ErrorProtocolo(f"audio.in mal formado ({len(trama)} bytes)")
    (ts_ms,) = _CABECERA_IN.unpack_from(trama)
    return ts_ms, trama[_CABECERA_IN.size:]


def codificar_audio_out(turn_id: str, pts_ms: int, sample_rate: int, pcm: bytes) -> bytes:
    if len(pcm) % 2:
        raise ErrorProtocolo("el PCM16 debe tener un número par de bytes")
    return _CABECERA_OUT.pack(uuid.UUID(turn_id).bytes, pts_ms, sample_rate) + pcm


def decodificar_audio_out(trama: bytes) -> tuple[str, int, int, bytes]:
    if len(trama) < _CABECERA_OUT.size or (len(trama) - _CABECERA_OUT.size) % 2:
        raise ErrorProtocolo(f"audio.out mal formado ({len(trama)} bytes)")
    crudo, pts_ms, sr = _CABECERA_OUT.unpack_from(trama)
    return str(uuid.UUID(bytes=crudo)), pts_ms, sr, trama[_CABECERA_OUT.size:]


# --------------------------------------------------------------------------- mensajes JSON
class _Mensaje(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def a_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


# Autenticación (ver shared/auth.py): el token nunca viaja; solo HMAC de nonces aleatorios.
class AuthChallenge(_Mensaje):
    type: Literal["auth.challenge"] = "auth.challenge"
    version: int = VERSION_PROTOCOLO
    nonce: str


class AuthResponse(_Mensaje):
    type: Literal["auth.response"] = "auth.response"
    mac: str
    nonce_cliente: str              # para que el servidor también demuestre conocer el token


class AuthOk(_Mensaje):
    type: Literal["auth.ok"] = "auth.ok"
    mac_servidor: str
    sesion: str
    personaje: str


# Cliente -> servidor
class VisionDelta(_Mensaje):
    type: Literal["vision.delta"] = "vision.delta"
    ts: int
    phash_dist: int
    caption: Optional[str] = None


class VisionFrame(_Mensaje):
    type: Literal["vision.frame"] = "vision.frame"
    ts: int
    jpeg_b64: str


class UserState(_Mensaje):
    type: Literal["user.state"] = "user.state"
    ts: int
    valence: float = Field(ge=-1.0, le=1.0)
    attention: float = Field(ge=0.0, le=1.0)


class PlaybackAck(_Mensaje):
    type: Literal["playback.ack"] = "playback.ack"
    turn_id: str
    pts_played: int


# Servidor -> cliente
class SttText(_Mensaje):
    """Lo que el servidor entendió que dijo el usuario."""
    type: Literal["stt.text"] = "stt.text"
    turn_id: str
    texto: str
    final: bool = True


class LlmText(_Mensaje):
    """Una frase de la respuesta del personaje, justo antes de su audio. final=True cierra el turno
    (puede llegar con texto vacío)."""
    type: Literal["llm.text"] = "llm.text"
    turn_id: str
    texto: str
    final: bool = False


class AvatarCmd(_Mensaje):
    type: Literal["avatar.cmd"] = "avatar.cmd"
    turn_id: str
    pts: int
    expression: Optional[str] = None
    hotkey: Optional[str] = None
    gaze: Optional[tuple[float, float]] = None
    mouth_form: Optional[float] = None


class PlaybackFlush(_Mensaje):
    type: Literal["playback.flush"] = "playback.flush"
    turn_id: str


class StateSync(_Mensaje):
    type: Literal["state.sync"] = "state.sync"
    emotion: dict[str, float]
    mood_label: str


class Error(_Mensaje):
    type: Literal["error"] = "error"
    codigo: str
    detalle: str = ""


Mensaje = Annotated[
    Union[AuthChallenge, AuthResponse, AuthOk, VisionDelta, VisionFrame, UserState, PlaybackAck,
          SttText, LlmText, AvatarCmd, PlaybackFlush, StateSync, Error],
    Field(discriminator="type"),
]
_ADAPTADOR = TypeAdapter(Mensaje)

MENSAJES_DEL_CLIENTE = (AuthResponse, VisionDelta, VisionFrame, UserState, PlaybackAck)
MENSAJES_DEL_SERVIDOR = (AuthChallenge, AuthOk, SttText, LlmText, AvatarCmd, PlaybackFlush, StateSync, Error)


def parsear_mensaje(texto: str | bytes) -> _Mensaje:
    try:
        return _ADAPTADOR.validate_json(texto)
    except Exception as e:  # noqa: BLE001 - pydantic.ValidationError o JSON inválido
        raise ErrorProtocolo(f"mensaje inválido: {e}") from e
