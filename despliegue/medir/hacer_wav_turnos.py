"""
WAV de prueba para medir la latencia de turno a turno: frases con la voz de Jorge (no la de Nix), algunas con
pausas a media frase (250-350 ms, para comprobar que el fin de turno no las corta) y silencio entre turnos.

    .venv/bin/python despliegue/medir/hacer_wav_turnos.py salida.wav [turnos] [silencio_entre_s]

Con más turnos que frases, la lista se repite. El silencio entre turnos (16 s por defecto) deja que Nix
termine de hablar: con BARGE_IN=0, una frase que empieza mientras ella habla se descarta.
"""
import asyncio, subprocess, sys, wave
import numpy as np
import edge_tts

SR = 16000
# Cada turno es una lista de trozos; entre trozos va una pausa a media frase (ms).
TURNOS = [
    (["Hola Nix, ¿cómo estás hoy?"], 0),
    (["Oye,", "¿qué juego me recomiendas para esta noche?"], 250),
    (["Ayer estuve jugando Minecraft con mis amigos y construimos una casa enorme."], 0),
    (["Creo que", "la pizza con piña sí está buena."], 300),
    (["¿Te gusta la música electrónica?"], 0),
    (["Estaba pensando", "en aprender a programar en Python."], 350),
    (["¿Cuál es tu anime favorito?"], 0),
    (["Mañana tengo examen", "y no he estudiado nada."], 250),
    (["Perdí otra vez en Roblox contra mi primo."], 0),
    (["Bueno,", "dime un dato curioso sobre los videojuegos."], 300),
    (["¿Qué opinas de los juegos de terror?"], 0),
    (["Me voy a preparar algo de cenar,", "ahorita regreso."], 300),
]
N_TURNOS = int(sys.argv[2]) if len(sys.argv) > 2 else len(TURNOS)
SILENCIO_ENTRE_S = float(sys.argv[3]) if len(sys.argv) > 3 else 16.0

async def voz(texto):
    mp3 = b"".join([p["data"] async for p in edge_tts.Communicate(texto, "es-MX-JorgeNeural").stream() if p["type"] == "audio"])
    pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(SR), "pipe:1"],
                         input=mp3, capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, "<i2")
    nz = np.nonzero(np.abs(x) > 300)[0]                        # quita el silencio de los bordes
    return x[max(0, nz[0] - 80): nz[-1] + 80]                 # 5 ms de margen: la pausa medida es la pedida

async def main():
    partes = [np.zeros(SR, "<i2")]
    for i_turno in range(N_TURNOS):
        trozos, pausa = TURNOS[i_turno % len(TURNOS)]
        for i, t in enumerate(trozos):
            if i:
                partes.append(np.zeros(SR * pausa // 1000, "<i2"))
            partes.append(await voz(t))
        partes.append(np.zeros(int(SR * SILENCIO_ENTRE_S), "<i2"))
    with wave.open(sys.argv[1], "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(np.concatenate(partes).tobytes())

asyncio.run(main())
