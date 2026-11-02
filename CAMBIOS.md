# Cambios de la limpieza y del audio del cliente (2026-11-02)

Decisiones tomadas por mi cuenta cuando había dudas (elegí siempre la opción más simple).

## 1. Auditoría

- `git status` limpio; últimos commits `d538c4c`, `4733d58`, `eb6dc99`, `43d7bda`, `d5e2533`.
- **Dependencias:** todas las de `requirements-client.txt` y `requirements-server.txt` se usan. No quité ninguna.
- **Logs de prueba:** no hay ninguno en git. Solo había cachés locales (`__pycache__`, `.pytest_cache`), que git ya
  ignora; los borré del disco.
- **Quitado:** `generar()` en `tests/test_gateway.py` (ninguna prueba la usaba).
- **Movido a `_revisar/`:** `uv.lock`. Casi vacío: no fija ninguna versión porque las dependencias viven en los
  `requirements-*.txt`. No lo borré por si quieres pasar a `uv sync` más adelante.
- **No quitado (falso positivo):** `intentos_restantes` en `server/juegos/reto_del_dia.py`. La herramienta lo marcó
  sin uso, pero se llena por posición (`Respuesta("incorrecta", INTENTOS - intentos)`); al quitarlo fallaron dos
  pruebas, así que lo dejé.
- **No quitado:** los campos del protocolo para visión, emociones y avatar (fases futuras) y
  `personajes/narwell.md` (se puede elegir con `PERSONAJE=narwell`).

## 2. Audio del cliente

- **Canales y frecuencia reales.** Cada dispositivo se consulta con `sd.query_devices` y se abre con sus canales
  (como mucho 2; algunos dicen tener 16) y su frecuencia nativa. Después:
  - el micrófono se pasa a mono **promediando** los canales y se lleva a 16 kHz;
  - la voz de Nix se lleva a la frecuencia del altavoz y se copia a sus canales.
  - *Duda:* si un micrófono estéreo solo trae señal en un canal, el promedio lo deja 6 dB más bajo. Elegí el
    promedio por ser lo más simple; el medidor del servidor (🎚️) dirá si hace falta otra cosa.
- **Remuestreo:** de 48 kHz se usa el decimador que ya existía; de cualquier otra frecuencia, un paso bajo más
  interpolación lineal, con estado entre bloques (`Remuestreador`).
- **El dúplex se sigue abriendo a 48 kHz** (lo necesita la cadena del cancelador de eco). Si la tarjeta no lo
  acepta, se cae al modo separado, como antes.
- **Nada de esto tumba el programa:**
  - si falla el dúplex: aviso y micrófono + altavoz por separado (sin cancelación de eco);
  - si falla el micrófono: error claro en el log y reintento cada 5 s;
  - si falla el altavoz: error claro, Nix sigue sin voz y lo reintenta cada 5 s.
- **APIs distintas:** si micrófono y altavoz son de APIs distintas (MME, DirectSound, WASAPI, WDM-KS), avisa en el log
  **antes** de intentar abrirlos juntos. Igual lo intenta, como pediste.
- **`--probar-audio`:** prueba el altavoz y el micrófono **configurados** (`MIC_DISPOSITIVO` / `ALTAVOZ_DISPOSITIVO`):
  tono de 1 s, 2 s de grabación con su nivel, y si abren juntos (dúplex). Con `--todos` prueba **cada**
  dispositivo de la lista.
  - *Duda:* «abrir cada dispositivo» podía significar los configurados o todos. Por defecto, los configurados
    (probar 40 dispositivos tarda y suena mucho); `--todos` hace lo otro.
- **`--listar`** ya existía; ahora también dice qué micrófono y altavoz usará Nix, y su API.
- Los avisos del audio usan el log en vez de `print`.
- No cambió la conexión con el servidor ni ninguna variable del `.env`.

## 3. Optimización

