"""
Cliente ("cuerpo"): captura el micrófono (o un WAV), lo envía al servidor, muestra lo que entiende y lo que
responde el personaje, y reproduce su voz.

    uv run python -m client.main                                  # micrófono y altavoces
    uv run python -m client.main --wav prueba.wav                 # archivo, sin micrófono
    uv run python -m client.main --wav prueba.wav --salida r.wav  # guarda la voz en vez de reproducirla
    uv run python -m client.main --listar                         # dispositivos de audio
    uv run python -m client.main --probar-audio                   # tono por el altavoz y nivel del micrófono
    uv run python -m client.main --pantalla on                    # arrancar con la captura de pantalla encendida
    uv run python -m client.main --camara on                      # arrancar con la presencia por cámara encendida
    uv run python -m client.main --vtube on                       # mover el modelo de VTube Studio
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

from client.barge import BargeCliente, Envio, Reproduccion
from client.audio_io import (Altavoz, ArchivoWav, AudioDuplex, ErrorAudio, GrabadorWav, elegir_par,
                             info_dispositivo, probar_audio)
from client.camara import Presencia
from client.indicador import Indicador
from client.screen_capture import EXCLUIDAS_POR_DEFECTO, Atajos, CapturaPantalla
from client.ws_client import ErrorAutenticacion, ejecutar
from shared.auth import validar_token
from shared.config_base import Entorno, cargar_env
from client.vtube import URL_POR_DEFECTO as VTUBE_URL, PuenteVTube, leer_hotkeys
from shared.protocol import CIERRE_NO_AUTORIZADO, CIERRE_REEMPLAZADO, AvatarCmd, LlmText, PlaybackFlush, SttText

log = logging.getLogger("nix.cliente")


def crear_mostrar(salida, reproduccion: Reproduccion | None = None, envio: Envio | None = None,
                  vtube: PuenteVTube | None = None):
    async def mostrar(msg) -> None:
        if isinstance(msg, SttText):
            print(f"🗣️  tú: {msg.texto}", flush=True)
        elif isinstance(msg, LlmText) and msg.texto:
            print(f"🤖 {msg.texto}", flush=True)
        elif isinstance(msg, PlaybackFlush):
            if reproduccion is None:
                salida.vaciar()
            elif (ack := reproduccion.callar()) is not None and envio is not None:
                await envio.mandar(ack)              # cuánto sonó: el historial guarda solo eso
            print("✋ (interrumpida)", flush=True)
        elif isinstance(msg, AvatarCmd) and vtube is not None:
            vtube.emocion(msg.expression)
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
    ap.add_argument("--vtube", choices=("on", "off"), default="on" if e.booleano("VTUBE", False) else "off",
                    help="mover un modelo de VTube Studio (expresiones y boca); apagado por defecto")
    ap.add_argument("--camara", choices=("on", "off"), default="off",
                    help="presencia por cámara (apagada por defecto; solo manda sí/no; Ctrl+Alt+C la apaga)")
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
        print("🎤 Habla cuando quieras (Ctrl+C para salir; Ctrl+Alt+P pantalla sí/no; Ctrl+Alt+C apaga la cámara).")
    envio, reproduccion = Envio(), Reproduccion(salida)
    al_audio = reproduccion.al_audio
    try:
        if args.wav:
            asyncio.run(ejecutar(args.servidor, token, fuente.tramas(), crear_mostrar(salida, reproduccion, envio),
                                 espera_final_s=args.espera, al_audio=al_audio, al_conectar=envio.conectar))
        else:
            indicador = Indicador()
            asyncio.run(conversar_con_pantalla(args.servidor, token, fuente, salida, al_audio,
                                               crear_captura(e, indicador), encender=args.pantalla == "on",
                                               presencia=crear_presencia(e, indicador),
                                               encender_camara=args.camara == "on",
                                               reproduccion=reproduccion, envio=envio,
                                               barge=crear_barge(e, reproduccion, envio),
                                               vtube=crear_vtube(e, salida) if args.vtube == "on" else None))
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


def crear_captura(e: Entorno, indicador: Indicador | None = None) -> CapturaPantalla:
    excluidas = e.lista("PANTALLA_EXCLUIR", "") or EXCLUIDAS_POR_DEFECTO
    return CapturaPantalla(fps=e.decimal("PANTALLA_FPS", 1.0), umbral=int(e.decimal("PANTALLA_UMBRAL", 10)),
                           enviar_imagen=e.booleano("PANTALLA_ENVIAR_IMAGEN", False), excluidas=excluidas,
                           al_cambiar_estado=indicador.pantalla if indicador else None)


def crear_barge(e: Entorno, reproduccion: Reproduccion, envio: Envio) -> BargeCliente | None:
    """BARGE_CLIENTE=1: callarla en el PC en cuanto hablas encima (apagado por defecto: probar con bocinas)."""
    if not e.booleano("BARGE_CLIENTE", False):
        return None
    barge = BargeCliente(reproduccion, envio, umbral_db=e.decimal("BARGE_CLIENTE_UMBRAL_DB", -35.0),
                         min_ms=e.decimal("BARGE_CLIENTE_MIN_MS", 250.0))
    log.info("✋ barge-in en el cliente: ENCENDIDO (umbral %.0f dBFS, %.0f ms de voz)", barge.umbral_db, barge.min_ms)
    return barge


def crear_vtube(e: Entorno, salida) -> PuenteVTube:
    hotkeys = leer_hotkeys(e.texto("VTS_HOTKEYS"))
    log.info("🎭 VTube Studio: ENCENDIDO (%s); emociones con hotkey: %s", e.texto("VTUBE_URL", VTUBE_URL),
             ", ".join(f"{k}->{v}" for k, v in hotkeys.items()) or "ninguna (solo la boca)")
    return PuenteVTube(hotkeys, e.texto("VTUBE_URL", VTUBE_URL), nivel=lambda: salida.nivel_salida)


def crear_presencia(e: Entorno, indicador: Indicador | None = None) -> Presencia:
    return Presencia(indice=int(e.decimal("CAMARA_INDICE", 0)),
                     al_cambiar_estado=indicador.camara if indicador else None)


async def conversar_con_pantalla(url: str, token: str, fuente, salida, al_audio, captura: CapturaPantalla,
                                 encender: bool = False, presencia: Presencia | None = None,
                                 encender_camara: bool = False, reproduccion: Reproduccion | None = None,
                                 envio: Envio | None = None, barge: BargeCliente | None = None,
                                 vtube: PuenteVTube | None = None) -> None:
    """La conversación de siempre, más la captura de pantalla y la presencia por cámara (las dos apagadas salvo
    `encender` / `encender_camara`) y sus atajos."""
    atajos = Atajos(captura, presencia)
    atajos.iniciar(asyncio.get_running_loop())
    if encender:
        captura.activar()
    elif captura.al_cambiar_estado is not None:
        captura.al_cambiar_estado(False)            # deja claro desde el inicio que está apagada
    if presencia is not None:
        if not (encender_camara and presencia.activar()) and presencia.al_cambiar_estado is not None:
            presencia.al_cambiar_estado(False)
    tareas = [asyncio.create_task(captura.correr(), name="pantalla")]
    if vtube is not None:
        tareas.append(asyncio.create_task(vtube.correr(), name="vtube"))
    if presencia is not None:
        tareas.append(asyncio.create_task(presencia.correr(), name="camara"))

    def al_conectar(enviar) -> None:
        captura.conectar(enviar)
        if presencia is not None:
            presencia.conectar(enviar)
        if envio is not None:
            envio.conectar(enviar)

    try:
        await conversar_siempre(url, token, fuente, salida, al_audio, al_conectar=al_conectar,
                                reproduccion=reproduccion, envio=envio, barge=barge, vtube=vtube)
    finally:
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)
        if presencia is not None:
            presencia.desactivar()
        atajos.detener()


async def conversar_siempre(url: str, token: str, fuente: AudioDuplex, salida, al_audio,
                            al_conectar=None, reproduccion: Reproduccion | None = None, envio: Envio | None = None,
                            barge: BargeCliente | None = None, vtube: PuenteVTube | None = None) -> None:
    """Con el micrófono: si se corta la conexión (reinicio del servidor, Wi-Fi...), se reconecta sola,
    esperando 1, 2, 4... hasta 10 s. No reintenta si el token es incorrecto o si otro cliente la reemplazó."""
    espera = 1.0
    while True:
        inicio = time.monotonic()
        try:
            origen = fuente.tramas() if barge is None else barge.vigilar(fuente.tramas())
            async with aclosing(origen) as tramas:
                await ejecutar(url, token, tramas, crear_mostrar(salida, reproduccion, envio, vtube), al_audio=al_audio,
                               al_conectar=al_conectar)
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
    try:
        mic, alt = elegir_par(sd, microfono, altavoz)
        microfono, altavoz = mic.indice, alt.indice
    except ErrorAudio:
        pass                                             # el bucle de abajo dice cuál está mal y por qué
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
