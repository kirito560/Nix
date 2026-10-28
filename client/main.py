"""
Cliente ("cuerpo"): captura el micrófono (o un WAV), lo envía al servidor, muestra lo que entiende y lo que
responde el personaje, y reproduce su voz.

    uv run python -m client.main                                  # micrófono y altavoces
    uv run python -m client.main --wav prueba.wav                 # archivo, sin micrófono
    uv run python -m client.main --wav prueba.wav --salida r.wav  # guarda la voz en vez de reproducirla
    uv run python -m client.main --listar                         # dispositivos de audio
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from websockets.exceptions import ConnectionClosed

from client.audio_io import Altavoz, ArchivoWav, GrabadorWav, Microfono
from client.ws_client import ErrorAutenticacion, ejecutar
from shared.auth import validar_token
from shared.config_base import Entorno, cargar_env
from shared.protocol import LlmText, PlaybackFlush, SttText

log = logging.getLogger("nix.cliente")


def crear_mostrar(salida):
    async def mostrar(msg) -> None:
        if isinstance(msg, SttText):
            print(f"🗣️  tú: {msg.texto}", flush=True)
        elif isinstance(msg, LlmText) and msg.texto:
            print(f"🤖 {msg.texto}", flush=True)
        elif isinstance(msg, PlaybackFlush):
            salida.vaciar()
            print("✋ (interrumpida)", flush=True)
    return mostrar


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
    ap.add_argument("--altavoz", default=e.texto("ALTAVOZ_DISPOSITIVO") or None,
                    help="índice o nombre de la salida de audio (ver --listar)")
    ap.add_argument("--salida", type=Path, help="guardar la voz del personaje en este WAV en vez de reproducirla")
    ap.add_argument("--espera", type=float, default=20.0,
                    help="con --wav: segundos que espera la respuesta al acabar el archivo")
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
        fuente = Microfono(_dispositivo(args.dispositivo))
        print("🎤 Habla cuando quieras (Ctrl+C para salir). Con altavoces, mejor usa audífonos: "
              "así su voz no vuelve por el micrófono.")
    salida = GrabadorWav(args.salida) if args.salida else Altavoz(_dispositivo(args.altavoz))
    try:
        asyncio.run(ejecutar(args.servidor, token, fuente.tramas(), crear_mostrar(salida),
                             espera_final_s=args.espera,
                             al_audio=lambda _turno, _pts, sr, pcm: salida.reproducir(sr, pcm)))
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
    finally:
        salida.cerrar()


def _dispositivo(valor):
    return int(valor) if valor and valor.isdigit() else valor


if __name__ == "__main__":
    main()
