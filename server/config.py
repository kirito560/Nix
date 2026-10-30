"""Configuración del servidor, leída del .env. Ver .env.example para la descripción de cada variable."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from shared.auth import validar_token
from shared.config_base import RAIZ, Entorno


@dataclass(frozen=True)
class ConfigServidor:
    token: str
    host: str = "127.0.0.1"
    puerto: int = 8765
    redes_permitidas: tuple = field(default_factory=lambda: (ipaddress.ip_network("127.0.0.0/8"),))
    personaje: str = "nix"
    carpeta_personajes: Path = RAIZ / "personajes"
    datos_dir: Path = Path("/datos/nix")
    # STT
    stt_proveedor: str = "auto"               # auto | deepgram | groq | stub
    deepgram_api_key: str = ""
    deepgram_modelo: str = "nova-3"
    deepgram_idioma: str = "es-419"
    groq_api_key: str = ""
    groq_modelo_stt: str = "whisper-large-v3-turbo"
    idioma: str = "es"
    # VAD
    silero_onnx: str = "/datos/modelos/silero_vad.onnx"
    vad_umbral: float = 0.5
    vad_silencio_ms: float = 400.0
    vad_min_voz_ms: float = 250.0
    stt_adelanto_ms: float = 200.0            # 0 = no adelantar la transcripción
    # LLM
    llm_proveedor: str = "auto"               # auto | groq | stub
    llm_modelo: str = "qwen/qwen3.8-27b"
    llm_modelo_respaldo: str = "openai/gpt-oss-120b"
    llm_razonamiento: str = "low"             # solo modelos gpt-oss: low | medium | high
    llm_max_tokens: int = 300
    llm_temperatura: float = 0.8
    historial_turnos: int = 10                # pares usuario/personaje que se recuerdan (la memoria llega en la fase 4)
    # TTS
    tts_proveedor: str = "auto"               # auto | edge | stub
    tts_voz: str = ""                         # vacío = la voz de la ficha del personaje
    tts_velocidad: str = "+0%"
    tts_conexiones: int = 1                   # conexiones con edge-tts abiertas de antemano (0 = abrir en cada frase)
    tts_ocio_s: float = 12.0                  # Microsoft cierra las inactivas entre 15 y 45 s
    # Barge-in: interrumpirla hablando. Con altavoces, su propia voz puede volver por el micrófono:
    # mientras habla, una frase solo cuenta si dura al menos BARGE_MIN_VOZ_MS; si no, se toma por eco.
    barge_in: bool = True
    barge_min_voz_ms: float = 400.0
    # Percepción: apagada por defecto; nunca se envían imágenes a la nube sin activarla a mano.
    vision_habilitada: bool = False

    @classmethod
    def desde_entorno(cls, fuente: Optional[Mapping[str, str]] = None) -> "ConfigServidor":
        e = Entorno(fuente)
        return cls(
            token=validar_token(e.texto("NIX_TOKEN")),
            host=e.texto("NIX_HOST", "127.0.0.1"),
            puerto=e.entero("NIX_PUERTO", 8765),
            redes_permitidas=tuple(ipaddress.ip_network(r, strict=False)
                                   for r in e.lista("NIX_REDES_PERMITIDAS", "127.0.0.0/8,192.168.1.0/24")),
            personaje=e.texto("PERSONAJE", "nix").lower(),
            datos_dir=Path(e.texto("NIX_DATOS_DIR", "/datos/nix")),
            stt_proveedor=e.texto("STT_PROVEEDOR", "auto").lower(),
            deepgram_api_key=e.texto("DEEPGRAM_API_KEY"),
            deepgram_modelo=e.texto("DEEPGRAM_MODELO", "nova-3"),
            deepgram_idioma=e.texto("DEEPGRAM_IDIOMA", "es-419"),
            groq_api_key=e.texto("GROQ_API_KEY"),
            groq_modelo_stt=e.texto("GROQ_MODELO_STT", "whisper-large-v3-turbo"),
            idioma=e.texto("IDIOMA", "es"),
            silero_onnx=e.texto("SILERO_ONNX", "/datos/modelos/silero_vad.onnx"),
            vad_umbral=e.decimal("VAD_UMBRAL", 0.5),
            vad_silencio_ms=e.decimal("VAD_SILENCIO_MS", 400.0),
            vad_min_voz_ms=e.decimal("VAD_MIN_VOZ_MS", 250.0),
            stt_adelanto_ms=e.decimal("STT_ADELANTO_MS", 200.0),
            llm_proveedor=e.texto("LLM_PROVEEDOR", "auto").lower(),
            llm_modelo=e.texto("LLM_MODELO", "qwen/qwen3.8-27b"),
            llm_modelo_respaldo=e.texto("LLM_MODELO_RESPALDO", "openai/gpt-oss-120b"),
            llm_razonamiento=e.texto("LLM_RAZONAMIENTO", "low").lower(),
            llm_max_tokens=e.entero("LLM_MAX_TOKENS", 300),
            llm_temperatura=e.decimal("LLM_TEMPERATURA", 0.8),
            historial_turnos=e.entero("HISTORIAL_TURNOS", 10),
            tts_proveedor=e.texto("TTS_PROVEEDOR", "auto").lower(),
            tts_voz=e.texto("TTS_VOZ"),
            tts_velocidad=e.texto("TTS_VELOCIDAD", "+0%"),
            tts_conexiones=e.entero("TTS_CONEXIONES", 1),
            tts_ocio_s=e.decimal("TTS_OCIO_S", 12.0),
            barge_in=e.booleano("BARGE_IN", True),
            barge_min_voz_ms=e.decimal("BARGE_MIN_VOZ_MS", 400.0),
            vision_habilitada=e.booleano("VISION_HABILITADA", False),
        )

    def ip_permitida(self, ip: str) -> bool:
        try:
            direccion = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if direccion.version == 6 and direccion.ipv4_mapped:
            direccion = direccion.ipv4_mapped
        return any(direccion in red for red in self.redes_permitidas)
