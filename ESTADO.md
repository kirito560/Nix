# Estado de Nix · bitácora

> Última actualización: 2026-10-30 (tarde). Al empezar cada sesión, léelo; al terminar cada tarea, actualízalo y haz commit.

## Estado actual

**Fase 2 en curso.** La conversación completa funciona con Groq de verdad y está probada (108 pruebas): micrófono → (AEC) → VAD → STT → LLM → frases → voz → altavoces, con barge-in. Ya está todo el código de la fase 2. **Falta:** probarla en el PC con Windows (y ajustar ahí el AEC) y decidir si se paga una voz más rápida.

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Esqueleto, VAD, STT en streaming, eco del texto, latencia por etapa | ✅ |
| 2 | LLM + TTS en streaming, orquestador, barge-in, AEC | 🟡 código completo; falta la prueba en el PC |
| 3 | Motor emocional | pendiente |
| 4 | Memoria de tres niveles | pendiente |
| 5 | Heartbeat y percepción | 🟡 pantalla y presencia por cámara hechas (2026-11-02, apagadas por defecto); falta el heartbeat |
| 6 | Avatar en VTube Studio | pendiente |
| 7 | Canales: Discord, Telegram, Twitch (cuenta `narchan_`) y Minecraft (cuenta `_Kirito690`) | pendiente |

### Lo que funciona hoy

- **Servidor** en `192.168.1.50:8765`: solo red local y autenticación mutua HMAC (el token no viaja).
- **STT:** `STT_PROVEEDOR=groq` (Groq Whisper `whisper-large-v3-turbo`). Deepgram queda como opción futura.
- **LLM (fase 2):** Groq en streaming, `qwen/qwen3.8-27b` (`server/pipeline/llm.py`). Si Groq lo retira (404 `model_not_found`), pasa solo a `LLM_MODELO_RESPALDO=openai/gpt-oss-120b`. Lleva la ficha del personaje, reglas para hablar en voz alta y los últimos 10 intercambios (solo en memoria).
- **Frases (fase 2):** `server/pipeline/frases.py` corta el texto a medida que llega. La primera frase sale en la primera coma; se quitan emojis, markdown y acotaciones.
- **TTS (fase 2):** edge-tts `es-MX-DaliaNeural` → ffmpeg → PCM de 24 kHz en trozos de 100 ms (`server/pipeline/tts.py`). Sintetiza hasta 2 frases a la vez.
- **Orquestador (fase 2):** `server/orquestador.py`. Turnos con 3 tareas encadenadas (pensar, sintetizar, emitir), historial y latencia por etapa.
- **Barge-in (fase 2):** si el usuario habla más de 400 ms mientras ella habla, el servidor cancela el turno y manda `playback.flush`; el cliente se calla al instante. Si la frase es más corta, se toma por eco y se descarta sin transcribirla.
- **Cliente:** micrófono o WAV, altavoces o WAV de salida (`--salida`). Muestra `🗣️ tú:` y `🤖`.
- **AEC (2026-10-30, `client/aec.py`):** flujo dúplex a 48 kHz (`AudioDuplex`): cada bloque de 20 ms del micrófono va junto con lo que sonó. PBFDAF de 13 particiones × 320 muestras (cubre 260 ms tras restar la latencia de la tarjeta), con detector de doble habla de Geigel. Con eco simulado: 34–42 dB de atenuación con retrasos de 10 a 200 ms; falla con 300 ms (por eso se resta la latencia de la tarjeta, o se fija con `AEC_RETRASO_MS`). Con el usuario hablando encima, su voz pasa casi intacta (correlación 0,96–0,997). Con una tarjeta falsa, de punta a punta: 28 dB tras converger. **Sin probar con micrófono y altavoces reales.**
- **Servicio (2026-10-29):** `nix.service` (servicio de usuario, `despliegue/nix.service`) activo y habilitado: arranca solo, se reinicia si falla, `MemoryMax=1G`. Registro: `journalctl --user -u nix -f`.
- **Conexiones listas (2026-10-30):** mientras hay un cliente conectado (y 5 min después), una conexión con edge-tts abierta y renovada cada ~10 s (Microsoft cierra las inactivas entre 15 y 45 s, con o sin ping; medido), y la de Groq viva con una petición cada 30 s (Groq la mantiene al menos 3 min). `server/pipeline/edge_pool.py` (usa funciones internas de edge-tts 7.3.1; si cambian, vuelve al modo normal) y `server/pipeline/groq_http.py`.
- **Transcripción adelantada (2026-10-30):** a los 200 ms de silencio se transcribe por adelantado; se usa si la frase termina sin más voz (`STT_ADELANTO_MS`).
- **Si el LLM falla**, dice «Uy, se me trabó el cerebro, ¿me lo repites?».
- **El cliente se reconecta solo** (1, 2, 4, 8 y 10 s); no reintenta con el token mal ni si otro cliente lo reemplazó.
- **Medición:** `python -m server.medir` mide el STT, cada modelo de LLM y el TTS por separado.
- **Prueba real en el servidor (2026-10-29):** cliente → servidor en `127.0.0.1` con un WAV hablado, todo real (Groq Whisper, Qwen, Dalia). Transcribió perfecto «Hola Nix, ¿qué opinas de la pizza con piña?» las 6 veces.

