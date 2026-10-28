"""
Arranque del servidor ("cerebro"): carga el .env, el personaje y levanta el gateway WebSocket.

    uv run python -m server.main
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from server.config import ConfigServidor
from server.gateway.ws_server import Gateway
from server.personaje import cargar_personaje
from server.pipeline.escucha import Escucha
from server.pipeline.stt import crear_stt
from server.pipeline.vad import DetectorVoz, crear_puntuador
from shared.config_base import cargar_env

log = logging.getLogger("nix")


async def principal() -> None:
    cargar_env()
    cfg = ConfigServidor.desde_entorno()
    personaje = cargar_personaje(cfg.personaje, cfg.carpeta_personajes, cfg.datos_dir)
    personaje.crear_carpeta()
    log.info("🎮 Personaje: %s (voz %s, datos en %s)", personaje.nombre, personaje.voz, personaje.datos)
    log.info("👁️ Visión: %s", "ENCENDIDA" if cfg.vision_habilitada else "apagada")
    stt = crear_stt(cfg)
    log.info("🎙️ STT: %s", stt.nombre)

    def crear_escucha(enviar):
        detector = DetectorVoz(crear_puntuador(cfg.silero_onnx), umbral=cfg.vad_umbral,
                               silencio_ms=cfg.vad_silencio_ms, min_voz_ms=cfg.vad_min_voz_ms)
        return Escucha(detector, stt, enviar, gateway.registro)

    gateway = Gateway(cfg, crear_escucha, personaje.nombre)
    parar = asyncio.Event()
    for senal in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(senal, parar.set)
    async with gateway.servir():
        log.info("🧠 Servidor escuchando en ws://%s:%d (redes permitidas: %s)", cfg.host, cfg.puerto,
                 ", ".join(map(str, cfg.redes_permitidas)))
        await parar.wait()
    log.info("👋 Servidor detenido. Latencias: %s", gateway.registro.resumen() or "sin turnos")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    try:
        if sys.platform != "win32":
            import uvloop
            uvloop.run(principal())
        else:
            asyncio.run(principal())
    except (ValueError, FileNotFoundError) as e:
        log.error("❌ %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
