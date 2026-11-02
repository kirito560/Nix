"""
Cliente ("cuerpo"): captura el micrófono (o un WAV), lo envía al servidor, muestra lo que entiende y lo que
responde el personaje, y reproduce su voz.

    uv run python -m client.main                                  # micrófono y altavoces
    uv run python -m client.main --wav prueba.wav                 # archivo, sin micrófono
    uv run python -m client.main --wav prueba.wav --salida r.wav  # guarda la voz en vez de reproducirla
    uv run python -m client.main --listar                         # dispositivos de audio
    uv run python -m client.main --probar-audio                   # tono por el altavoz y nivel del micrófono
    uv run python -m client.main --pantalla on                    # arrancar con la captura de pantalla encendida
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from contextlib import aclosing
from pathlib import Path

from websockets.exceptions import ConnectionClosed

from client.audio_io import (Altavoz, ArchivoWav, AudioDuplex, ErrorAudio, GrabadorWav, info_dispositivo,
                             probar_audio)
from client.indicador import IndicadorPantalla
from client.screen_capture import EXCLUIDAS_POR_DEFECTO, Atajos, CapturaPantalla
from client.ws_client import ErrorAutenticacion, ejecutar
from shared.auth import validar_token
from shared.config_base import Entorno, cargar_env
from shared.protocol import CIERRE_NO_AUTORIZADO, CIERRE_REEMPLAZADO, LlmText, PlaybackFlush, SttText

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


def main(argv: list[str] | None = None) -> None:
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
    ap.add_argument("--sin-aec", action="store_true", default=not e.booleano("AEC", True),
                    help="no cancelar el eco de los altavoces (con audífonos no hace falta)")
    ap.add_argument("--espera", type=float, default=20.0,
                    help="con --wav: segundos que espera la respuesta al acabar el archivo")
    ap.add_argument("--listar", action="store_true", help="listar dispositivos de audio y salir")
    ap.add_argument("--probar-audio", action="store_true",
                    help="tocar un tono en el altavoz, medir el micrófono y probar si abren juntos")
    ap.add_argument("--todos", action="store_true", help="con --probar-audio: probar cada dispositivo")
    ap.add_argument("--pantalla", choices=("on", "off"), default="off",
                    help="captura de pantalla al arrancar (apagada por defecto; Ctrl+Alt+P la enciende o pausa)")
    args = ap.parse_args(argv)
    if args.listar:
        listar(_dispositivo(args.dispositivo), _dispositivo(args.altavoz))
        return
    if args.probar_audio:
        ok = probar_audio(_dispositivo(args.dispositivo), _dispositivo(args.altavoz), todos=args.todos)
        sys.exit(0 if ok else 1)
    try:
        token = validar_token(e.texto("NIX_TOKEN"))
    except ValueError as err:
        log.error("❌ %s", err)
        sys.exit(1)
    if args.wav:
        fuente = ArchivoWav(args.wav)
        salida = GrabadorWav(args.salida) if args.salida else Altavoz(_dispositivo(args.altavoz))
    else:
        retraso = e.texto("AEC_RETRASO_MS")
        fuente = salida = AudioDuplex(_dispositivo(args.dispositivo), _dispositivo(args.altavoz),
                                      aec=not args.sin_aec, retraso_ms=float(retraso) if retraso else None)
        print("🎤 Habla cuando quieras (Ctrl+C para salir; Ctrl+Alt+P enciende o pausa la pantalla).")
    al_audio = lambda _turno, _pts, sr, pcm: salida.reproducir(sr, pcm)  # noqa: E731
    try:
        if args.wav:
            asyncio.run(ejecutar(args.servidor, token, fuente.tramas(), crear_mostrar(salida),
                                 espera_final_s=args.espera, al_audio=al_audio))
        else:
            asyncio.run(conversar_con_pantalla(args.servidor, token, fuente, salida, al_audio,
                                               crear_captura(e), encender=args.pantalla == "on"))
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


ESPERA_MAX_S = 10.0


def siguiente_espera(espera: float) -> float:
    """1, 2, 4, 8, 10, 10... segundos entre reintentos de conexión."""
    return min(espera * 2, ESPERA_MAX_S)


def crear_captura(e: Entorno) -> CapturaPantalla:
    excluidas = e.lista("PANTALLA_EXCLUIR", "") or EXCLUIDAS_POR_DEFECTO
    return CapturaPantalla(fps=e.decimal("PANTALLA_FPS", 1.0), umbral=int(e.decimal("PANTALLA_UMBRAL", 10)),
                           enviar_imagen=e.booleano("PANTALLA_ENVIAR_IMAGEN", False), excluidas=excluidas,
                           al_cambiar_estado=IndicadorPantalla())


async def conversar_con_pantalla(url: str, token: str, fuente, salida, al_audio, captura: CapturaPantalla,
                                 encender: bool = False) -> None:
    """La conversación de siempre, más la captura de pantalla (apagada salvo `encender`) y sus atajos."""
    atajos = Atajos(captura)
    atajos.iniciar(asyncio.get_running_loop())
    if encender:
        captura.activar()
    elif captura.al_cambiar_estado is not None:
        captura.al_cambiar_estado(False)            # deja claro desde el inicio que está apagada
    tarea = asyncio.create_task(captura.correr(), name="pantalla")
    try:
        await conversar_siempre(url, token, fuente, salida, al_audio, al_conectar=captura.conectar)
    finally:
        tarea.cancel()
        await asyncio.gather(tarea, return_exceptions=True)
        atajos.detener()


async def conversar_siempre(url: str, token: str, fuente: AudioDuplex, salida, al_audio,
                            al_conectar=None) -> None:
    """Con el micrófono: si se corta la conexión (reinicio del servidor, Wi-Fi...), se reconecta sola,
    esperando 1, 2, 4... hasta 10 s. No reintenta si el token es incorrecto o si otro cliente la reemplazó."""
    espera = 1.0
    while True:
        inicio = time.monotonic()
        try:
            async with aclosing(fuente.tramas()) as tramas:
                await ejecutar(url, token, tramas, crear_mostrar(salida), al_audio=al_audio, al_conectar=al_conectar)
            return
        except ConnectionClosed as err:
            codigo = err.rcvd.code if err.rcvd else None
            if codigo in (CIERRE_NO_AUTORIZADO, CIERRE_REEMPLAZADO):
                raise
            motivo = "el servidor cerró la conexión"
        except OSError as err:
            motivo = f"no pude conectar ({err})"
        salida.vaciar()
        if time.monotonic() - inicio > 60:
            espera = 1.0
        log.warning("🔌 %s; reintento en %.0f s", motivo, espera)
        await asyncio.sleep(espera)
        espera = siguiente_espera(espera)


def listar(microfono=None, altavoz=None) -> None:
    """Todos los dispositivos (> entrada y < salida predeterminadas de Windows) y los que usará Nix."""
    try:
        import sounddevice as sd
        print(sd.query_devices())
    except Exception as e:  # noqa: BLE001 - falta PortAudio, etc.
        print(f"❌ no pude listar los dispositivos de audio: {e}")
        return
    print("\nNix usará (MIC_DISPOSITIVO / ALTAVOZ_DISPOSITIVO en el .env; vacío = el predeterminado):")
    for nombre, valor, tipo in (("micrófono", microfono, "input"), ("altavoz", altavoz, "output")):
        try:
            print(f"  {nombre}: {info_dispositivo(sd, valor, tipo)}")
        except ErrorAudio as e:
            print(f"  {nombre}: ❌ {e}")
    print("Para cancelación de eco, micrófono y altavoz deben ser de la misma API (MME, DirectSound, WASAPI...).")


def _dispositivo(valor):
    return int(valor) if valor and valor.isdigit() else valor


if __name__ == "__main__":
    main()