### Latencia medida (2026-10-29, con Groq)

`server.medir`, 8 repeticiones por proveedor:

| Proveedor | p50 | p95 | Notas |
|---|---|---|---|
| STT Groq `whisper-large-v3-turbo` | 305–319 ms | 406–467 ms | frase de ~3 s |
| LLM `qwen/qwen3.8-27b`, 1ª frase | **267 ms** | 285 ms | el más rápido (vista previa, $0,80/$4 por millón de tokens) |
| LLM `openai/gpt-oss-120b`, 1ª frase | 403 ms | 587 ms | producción, $0,15/$0,60; suena más mexicano |
| LLM `openai/gpt-oss-20b`, 1ª frase | 438 ms | 592 ms | más lento que el 120b y su español fue peor (cortó la respuesta) |
| TTS edge-tts Dalia, primer audio | 790–810 ms | 875–900 ms | casi todo es abrir la conexión con Microsoft; ffmpeg añade ~15 ms |

Los modelos `llama-3.1-8b-instant` y `llama-3.3-70b-versatile` aparecen en la documentación, pero **esta cuenta no tiene acceso** (404). Los disponibles: `qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b` y otros que no sirven aquí.

**Conversación completa** (6 turnos reales con un WAV contra el servicio, 8 s de pausa entre turnos), p50:

| Etapa | 2026-10-29 | 2026-10-30 (mejoras) | Qué cambió |
|---|---|---|---|
| silencio que espera el VAD | 420 ms | 421 ms | (igual: `VAD_SILENCIO_MS=400`) |
| STT | 431 ms | **265 ms** | transcripción adelantada a los 200 ms de silencio + conexión con Groq viva |
| LLM hasta el 1er token | 266 ms | 224 ms | conexión con Groq viva (httpx la cerraba a los 5 s) |
| TTS hasta el 1er audio | 860 ms | **193 ms** | conexiones con edge-tts abiertas de antemano (abrir una cuesta ~470 ms) |
| **Total desde que dejas de hablar** | **2052 ms** | **1094 ms** (p95 1404) | |
| **Desde que el VAD cierra la frase** | 1632 ms | **683 ms** (p95 982) | |

**¿Llegamos a ~500 ms?** Desde que el VAD cierra la frase, casi (683 ms), sin pagar nada. Desde que dejas de hablar, 1,1 s: casi la mitad es la espera del VAD (420 ms).
- Bajar `VAD_SILENCIO_MS` a 300 ahorra 100 ms, con el riesgo de que responda en las pausas a media frase.
- Lo que sigue pesando es el STT por frase de Groq (~370 ms por petición). Con STT en streaming (Deepgram) y voz de pago (~100 ms) se estimaba ~0,5 s desde que cierra el VAD; con edge-tts ya en ~190 ms, la ganancia de una voz de pago es mucho menor que antes (~100 ms).

## Decisiones tomadas

