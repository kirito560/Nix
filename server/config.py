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
    # Palabras que Whisper debe reconocer bien (prompt de contexto) y confianza mínima (avg_logprob):
    # por debajo, Nix pide que se lo repitan en vez de responder a algo mal entendido.
    stt_prompt: str = "Nix, Minecraft, PvP, Roblox, Visual Studio, VTube Studio."
    stt_min_logprob: float = -1.0
    # VAD
    silero_onnx: str = "/datos/modelos/silero_vad.onnx"
    vad_umbral: float = 0.5
    vad_silencio_ms: float = 300.0             # con 250 se cortaban frases con pausas de ~300 ms (2026-11-02)
    vad_min_voz_ms: float = 250.0
    vad_pre_roll_ms: float = 600.0            # audio de antes de que el VAD detecte la voz (no cortar el arranque)
    stt_adelanto_ms: float = 150.0            # 0 = no adelantar la transcripción; menor que vad_silencio_ms
    # Memoria de largo plazo (server/memoria.py)
    memoria: bool = True
    memoria_modelo: str = "openai/gpt-oss-20b"
    memoria_cada_turnos: int = 3
    # LLM
    llm_proveedor: str = "auto"               # auto | groq | freellmapi | stub
    freellmapi_url: str = ""                  # router local compatible con OpenAI, p. ej. http://127.0.0.1:3001/v1
    freellmapi_clave: str = ""
    freellmapi_espera_s: float = 4.0          # tope hasta el primer texto del router; pasado, responde Groq
    llm_modelo: str = "qwen/qwen3.8-27b"
    llm_modelo_respaldo: str = "openai/gpt-oss-120b"
    llm_razonamiento: str = "low"             # solo modelos gpt-oss: low | medium | high
    llm_max_tokens: int = 90                  # una o dos frases (~30-60 tokens en español)
    respuesta_max_oraciones: int = 2          # tope duro de oraciones por respuesta (0 = sin tope)
    llm_temperatura: float = 0.8
    llm_espera_s: float = 6.0                 # sin texto de Groq en este tiempo: responde el modelo de respaldo
    historial_turnos: int = 6                 # pares usuario/personaje que van en cada turno (lo de antes, a la memoria)
    # TTS
    tts_proveedor: str = "auto"               # auto | edge | stub
    tts_voz: str = ""                         # vacío = la voz de la ficha del personaje
    tts_velocidad: str = "+0%"
    tts_conexiones: int = 1                   # conexiones con edge-tts abiertas de antemano (0 = abrir en cada frase)
    tts_ocio_s: float = 12.0                  # Microsoft cierra las inactivas entre 15 y 45 s
    # Barge-in: interrumpirla hablando. Con altavoces, su propia voz puede volver por el micrófono:
    # mientras habla, una frase solo cuenta si dura al menos BARGE_MIN_VOZ_MS; si no, se toma por eco.
    stt_cierre_temprano: bool = True         # cerrar el turno si el adelanto ya es una frase completa (. ? !)
    barge_in: bool = True
    barge_min_voz_ms: float = 400.0
    # Percepción: apagada por defecto; nunca se envían imágenes a la nube sin activarla a mano.
    vision_habilitada: bool = False
    vision_proveedor: str = "auto"            # auto | groq | stub
    vision_modelo: str = "qwen/qwen3.8-27b"   # acepta imágenes (comprobado el 2026-11-02)
    vision_max_tokens: int = 120              # tope del resumen de pantalla en el prompt del LLM

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
            stt_prompt=e.texto("STT_PROMPT", "Nix, Minecraft, PvP, Roblox, Visual Studio, VTube Studio."),
            stt_min_logprob=e.decimal("STT_MIN_LOGPROB", -1.0),
            silero_onnx=e.texto("SILERO_ONNX", "/datos/modelos/silero_vad.onnx"),
            vad_umbral=e.decimal("VAD_UMBRAL", 0.5),
            vad_silencio_ms=e.decimal("VAD_SILENCIO_MS", 300.0),
            vad_min_voz_ms=e.decimal("VAD_MIN_VOZ_MS", 250.0),
            vad_pre_roll_ms=e.decimal("VAD_PRE_ROLL_MS", 600.0),
            stt_adelanto_ms=e.decimal("STT_ADELANTO_MS", 150.0),
            llm_proveedor=e.texto("LLM_PROVEEDOR", "auto").lower(),
            memoria=e.booleano("MEMORIA", True),
            memoria_modelo=e.texto("MEMORIA_MODELO", "openai/gpt-oss-20b"),
            memoria_cada_turnos=e.entero("MEMORIA_CADA_TURNOS", 3),
            freellmapi_url=e.texto("FREELLMAPI_URL"),
            freellmapi_clave=e.texto("FREELLMAPI_CLAVE"),
            freellmapi_espera_s=e.decimal("FREELLMAPI_ESPERA_S", 4.0),
            llm_modelo=e.texto("LLM_MODELO", "qwen/qwen3.8-27b"),
            llm_modelo_respaldo=e.texto("LLM_MODELO_RESPALDO", "openai/gpt-oss-120b"),
            llm_razonamiento=e.texto("LLM_RAZONAMIENTO", "low").lower(),
            llm_max_tokens=e.entero("LLM_MAX_TOKENS", 90),
            respuesta_max_oraciones=e.entero("RESPUESTA_MAX_ORACIONES", 2),
            llm_temperatura=e.decimal("LLM_TEMPERATURA", 0.8),
            llm_espera_s=e.decimal("LLM_ESPERA_S", 6.0),
            historial_turnos=e.entero("HISTORIAL_TURNOS", 6),
            tts_proveedor=e.texto("TTS_PROVEEDOR", "auto").lower(),
            tts_voz=e.texto("TTS_VOZ"),
            tts_velocidad=e.texto("TTS_VELOCIDAD", "+0%"),
            tts_conexiones=e.entero("TTS_CONEXIONES", 1),
            tts_ocio_s=e.decimal("TTS_OCIO_S", 12.0),
            barge_in=e.booleano("BARGE_IN", True),
            stt_cierre_temprano=e.booleano("STT_CIERRE_TEMPRANO", True),
            barge_min_voz_ms=e.decimal("BARGE_MIN_VOZ_MS", 400.0),
            vision_habilitada=e.booleano("VISION_HABILITADA", False),
            vision_proveedor=e.texto("VISION_PROVEEDOR", "auto").lower(),
            vision_modelo=e.texto("VISION_MODELO", "qwen/qwen3.8-27b"),
            vision_max_tokens=int(e.decimal("VISION_MAX_TOKENS", 120)),
        )

    def ip_permitida(self, ip: str) -> bool:
        try:
            direccion = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if direccion.version == 6 and direccion.ipv4_mapped:
            direccion = direccion.ipv4_mapped
        return any(direccion in red for red in self.redes_permitidas)
