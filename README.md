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
despliegue/  servicios de systemd y medir/ (medidor de latencia de turno a turno)
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
4. Elige el micrófono y el altavoz y pruébalos (ver [Audio del PC](#audio-del-pc)).
5. Arranca el cliente y déjalo abierto:
   ```powershell
   uv run --python .venv python -m client.main
   ```

Para actualizar más adelante: `git pull` y repetir el `uv pip install`.

### Audio del PC

Nix usa **dos dispositivos** del PC:

| Dispositivo | Variable del `.env` | Para qué |
|---|---|---|
| **Micrófono** (entrada) | `MIC_DISPOSITIVO` | Lo que dices. Se manda al servidor en mono a 16 kHz. |
| **Altavoz** (salida) | `ALTAVOZ_DISPOSITIVO` | Donde suena la voz de Nix: audífonos o bocinas. |

Si una variable está vacía o no existe, se usa el predeterminado de Windows. Cada uno se abre con sus canales y
su frecuencia reales; el cliente convierte el audio solo.

1. **Ver la lista** (el número de la izquierda es el que va en el `.env`):
   ```powershell
   uv run --python .venv python -m client.main --listar
   ```
   La lista tiene el mismo dispositivo varias veces, una por API: MME, Windows DirectSound, Windows WASAPI y
   Windows WDM-KS. Al final dice qué micrófono y qué altavoz usará Nix.
2. **Elegir** con `notepad .env`, por ejemplo:
   ```
   MIC_DISPOSITIVO=14
   ALTAVOZ_DISPOSITIVO=17
   ```
   Escoge micrófono y altavoz **de la misma API** (los dos DirectSound, o los dos MME…). Solo así se abren
   juntos y hay **cancelación de eco**, que evita que Nix se oiga a sí misma. Con audífonos no hace falta; con
   bocinas, sí. Evita «CABLE Input/Output (VB-Audio)»: son cables virtuales y no suenan.
3. **Probar:**
   ```powershell
   uv run --python .venv python -m client.main --probar-audio
   ```
   Toca un tono de 1 s en el altavoz, graba 2 s del micrófono (habla) y dice para cada uno «✅ funciona» o
   «❌ falló» con el motivo. Si el micrófono abre pero llega «casi en silencio», es el equivocado o está
   silenciado en Windows. También dice si los dos abren juntos (cancelación de eco).
   Con `--probar-audio --todos` prueba **cada** dispositivo de la lista (tarda y suena varias veces) y al final
   dice cuáles funcionaron.

Si el cliente no puede abrir los dos juntos, avisa en la ventana y sigue con micrófono y altavoz por separado,
sin cancelación de eco. Si no abre el micrófono o el altavoz, lo dice y lo reintenta cada 5 s: el programa no
se cierra.

## Configuración (.env)

Copia `.env.example` como `.env` en los dos equipos. `NIX_TOKEN` debe ser **el mismo** en ambos:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

El servidor ya tiene su `.env` con un token generado; cópialo al `.env` del cliente.

## Ejecutar

En el servidor, Nix corre como **servicio de usuario** (`despliegue/nix.service`): arranca sola al encender
el equipo y se reinicia si falla.

```bash
systemctl --user restart nix          # tras cambiar el .env o actualizar el código
systemctl --user status nix
journalctl --user -u nix -f           # ver lo que entiende, lo que responde y la latencia
```

A mano (con el servicio parado: `systemctl --user stop nix`):

```bash
# Servidor
uv run --python .venv python -m server.main
uv run --python .venv python -m server.medir          # latencia de STT, LLM y TTS por separado

# Cliente (Windows)
uv run --python .venv python -m client.main              # micrófono predeterminado
uv run --python .venv python -m client.main --listar     # ver micrófonos y altavoces
uv run --python .venv python -m client.main --probar-audio   # tono en el altavoz y nivel del micrófono
uv run --python .venv python -m client.main --pantalla on    # con la captura de pantalla encendida
uv run --python .venv python -m client.main --camara on      # con la presencia por cámara encendida
uv run --python .venv python -m client.main --wav x.wav  # enviar un WAV (también sirve en Linux)
uv run --python .venv python -m client.main --wav x.wav --salida r.wav   # guardar su voz en un WAV

# Pruebas
uv run --python .venv pytest -q
```

### Medidor del micrófono (en el servidor)

Cada 10 s, mientras el PC está conectado, el servidor escribe cuánto audio le llegó, a qué volumen y cuántas
veces detectó voz:

```bash
journalctl --user -u nix -f | grep --line-buffered -E "🎚️|🗣️|🙉|🎧"
```

```
🎚️ micrófono: 10.0 s de audio en 10 s | RMS -27 dBFS, pico -6 dBFS | voz detectada 2 veces
```

- **RMS** entre -35 y -20 dBFS mientras hablas: bien. Por debajo de -50, o el aviso «casi silencio»: el
  micrófono es el equivocado o está silenciado.
- **Voz detectada 0 veces** aunque hablaste: no llega voz (micrófono o volumen).
- **Menos de 10 s de audio en 10 s:** se pierden tramas por la red o el PC va atrasado.
- `🙉` explica por qué se descartó una frase (poca voz, alucinación, confianza baja…). `🎧 eco atenuado` sale en
  la ventana del PC.

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
- **Conexiones listas de antemano:** abrir la conexión con Microsoft cuesta ~470 ms y con Groq ~70 ms. Mientras
  hay un cliente conectado (y 5 minutos después), el servidor tiene `TTS_CONEXIONES` conexión de voz abierta (renovada cada ~10 s)
  y la de Groq viva (`server/pipeline/edge_pool.py`, `server/pipeline/groq_http.py`). Sin nadie conectado,
  no se mantiene nada abierto.
- **Barge-in:** si hablas mientras ella habla durante más de `BARGE_MIN_VOZ_MS` (400 ms), se calla al
  instante (el servidor manda `playback.flush` y el cliente vacía su búfer). Una frase más corta durante su
  voz se toma por eco de los altavoces y se ignora.
- **Cancelación de eco (AEC, en el cliente):** micrófono y altavoz van en un solo flujo de audio a 48 kHz,
  así cada trozo del micrófono llega junto con lo que sonaba en ese momento. Un filtro adaptativo
  (`client/aec.py`) aprende el camino altavoz → habitación → micrófono y resta su voz. Tarda unos segundos
  en aprender; cada 5 s de su voz sonando sale `🎧 eco atenuado N dB` (más de 20 dB es bueno). Un vigilante
  (`client/senal.py`) impide que la salida suene más de 3 dB por encima del micrófono; si el filtro se
  desajusta, lo reinicia y lo avisa. Si la tarjeta no acepta el flujo doble, sigue sin AEC y lo avisa. Con
  audífonos no hace falta: `AEC=0` o `--sin-aec`.
- **Limpieza de la voz (en el cliente):** después del AEC, un pasa-altas de 80 Hz quita zumbido y viento, y un
  normalizador lleva el volumen a -23 dBFS por ventanas de 1 s (como mucho +12 dB, para no subir el ruido).
  Al servidor llega PCM16 mono a 16 kHz.
- **Transcripción:** Whisper recibe el idioma (`IDIOMA=es`) y un prompt con palabras que se dicen seguido
  (`STT_PROMPT`: Nix, Minecraft, PvP, Roblox…). Se descartan textos vacíos o de un carácter. Si Whisper no
  está seguro (`avg_logprob` menor que `STT_MIN_LOGPROB`, por defecto -1,0), Nix dice «Perdón, no te
  entendí bien, ¿me lo repites?» en vez de responder a algo mal entendido.
- **Fin de turno:** el turno se cierra tras `VAD_SILENCIO_MS` (300 ms) de silencio. Con 250 se cortaban frases con
  pausas de ~300 ms («Creo que… la pizza»).
- **Transcripción adelantada:** cuando llevas `STT_ADELANTO_MS` (150 ms) callado, ya se empieza a transcribir;
  si al cerrarse el turno no hubo más voz, se usa ese resultado.
- **Primera frase de la voz:** sale en cuanto el LLM da un punto, una coma o 5 palabras; las siguientes, por
  puntuación.
- **Voz:** edge-tts en streaming (el MP3 entra a ffmpeg mientras llega). Un ffmpeg siempre ya arrancado (~35 ms
  menos) y una caché en memoria de frases cortas (las frases fijas y 15 aperturas típicas se precargan al
  arrancar): lo repetido suena al instante.
- **Si Qwen llega a su límite de uso** (429: la cuenta gratuita de Groq admite ~5 turnos por minuto) o Groq no tiene
  capacidad (498/503), ese turno lo responde el modelo de respaldo (`LLM_MODELO_RESPALDO`, gpt-oss) sin que se
  note; el siguiente vuelve a Qwen. En el log: `journalctl --user -u nix | grep RESPALDO`.
- **Router de LLM local (opcional):** con `LLM_PROVEEDOR=freellmapi` el LLM pasa por freellmapi y, si falla,
  responde Groq directo. Ver «Router de LLM local (freellmapi)».
- **Si Groq falla** (en la transcripción, o en el LLM y también en el respaldo), dice «Uy, se me trabó el cerebro,
  ¿me lo repites?» en vez de quedarse callada, y sigue escuchando.
- **El cliente se reconecta solo** si se corta la conexión (1, 2, 4, 8 y luego 10 s entre intentos).
- Sin `GROQ_API_KEY`, el STT y el LLM son simulados (la voz sí es real).

## Personalidad y conversación

- **Respuestas cortas:**
  - el prompt pide una o dos frases, sin listas ni discursos;
  - `LLM_MAX_TOKENS=90`;
  - además hay un tope duro de 2 oraciones (`RESPUESTA_MAX_ORACIONES`): al cerrarse la 2.ª, se deja de leer al
    LLM. En el log sale `✂️ respuesta cortada en 2 oraciones`.
- **Tono:**
  - Nix bromea con cariño y sin presionar (`personajes/nix.md`);
  - si el LLM suelta una expresión de la lista de `server/tono.py` («skill issue», «no me hagas perder el tiempo»,
    «manco»…), esa frase no se dice (`🧹 frase descartada por el tono`).
- **Aclaraciones:** una sola palabra corta y rara («ble», «tor») o una transcripción poco fiable → «Perdón, no te
  entendí bien, ¿me lo repites?». Si algo más largo no tiene sentido, el prompt le pide que pida repetir en vez de
  inventar.
- **Memoria de sesión:** recuerda los últimos 10 turnos (`HISTORIAL_TURNOS`) de la conversación actual. Se borran
  al desconectarse el PC; no se guardan en disco ni en memoria de largo plazo.
- **Ánimo:** energía, paciencia y cercanía, de 0 a 1:
  - cada respuesta los mueve como mucho 0,1 (risas y «gracias» suben la cercanía; insultos bajan la paciencia);
  - vuelven solos a su base (mitad en 10 min);
  - solo cambian el tono que se le pide en el prompt, no la voz;
  - viven en la sesión.
- **Presencia y pantalla** son solo contexto: Nix no las menciona salvo que le preguntes, y ningún cambio de
  presencia la hace hablar.

**Probar la conversación** (con el servicio corriendo en el servidor):
```powershell
# En el PC
cd $HOME\Nix
uv run --python .venv python -m client.main
```
```bash
# En el servidor: lo que entiende, lo que responde, cortes, filtro de tono y aclaraciones
journalctl --user -u nix -f | grep --line-buffered -E "🗣️|🤖|✂️|🧹|🙉|RESPALDO|⏱️ turno"
```

## Seguridad

- El servidor escucha solo en `NIX_HOST` (la IP local, `192.168.1.50`) y rechaza IPs fuera de
  `NIX_REDES_PERMITIDAS` (por defecto `127.0.0.0/8,192.168.1.0/24`).
- **Autenticación mutua por desafío-respuesta HMAC-SHA256** (`shared/auth.py`): el token nunca viaja
  por la red. El servidor comprueba que el cliente conoce el token y el cliente comprueba lo mismo
  del servidor. Sin autenticarse en 5 s, la conexión se cierra (código 4401).
- El WebSocket no va cifrado (`ws://`): dentro de la red local el audio viaja en claro. Si algún día
  sale de la LAN, hay que ponerle TLS (`wss://`) o una VPN.
- **Pantalla apagada por defecto, con doble llave:** el PC no captura nada hasta que la enciendes
  (`--pantalla on` o Ctrl+Alt+P) y el servidor ignora todo lo de pantalla con `VISION_HABILITADA=0`. Ninguna
  captura se guarda en disco; ver [Pantalla](#pantalla-nix-ve-lo-que-haces).

## Qué se captura y qué sale del PC

| Sensor | Encendido al arrancar | Cómo se enciende / apaga | Qué se captura en el PC | Qué sale del PC hacia el servidor | ¿Se guarda en disco? |
|---|---|---|---|---|---|
| **Micrófono** | Sí (es la conversación) | Cerrando el cliente (Ctrl+C) | Audio, en memoria | Audio PCM 16 kHz, mientras el cliente está abierto (el servidor lo manda a Groq para transcribirlo) | No |
| **Pantalla** | **No** | `--pantalla on` · **Ctrl+Alt+P** enciende o pausa al instante | 1 captura por segundo, reducida a 768 px, en memoria | Normalmente **solo el título de la ventana activa** cuando la pantalla cambia. Una imagen reducida **solo si la pides** (Ctrl+Alt+M o `PANTALLA_ENVIAR_IMAGEN=1`); el servidor la manda a Groq para describirla y la suelta | No |
| **Cámara** | **No** | Solo `--camara on` · **Ctrl+Alt+C** la apaga al instante | 2 frames por segundo a 320×240, en memoria; cada uno se suelta después de buscar una cara | **Solo un sí/no** («hay alguien frente a la cámara»), y solo cuando cambia. **Nunca** frames ni imágenes | No |

- **El título de la ventana de PowerShell** dice siempre qué está encendido, por ejemplo
  `Nix [EN VIVO] - VIENDO TU PANTALLA (Ctrl+Alt+P pausa) - CAMARA ENCENDIDA (Ctrl+Alt+C apaga)`, o
  `Nix - pantalla y camara apagadas`. Cada vez que algo se enciende o se apaga sale además una línea 🔴 / ⚫.
- **En el servidor**, la pantalla y la presencia solo se usan como contexto para el LLM. La presencia se olvida al
  desconectarse el PC y no entra en el historial ni en ninguna memoria.

## Cámara: Nix sabe si estás ahí

La cámara está **apagada** cada vez que arrancas el cliente y solo se enciende con `--camara on`:

```powershell
uv run --python .venv python -m client.main --camara on
uv run --python .venv python -m client.main --pantalla on --camara on   # las dos
```

- Busca una cara (clasificador Haar de OpenCV) 2 veces por segundo, a 320×240.
- Al servidor solo le dice **sí o no**, y solo cuando cambia:
  - «sí» después de 2 frames seguidos con cara;
  - «no» después de 4 s sin ninguna, para no cambiar cada vez que miras a un lado.
- **Ctrl+Alt+C** la apaga al instante y suelta la cámara (se apaga su luz). Para volver a encenderla, reinicia el
  cliente con `--camara on`.
- Si no hay cámara, está ocupada (Zoom, Discord, OBS…) o falta OpenCV, avisa en la ventana
  («📷 la cámara queda apagada: …») y todo lo demás sigue funcionando. Con varias cámaras, elige con
  `CAMARA_INDICE` (0 = la primera).
- En el servidor, Nix recibe en sus instrucciones «el usuario está frente a la cámara» o «no está…». En el log del
  servidor sale como `📷 presencia: …`.

## Pantalla: Nix ve lo que haces

La captura de pantalla está **apagada** cada vez que arrancas el cliente.

**Encenderla:**
```powershell
uv run --python .venv python -m client.main --pantalla on   # arranca ya encendida
uv run --python .venv python -m client.main                 # arranca apagada (igual que --pantalla off)
```

**Atajos de teclado** (funcionan aunque la ventana de Nix no esté al frente):

| Atajo | Qué hace |
|---|---|
| **Ctrl+Alt+P** | Enciende la captura, o la **pausa al instante** si está encendida |
| **Ctrl+Alt+M** | «Mira ahora»: manda **una** imagen de la pantalla para que Nix la describa (solo si está encendida) |

**Cómo saber si está activa:** al encender sale `🔴 PANTALLA COMPARTIDA: Nix ve tu pantalla…` y el título de
la ventana de PowerShell cambia a `Nix [EN VIVO] - VIENDO TU PANTALLA (Ctrl+Alt+P pausa)`; al pausar sale
`⚫ Pantalla en pausa…`.

**Qué se envía al servidor:**
- **Normalmente, ninguna imagen.** El PC captura 1 vez por segundo (`PANTALLA_FPS`), en memoria, y calcula un
  pHash. Si la pantalla cambió de verdad (`PANTALLA_UMBRAL`, y como mucho cada 3 s), manda solo **el título
  de la ventana activa** (p. ej. «Ventana activa: Minecraft 1.21»).
- **Una imagen reducida** (≤ 768 px de ancho, JPEG ≤ 200 KB) **solo si la pides**: Ctrl+Alt+M, o
  `PANTALLA_ENVIAR_IMAGEN=1` para mandarla en cada cambio. El servidor la describe con el modelo de visión
  (`VISION_MODELO`, Qwen en Groq: la imagen sí sale a Groq para eso), se queda solo con el texto y suelta la
  imagen.
- **Ventanas privadas:** si el título de la ventana activa contiene alguna palabra de `PANTALLA_EXCLUIR`
  (contraseñas, gestores de claves, inicio de sesión, bancos…), no se captura nada y Nix solo sabe «ventana
  privada». Ojo: solo se revisa la ventana activa; si pides una imagen, sale todo lo visible.
- **Al pausar**, el servidor olvida lo que sabía de tu pantalla.
- **Nada se guarda en disco**, ni en el PC ni en el servidor.

**En el servidor** hace falta `VISION_HABILITADA=1` (y `systemctl --user restart nix`). Nix recibe las
últimas descripciones (como mucho `VISION_MAX_TOKENS`, 120 tokens; caducan a los 10 min) junto a sus
instrucciones, para comentarlas solo si viene al caso. En el log del servidor salen como `👁️ pantalla: …`.
Sin `GROQ_API_KEY` (o con `VISION_PROVEEDOR=stub`) las imágenes se quedan en «pantalla sin descripción».

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
- `frase_1→tts_1er_audio`: lo que tarda el TTS en dar el primer audio. Con edge-tts, 150 ms si Microsoft ya
  sintetizó esa frase y 300–500 ms si es nueva; 1–2 ms si sale de la caché propia.
- `tts_1er_audio→audio_enviado`: envío al cliente.
- `total` cuenta desde que dejas de hablar; `tras_vad`, desde que el VAD cierra la frase.

Para medir: arranca el servidor, habla (o envía un WAV) varias veces y mira la línea `acumulado`.
Al detener el servidor (Ctrl+C) se imprime el resumen final.

```bash
journalctl --user -u nix -f | grep --line-buffered "⏱️"          # cada turno y el acumulado
```

**Medido el 2026-11-02** (24 turnos, voz sintética con pausas a media frase, servicio real):

| Etapa | Antes (p50 / p95) | Ahora (p50 / p95) |
|---|---|---|
| fin de voz → cierre del turno | 421 / 422 ms | 321 / 322 ms |
| → texto (Whisper en Groq) | 291 / 365 ms | 269 / 679 ms |
| → primer token del LLM (Qwen en Groq) | 315 / 1708 ms | 329 / 614 ms |
| → primera frase | 11 / 60 ms | 10 / 48 ms |
| → primer audio (edge-tts) | 372 / 801 ms | 272 / 505 ms |
| **total** | **1495 / 2936 ms** | **1268 / 1551 ms** |

Groq varía ±150 ms de una medición a otra. Por qué no baja de ~1,2 s: ver `ESTADO.md`.

### Medir 20 turnos con un proveedor

`despliegue/medir/medir_turnos.sh` reinicia Nix con el proveedor de LLM que le digas, le manda un WAV de
frases habladas (voz sintética, con pausas a media frase) como si fuera el PC y resume el log: p50/p95 por
etapa, cuántas veces hubo RESPALDO y, con el router, qué modelos eligió. No toca el `.env`: el proveedor se pasa
al servicio solo durante la medición, y al terminar Nix vuelve a lo que diga el `.env`.

```bash
cd ~/nix
despliegue/medir/medir_turnos.sh groq 20          # ~7 minutos
despliegue/medir/medir_turnos.sh freellmapi 20    # necesita FREELLMAPI_URL y FREELLMAPI_CLAVE en el .env
```

Los WAV y los logs quedan en `~/.cache/nix-medir/`. Mientras mide, no le hables a Nix desde el PC.

## Router de LLM local (freellmapi)

Opcional. [freellmapi](https://github.com/tashfeenahmed/freellmapi) es un router que corre en el servidor y
junta varias cuentas gratuitas de LLM (Google, Cerebras, Mistral, OpenRouter, Groq...) detrás de una sola
dirección compatible con OpenAI. Nix le pide `model="auto"` y el router elige el modelo de cada turno.

- **Por defecto Nix sigue usando Groq directo.** El router solo se usa con `LLM_PROVEEDOR=freellmapi`.
- **Si el router falla** (no está encendido, 429, error 5xx o tarda más de `FREELLMAPI_ESPERA_S` segundos en
  dar la primera palabra), ese turno lo responde Groq directo y en el log sale `RESPALDO`. El turno
  siguiente vuelve a probar el router.
- Si el router ya empezó a responder y se corta a media frase, no se cambia a Groq: Nix dice lo que alcanzó
  a decir, igual que con Groq.

### Instalarlo en el servidor

El router solo tiene que oírse **dentro del servidor** (`127.0.0.1:3001`): Nix corre en la misma máquina, y el
panel se abre por un túnel SSH. Guarda las claves de los proveedores, así que nunca se publica en la red.

**Opción A: con Docker.** Usa el `docker-compose.yml` de este repositorio, no el del router. Publica el puerto
solo en localhost (`"127.0.0.1:3001:3001"`). **No lo cambies a `3001:3001` ni a `0.0.0.0`:** Docker escribe
sus propias reglas de iptables y se salta ufw, así que el router quedaría abierto a toda la red aunque ufw
diga lo contrario. Tampoco trae el servicio de actualización automática del original (monta el socket de
Docker, que equivale a root). El servidor todavía no tiene Docker.

```bash
mkdir -p ~/freellmapi && cd ~/freellmapi
cp ~/nix/despliegue/freellmapi/docker-compose.yml .
# Clave con la que el router cifra las claves de los proveedores. Se queda en ~/freellmapi/.env (no en Nix).
printf "ENCRYPTION_KEY=%s\nPORT=3001\n" "$(openssl rand -hex 32)" > .env
chmod 600 .env
docker compose up -d
docker compose logs -f                          # Ctrl+C para salir
ss -ltn | grep 3001                             # debe decir 127.0.0.1:3001, nunca 0.0.0.0:3001 ni [::]:3001
```

**Opción B: sin Docker.** El servidor ya tiene Node 22 (el router pide Node 20 o más). Aquí sí manda ufw: no le
abras el puerto 3001 (`ufw allow 3001` nunca).

```bash
cd ~
git clone https://github.com/tashfeenahmed/freellmapi.git
cd freellmapi
npm install
npm run build
printf "ENCRYPTION_KEY=%s\nPORT=3001\n" "$(openssl rand -hex 32)" > .env
chmod 600 .env

# Como servicio de usuario, igual que Nix:
cp ~/nix/despliegue/freellmapi/freellmapi.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now freellmapi
journalctl --user -u freellmapi -f              # Ctrl+C para salir
ss -ltn | grep 3001                             # comprueba que escucha en el puerto 3001
```

**Abrir el panel desde el PC:** con un túnel SSH. En PowerShell del PC Windows:

```powershell
ssh -N -L 3001:127.0.0.1:3001 narchan@192.168.1.50
```

Déjalo abierto y entra en `http://localhost:3001` en el navegador del PC. Al cerrar la ventana (Ctrl+C) se
cierra el túnel. Entonces:

1. En **Keys**, añade las claves de los proveedores gratuitos que quieras usar.
   No conviene añadir la misma clave de Groq que usa Nix: el router gastaría el mismo límite por minuto
   y, cuando se agote, también fallará el respaldo.
2. Copia la **clave unificada** que sale arriba en la página **Keys** (empieza por `freellmapi-`).

### Activarlo en Nix

En `~/nix/.env` (nunca en el código):

```bash
LLM_PROVEEDOR=freellmapi
FREELLMAPI_URL=http://127.0.0.1:3001/v1
FREELLMAPI_CLAVE=freellmapi-...        # la clave unificada del panel
# Opcional: segundos máximos hasta la primera palabra del router (por defecto 4); después responde Groq.
FREELLMAPI_ESPERA_S=4
```

```bash
uv pip install --python .venv -r requirements-server.txt     # instala la librería openai
systemctl --user restart nix
journalctl --user -u nix -f | grep --line-buffered -E "🧠|RESPALDO|⏱️ acumulado"
```

Al arrancar, el log dice `🧠 LLM: freellmapi (model=auto en ...; respaldo: Groq ...)`, y en cada turno
`🧠 el router eligió <modelo>`. Para volver a Groq directo, quita `LLM_PROVEEDOR=freellmapi`
(o pon `auto`) y reinicia Nix.

### Comparar la latencia de los dos

Con el router encendido y configurado, mide 20 turnos con cada uno y compara la línea
`stt_final→llm_1er_token` (lo que tarda el LLM en empezar) y el `total`:

```bash
cd ~/nix
despliegue/medir/medir_turnos.sh groq 20
despliegue/medir/medir_turnos.sh freellmapi 20
```

Las demás etapas (VAD, Whisper, voz) no dependen del LLM, así que deberían salir iguales. Si con el router
sale mucho `RESPALDO`, sus turnos cuentan el tiempo perdido con el router más lo que tarda Groq.

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
