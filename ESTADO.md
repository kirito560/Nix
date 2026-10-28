# Estado de Nix · bitácora

> Última actualización: 2026-10-28 (noche). Al empezar cada sesión, léelo; al terminar cada tarea, actualízalo y haz commit.

## Estado actual

**Fase 2 en curso.** La conversación completa ya funciona y está probada (92 pruebas): micrófono → VAD → STT → LLM → frases → voz → altavoces, con barge-in. **Falta:** medir con Groq de verdad (no hay clave), la cancelación de eco (AEC) y la primera prueba en el PC con Windows.

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Esqueleto, VAD, STT en streaming, eco del texto, latencia por etapa | ✅ |
| 2 | LLM + TTS en streaming, orquestador, barge-in, AEC | 🟡 hecho salvo AEC; sin medir con Groq |
| 3 | Motor emocional | pendiente |
| 4 | Memoria de tres niveles | pendiente |
| 5 | Heartbeat y percepción | pendiente |
| 6 | Avatar en VTube Studio | pendiente |
| 7 | Canales: Discord, Telegram, Twitch (cuenta `narchan_`) y Minecraft (cuenta `_Kirito690`) | pendiente |

### Lo que funciona hoy

- **Servidor** en `192.168.1.50:8765`: solo red local y autenticación mutua HMAC (el token no viaja).
- **STT:** `STT_PROVEEDOR=groq` (Groq Whisper `whisper-large-v3-turbo`). Deepgram queda como opción futura.
- **LLM (fase 2):** Groq en streaming, `openai/gpt-oss-20b` con `reasoning_effort=low` (`server/pipeline/llm.py`). Lleva la ficha del personaje, reglas para hablar en voz alta y los últimos 10 intercambios (solo en memoria).
- **Frases (fase 2):** `server/pipeline/frases.py` corta el texto a medida que llega. La primera frase sale en la primera coma; se quitan emojis, markdown y acotaciones.
- **TTS (fase 2):** edge-tts `es-MX-DaliaNeural` → ffmpeg → PCM de 24 kHz en trozos de 100 ms (`server/pipeline/tts.py`). Sintetiza hasta 2 frases a la vez.
- **Orquestador (fase 2):** `server/orquestador.py`. Turnos con 3 tareas encadenadas (pensar, sintetizar, emitir), historial y latencia por etapa.
- **Barge-in (fase 2):** si el usuario habla más de 400 ms mientras ella habla, el servidor cancela el turno y manda `playback.flush`; el cliente se calla al instante. Si la frase es más corta, se toma por eco y se descarta sin transcribirla.
- **Cliente:** micrófono o WAV, altavoces (`Altavoz`) o WAV de salida (`--salida`). Muestra `🗣️ tú:` y `🤖`.
- **Medición:** `python -m server.medir` mide el STT, cada modelo de LLM y el TTS por separado.
- **Prueba real en el servidor (2026-10-28):** cliente → servidor en `127.0.0.1` con un WAV hablado; la respuesta salió con la voz real de Dalia (8,9 s de audio). El STT y el LLM fueron los simulados.

### Latencia medida (2026-10-28, sin clave de Groq)

| Etapa | Medido | Notas |
|---|---|---|
| Silencio que espera el VAD | 420 ms | `VAD_SILENCIO_MS=400` |
| TTS edge-tts, primer audio | **p50 ~790 ms**, p95 ~880 ms | Casi todo es edge-tts abriendo una conexión nueva con Microsoft en cada frase. ffmpeg añade ~15 ms (antes ~115 ms; se arregló con `-probesize 32 -analyzeduration 0 -fflags nobuffer`). |
| STT Groq Whisper | sin medir | falta la clave |
| LLM Groq, primer token | sin medir | falta la clave |

**Conclusión:** con edge-tts **no se llega a ~500 ms**: solo la voz ya tarda ~790 ms. Estimación (por confirmar con `server.medir`): unos 1,6–1,9 s desde que dejas de hablar y unos 1,2–1,5 s desde que el VAD cierra la frase. Las alternativas de pago y su costo están en «Decisiones pendientes».

## Decisiones tomadas