- **Proyecto nuevo, sin importar el código de Narwell.** Del archivo solo se copiaron el VAD y los filtros de Whisper.
- **Cerebro/cuerpo:** cerebro = este servidor (Ubuntu, Python 3.13 con uv, `.venv`); cuerpo = el PC con Windows, que se conecta al cerebro por `ws://` (sin cifrar, solo en la LAN).
- **STT = Groq Whisper** (2026-10-28, Kirito). Deepgram, para el futuro.
- **LLM = Groq `qwen/qwen3.8-27b`** (2026-10-29, por medición: el primer token más rápido con buen español). Riesgos: está en vista previa (Groq puede retirarlo; por eso el respaldo automático a `openai/gpt-oss-120b`) y en 1 de 6 pruebas soltó una frase sin sentido («Aíto de los takes»). Si eso se repite, o por costo, se cambia a `LLM_MODELO=openai/gpt-oss-120b` (+~140 ms).
- **TTS = edge-tts `es-MX-DaliaNeural`** (2026-10-28, Kirito).
- **El audio se decodifica en el servidor** (ffmpeg) y viaja como PCM: el cliente de Windows no necesita ffmpeg.
- **Protección contra el eco:** AEC en el cliente y, además, en el servidor una frase necesita 400 ms de voz para interrumpirla mientras ella habla.
- **AEC en el cliente, no en el servidor:** solo el PC sabe exactamente qué sonó y cuándo; el flujo dúplex da esa alineación muestra a muestra.
- **Visión apagada por defecto** (`VISION_HABILITADA=0`).
- **Git:** deploy key `~/.ssh/nix_deploy` (`core.sshCommand` en `.git/config`). El repositorio `kirito560/Nix` es **público**: nunca subir `.env` ni datos personales.
- **Idioma:** todo en español.

## Lo que NO se toca

- **Servidor de Minecraft 26.3** (`~/minecraft-servidor`, servicio `minecraft`, `minecraft-consola.socket`, mundo «la tracalosa»).
- **DuckDNS** (`narwell.duckdns.org`, `duckdns.timer` cada 5 min).
- **Respaldos** en `/datos/respaldos/minecraft` (`respaldo-minecraft.timer`, 04:00).
- **Narwell archivada** en `/datos/archivo/narwell-2026-10-28`. Sus servicios están desactivados. `~/viaproxy` (con la sesión de `_Kirito690`) está apagado, pero la fase 7 de Minecraft lo puede reutilizar.

## Pendientes (de Kirito)

1. ~~Clave de Groq~~ ✅ puesta en `~/nix/.env` (2026-10-29).
2. ~~Firewall~~ ✅ comprobado: `8765/tcp ALLOW IN 192.168.1.0/24`.
   ⚠️ **Seguridad:** SSH (puerto 22) acepta conexiones desde cualquier dirección. Recomendado: entrar solo con llave.
3. **Probar el audio en Windows** (2026-11-02): el cliente ya se conecta desde el PC (192.168.1.64). Falta
   `git pull` y `python -m client.main --probar-audio` para elegir micrófono y altavoz que funcionen (README,
   «Audio del PC»). Los cambios de la limpieza **no se han subido a GitHub**: Kirito pidió no hacer push hasta
   revisarlos.
4. **Decidir la voz de pago:** ya no hace tanta falta (edge-tts quedó en ~190 ms con las conexiones listas); lo que más ganaría ahora es STT en streaming (Deepgram).
5. Fase 7: credenciales de Discord, Telegram y Twitch (`narchan_`).

### Decisiones pendientes: voz más rápida (precios consultados el 2026-10-28)

Uso supuesto: Nix habla 15 min al día (ligero, ~360 mil caracteres al mes) o 1 h al día (intenso, ~1,44 millones).

| Servicio | Latencia (dicha por el proveedor) | Precio | Ligero/mes | Intenso/mes |
|---|---|---|---|---|
| Deepgram Aura-2 (TTS) + Nova-3 (STT en streaming) | TTS rápido; STT en streaming | $0,030 / 1000 caracteres; Nova-3 $0,0048/min | ~$13 | ~$52 |
| ElevenLabs Flash v2.5 | ~75 ms | $0,04 / 1000 caracteres (plan Creator $22: 550 mil) | $22 (plan) | ~$58–99 |
| Cartesia Sonic | sub-100 ms | Pro $5 (100 mil) + $65/millón; Startup $49 (1,25 millones) | ~$22 | ~$58 |

