# Nix

Compañera virtual autónoma en tiempo real. Un **servidor** en Ubuntu (el "cerebro") y un **cliente**
en Windows (el "cuerpo") hablan por WebSocket dentro de la red local.

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Esqueleto, VAD, STT en streaming, eco del texto, latencia por etapa | ✅ |
| 2 | LLM + TTS en streaming, orquestador, barge-in, AEC | pendiente |
| 3 | Motor emocional | pendiente |
| 4 | Memoria de tres niveles | pendiente |
| 5 | Heartbeat y percepción | pendiente |
| 6 | Avatar en VTube Studio | pendiente |
| 7 | Canales: Discord, Telegram, Twitch y Minecraft | pendiente |

## Estructura

```
shared/      protocolo de mensajes (pydantic), autenticación HMAC, lectura del .env
server/      gateway WebSocket, VAD Silero, STT, latencias, personajes, juegos (marcador y reto del día)
client/      captura de micrófono o WAV, conexión con el servidor
personajes/  fichas de personaje (nix.md, narwell.md)
tests/       pytest
```

Código reutilizado de Narwell (copiado desde `/datos/archivo/narwell-2026-10-28`, no importado):
la máquina de estados del VAD y Silero en ONNX (`server/pipeline/vad.py`) y los filtros de
alucinaciones de Whisper (`server/pipeline/filtros_whisper.py`).

## Instalación

### Servidor (Ubuntu, Python 3.13 con uv)

```bash
cd ~/nix
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements-server.txt
# Modelo del VAD (2,3 MB):
curl -L -o /datos/modelos/silero_vad.onnx \
  https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx
```

Abrir el puerto **solo** para la red local (una vez):

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp comment 'Nix WebSocket'
```

### Cliente (Windows)

1. Instala Python 3.13 y uv (`winget install astral-sh.uv`).
2. Copia la carpeta del proyecto (o `git clone`) y en PowerShell:
   ```powershell
   uv venv --python 3.13 .venv
   uv pip install --python .venv -r requirements-client.txt
   ```

## Configuración (.env)

Copia `.env.example` como `.env` en los dos equipos. `NIX_TOKEN` debe ser **el mismo** en ambos:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

El servidor ya tiene su `.env` con un token generado; cópialo al `.env` del cliente.

## Ejecutar

```bash
# Servidor
uv run --python .venv python -m server.main

# Cliente (Windows)
uv run --python .venv python -m client.main              # micrófono predeterminado
uv run --python .venv python -m client.main --listar     # ver micrófonos
uv run --python .venv python -m client.main --wav x.wav  # enviar un WAV (también sirve en Linux)

# Pruebas
uv run --python .venv pytest -q
```

## Seguridad

- El servidor escucha solo en `NIX_HOST` (la IP local, `192.168.1.50`) y rechaza IPs fuera de
  `NIX_REDES_PERMITIDAS` (por defecto `127.0.0.0/8,192.168.1.0/24`).
- **Autenticación mutua por desafío-respuesta HMAC-SHA256** (`shared/auth.py`): el token nunca viaja
  por la red. El servidor comprueba que el cliente conoce el token y el cliente comprueba lo mismo
  del servidor. Sin autenticarse en 5 s, la conexión se cierra (código 4401).
- El WebSocket no va cifrado (`ws://`): dentro de la red local el audio viaja en claro. Si algún día
  sale de la LAN, hay que ponerle TLS (`wss://`) o una VPN.
- **Visión apagada por defecto** (`VISION_HABILITADA=0`): el servidor ignora cualquier mensaje de
  visión y nunca envía imágenes a la nube si no la activas tú.

## Latencia

Cada turno registra marcas de tiempo del servidor y muestra en consola la duración por etapa y el
acumulado con p50/p95:

```
⏱️ turno ee10b77d | voz_fin→vad_fin 421 ms | vad_fin→stt_final 0 ms | stt_final→eco 1 ms | total 422 ms
⏱️ acumulado | ... | total p50 421 / p95 423 ms (n=4)
```

- `voz_fin→vad_fin`: silencio que espera el VAD para dar la frase por terminada (`VAD_SILENCIO_MS`).
  Es casi todo el tiempo; bajarlo reduce la latencia pero corta frases en las pausas.
- `vad_fin→stt_final`: lo que tarda el proveedor STT en dar el texto final.
- `stt_final→eco`: envío al cliente.

Para medir: arranca el servidor, habla (o envía un WAV) varias veces y mira la línea `acumulado`.
Al detener el servidor (Ctrl+C) se imprime el resumen final.

## Proveedores de voz a texto (STT)

`STT_PROVEEDOR=auto` usa el primero que tenga clave:

| Proveedor | Tipo | Notas |
|---|---|---|
| Deepgram Nova-3 | streaming real por WebSocket | el audio viaja mientras hablas; al cerrar la frase se manda `Finalize` |
| Groq Whisper (`whisper-large-v3-turbo`) | por frase (no streaming) | aplica los filtros de alucinaciones de Narwell |
| stub | simulado | **sin claves**: responde "(simulado) dijiste algo de N segundos" |

## Personajes

`PERSONAJE=nix` en el `.env` elige `personajes/nix.md`; `PERSONAJE=narwell` carga a Narwell.
Cada archivo lleva una cabecera con `nombre` y `voz`, y después la ficha (el carácter estable del prompt).
Cada personaje tiene su propia carpeta de datos, `NIX_DATOS_DIR/<personaje>` (`/datos/nix/nix`), así que
Nix empieza sin ningún recuerdo de Narwell. Esa carpeta tendrá la memoria (fase 4) y ya guarda los juegos.
Los canales de la fase 7 (Discord, Telegram, Twitch y Minecraft con la cuenta `_Kirito690`) usarán el
`nombre` de la ficha.

## Juegos: marcador de rivalidad y reto del día

- `server/juegos/rivalidad.py`: retos ganados, perdidos y empatados con cada persona (`canal:id`),
  por juego y con racha, en `juegos.sqlite`. Persiste entre conversaciones.
- `server/juegos/reto_del_dia.py`: un reto por día, que rota entre trivia de anime, adivinanza, trivia de
  juegos, récord y pregunta polémica, sin repetirse en 30 días. Trivias y adivinanzas se califican solas
  (3 intentos por persona) y suman al marcador. El banco está en `server/juegos/banco_retos.py`.

Todavía no están conectados a la conversación (eso llega con el LLM en la fase 2 y los canales en la 7);
se prueban desde la consola:

```bash
uv run --python .venv python -m server.juegos reto
uv run --python .venv python -m server.juegos responder telegram:1 Kirito "es Ryuk"
uv run --python .venv python -m server.juegos registrar twitch:amigo Amigo ppt gana_persona
uv run --python .venv python -m server.juegos marcador
```

## Qué es stub en la fase 1

- STT: si no hay `DEEPGRAM_API_KEY` ni `GROQ_API_KEY`, el texto es simulado.
- VAD: si falta `silero_vad.onnx`, se usa uno por volumen (aviso en consola).
