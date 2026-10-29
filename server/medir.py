"""
Mide la latencia de cada proveedor por separado, con los datos del .env (sin cliente ni micrófono):

    uv run --python .venv python -m server.medir                 # 8 repeticiones
    uv run --python .venv python -m server.medir -n 15 --modelos openai/gpt-oss-20b llama-3.1-8b-instant

  STT   Groq Whisper: desde que se sube una frase de ~3 s hasta el texto (la frase la genera edge-tts).
  LLM   cada modelo: primer token y primera frase lista para el TTS, con la ficha del personaje.
  TTS   edge-tts: desde el texto hasta el primer audio decodificado.

Sin GROQ_API_KEY solo se mide el TTS. No imprime claves.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from contextlib import aclosing

import numpy as np

from server.config import ConfigServidor
from server.latencia import percentil
from server.orquestador import prompt_sistema
from server.personaje import cargar_personaje
from server.pipeline.frases import Segmentador
from server.pipeline.llm import LLMGroq
from server.pipeline.stt import STTGroqWhisper
from server.pipeline.tts import TTSEdge
from shared.config_base import cargar_env

MODELOS = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
PREGUNTAS = ["¿Qué opinas de la pizza con piña?", "Te reto a una trivia de anime.", "¿Cómo te fue hoy?",
             "Perdí otra vez en piedra, papel o tijera.", "¿Cuál es el mejor juego de terror?"]
FRASES_TTS = ["¡GG, eso estuvo roto!", "Oye, ¿otra vez tú? Va, revancha.", "Ni modo, skill issue de mi parte.",
              "¿Pizza con piña? Obvio sí, no me juzgues."]


def resumen(nombre: str, ms: list[float]) -> str:
    if not ms:
        return f"{nombre:<34} sin datos"
    return f"{nombre:<34} p50 {percentil(ms, 50):6.0f} ms   p95 {percentil(ms, 95):6.0f} ms   (n={len(ms)})"


async def medir_tts(tts: TTSEdge, n: int) -> list[float]:
    tiempos = []
    for i in range(n):
        t0 = time.monotonic()
        async with aclosing(tts.sintetizar(FRASES_TTS[i % len(FRASES_TTS)])) as audio:
            async for _ in audio:
                tiempos.append((time.monotonic() - t0) * 1000)
                break
    return tiempos


async def medir_llm(cfg: ConfigServidor, modelo: str, sistema: str, n: int) -> tuple[list[float], list[float], str]:
    llm = LLMGroq(cfg.groq_api_key, modelo, cfg.llm_max_tokens, cfg.llm_temperatura, cfg.llm_razonamiento)
    primero, frase, ejemplo = [], [], ""
    for i in range(n):
        mensajes = [{"role": "system", "content": sistema}, {"role": "user", "content": PREGUNTAS[i % len(PREGUNTAS)]}]
        t0, seg, texto, t_frase = time.monotonic(), Segmentador(), "", None
        async for trozo in llm.responder(mensajes):
            if not texto:
                primero.append((time.monotonic() - t0) * 1000)
            texto += trozo
            if t_frase is None and seg.agregar(trozo):
                t_frase = (time.monotonic() - t0) * 1000
        frase.append(t_frase if t_frase is not None else (time.monotonic() - t0) * 1000)
        ejemplo = ejemplo or f"«{PREGUNTAS[0]}» → «{texto.strip()[:140]}»"
    await llm.http.aclose()
    return primero, frase, ejemplo


async def medir_stt(cfg: ConfigServidor, n: int) -> tuple[list[float], str]:
    """Genera la pregunta con edge-tts (voz masculina), la pasa a 16 kHz y la sube n veces."""
    import edge_tts
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-loglevel", "error", "-f", "mp3", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000",
        "pipe:1", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    mp3 = b"".join([p["data"] async for p in edge_tts.Communicate(PREGUNTAS[0], "es-MX-JorgeNeural").stream()
                    if p["type"] == "audio"])
    pcm, _ = await proc.communicate(mp3)
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    stt = STTGroqWhisper(cfg.groq_api_key, cfg.groq_modelo_stt, cfg.idioma)
    tiempos, texto = [], ""
    for _ in range(n):
        t0 = time.monotonic()
        datos = await stt.transcribir(audio)
        tiempos.append((time.monotonic() - t0) * 1000)
        texto = datos.get("text", "").strip()
    await stt.http.aclose()
    return tiempos, texto


async def principal(n: int, modelos: list[str]) -> None:
    cargar_env()
    cfg = ConfigServidor.desde_entorno()
    personaje = cargar_personaje(cfg.personaje, cfg.carpeta_personajes, cfg.datos_dir)
    print(f"Midiendo con {n} repeticiones (la primera de cada proveedor incluye abrir la conexión)\n")
    print(resumen(f"TTS edge ({cfg.tts_voz or personaje.voz})",
                  await medir_tts(TTSEdge(cfg.tts_voz or personaje.voz, cfg.tts_velocidad), n)))
    if not cfg.groq_api_key:
        print("\nSin GROQ_API_KEY en el .env: no se miden el STT ni el LLM.")
        return
    tiempos, texto = await medir_stt(cfg, n)
    print(resumen(f"STT groq ({cfg.groq_modelo_stt})", tiempos) + f"   «{texto}»")
    sistema = prompt_sistema(personaje)
    for modelo in modelos:
        try:
            primero, frase, ejemplo = await medir_llm(cfg, modelo, sistema, n)
        except Exception as e:  # noqa: BLE001 - un modelo retirado no debe parar la medición
            print(f"LLM {modelo}: error: {e}")
            continue
        print(resumen(f"LLM {modelo} 1er token", primero))
        print(resumen(f"LLM {modelo} 1ª frase", frase) + f"\n    {ejemplo}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Latencia de STT, LLM y TTS por separado")
    ap.add_argument("-n", type=int, default=8)
    ap.add_argument("--modelos", nargs="+", default=MODELOS)
    args = ap.parse_args()
    asyncio.run(principal(args.n, args.modelos))


if __name__ == "__main__":
    main()