Costo del LLM en Groq, suponiendo ~1500 tokens de entrada por turno (ficha + historial): **Qwen ~$6/mes en uso ligero y ~$26/mes en uso intenso; `gpt-oss-120b`, ~$1–5/mes**. Whisper cuesta $0,04 por hora (centavos). Deepgram da $200 de crédito gratis.
**Por comprobar antes de decidir:** que la voz en español mexicano del servicio elegido suene bien para Nix, y medir su latencia real desde este servidor.

## Siguiente paso

1. Subir a GitHub los commits locales (limpieza y audio/transcripción; cuando Kirito lo apruebe), hacer
   `git pull` en el PC, correr `--probar-audio` y conversar mirando el medidor (`🎚️`) y `🎧 eco atenuado`. Con micrófono y altavoz que funcionen, volver a encender el barge-in
   (`BARGE_IN=1` en el `.env` del servidor) y ver si Nix ya no se interrumpe sola.
2. Escuchar si Qwen convence o se cambia a `gpt-oss-120b`, y si `whisper-large-v3` entiende mejor que turbo.
3. Kirito decide si paga una voz más rápida (y STT en streaming) para acercarse a ~500 ms.
4. Con el PC: probar el AEC con altavoces (mirar `🎧 eco atenuado`; si es bajo, ajustar `AEC_RETRASO_MS`). Con eso se cierra la fase 2.

## Historial

- 2026-10-28: fase 1 (`023d9ba`). Deploy key `nix_deploy` configurada. Revisión general y creación de esta bitácora (`2873582`).
- 2026-10-28: fase 2 (sin AEC): LLM Groq, frases, TTS edge-tts, orquestador, barge-in con protección contra el eco, cliente con altavoces, `server.medir`. STT fijado a Groq. Guía del cliente de Windows en el README.
- 2026-10-29: clave de Groq puesta. Medición real con `server.medir` y 6 conversaciones completas en el servidor (2,05 s p50 desde que dejas de hablar). LLM cambiado a `qwen/qwen3.8-27b` con respaldo automático a `gpt-oss-120b`.
- 2026-10-29: firewall comprobado (ya estaba bien). Nix instalada como servicio de usuario `nix` y probada contra 192.168.1.50.
- 2026-10-30: AEC en el cliente (`client/aec.py`, `AudioDuplex`), probado con eco simulado y con una tarjeta falsa. Código de la fase 2 completo.
- 2026-10-30: mejoras de latencia (de 2,05 s a 1,09 s p50): conexiones con edge-tts listas, Groq con conexión viva, transcripción adelantada. Frase de disculpa si falla el LLM. Reconexión automática del cliente.

### 2026-11-02 — primera prueba desde el PC Windows
- El cliente se instaló en el PC (192.168.1.64), se conectó y se autenticó, pero el VAD no detectó voz.
- Añadido al gateway un medidor de nivel del micrófono: cada 10 s registra cuántos segundos de audio llegan y
  su nivel/pico en dBFS, con aviso si es casi silencio. Sirve para diagnosticar micrófono equivocado o mudo.
- Primera conversación real desde el PC (micrófono 14 y auriculares 17 del BKD-11, DirectSound, latencia de la
  tarjeta 280 ms): llegan frases y Nix responde, pero el barge-in la cortaba en cada turno, a los 1-3 s de
  empezar a hablar (su propia voz volvía al micrófono). Para probar: `BARGE_IN=0` y STT `whisper-large-v3`
  (más preciso que turbo; había errores como «No, Torin»). Solo en .env del servidor.

### 2026-11-02 — limpieza y audio del cliente (commits locales, sin push)
- Auditoría: dependencias todas en uso; quitado código muerto en pruebas; `uv.lock` (vacío) movido a `_revisar/`.
  Detalle y decisiones en `CAMBIOS.md`.
- Audio del cliente (`client/audio_io.py`, `client/main.py`):
  - cada dispositivo se abre con sus canales y frecuencia reales y se convierte a mono 16 kHz;
  - si el dúplex falla, micrófono y altavoz se abren por separado;
  - un dispositivo que no abre ya no tumba el programa: avisa y reintenta cada 5 s;
  - aviso si micrófono y altavoz son de APIs distintas;
  - nuevos `--probar-audio` (y `--todos`); `--listar` dice qué usará Nix.
- Búfer de reproducción sin recortes por callback.
- 127 pruebas pasan (`PRUEBAS.md`), 19 nuevas con un sounddevice falso. **Falta probarlo con la tarjeta real del PC.**
- En el `.env` del servidor siguen `BARGE_IN=0` y `GROQ_MODELO_STT=whisper-large-v3`, de la prueba anterior.

