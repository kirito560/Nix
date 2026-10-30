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
| 5 | Heartbeat y percepción | pendiente |
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
- **Si el LLM falla**, dice «Uy, se me trabó el cerebro. ¿Me lo repites?».
- **El cliente se reconecta solo** (1, 2, 4… 30 s); no reintenta con el token mal ni si otro cliente lo reemplazó.
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
   ⚠️ **Seguridad:** SSH (puerto 22) está abierto a cualquier dirección y acepta contraseñas (`passwordauthentication yes`). Kirito debe cambiar su contraseña (`passwd`) y, después, pasar a entrar solo con llave.
3. **Probar el cliente en Windows** (guía en el README, «Cliente (Windows)»).
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

1. Primera conversación real desde el PC con Windows, con audífonos (escuchar si Qwen convence o se cambia a `gpt-oss-120b`).
2. Kirito decide si paga una voz más rápida (y STT en streaming) para acercarse a ~500 ms.
3. Con el PC: probar el AEC con altavoces (mirar `🎧 eco atenuado`; si es bajo, ajustar `AEC_RETRASO_MS`). Con eso se cierra la fase 2.

## Historial

- 2026-10-28: fase 1 (`023d9ba`). Deploy key `nix_deploy` configurada. Revisión general y creación de esta bitácora (`2873582`).
- 2026-10-28: fase 2 (sin AEC): LLM Groq, frases, TTS edge-tts, orquestador, barge-in con protección contra el eco, cliente con altavoces, `server.medir`. STT fijado a Groq. Guía del cliente de Windows en el README.
- 2026-10-29: clave de Groq puesta. Medición real con `server.medir` y 6 conversaciones completas en el servidor (2,05 s p50 desde que dejas de hablar). LLM cambiado a `qwen/qwen3.8-27b` con respaldo automático a `gpt-oss-120b`.
- 2026-10-29: firewall comprobado (ya estaba bien). Nix instalada como servicio de usuario `nix` y probada contra 192.168.1.50.
- 2026-10-30: AEC en el cliente (`client/aec.py`, `AudioDuplex`), probado con eco simulado y con una tarjeta falsa. Código de la fase 2 completo.
- 2026-10-30: mejoras de latencia (de 2,05 s a 1,09 s p50): conexiones con edge-tts listas, Groq con conexión viva, transcripción adelantada. Frase de disculpa si falla el LLM. Reconexión automática del cliente.
