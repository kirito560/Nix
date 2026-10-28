# Estado de Nix · bitácora

> Última actualización: 2026-10-28. Al empezar cada sesión, léelo; al terminar cada tarea, actualízalo y haz commit.

## Estado actual

**Fase 1 terminada** (commit `023d9ba`, subido a GitHub `kirito560/Nix`). **Fase 2 sin empezar** (esperando la orden de Kirito).

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Esqueleto, VAD, STT en streaming, eco del texto, latencia por etapa | ✅ hecho, 72 pruebas en verde |
| 2 | LLM + TTS en streaming, orquestador, barge-in, AEC | pendiente |
| 3 | Motor emocional | pendiente |
| 4 | Memoria de tres niveles | pendiente |
| 5 | Heartbeat y percepción | pendiente |
| 6 | Avatar en VTube Studio | pendiente |
| 7 | Canales: Discord, Telegram, Twitch (cuenta `narchan_`) y Minecraft (cuenta `_Kirito690`) | pendiente |

### Lo que funciona hoy

- Servidor WebSocket en `192.168.1.50:8765`: solo acepta la red local (`ufw` abierto solo para `192.168.1.0/24`) y usa autenticación mutua HMAC (el token no viaja por la red).
- VAD Silero en ONNX (`/datos/modelos/silero_vad.onnx` ya descargado) → STT → eco del texto al cliente, con la latencia de cada etapa (p50 y p95).
- Cliente para Windows: micrófono o WAV.
- Personajes en `personajes/` (`nix`, `narwell`), elegidos con `PERSONAJE`. Cada uno tiene su carpeta de datos (`/datos/nix/<personaje>`).
- Juegos: marcador de rivalidad y reto del día. Funcionan desde la consola, pero todavía no están conectados a la conversación.

### A medias o sin probar

- **El STT es simulado**: `DEEPGRAM_API_KEY` y `GROQ_API_KEY` están vacías en `.env`. Nunca se ha probado con un proveedor real.
- **Nunca se ha probado el cliente en el PC con Windows** contra el servidor: solo pruebas automáticas en `127.0.0.1`.
- Falta comprobar que el `NIX_TOKEN` está copiado en el `.env` del PC.
- No hay servicio de systemd para Nix: se arranca a mano.

## Decisiones tomadas

- **Proyecto nuevo, sin importar el código de Narwell.** Del archivo solo se copiaron la máquina de estados del VAD y Silero (`server/pipeline/vad.py`) y los filtros de alucinaciones de Whisper (`server/pipeline/filtros_whisper.py`).
- **Arquitectura cerebro/cuerpo:** cerebro = este servidor (Ubuntu, Python 3.13 con uv, `.venv`); cuerpo = el PC con Windows. El cuerpo se conecta al cerebro por WebSocket (`ws://`, sin cifrar, solo en la LAN; para salir de la LAN haría falta `wss://` o una VPN).
- **Protocolo tipado con pydantic** (`shared/protocol.py`).
- **STT:** `auto` usa Deepgram Nova-3 (streaming) si hay clave; si no, Groq Whisper (por frase); si no, el simulado.
- **Visión apagada por defecto** (`VISION_HABILITADA=0`): no se envían imágenes a ninguna nube.
- **Git:** el repositorio usa la deploy key `~/.ssh/nix_deploy` (`core.sshCommand` en `.git/config`). `~/.ssh/id_ed25519` es la deploy key de otro repositorio (`NarChan-ia`).
- **Idioma:** todo en español (código, documentación y respuestas).

## Lo que NO se toca

- **Servidor de Minecraft 26.3** (Fabric, lista blanca, `online-mode=true`): `~/minecraft-servidor`, servicio `minecraft` y `minecraft-consola.socket`. Mundo «la tracalosa».
- **DuckDNS** (`narwell.duckdns.org`): `~/duckdns/actualizar.sh` + `duckdns.timer` (cada 5 min). El token está en `~/.config/duckdns/token`.
- **Respaldos diarios** del mundo en `/datos/respaldos/minecraft` (`respaldo-minecraft.timer`, 04:00).
- **Narwell archivada** en `/datos/archivo/narwell-2026-10-28`. Sus servicios (`viaproxy`, `narwell-mc`, `narwell-mc-cerebro`, `narwell-mc-diario.timer`) están **desactivados**.
- `~/viaproxy`: ViaProxy 3.4.14 (mineflayer 26.1 → servidor 26.3) con la sesión de `_Kirito690` en `saves.json`. Está apagado, pero lo puede reutilizar la fase 7 de Minecraft. Lecciones de Narwell para entonces: mineflayer necesita enviar `tick_end` en cada tick, y el proxy solo debe escuchar en `127.0.0.1`.

## Pendientes (de Kirito)

1. Poner `DEEPGRAM_API_KEY` o `GROQ_API_KEY` en `~/nix/.env` para probar el STT real.
2. Probar el cliente en el PC con Windows (copiar `NIX_TOKEN` a su `.env`).
3. Antes de la fase 2: elegir el LLM (¿Groq?) y el TTS (¿edge-tts `es-MX-DaliaNeural`, que es la voz de la ficha?).
4. Fase 7: credenciales de Discord, Telegram y Twitch (`narchan_`).

## Siguiente paso

**Esperar la orden de Kirito.** Cuando la dé: cerrar los pendientes 1 y 2 (probar la fase 1 de punta a punta con voz real) y después empezar la fase 2.

## Historial

- 2026-10-28: fase 1 (`023d9ba`). Deploy key `nix_deploy` configurada y probada (lectura y `push --dry-run`). Revisión general y creación de esta bitácora.