### 2026-11-02 — eco, señal, transcripción y estabilidad (commits locales, sin push)
- **Eco:** `GuardaEco` (`client/senal.py`):
  - limita la salida del cancelador a +3 dB sobre el micrófono y lo reinicia si se desajusta;
  - deja pasar el micrófono cuando solo hay ruido;
  - cambios suaves; log cada 5 s.
  - El log anterior (-10 a +5 dB) mostraba que el filtro se desajustaba.
- **Señal:** pasa-altas de 80 Hz y normalización a -23 dBFS por ventanas de 1 s (tope +12 dB, limitador suave).
  Se confirmó que al servidor llega PCM16 mono a 16 kHz.
- **Medidor del servidor:** RMS, segundos de audio y voces detectadas cada 10 s.
- **Transcripción:**
  - prompt de contexto (`STT_PROMPT`);
  - se descartan textos vacíos o de un carácter;
  - con confianza baja (`STT_MIN_LOGPROB`, -1,0) Nix pide que lo repitas.
- **Estabilidad:** reconexión 1, 2, 4, 8, 10 s; si falla la transcripción de Groq, Nix lo dice y sigue escuchando.
- 170 pruebas pasan (`PRUEBAS.md`); detalle y decisiones en `CAMBIOS.md`.
- **Pendiente:**
  - probar todo con la tarjeta real del PC;
  - ajustar `STT_MIN_LOGPROB` si pide repetir demasiado o muy poco;
  - volver a encender el barge-in (`BARGE_IN=1`) cuando el eco quede bien;
  - `keyterm` para Deepgram (no se usa hoy).

### 2026-11-02 — Nix ve la pantalla (commits locales, sin push)
Kirito lo llamó «fase 2: ver la pantalla». En el plan es la parte de percepción de la fase 5.
- **Cliente:**
  - `client/screen_capture.py`: mss a 1 fps, 768 px, pHash; apagada al arrancar;
  - `--pantalla on|off`, **Ctrl+Alt+P** (enciende / pausa al instante), **Ctrl+Alt+M** (manda una imagen);
  - ventanas de contraseñas y bancos excluidas (`PANTALLA_EXCLUIR`); nada en disco;
  - `client/indicador.py`: línea 🔴 y título de la ventana mientras está activa.
- **Qué sale del PC:** normalmente solo el título de la ventana activa; la imagen reducida, solo si se pide.
- **Servidor:**
  - `server/vision.py`: describe las imágenes con `qwen/qwen3.8-27b` en Groq (acepta imágenes: ~0,6–0,9 s);
    sin clave, «pantalla sin descripción»;
  - resumen de pantalla de hasta 120 tokens en el prompt del LLM.
  - En el `.env` de este servidor quedó `VISION_HABILITADA=1`; en el código y en `.env.example` sigue 0.
- **Dependencias nuevas del cliente:** mss, pillow, pynput (`uv pip install -r requirements-client.txt`).
- 202 pruebas pasan (`PRUEBAS.md`); decisiones en `CAMBIOS.md`.
- **Pendiente:**
  - probarlo en Windows (atajos con pynput, título de la ventana, mss con varios monitores: solo se captura el
    principal);
  - que Nix pida mirar cuando le dices «mira mi pantalla» (hoy hay que pulsar Ctrl+Alt+M);
  - subir los commits a GitHub cuando Kirito lo apruebe.

## Cambios en el servidor fuera del repositorio (registro, 2026-11-02)

Auditoría de secretos del mismo día:
- **Git:** `git ls-files` solo tiene `.env.example`, con las claves vacías. `.env` nunca entró en ningún commit
  (está en `.gitignore`).
- **Valores reales:** el `NIX_TOKEN` y la `GROQ_API_KEY` reales aparecen **0 veces** en `git log --all -p`.
- **Otros secretos:** ninguna llave privada ni clave con prefijo conocido (`gsk_`, `sk-`, `ghp_`). Las 326 líneas
  que da la búsqueda de palabras como «api_key» o «token» son nombres de
  variables, código y valores falsos de las pruebas (`"x" * 40`, `"CLAVE"`, `token-de-prueba-…`).