- **Reproducción:** el búfer de voz (`ColaBytes`) ya no recorta su inicio en cada callback de 20 ms (con frases
  largas eso copiaba todo lo pendiente); avanza un índice y compacta de vez en cuando.
- **Dúplex:** sin copias extra de la salida. La energía del eco se calcula con `np.dot` en vez de crear arreglos.
- **Bucle de asyncio:** el cancelador de eco tarda ~0,2 ms por trama de 20 ms (medido en el servidor), así que no
  hace falta moverlo a otro hilo. Los callbacks de audio solo copian la trama y la pasan con
  `call_soon_threadsafe`; si el bucle se atrasa, se descarta la trama en vez de bloquear.
- **Envío:** una trama de 20 ms por mensaje; ya era eficiente, no cambió.
- **Reconexión:** verificada. Espera 1, 2, 4… hasta 30 s entre intentos (nunca reintenta sin pausa) y no reintenta
  si el token es incorrecto o si otro cliente la reemplazó. No cambió. El audio que no abre también espera 5 s.

---

# Segunda pasada: eco, señal, transcripción y estabilidad (2026-11-02)

## 1. Cancelación de eco (`client/senal.py`: `GuardaEco`)

- **Qué significaba el «-10 a 5 dB» del log:** un valor negativo quiere decir que la salida del cancelador sonaba
  **más fuerte** que el micrófono. El filtro se desajustaba y metía ruido en vez de quitar eco.
- **Límite de +3 dB:** si la salida pasa de 3 dB por encima del micrófono, se baja a ese tope. Si sigue así
  0,5 s mientras suena Nix, el filtro se reinicia (`CanceladorEco.reiniciar()`) y se avisa en el log.
- **Elegí el «umbral mínimo de señal»** en vez del suelo de -6 dB. Un suelo de -6 dB obligaría a **subir** la
  salida cuando el cancelador quita mucho eco, es decir, a devolver el eco. Ese eco era lo que hacía que Nix se
  interrumpiera sola. Con el umbral, cuando el micrófono solo trae ruido (< -55 dBFS) pasa el micrófono tal cual,
  sin tocarlo.
- **Suavizado:** la ganancia baja con τ = 10 ms y sube con τ = 200 ms, y dentro de cada trama cambia en rampa.
  El paso entre «micrófono tal cual» y «salida del cancelador» es igual de gradual.
- **Log:** «🎧 eco atenuado N dB» sale cada **5 s de audio con su voz sonando**, no por bloques ni por energía.
  Cuento segundos de audio, no de reloj, para que sea igual en las pruebas.

## 2. Calidad de la señal (`client/senal.py`: `PasaAltas`, `Normalizador`, `Acondicionador`)

- **Formato confirmado:** el cliente ya mandaba PCM16 mono a 16 kHz. El dúplex pasa de 48 kHz a 16 kHz con el
  decimador, y el micrófono suelto con `Remuestreador`. Cada trama mide exactamente 640 bytes (`Tramador`). No
  hacía falta remuestreo nuevo.
- **Pasa-altas:** Butterworth de 2.º orden a 80 Hz, después del cancelador. Va después porque el cancelador
  necesita la señal sin filtrar para modelar el eco.
- **Normalización:** mide el RMS del último segundo y lo lleva a -23 dBFS. La ganancia va de -12 a **+12 dB como
  tope**; con solo ruido (< -50 dBFS) se queda quieta. Baja rápido (0,1 s), sube lento (1 s), y un limitador
  impide que una trama pase de 0,9.
  - *Duda:* con solo ruido, ¿bajar la ganancia o mantenerla? La mantengo: si la bajara, el inicio de cada frase
    sonaría flojo.
- **Orden:** micrófono → cancelador de eco → guardián → pasa-altas → normalización → servidor. Sin dúplex:
  micrófono → pasa-altas → normalización.
- Las tareas 1 y 2 van **en un solo commit**: tocan las mismas líneas de `client/audio_io.py`.

