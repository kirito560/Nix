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