- **Proyecto nuevo, sin importar el código de Narwell.** Del archivo solo se copiaron el VAD y los filtros de Whisper.
- **Cerebro/cuerpo:** cerebro = este servidor (Ubuntu, Python 3.13 con uv, `.venv`); cuerpo = el PC con Windows, que se conecta al cerebro por `ws://` (sin cifrar, solo en la LAN).
- **STT = Groq Whisper** (2026-10-28, Kirito). Deepgram, para el futuro.
- **LLM = Groq `openai/gpt-oss-20b`** (2026-10-28). Según la documentación de Groq de hoy es el modelo de producción más rápido (1000 tokens/s, $0,075/$0,30 por millón de tokens). Alternativas: `llama-3.1-8b-instant` (560 t/s, español más flojo) y `llama-3.3-70b-versatile` (280 t/s, buen español). Se elegirá midiendo el primer token y la calidad en español con `server.medir`. Se cambia con `LLM_MODELO`.
- **TTS = edge-tts `es-MX-DaliaNeural`** (2026-10-28, Kirito).
- **El audio se decodifica en el servidor** (ffmpeg) y viaja como PCM: el cliente de Windows no necesita ffmpeg.
- **Protección contra el eco sin AEC:** mientras ella habla, una frase necesita 400 ms de voz para contar. Se recomiendan audífonos hasta tener AEC.
- **Visión apagada por defecto** (`VISION_HABILITADA=0`).
- **Git:** deploy key `~/.ssh/nix_deploy` (`core.sshCommand` en `.git/config`). El repositorio `kirito560/Nix` es **público**: nunca subir `.env` ni datos personales.
- **Idioma:** todo en español.

## Lo que NO se toca

- **Servidor de Minecraft 26.3** (`~/minecraft-servidor`, servicio `minecraft`, `minecraft-consola.socket`, mundo «la tracalosa»).
- **DuckDNS** (`narwell.duckdns.org`, `duckdns.timer` cada 5 min).
- **Respaldos** en `/datos/respaldos/minecraft` (`respaldo-minecraft.timer`, 04:00).
- **Narwell archivada** en `/datos/archivo/narwell-2026-10-28`. Sus servicios están desactivados. `~/viaproxy` (con la sesión de `_Kirito690`) está apagado, pero la fase 7 de Minecraft lo puede reutilizar.

## Pendientes (de Kirito)

1. **Clave de Groq.** `/datos/archivo/narwell-2026-10-28/.env` **no existe** (Narwell nunca tuvo `.env` en el servidor). Hay que poner `GROQ_API_KEY` en `~/nix/.env` a mano. Después: `python -m server.medir`.
2. **Firewall:** comprobar `sudo ufw status` (debe salir `8765/tcp ALLOW 192.168.1.0/24`). No pude verlo: `sudo` pide contraseña.
3. **Probar el cliente en Windows** (guía en el README, «Cliente (Windows)»).
4. **Decidir la voz de pago** si se quiere bajar a ~500 ms (abajo).
5. Fase 7: credenciales de Discord, Telegram y Twitch (`narchan_`).

### Decisiones pendientes: voz más rápida (precios consultados el 2026-10-28)

Uso supuesto: Nix habla 15 min al día (ligero, ~360 mil caracteres al mes) o 1 h al día (intenso, ~1,44 millones).

| Servicio | Latencia (dicha por el proveedor) | Precio | Ligero/mes | Intenso/mes |
|---|---|---|---|---|
| Deepgram Aura-2 (TTS) + Nova-3 (STT en streaming) | TTS rápido; STT en streaming | $0,030 / 1000 caracteres; Nova-3 $0,0048/min | ~$13 | ~$52 |
| ElevenLabs Flash v2.5 | ~75 ms | $0,04 / 1000 caracteres (plan Creator $22: 550 mil) | $22 (plan) | ~$58–99 |
| Cartesia Sonic | sub-100 ms | Pro $5 (100 mil) + $65/millón; Startup $49 (1,25 millones) | ~$22 | ~$58 |

Groq (LLM y Whisper) cuesta muy poco con estos modelos: menos de $2 al mes con este uso. Deepgram da $200 de crédito gratis.
**Por comprobar antes de decidir:** que la voz en español mexicano del servicio elegido suene bien para Nix, y medir su latencia real desde este servidor.

## Siguiente paso

1. Kirito pone `GROQ_API_KEY` → correr `python -m server.medir` → elegir el modelo de LLM con datos.
2. Primera conversación real desde el PC con Windows, con audífonos.
3. Cerrar la fase 2: **AEC** en el cliente (cancelación de eco con la señal que se reproduce como referencia; necesita el PC para medir el retraso entre altavoz y micrófono) y, si Kirito decide, un TTS más rápido.

## Historial

- 2026-10-28: fase 1 (`023d9ba`). Deploy key `nix_deploy` configurada. Revisión general y creación de esta bitácora (`2873582`).
- 2026-10-28: fase 2 (sin AEC): LLM Groq, frases, TTS edge-tts, orquestador, barge-in con protección contra el eco, cliente con altavoces, `server.medir`. STT fijado a Groq. Guía del cliente de Windows en el README.