| Dónde | Qué | Cuándo |
|---|---|---|
| `~/nix/.env` (permisos 600, fuera de git) | Tiene `NIX_TOKEN` y `GROQ_API_KEY` (la clave la puso Kirito). Valores que difieren de `.env.example`: `BARGE_IN=0` (era 1; Nix se interrumpía sola), `GROQ_MODELO_STT=whisper-large-v3` (era turbo; transcribe mejor) y `VISION_HABILITADA=1` (era 0; segunda llave de la pantalla) `LLM_MAX_TOKENS=90` (era 300; respuestas cortas), `VAD_SILENCIO_MS=300` (era 400) y `STT_ADELANTO_MS=150` (era 200; latencia, 2026-11-02). Desde el 2026-11-03 tiene `FREELLMAPI_URL=http://127.0.0.1:3001/v1` y `FREELLMAPI_CLAVE` vacía (las puso Kirito), con `LLM_PROVEEDOR=auto`. Las variables nuevas (`STT_PROMPT`, `STT_MIN_LOGPROB`, `VISION_*`, `PANTALLA_*`, `AEC*`) no están: se usan sus valores por defecto. | 2026-10-29 a 11-02 |
| `~/.config/systemd/user/nix.service` | Copia de `despliegue/nix.service` (idéntica), habilitada (`systemctl --user enable --now nix`). | 2026-10-29 |
| Servicio `nix` | Reiniciado varias veces para aplicar cambios del código y del `.env`; hoy queda activo con el código local más nuevo (aún sin push). | 2026-10-29 a 11-02 |
| `~/nix/.venv` (fuera de git) | Paquetes de los `requirements`, más mss, pillow, pynput, python-xlib y six para correr las pruebas de pantalla. | 2026-11-02 |
| `~/nix/.git/config` | `core.sshCommand` para usar la llave de despliegue `~/.ssh/nix_deploy` al hacer push (la llave ya existía). | 2026-10-28 |
| GitHub | Push de los commits hasta `d538c4c` (antes de la regla de no hacer push). Los commits posteriores siguen solo en local. | hasta 2026-11-02 |
| Permisos de administrador | Solo lecturas: el estado del firewall y la configuración de SSH. No se cambió el firewall (la regla `8765/tcp ALLOW 192.168.1.0/24` ya existía), ni SSH, ni se instaló nada con apt (ffmpeg ya estaba). | 2026-10-29 |
| `~/nix/.venv` y servicio `nix` (2026-11-03) | Instalada la librería `openai` 3.42.1 (y sus dependencias). Durante las mediciones se pasó `LLM_PROVEEDOR` (y una vez `FREELLMAPI_URL=http://127.0.0.1:3001/v1`) con `systemctl --user set-environment`; se quitaron al terminar y Nix quedó reiniciado con su `.env`. Los WAV y logs de la medición están en `~/.cache/nix-medir/`. El router freellmapi no se instaló. | 2026-11-03 |
| Cachés y temporales | Borradas las cachés locales `__pycache__` y `.pytest_cache` (git ya las ignoraba). `uvx vulture` quedó en la caché de uv (`~/.cache/uv`). Imagen de prueba en la carpeta temporal de la sesión (`/tmp/claude-1000/…/scratchpad/prueba.jpg`). | 2026-11-02 |
| Groq (internet) | Llamadas de prueba con la clave: lista de modelos, una transcripción y dos descripciones de una imagen de prueba (patrón de colores, sin datos). | 2026-11-02 |

**No se tocó:** Minecraft, DuckDNS, `/datos/respaldos` ni el archivo de Narwell. `/datos/nix` y
`/datos/modelos/silero_vad.onnx` son del 2026-10-28 (fase 1). Linger ya estaba activo.


### 2026-11-02 — presencia por cámara (commits locales, sin push)
Kirito la llamó «fase 3: presencia por cámara». En el plan es percepción (fase 5). El motor emocional, la fase 3
del plan, sigue sin empezar.
- **Cliente:**
  - `client/camara.py`: OpenCV a 320×240 y 2 fps, cara sí/no con Haar;
  - apagada al iniciar, solo `--camara on`, **Ctrl+Alt+C** la apaga al instante;
  - sin cámara o sin OpenCV, avisa y queda apagada;
  - ningún frame se guarda ni sale del PC.
