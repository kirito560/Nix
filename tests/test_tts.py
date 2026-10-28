import asyncio
import shutil
import subprocess

import numpy as np
import pytest

from server.config import ConfigServidor
from server.pipeline.tts import TTSEdge, TTSStub, crear_tts


async def test_stub_da_pcm_con_la_duracion_de_la_frase():
    pcm = b"".join([t async for t in TTSStub().sintetizar("x" * 20)])
    assert len(pcm) == 24000 * 2 * 1            # 20 caracteres * 0.05 s


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="falta ffmpeg")
async def test_edge_decodifica_el_mp3_en_streaming():
    """Sin red: un MP3 hecho con ffmpeg ocupa el lugar de edge-tts y se le entrega en trozos."""
    mp3 = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                          "-ar", "24000", "-ac", "1", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout

    async def fuente(_texto):
        for i in range(0, len(mp3), 2000):
            await asyncio.sleep(0)
            yield mp3[i:i + 2000]

    trozos = [t async for t in TTSEdge("es-MX-DaliaNeural", fuente_mp3=fuente).sintetizar("hola")]
    pcm = np.frombuffer(b"".join(trozos), dtype="<i2")
    assert len(trozos) > 1 and all(len(t) % 2 == 0 for t in trozos)
    assert 0.9 < len(pcm) / 24000 < 1.2 and np.abs(pcm).max() > 1000


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="falta ffmpeg")
async def test_edge_propaga_errores_de_la_fuente():
    async def rota(_texto):
        raise ConnectionError("sin internet")
        yield b""

    with pytest.raises(ConnectionError):
        [t async for t in TTSEdge("v", fuente_mp3=rota).sintetizar("hola")]


def test_eleccion_de_tts():
    cfg = ConfigServidor(token="x" * 40)
    assert crear_tts(ConfigServidor(token="x" * 40, tts_proveedor="stub"), "v").nombre == "stub"
    tts = crear_tts(cfg, "es-MX-DaliaNeural")
    if shutil.which("ffmpeg"):
        assert tts.nombre == "edge" and tts.voz == "es-MX-DaliaNeural"
    assert crear_tts(ConfigServidor(token="x" * 40, tts_voz="es-MX-JorgeNeural"), "v").voz == "es-MX-JorgeNeural" \
        or not shutil.which("ffmpeg")