## 3. Medidor del servidor (`server/gateway/ws_server.py`)

- Cada 10 s escribe, por ejemplo: `🎚️ micrófono: 10.0 s de audio en 10 s | RMS -31 dBFS, pico -9 dBFS |
  voz detectada 2 veces`. «Voz detectada» cuenta las frases que el VAD empezó: estén o no descartadas
  después.
- Solo escribe mientras llega audio, que con el cliente conectado es siempre. Si no hay cliente, no hay línea.

## 4. Transcripción (`server/pipeline/stt.py`, `filtros_whisper.py`, `escucha.py`, `orquestador.py`)

- **Idioma:** confirmado. A Groq ya se mandaba `language="es"` (variable `IDIOMA`, que vale `es`).
- **Prompt de contexto:** Groq Whisper acepta `prompt`. Se manda «Nix, Minecraft, PvP, Roblox, Visual Studio,
  VTube Studio.».
  - Es una variable **nueva** del `.env`, `STT_PROMPT`; no se renombró ninguna. Si no la pones, se usa ese valor.
  - Whisper a veces «transcribe» el propio prompt cuando solo hay ruido; esos textos se descartan.
  - Deepgram (no se usa hoy) no recibe estas palabras. Queda pendiente: su parámetro equivalente es `keyterm`.
- **Vacías o de un carácter:** se descartan antes del LLM con cualquier proveedor (`es_texto_vacio`, en la
  escucha).
- **Confianza baja:** Groq da `avg_logprob` por segmento. Si el promedio, ponderado por la duración, es menor que
  `STT_MIN_LOGPROB` (variable nueva; por defecto **-1,0**, el umbral que usa Whisper), Nix **no responde** a lo
  que entendió y dice «No te entendí bien. ¿Me lo repites?». En el log queda lo que entendió y su confianza.
  - *Duda:* el umbral. Elegí el estándar de Whisper; si pide repetir demasiado, se baja (p. ej. -1,2) en el `.env`.
- Las frases fijas (`Orquestador.decir`) no pasan por el LLM, no se guardan en el historial y no cuentan para
  las estadísticas de latencia.

## 5. Estabilidad

- **Reconexión del cliente:** espera 1, 2, 4, 8 y luego 10 s entre intentos (antes llegaba a 30 s). Nunca
  reintenta sin pausa. Si la conexión duró más de 60 s, vuelve a empezar en 1 s. Solo cambió el tope de la espera;
  la conexión, la autenticación y los casos en que no reintenta siguen igual.
- **Si Groq falla:**
  - en el LLM ya decía «Uy, se me trabó el cerebro. ¿Me lo repites?» (`FRASE_SIN_CEREBRO`);
  - ahora también si falla la **transcripción** (Groq caído, límite, sin internet): misma frase, error en el log,
    y la escucha sigue con la frase siguiente.
  - *Duda:* «la frase de error configurada». Es una constante del código, no una variable del `.env`, para no
    añadir configuración que nadie pidió.

## 6. Pruebas

- `tests/test_senal.py` (16): pasa-altas, normalización (objetivo, tope, ruido, sin saturar, sin escalones),
  acondicionador, guardián del eco (+3 dB, reinicio, buena cancelación, ruido, sin cortes, informe cada 5 s),
  remuestreo de 48 y 22,05 kHz y tramas PCM16 mono 16 kHz.
- `tests/test_transcripcion.py` (27): textos vacíos, prompt (enviado y repetido), confianza baja y su umbral,
  Groq que falla, LLM que falla, frases fijas, medidor y espera de reconexión.
- **Lo que encontraron las pruebas:** el primer limitador de picos (por trama) dejaba pasar 0,9008 y metía un
  escalón en el borde de la trama. Lo cambié por un **limitador suave** (lineal hasta 0,7 y curva hasta 0,9),
  continuo y que nunca pasa del techo.
- 170 pruebas pasan (`PRUEBAS.md`).
