"""
Cliente ("cuerpo"): captura el micrófono (o un WAV) y lo envía al servidor; muestra lo que reconoce.

    uv run python -m client.main                       # micrófono
    uv run python -m client.main --wav prueba.wav      # archivo, sin micrófono
    uv run python -m client.main --listar              # dispositivos de audio
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from websockets.exceptions import ConnectionClosed

from client.audio_io import ArchivoWav, Microfono
from client.ws_client import ErrorAutenticacion, ejecutar
from shared.auth import validar_token
from shared.config_base import Entorno, cargar_env
from shared.protocol import SttText

log = logging.getLogger("nix.cliente")


async def mostrar(msg) -> None:
    if isinstance(msg, SttText):
        print(f"🗣️  {msg.texto}", flush=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    cargar_env()
    e = Entorno()
    ap = argparse.ArgumentParser(description="Cliente de Nix")
    ap.add_argument("--servidor", default=e.texto("NIX_SERVIDOR", "ws://192.168.1.50:8765"))
    ap.add_argument("--wav", type=Path, help="enviar este WAV en lugar del micrófono")
    ap.add_argument("--dispositivo", default=e.texto("MIC_DISPOSITIVO") or None,
                    help="índice o nombre del micrófono (ver --listar)")
    ap.add_argument("--listar", action="store_true", help="listar dispositivos de audio y salir")
    args = ap.parse_args()
    if args.listar:
        import sounddevice as sd
        print(sd.query_devices())
        return
    try:
        token = validar_token(e.texto("NIX_TOKEN"))
    except ValueError as err:
        log.error("❌ %s", err)
        sys.exit(1)
    if args.wav:
        fuente = ArchivoWav(args.wav)
    else:
        dispositivo = int(args.dispositivo) if args.dispositivo and args.dispositivo.isdigit() else args.dispositivo
        fuente = Microfono(dispositivo)
        print("🎤 Habla cuando quieras (Ctrl+C para salir)")
    try:
        asyncio.run(ejecutar(args.servidor, token, fuente.tramas(), mostrar))
    except ErrorAutenticacion as err:
        log.error("❌ autenticación: %s", err)
        sys.exit(1)
    except ConnectionClosed as err:
        log.error("❌ el servidor cerró la conexión: %s", err.rcvd.reason if err.rcvd else err)
        sys.exit(1)
    except OSError as err:
        log.error("❌ no pude conectar con %s: %s", args.servidor, err)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