- **Protocolo:** `presencia.update {ts, presente, activa}`, solo cuando cambia.
- **Servidor:** `server/sesion.py` guarda la presencia mientras dura la conexión y solo la usa como una línea del
  prompt del LLM. No va al historial ni a disco.
- **Título de la ventana:** ahora es ASCII (`Nix [EN VIVO] - …`). En Windows salía «Nx», casi seguro por el emoji
  🔴 en el título.
- **OpenCV:** fijado a `opencv-python-headless` 4.14, porque OpenCV 5 ya no trae el clasificador Haar.
- 225 pruebas pasan (`PRUEBAS.md`); decisiones en `CAMBIOS.md`.
- **Pendiente:**
  - probar en Windows: la cámara real, que el Haar detecte bien con la luz del cuarto, y que el título ya diga «Nix»;
  - ajustar los umbrales (2 frames / 4 s) si cambia demasiado o muy lento;
  - con la memoria de largo plazo (fase 4), dejar la presencia fuera;
  - subir los commits cuando Kirito lo apruebe.

### 2026-11-02 — ronda 2: personalidad y conversación (commits locales, sin push)
- **Respuestas cortas:** prompt de una o dos frases, `LLM_MAX_TOKENS` 90 (también en el `.env` del servidor) y tope
  duro de 2 oraciones (`RESPUESTA_MAX_ORACIONES`).
- **Tono:** ficha de Nix sin burlas ni «skill issue»; filtro `server/tono.py` que no deja decir frases hostiles.
- **Aclaraciones:** palabra corta y rara → «Perdón, no te entendí bien, ¿me lo repites?»; el prompt pide no
  inventar.
- **Memoria de sesión:** el historial (10 turnos) pasó del servidor a la sesión; se borra al desconectarse.
- **Ánimo** (`server/animo.py`): energía, paciencia y cercanía de 0 a 1, ±0,1 por respuesta, vuelven a la base;
  solo cambian el tono del prompt. Es una primera versión simple del motor emocional (fase 3 del plan).
- **Presencia:** solo contexto; el texto pide no mencionarla salvo que pregunten. Ningún código responde a ella.
- **Repositorio:** quitada la nota de seguridad pedida y neutralizadas otras menciones en textos.
- 264 pruebas pasan (`PRUEBAS.md`); decisiones en `CAMBIOS.md`.
- **Prueba real con Groq:** respuestas amables y de 1 o 2 oraciones. **Pero dos turnos dieron 429:** la cuenta
  gratuita de Groq permite **7.000 tokens de entrada por minuto** con Qwen y cada turno manda ~1.300 (ficha +
  historial + contexto). Con más de ~5 turnos por minuto Nix dice «se me trabó el cerebro».
- **Pendiente:**
  - ~~Límite de Groq~~ → hecho: opción (a), ver abajo.
  - Probar la conversación en el PC y ajustar las señales del ánimo y la lista de expresiones hostiles.
  - Subir los commits cuando Kirito lo apruebe.

### 2026-11-02 — respaldo cuando Qwen llega a su límite (commits locales, sin push)
- Opción (a) elegida por Kirito.
  - Si Qwen responde 429 (límite por minuto) o 498/503 (sin capacidad), ese turno lo responde
    `openai/gpt-oss-120b` sin que se note, y el siguiente vuelve a Qwen.
  - Si el respaldo también falla, Nix dice la frase de error y sigue escuchando.
  - En el log sale «RESPALDO» (`journalctl --user -u nix | grep RESPALDO`).
- 272 pruebas pasan (8 nuevas).
- Prueba real: el primer turno de una ráfaga chocó con el límite y lo respondió gpt-oss en 484 ms.
- **Pendiente:** si los dos modelos llegan juntos al límite en charlas muy rápidas, queda la frase de error.
  Siguientes pasos posibles: acortar la ficha que se manda en cada turno, o la capa de pago de Groq.

### 2026-11-02 — latencia (commits locales, sin push)
Objetivo de Kirito: de fin de voz a primera palabra, menos de 500 ms (p50). **No se logró:** quedó en 1268 ms p50
(antes 1495), y el p95 bajó de 2936 a 1551 ms. Medido con 24 turnos de voz sintética con pausas, contra el
servicio real.

