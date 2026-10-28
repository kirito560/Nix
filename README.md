# Nix

Compañera virtual autónoma en tiempo real. Un **servidor** en Ubuntu (el "cerebro") y un **cliente**
en Windows (el "cuerpo") hablan por WebSocket dentro de la red local.

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Esqueleto, VAD, STT en streaming, eco del texto, latencia por etapa | ✅ |
| 2 | LLM + TTS en streaming, orquestador, barge-in, AEC | 🟡 en curso (falta AEC y medir con Groq) |
| 3 | Motor emocional | pendiente |
| 4 | Memoria de tres niveles | pendiente |
| 5 | Heartbeat y percepción | pendiente |
| 6 | Avatar en VTube Studio | pendiente |
| 7 | Canales: Discord, Telegram, Twitch y Minecraft | pendiente |

## Estructura

```
shared/      protocolo de mensajes (pydantic), autenticación HMAC, lectura del .env
server/      gateway WebSocket, VAD Silero, STT, LLM, TTS, orquestador, latencias, personajes, juegos
client/      micrófono o WAV, altavoces, conexión con el servidor
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

El servidor también necesita `ffmpeg` (para decodificar la voz de edge-tts): `sudo apt install ffmpeg`.

### Cliente (Windows)

En **PowerShell** (no hace falta ser administrador):

1. Instala uv y Git (si ya los tienes, sáltalo). Cierra y vuelve a abrir PowerShell al terminar:
   ```powershell
   winget install astral-sh.uv
   winget install Git.Git
   ```
2. Descarga el proyecto (el repositorio es público) e instala las dependencias. uv descarga Python 3.13 solo:
   ```powershell
   cd $HOME
   git clone https://github.com/kirito560/Nix.git
   cd Nix
   uv venv --python 3.13 .venv
   uv pip install --python .venv -r requirements-client.txt
   ```
3. Crea el `.env` del cliente con el `NIX_TOKEN` del servidor. En el servidor, muéstralo con
   `grep ^NIX_TOKEN= ~/nix/.env` y cópialo. En el PC:
   ```powershell
   notepad .env
   ```
   Escribe estas dos líneas (con tu token) y guarda:
   ```
   NIX_TOKEN=pega-aquí-el-token-del-servidor
   NIX_SERVIDOR=ws://192.168.1.50:8765
   ```
   El cliente no necesita ninguna otra clave: Groq y lo demás viven solo en el servidor.
4. Comprueba los dispositivos de audio (opcional): `uv run --python .venv python -m client.main --listar`.
   Si no quieres los predeterminados, añade al `.env` `MIC_DISPOSITIVO=<número>` y `ALTAVOZ_DISPOSITIVO=<número>`.

Para actualizar más adelante: `git pull` y repetir el `uv pip install`.

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
uv run --python .venv python -m server.medir          # latencia de STT, LLM y TTS por separado

# Cliente (Windows)
uv run --python .venv python -m client.main              # micrófono predeterminado
uv run --python .venv python -m client.main --listar     # ver micrófonos
uv run --python .venv python -m client.main --wav x.wav  # enviar un WAV (también sirve en Linux)
uv run --python .venv python -m client.main --wav x.wav --salida r.wav   # guardar su voz en un WAV

# Pruebas
uv run --python .venv pytest -q
```

### ¿Cómo sé que se conectó?

- En el **PC** aparece `✅ conectado con ws://192.168.1.50:8765 (personaje: Nix)` y después
  `🎤 Habla cuando quieras`. Al hablar verás `🗣️  tú: …` (lo que entendió) y `🤖 …` (lo que responde),
  y la oirás por los altavoces.
- En el **servidor** aparecen `✅ cliente 192.168.1.x autenticado`, `🗣️ usuario: …` y la línea `⏱️` de latencia.
- Si falla:
  - `no pude conectar`: el servidor no está corriendo, la IP no es esa, o el firewall bloquea el puerto
    (`sudo ufw status` debe mostrar `8765/tcp ALLOW 192.168.1.0/24`).
  - `autenticación` o `el servidor cerró la conexión`: el `NIX_TOKEN` del PC no es igual al del servidor.

## Conversación (fase 2)

```
micrófono → VAD → STT (Groq Whisper) → LLM (Groq, streaming) → frases → TTS (edge-tts) → altavoces
```

- **LLM:** Groq con `LLM_MODELO` (por defecto `openai/gpt-oss-20b` con `LLM_RAZONAMIENTO=low`). Se le pasa la
  ficha del personaje y los últimos `HISTORIAL_TURNOS` intercambios (solo en memoria).
- **Frases:** el texto se corta en frases a medida que llega; la primera sale en cuanto hay una coma, para
  que la voz empiece antes. Se quitan emojis, markdown y acotaciones entre asteriscos.
- **TTS:** edge-tts con la voz de la ficha (`es-MX-DaliaNeural`), decodificada a PCM de 24 kHz con ffmpeg
  sobre la marcha. Se sintetizan hasta 2 frases a la vez, así la siguiente ya está lista.
- **Barge-in:** si hablas mientras ella habla durante más de `BARGE_MIN_VOZ_MS` (400 ms), se calla al
  instante (el servidor manda `playback.flush` y el cliente vacía su búfer). Una frase más corta durante su
  voz se toma por eco de los altavoces y se ignora. **Todavía no hay cancelación de eco (AEC):** usa
  audífonos; con altavoces, su voz puede volver por el micrófono.
- Sin `GROQ_API_KEY`, el STT y el LLM son simulados (la voz sí es real).

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
⏱️ turno 6c1f02aa | voz_fin→vad_fin 421 ms | vad_fin→stt_final 0 ms | stt_final→llm_1er_token 1 ms | llm_1er_token→frase_1 0 ms | frase_1→tts_1er_audio 790 ms | tts_1er_audio→audio_enviado 0 ms | total 1212 ms | tras_vad 791 ms
⏱️ acumulado | ... | total p50 1338 / p95 1949 ms (n=3)
```

- `voz_fin→vad_fin`: silencio que espera el VAD para dar la frase por terminada (`VAD_SILENCIO_MS`).
  Es casi todo el tiempo; bajarlo reduce la latencia pero corta frases en las pausas.
- `vad_fin→stt_final`: lo que tarda el proveedor STT en dar el texto final.
- `stt_final→llm_1er_token`, `llm_1er_token→frase_1`: lo que tarda el LLM en empezar y en dar la primera frase.
- `frase_1→tts_1er_audio`: lo que tarda el TTS en dar el primer audio (con edge-tts, unos 800 ms).
- `tts_1er_audio→audio_enviado`: envío al cliente.
- `total` cuenta desde que dejas de hablar; `tras_vad`, desde que el VAD cierra la frase.

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

## Qué es simulado (stub)

- STT y LLM: si no hay `GROQ_API_KEY` (ni `DEEPGRAM_API_KEY` para el STT), el texto es simulado.
- TTS: si falta edge-tts o ffmpeg, un tono en lugar de la voz.
- VAD: si falta `silero_vad.onnx`, se usa uno por volumen (aviso en consola).