- **Fin de turno 400 → 300 ms.** Con 250 se cortaban frases con pausas de ~300 ms. La transcripción adelantada pasó
  a 150 ms.
- **Primera frase** en la primera coma o a las 5 palabras.
- **TTS:**
  - confirmado que ya era streaming;
  - ffmpeg de reserva (~35 ms menos por frase);
  - caché de frases cortas, precargada con las fijas y 15 aperturas típicas.
- 288 pruebas pasan.

**Por qué no baja de ~500 ms.** Cada etapa es un servicio en la nube y van una tras otra; los suelos medidos son:

| Etapa | Suelo medido |
|---|---|
| Esperar el silencio que cierra el turno | 300 ms (menos corta frases) |
| Whisper en Groq | ~300–350 ms por frase, casi todo red y cola (turbo: 314 ms, no compensa). La transcripción adelantada esconde ~150 ms: quedan ~270 |
| Qwen en Groq, primer token | ~330 ms con ~1.300 tokens de entrada |
| Microsoft, primer audio de una frase nueva | ~270–450 ms (150 si ya la sintetizó; solo MP3) |

Suma ≈ 1,2 s. Para acercarse a 500 ms habría que solapar etapas o cambiar de proveedor:
- **Deepgram en streaming** (ya hay código, `STT_PROVEEDOR=deepgram`): el texto está casi listo al cerrar el
  turno: −200 a −250 ms. Es de pago: ~$0.0048/min.
- **TTS rápido de pago** (Cartesia, ElevenLabs Flash: ~100 ms al primer audio): −150 a −300 ms.
- **Empezar el LLM con la transcripción adelantada** (especulativo), antes de cerrar el turno: −100 a −250 ms,
  pero gasta peticiones de Groq que luego se tiran (y la cuenta gratuita ya llega a su límite).
- **Fin de turno «inteligente»** (cerrar antes si la frase suena terminada): −100 ms sin cortar frases.

Aun con todo eso, el mínimo realista ronda 600–700 ms. Para menos de 500 haría falta empezar a responder mientras
todavía hablas, como los asistentes de voz comerciales.

**Pendiente:**
- con `BARGE_IN=0`, si empiezas a hablar justo antes de que Nix termine, esa frase se pierde sin aviso;
- probar en el PC con voz real (las pausas reales pueden ser más largas que las de prueba).

### 2026-11-03 — router de LLM local freellmapi, opcional (commits locales, sin push)
- `LLM_PROVEEDOR=freellmapi` usa un router local compatible con OpenAI (`model="auto"`). Si falla antes de la
  primera palabra (sin conexión, 429, 5xx, más de 4 s), ese turno lo responde Groq directo y el log dice
  RESPALDO. **Por defecto sigue Groq directo**; el `.env` del servidor no cambió.
- URL y clave en `.env`: `FREELLMAPI_URL` y `FREELLMAPI_CLAVE` (`.env.example` sin valores).
- Medidor de turnos en el repositorio: `despliegue/medir/medir_turnos.sh <groq|freellmapi> [turnos]`.
- Medido: Groq directo, 20 turnos, total p50 1203 / p95 1623 ms. Modo router con el router apagado, 5 turnos:
  5 RESPALDO y la misma latencia.
- **El router solo escucha en localhost:** el compose del repo (`despliegue/freellmapi/docker-compose.yml`)
  publica `127.0.0.1:3001:3001`; nunca 0.0.0.0, porque Docker se salta ufw. El panel se abre con un túnel SSH.
- **Pendiente (Kirito):** instalar el router (README, «Router de LLM local»), añadir claves de proveedores en su
  panel, poner la URL y la clave en `.env`, y medir 20 turnos con cada uno.
- 303 pruebas pasan.
- **Revisión del 2026-11-03:**
  - `.env` tiene `FREELLMAPI_URL` (correcta) y `FREELLMAPI_CLAVE` (vacía); `LLM_PROVEEDOR` sigue en `auto`.
  - `medir_turnos.sh` es ejecutable.
  - Búsqueda de secretos (prefijos `gsk_`, `sk-`, `ghp_`, `freellmapi-`, llaves privadas, asignaciones
    largas a clave/token/password) en los 86 archivos versionados y en todo el historial: 0 coincidencias.
    Los valores reales de `NIX_TOKEN` y `GROQ_API_KEY` aparecen 0 veces.
