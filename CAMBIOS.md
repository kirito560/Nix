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

---

# Visión: Nix puede ver la pantalla (2026-11-02)

## 1. Captura (`client/screen_capture.py`)

- **Cómo se resuelve «no mandar la imagen salvo que el usuario lo pida».** Por defecto, cuando la pantalla cambia
  (pHash), el cliente manda **solo una descripción breve hecha en el PC: el título de la ventana activa**
  (`vision.delta`). La imagen reducida (≤ 768 px, JPEG ≤ 200 KB, `vision.frame`) sale solo si la pides:
  - **Ctrl+Alt+M** («mira ahora»): una sola imagen;
  - **`PANTALLA_ENVIAR_IMAGEN=1`**: en cada cambio.
  - *Duda:* «descripción en el cliente o en el servidor». En el PC no hay modelo de visión (las claves viven en
    el servidor), así que la descripción del cliente es el título de la ventana y la del servidor, la del modelo
    de visión cuando llega una imagen.
- **mss** a 1 fps por defecto (`PANTALLA_FPS`, entre 0,2 y 2), pantalla principal, reducida a 768 px con Pillow.
- **pHash** de 64 bits (DCT de 32×32, en numpy). Solo se avisa si cambian ≥ `PANTALLA_UMBRAL` bits (10) y han
  pasado ≥ 3 s desde el último aviso, para no inundar al servidor mientras haces scroll.
- **Sin disco:** `grab` (nunca `shot`) y JPEG en `BytesIO`. La imagen se suelta al terminar cada vuelta.
- **Encender y apagar:**
  - `--pantalla on|off` al arrancar (por defecto `off`);
  - **Ctrl+Alt+P** enciende o pausa al instante (pynput);
  - al pausar se avisa al servidor (`activa=false`), que olvida lo que sabía de tu pantalla.
  - *Duda:* `--pantalla on|off` es por arranque: no queda guardado. Así, si no lo pones, siempre arranca apagada.
- **Ventanas excluidas:** si el título de la ventana activa contiene alguna palabra de `PANTALLA_EXCLUIR`
  (contraseñas, gestores de claves, inicio de sesión, bancos mexicanos y PayPal), **no se captura**. Nix solo
  recibe «ventana privada; no se mira».
  - *Limitación:* solo se mira la ventana **activa**. Un banco abierto en otra ventana visible sí saldría en la
    imagen. Por eso la imagen no sale si no la pides.
- **Protocolo:** `vision.delta` lleva `activa` (por defecto `true`) y `vision.frame` lleva `caption`. Los dos con
  límites de tamaño.
- Dependencias nuevas del cliente: `mss`, `pillow`, `pynput`.

## 2. Indicador (`client/indicador.py`)

- **Al encender o pausar**, sale una línea bien visible: «🔴 PANTALLA COMPARTIDA: Nix ve tu pantalla. Ctrl+Alt+P
  la pausa al instante» / «⚫ Pantalla en pausa…». Al arrancar sale la de pausa, para que quede claro el estado.
- **Mientras está activa**, el título de la ventana de PowerShell (visible también en la barra de tareas) es
  «🔴 Nix · VIENDO TU PANTALLA (Ctrl+Alt+P pausa)».
  - *Duda:* el cliente es de consola, sin interfaz gráfica, así que el «ícono» es el título de la ventana.

## 3. Servidor (`server/vision.py`, gateway, orquestador, config, main)

- **Modelo de visión:** `qwen/qwen3.8-27b`, el mismo que ya usa Nix para conversar. Lo probé con tu clave: acepta
  imágenes y describe en ~0,6–0,9 s, con unos 800 tokens por captura de 768 px. En tu cuenta de Groq no hay otros
  modelos con visión (los Llama 4 no aparecen) y `gpt-oss` no acepta imágenes. Se cambia con `VISION_MODELO`.
- **Interfaz `ProveedorVision`:**
  - `VisionGroq`, con instrucción de no copiar datos personales, contraseñas, cuentas ni mensajes;
  - `VisionStub`, sin clave o con `VISION_PROVEEDOR=stub`, que devuelve «pantalla sin descripción».
- **`Vista`:**
  - `vision.delta` con descripción: actualiza el resumen;
  - `activa=false`: lo borra;
  - `vision.frame`: describe la imagen, como mucho una a la vez, quedándose con la última si llegan varias.
    La imagen solo vive en memoria mientras se describe, y se rechaza si pasa de 300 KB.
- **`ResumenPantalla`:**
  - guarda las 3 últimas descripciones, con su antigüedad, y caducan a los 10 min;
  - el texto para el LLM nunca pasa de `VISION_MAX_TOKENS` (120 por defecto, a ≈ 4 caracteres por token);
  - se añade al final del prompt de sistema en cada turno, con la indicación de usarlo solo si viene al caso.
- **Doble llave:** el servidor sigue ignorando todo lo de pantalla si `VISION_HABILITADA=0`. En el `.env` de este
  servidor lo puse en **1** para que funcione cuando enciendas la captura en el PC. En el código y en
  `.env.example`, el valor por defecto sigue siendo 0.

## 4. Pruebas (`tests/test_pantalla.py`, 32)

- **pHash:** la misma pantalla con ruido queda cerca y otra pantalla, lejos; solo se avisa cuando cambia; no más
  de un aviso cada 3 s; «mira ahora».
- **Tamaño:**
  - se reduce a 768 px sin deformar y nunca se agranda;
  - el JPEG no pasa del límite, ni con ruido puro;
  - una pantalla 4K sale ≤ 768 px y ≤ 200 KB;
  - el protocolo rechaza imágenes y textos enormes.
- **Nada en disco:** captura con un mss falso más una imagen pedida. Ninguna apertura de archivo para escribir,
  `Image.save` solo a `BytesIO`, `shot()` prohibido, y ni la carpeta de trabajo ni la temporal ganan archivos.
- **Apagada al iniciar:** el objeto nace apagado, conectado no captura nada, y `client.main` sin `--pantalla`
  arranca apagado.
- **Resto:** pausa (avisa al servidor y al indicador), ventanas excluidas, atajos (con y sin pynput),
  indicador, stub y Groq (falso), la `Vista` describe solo la última, el resumen respeta el tope y caduca, el
  resumen llega al prompt del LLM y el gateway lo ignora si `VISION_HABILITADA=0`.
- **Fallo encontrado:** el primer aviso podía quedar bloqueado si el reloj marcaba menos de 3 s (empezaba con «último
  envío = 0»). Corregido: empieza en «nunca».
- Pillow se añadió a las dependencias de pruebas del servidor. 202 pruebas pasan (`PRUEBAS.md`).

---

# Presencia por cámara (2026-11-02)

## 1. Cámara (`client/camara.py`)

- **OpenCV con el clasificador Haar de caras**, a 320×240 y 2 fps. Elegí Haar en vez de MediaPipe porque ya viene
  dentro de OpenCV, sin descargar modelos.
  - **Versión fijada:** `opencv-python-headless==4.14.0.94`. **OpenCV 5 ya no trae Haar** (lo comprobé: lo
    cambió por YuNet, que necesita un modelo aparte). La versión «headless» no trae ventanas, que no hacen
    falta.
- **Solo devuelve un booleano.** Cada frame vive lo que tarda la detección (`del frame` justo después); no se guarda
  ni el anterior. Lectura y detección van en un hilo para no frenar el audio.
- **Para que no parpadee**, porque Haar falla a veces si miras a un lado o hay poca luz:
  - «presente» pide 2 frames seguidos con cara;
  - «ausente» pide 4 s sin ninguna, contados desde la última cara o desde que se encendió.
  - *Duda:* los umbrales. Elegí unos simples; se ajustan en `PRESENTE_TRAS_FRAMES` y `AUSENTE_TRAS_S`.
- **Sin cámara, cámara ocupada o sin OpenCV:** aviso en el log («📷 la cámara queda apagada: …») y sigue apagada.
- **Protocolo:** el mensaje `presencia.update {ts, presente}` entra en este commit, porque `camara.py` lo necesita
  para importarse. Lleva además `activa` (por defecto `true`; `false` = la cámara se apagó y el servidor olvida
  el dato).
  - *Duda:* pediste solo `{ts, presente}`. Sin `activa`, al apagar la cámara habría que mandar `presente=false`,
    y Nix creería que te fuiste. Es otro booleano: no sale nada más de la cámara.

## 2. Envío y control (`client/main.py`, `client/indicador.py`, atajos)

- **`--camara on|off`** (por defecto `off`) y **Ctrl+Alt+C**, que **solo apaga**: encenderla es solo con
  `--camara on`, como pediste. Al apagar se suelta la cámara (se apaga su luz) y se manda `activa=false`.
- **`presencia.update` sale solo cuando cambia** el sí/no, nunca por frame. Al reconectar con el servidor se manda el
  estado actual, porque cada sesión del servidor empieza sin el dato.
- **Indicador:** un solo `Indicador` para pantalla y cámara. Cada cambio imprime una línea (🔴 CÁMARA ENCENDIDA…
  / ⚫ Cámara apagada) y el título de la ventana dice lo que está encendido en ese momento.
- `CAMARA_INDICE` en `.env.example` para elegir la cámara (0 = la primera).

## 3. Servidor (`server/sesion.py`, gateway, main)

- **`EstadoSesion`:** vive con la conexión actual. El gateway crea uno nuevo al conectarse un cliente y lo tira al
  desconectarse. `presencia.update` lo actualiza; con `activa=false` el dato vuelve a «sin dato».
- **Solo se usa en el contexto del LLM.** En cada turno se añade una línea al prompt de sistema: «el usuario está
  frente a la cámara» o «no está…». Sin dato, no se añade nada.
- **No es memoria:** no entra en el historial de la conversación ni se guarda en disco. Todavía no hay memoria de
  largo plazo (fase 4); cuando llegue, la presencia no debe entrar en ella.
- No hace falta encender nada en el servidor: si el cliente no manda presencia, no hay línea. A diferencia de la
  pantalla, no hay segunda llave, porque es un solo booleano.

## 5. Título de la ventana («Nx» → «Nix»)

- **En el código siempre decía «Nix».** El título vive en `client/indicador.py`, no en `screen_capture.py`, y
  no había «Nx» en ningún archivo.
- **Causa más probable:** el emoji 🔴 al inicio del título. Ocupa dos unidades UTF-16 y la consola clásica de
  Windows (`SetConsoleTitleW` en conhost) lo maneja mal, comiéndose letras de al lado. No lo pude comprobar en
  Windows.
- **Corrección:** el título va **solo en ASCII**. Ejemplos:
  - `Nix [EN VIVO] - VIENDO TU PANTALLA (Ctrl+Alt+P pausa)`;
  - `Nix [EN VIVO] - ... - CAMARA ENCENDIDA (Ctrl+Alt+C apaga)`;
  - `Nix - pantalla y camara apagadas`.

  Una prueba exige que empiece por «Nix», que no contenga «Nx» y que sea ASCII. Las líneas impresas en la consola
  conservan sus emojis, porque esas sí se veían bien.

## 4. Pruebas (`tests/test_camara.py`, 20)

- **Arranca apagada:**
  - el objeto nace apagado;
  - conectado y apagado no abre la cámara;
  - `client.main` sin `--camara` la deja apagada; con `--camara on` pide encenderla.
- **Nunca escribe archivos:** OpenCV falso en el que `imwrite`, `imencode` y `VideoWriter` lanzan error. Ninguna
  apertura de archivo para escribir y ningún archivo nuevo en la carpeta de trabajo ni en la temporal. Además,
  después de cada paso ni `Presencia` ni la cámara guardan un frame.
- **Solo al cambiar:**
  - parpadeo de un frame sin cara: no manda nada;
  - «ausente» solo tras 4 s;
  - no se repite;
  - el mensaje solo tiene `type`, `ts`, `presente` y `activa`;
  - al reconectar manda el estado actual.
- **Sin cámara o sin OpenCV:** queda apagada, lo avisa, el bucle no falla y la conversación sigue con
  `--camara on`.
- **Apagar:** suelta la cámara y avisa al servidor y al indicador. Ctrl+Alt+C apaga pero no enciende.
- **Indicador, Haar real** (con el OpenCV instalado; si falta, la prueba se salta) **y servidor:** estado de la
  sesión, que el gateway olvida al desconectar, y la presencia va al prompt pero no al historial.
- 225 pruebas pasan (`PRUEBAS.md`).

---

# Ronda 2: personalidad y conversación (2026-11-02)

## 1. Respuestas cortas

- **Prompt:** «una o dos frases; nada de listas, discursos ni explicaciones largas; si te piden algo largo, da lo
  esencial en dos frases y ofrece seguir».
- **`LLM_MAX_TOKENS` de 300 a 90.** Medido con Groq: Qwen no gasta tokens pensando y dos frases ocupan unos
  27–38 tokens. Cambié también el valor en el `.env` del servidor.
  - El modelo de respaldo, gpt-oss, sí razona (~30 tokens) y esos tokens cuentan dentro del límite. Para él se
    suman 100 de margen, así la respuesta no sale vacía.
- **Tope duro de 2 oraciones** (`RESPUESTA_MAX_ORACIONES`, variable nueva). Al terminar la 2.ª oración se cierra el
  stream del LLM: no se paga ni se dice el resto.
  - *Duda:* el tope por oraciones podía ser demasiado. Lo dejé porque el prompt solo no basta. Con 0 se apaga.

## 2. Tono (`personajes/nix.md`, `server/tono.py`, orquestador)

- **Ficha de Nix:**
  - «burlona» pasa a «bromista»;
  - se quitó «skill issue» de su jerga;
  - «lo pica todo el tiempo» pasa a «le hace bromas de vez en cuando, con cariño»;
  - límites nuevos: nada de insultos ni de burlas repetidas, ni de broma, y no presionar.
- **Instrucciones de voz:** bromas con cariño y sin presionar, con ejemplos de lo que no debe decir.
- **Red de seguridad (`server/tono.py`):** una lista corta de insultos y frases hirientes («skill issue», «no me
  hagas perder el tiempo», «manco», «noob», «idiota»…). Si una frase del LLM contiene una, **no se dice** y queda en
  el log («🧹 frase descartada por el tono»). Si se quitara todo, dice «Jaja, va, ¿en qué estábamos?».
  - *Duda:* palabras como «tonto» pueden salir sin mala intención («me hice el tonto»). Elegí quitarlas igual: es
    mejor perder una frase que decir algo que suene feo.

## 3. Aclaraciones

- **Regla fija** en la escucha (`poco_claro`): si lo transcrito es **una sola palabra de menos de 4 letras** que no
  es una respuesta corta conocida («sí», «no», «ok», «va», «sale», «qué», «hola», «gg»…), Nix no la manda al LLM
  y dice «Perdón, no te entendí bien, ¿me lo repites?».
- **El LLM decide lo demás** (frases más largas pero confusas o sin sentido): el prompt le pide que, en ese caso,
  pida repetir en una sola frase y que no invente ni adivine.
  - *Duda:* ¿hasta dónde llega la regla fija? Elegí la opción simple (una palabra muy corta). Una regla más amplia
    pediría repetir cosas válidas como «Fortnite».
- Ya existía: confianza baja de Whisper → pedir repetir; texto vacío o de un carácter → se ignora.
- Las frases fijas quedaron en **una sola oración**: «Perdón, no te entendí bien, ¿me lo repites?» y «Uy, se me
  trabó el cerebro, ¿me lo repites?».

## 4. Memoria de sesión

- **El historial deja de ser de todo el servidor y pasa a la sesión** (`EstadoSesion.historial`). Antes
  sobrevivía a las reconexiones; ahora cada conexión del PC empieza vacía y, al desconectarse, se tira.
- Se guardan los **últimos 10 turnos** (`HISTORIAL_TURNOS=10`, que ya existía; cada turno es lo que dijiste más lo
  que respondió Nix) y van como contexto al LLM.
- Las frases fijas («no te entendí», «se me trabó») no entran. Nada se guarda en disco ni en memoria de largo
  plazo.
  - *Duda:* un corte de Wi-Fi de un segundo también borra la conversación. Es lo que pediste («se borran al
    desconectarse») y es lo más simple.

## 5. Estados de ánimo (`server/animo.py`)

- **Tres valores de 0 a 1:** energía (base 0,7), paciencia (0,7) y cercanía (0,5).
- **Después de cada respuesta** cambian según señales sencillas de lo que dijiste:
  - risas o «GG»: más energía y cercanía;
  - «gracias» o cariño: más cercanía;
  - «cansado» o «aburrido»: menos energía;
  - insultos o «no sirves»: menos paciencia y cercanía;
  - platicar sin más: +0,01 de cercanía.
- **Topes:** cada valor se mueve **como mucho 0,1 por respuesta** y siempre queda entre 0 y 1.
- **Vuelta a la base:** la distancia a la base se reduce a la mitad cada 10 min.
- **Solo cambia el tono:** una línea en el prompt de sistema, por ejemplo «Tu ánimo ahora (solo cambia tu tono; no lo
  menciones): con mucha energía, paciente, amistosa». La voz no cambia.
- **Vive en la sesión** y se olvida al desconectarse.
- Las frases fijas («no te entendí») no lo mueven.
  - *Duda:* ¿ánimo por sesión o de todo el servidor? Elegí por sesión, igual que el historial: es lo más simple y
    no deja nada guardado.
  - Es una primera versión muy simple de lo que en el plan es el «motor emocional» (fase 3).

## 6. Presencia por cámara

- **Ningún código hace hablar a Nix por la presencia** (lo revisé: el gateway solo actualiza el dato de la sesión).
  No había respuestas automáticas que quitar.
- El texto que va al prompt ya no invita a comentarlo. Antes decía «puede haberse levantado y no oírte bien»;
  ahora dice «el usuario está / no está frente a la cámara. Es solo contexto: no lo menciones salvo que el
  usuario pregunte si lo ves o si está ahí».

## 7. Pruebas (`tests/test_personalidad.py`, 39)

- **Límite de tokens:**
  - 90 por defecto (y +100 de margen con gpt-oss);
  - una respuesta larga se corta en 2 oraciones y el stream deja de leerse;
  - las comas no cuentan como oración;
  - la respuesta queda dentro de 90 × 4 caracteres.
- **Insultos:** seis respuestas de prueba (cuatro hostiles); ninguna frase dicha contiene una expresión de la
  lista; si todo era hostil, dice la frase amable. Las frases fijas y la jerga de la ficha tampoco tienen
  insultos, y el filtro no confunde palabras parecidas.
- **Aclaraciones:** palabras cortas raras piden repetir, las válidas no, y lo poco claro no llega al LLM.
- **Memoria de sesión:** guarda 10 turnos, se borra al desconectarse y respeta `HISTORIAL_TURNOS`.
- **Ánimo:**
  - 500 turnos al azar sin salir de 0 a 1 ni moverse más de 0,1 por respuesta;
  - vuelve a la base (mitad en 10 min);
  - el texto del tono cambia;
  - las frases fijas no lo mueven.
- **Presencia:** el PC manda cuatro cambios de presencia y Nix no responde nada (ni texto ni audio); el texto del
  contexto pide no mencionarla.
- 264 pruebas pasan (`PRUEBAS.md`).
- **Prueba real con Groq** (8 frases parecidas a las de la conversación anterior):
  - todas las respuestas son amables, sin insultos, de 1 o 2 oraciones;
  - el pedido largo quedó en 2 oraciones;
  - **dos turnos fallaron con 429:** la cuenta gratuita de Groq permite 7.000 tokens de entrada por minuto con
    Qwen, y cada turno manda ~1.300 (ficha + historial). Queda pendiente (ver ESTADO.md).

## 8. Repositorio: notas sobre contraseñas

- **Quitada de `ESTADO.md`** la nota de seguridad que pediste quitar. Nunca incluyó ningún dato secreto.
- **También en `ESTADO.md`:**
  - la recomendación de SSH quedó neutral («acepta conexiones desde cualquier dirección; recomendado entrar solo
    con llave»), sin hablar de cambiar contraseñas;
  - la auditoría y el registro de cambios ya no mencionan la contraseña del servidor ni `sudo`; dicen «permisos de
    administrador» y «solo lecturas».
- **Se quedan, porque son código, comandos o la función de pantalla:**
  - `sudo ufw …` y `sudo apt install ffmpeg` en el README y en `requirements-server.txt`;
  - la lista `PANTALLA_EXCLUIR` («contraseña», «password»…);
  - las descripciones de que no se capturan ventanas de contraseñas.
- **Ojo:** el texto viejo sigue en el historial de git (commit `d99378b`, ya subido a GitHub). Borrarlo de ahí
  obligaría a reescribir el historial y forzar el push; no lo hice.


---

# Respaldo cuando Qwen llega a su límite (2026-11-02)

- **Qué hace:** si Groq contesta a Qwen con **429** (límite de tokens o peticiones por minuto) o con **498/503** (sin
  capacidad), ese turno lo responde `LLM_MODELO_RESPALDO` (`openai/gpt-oss-120b`), con la misma conversación.
  - El error llega como respuesta HTTP, antes de cualquier texto: Nix no se corta ni repite nada, y el usuario no
    nota el cambio. Lo que dice el respaldo entra al historial como cualquier respuesta suya.
  - **Solo ese turno:** el siguiente vuelve a probar Qwen. El 404 «modelo retirado» sigue cambiando para siempre,
    como antes.
  - *Duda:* «429 o similar». Elegí 429, 498 y 503, que son pasajeros. Errores como 401 (clave mala) o 400 no usan
    el respaldo, porque no ayudaría.
- **Si el respaldo también falla:** el error sube, Nix dice «Uy, se me trabó el cerebro, ¿me lo repites?» y sigue
  escuchando, igual que antes.
- **Log:** cada uso sale como `🧠 RESPALDO: qwen/qwen3.8-27b no responde ahora (Groq respondió 429: …); este turno
  lo responde openai/gpt-oss-120b`. Si el respaldo falla: `🧠 RESPALDO: openai/gpt-oss-120b también falló (…)`.
  Se filtra con `journalctl --user -u nix | grep RESPALDO`.
- **Pruebas:** 8 nuevas en `tests/test_llm.py`.
  - Un 429, 503 o 498 simulado en Qwen hace que responda gpt-oss, con razonamiento y margen de tokens, y el log
    dice «RESPALDO».
  - Solo ese turno; si los dos fallan, error; sin respaldo, error; un 401 no usa el respaldo.
  - De punta a punta con el Orquestador: respaldo → frase de error → Qwen de vuelta.
- **Prueba real con Groq:** 8 turnos seguidos con la ficha de Nix. El primero chocó con el límite de Qwen y lo
  respondió gpt-oss en 484 ms en total; los siguientes ya volvieron a Qwen.

---

# Latencia (2026-11-02)

Medido con un WAV de 12 frases (voz de Jorge, algunas con pausas a media frase de 250 a 370 ms) mandado al
servicio real como si fuera el PC, con 16 s entre turnos. Groq varía ±150 ms de una medición a otra.

## Base

total **p50 1495 / p95 2936 ms** (n=12):
- VAD 421 ms;
- transcripción 291 ms;
- LLM, primer token 315 ms;
- primera frase 11 ms;
- TTS, primer audio 372 ms.

## 1. Fin de turno más rápido

- **Con 250 ms:** 2 de 12 frases se cortaron en una pausa de ~300 ms. «Creo que | la pizza…» y «Bueno, | dime…»: el
  principio se descartó por corto y el resto llegó como otro turno. En una primera prueba, con pausas más largas
  (410–490 ms), se cortaron 6.
- **Con 300 ms: ninguna frase cortada.** Elegido: `VAD_SILENCIO_MS=300`, en el código, en `.env.example` y en el
  `.env` del servidor.
- **La transcripción adelantada pasa de 200 a 150 ms** (`STT_ADELANTO_MS`), para que siga empezando antes de que el
  VAD cierre.
- **Resultado:** el VAD ahorra 100 ms (421 → 321), pero la transcripción espera más (Whisper tarda ~350 ms en Groq y
  ya no le da tiempo a terminar antes del cierre). De fin de voz a texto se pasó de ~712 a ~683 ms: **unos 30 ms de
  ganancia real**.
- **Probé `whisper-large-v3-turbo`:** 314 ms contra 353, y transcribe igual. Casi todo es red y cola de Groq, así
  que no lo cambié.
- **Ojo:** con la interrupción por voz apagada (`BARGE_IN=0`), una frase que empieza mientras Nix habla se descarta
  sin aviso. Con turnos de 13 s, dos frases se perdieron así en la primera medición; por eso medí con 16 s.

## 2. Primera frase más corta (`server/pipeline/frases.py`)

- **La primera frase** sale en el primer final de oración, la primera coma (o «;», «:»), o al completar **5
  palabras**, lo que llegue antes.
  - *Duda:* «4 a 6 palabras». Elegí 5.
  - Antes, la coma solo contaba después de 20 caracteres.
- **Las demás frases** se cortan como antes: fin de oración, o espacio si pasan de 160 caracteres.
- **Medido:** primera frase en 13 ms p50 (antes 11–14) y 58 ms p95 (antes 38–75). Qwen ya empieza casi siempre
  con algo corto («¡Muy bien!», «Depende de tu humor:»), así que el cambio solo ayuda cuando la primera oración es
  larga y sin comas. El total no se movió de forma medible (lo domina la variación de Groq).
- **Costo:** si la primera oración se corta a la 5.ª palabra, la voz hace una pequeña pausa ahí, porque son dos
  síntesis.

## 3. TTS en streaming (`server/pipeline/tts.py`)

- **Confirmado: ya trabajaba en streaming.**
  - Microsoft manda el MP3 en trozos de 720 bytes (120 ms de audio) mientras sintetiza; llegan en ráfagas.
  - Cada trozo entra a ffmpeg al llegar, y el PCM sale en trozos de 100 ms. ffmpeg necesita 2 trozos para dar el
    primer audio.
  - No se espera a la frase completa.
- **Probé pedir PCM directo a Microsoft** (`raw-24khz-16bit-mono-pcm` y otros): no devuelve audio. Solo da MP3.
- **Hallazgo:** Microsoft guarda una caché de las frases que ya sintetizó.

  | Caso | Primer audio | Detalle |
  |---|---|---|
  | Frase repetida | ~150 ms | (mis medidas anteriores eran así) |
  | Frase nueva, 2–3 palabras | ~320 ms | (300–590 ms en general) |
  | Frase nueva, 4–8 palabras | ~450 ms | |

  Por eso dentro de una conversación real da 300–400 ms: el texto siempre es nuevo.
- **Cambios:**
  - **ffmpeg de reserva:** siempre hay uno ya arrancado. Primer PCM tras el MP3: 7 ms en vez de 42 ms (unos 35 ms
    por frase).
  - **Caché de voz propia** (frases de hasta 50 caracteres, 300 frases, solo en memoria): una frase corta que ya se
    dijo sale al instante. Al arrancar se precargan las frases fijas y 15 aperturas típicas de Nix («Uy,», «Jaja,»,
    «¡Obvio!», «¡Qué onda!»…).
- **Medido** (`frase_1→tts_1er_audio`):
  - antes, p50 372 / p95 801 ms;
  - después, p50 380–385 / p95 630–730 ms.
  - Sin cambio claro en el p50: lo domina el tiempo de Microsoft con texto nuevo. Los 35 ms de ffmpeg quedan
    dentro del ruido.
  - La caché solo ayuda cuando la primera frase se repite: no se nota en 12 turnos recién arrancado, sí en una
    charla larga.


## 4. Pruebas (`tests/test_fin_de_turno.py`, 16)

- **Fin de turno:**
  - 300 ms por defecto (config, `.env` y `DetectorVoz`), con la transcripción adelantada antes del cierre;
  - el fin llega 300 ms (± 32) después de la voz, 100 ms antes que con 400;
  - pausas de 150–250 ms no cortan la frase y una de 500 ms sí.
- **Primera frase:**
  - con 5 palabras si no hay coma, y entre 4 y 6;
  - en la coma o en el punto si llegan antes;
  - las demás solo por puntuación;
  - no cuenta una palabra a medias;
  - `palabras_primera` se puede configurar.
- **TTS:**
  - una frase corta repetida sale de la caché sin llamar a Microsoft;
  - las largas o cortadas a medias no se guardan;
  - la precarga llena la caché;
  - se usa el ffmpeg de reserva, se repone otro y al cerrar no queda ninguno.
- 288 pruebas pasan (`PRUEBAS.md`).

## 5. Medición final

24 turnos: el mismo WAV dos veces seguidas, sin reiniciar, para que la caché se llene como en una charla. Las 24
frases llegaron enteras. Un turno lo respondió el respaldo por el límite de Groq. 3 de 24 primeras frases salieron
de la caché, en 1–2 ms.

```
voz_fin→vad_fin          p50 321 / p95 322 ms (n=24)
vad_fin→stt_final        p50 269 / p95 679 ms
stt_final→llm_1er_token  p50 329 / p95 614 ms
llm_1er_token→frase_1    p50  10 / p95  48 ms
frase_1→tts_1er_audio    p50 272 / p95 505 ms
tts_1er_audio→enviado    p50   1 / p95   1 ms
total                    p50 1268 / p95 1551 ms   (antes 1495 / 2936, n=12)
tras_vad                 p50 947 / p95 1230 ms   (antes 1083 / 2515)
```

# Router de LLM local: freellmapi (2026-11-03)

Pedido: poder usar un router local compatible con OpenAI (freellmapi) como LLM, sin quitar Groq.

## Código
- `server/pipeline/llm.py`:
  - **`LLMRouter`**: librería `openai` con `base_url=FREELLMAPI_URL` y `model="auto"`, en streaming. Devuelve los
    trozos de `delta.content`, igual que `LLMGroq`. Registra qué modelo eligió el router en cada turno.
  - **`LLMConRespaldo`**: si el router falla antes de dar texto, ese turno lo responde Groq directo (con su
    propio respaldo de gpt-oss) y se registra `🧠 RESPALDO: el router freellmapi falló (...)`. El turno siguiente
    vuelve a probar el router.
  - `crear_llm`: `LLM_PROVEEDOR=freellmapi` → router con Groq de respaldo. Sin la variable, `auto` o `groq` →
    Groq directo, como antes.
- `server/config.py`: `FREELLMAPI_URL`, `FREELLMAPI_CLAVE` y `FREELLMAPI_ESPERA_S` (4 s). Los nombres de las
  variables que ya existían no cambian.
- `server/main.py`: el log de arranque dice qué proveedor y qué respaldo se usan.
- `requirements-server.txt`: `openai==3.42.1`, instalada en `.venv`.

## Decisiones (la opción más simple)
- **Cualquier fallo del router antes de la primera palabra pasa a Groq**, no solo conexión, 429, 5xx y tiempo
  agotado. Una clave mal puesta (401) o un 400 también: si no, Nix se quedaría callada por un error de
  configuración. El log dice el tipo de error, así se nota.
- **Si el router ya empezó a hablar y se corta, no se cambia a Groq:** no se puede empezar otra respuesta a
  media frase. El error sube y pasa lo mismo que con Groq.
- **La librería `openai` no reintenta (`max_retries=0`):** cada reintento sumaría latencia, y para eso está Groq.
- **Tope hasta la primera palabra: 4 s** (`FREELLMAPI_ESPERA_S`). El router puede tardar probando proveedores;
  pasado ese tiempo, responde Groq.
- **Si falta `FREELLMAPI_URL`**, avisa y usa Groq. **Si falta `GROQ_API_KEY`**, usa el router sin respaldo.

## Medidor de turnos
`despliegue/medir/medir_turnos.sh <groq|freellmapi> [turnos]` es el medidor de la ronda de latencia, que estaba
solo en la carpeta temporal de la sesión. Ahora está en el repositorio, y el WAV de prueba admite N turnos
(repite las 12 frases). El proveedor se pasa al servicio con `systemctl --user set-environment` durante la
medición; el `.env` no se toca.

## Pruebas (`tests/test_router_llm.py`, 15)
Elección por `LLM_PROVEEDOR`; streaming con `model=auto` y la clave en la cabecera; respaldo con 429, 500,
502, 503, sin conexión y tiempo agotado; el turno siguiente vuelve al router; si Groq también falla, sube el
error; con el orquestador responde Groq y queda RESPALDO en el log. 303 pruebas pasan.

## Medición en el servicio real (2026-11-03)
El router **no está instalado** en el servidor (hacen falta claves de proveedores que solo tiene Kirito), así
que no se pudo medir su latencia. Se midió:

| | Turnos | RESPALDO | LLM (p50 / p95) | Total (p50 / p95) |
|---|---|---|---|---|
| `groq` | 20/20 | 0 | 338 / 472 ms | 1203 / 1623 ms |
| `freellmapi` con el router apagado | 5/5 | 5 | 323 / 340 ms | 1228 / 1538 ms |

Con el router apagado, la conexión se rechaza al instante y el respaldo no suma latencia apreciable.

## Router solo en localhost (2026-11-03)
Pedido: el `docker-compose.yml` del router publica el puerto solo en localhost, porque Docker se salta ufw.
- `despliegue/freellmapi/docker-compose.yml` (nuevo): puerto `"127.0.0.1:3001:3001"`, fijo y sin variable. El
  compose original admite `HOST_BIND=0.0.0.0`. También quité su servicio de actualización automática
  (Watchtower), porque monta el socket de Docker. Añadí un tope de 512 MB de memoria.
- `despliegue/freellmapi.service` pasó a `despliegue/freellmapi/freellmapi.service`.
- README:
  - dos formas de instalar el router: con Docker (con este compose) o sin Docker (Node 22, protegido por ufw);
  - una línea `ss -ltn` para comprobar que solo escucha en `127.0.0.1:3001`;
  - el panel se abre desde el PC con un túnel SSH (`ssh -N -L 3001:127.0.0.1:3001 ...`). Antes decía
    `http://192.168.1.50:3001`, que daba por hecho que estaba abierto a la red.

# Ronda 3: emociones, cierre temprano, interrupciones, memoria y VTube Studio (2026-11-03)

Pedido de Kirito, en orden A → B → C → D → E, con pruebas, commit y medición (`medir_turnos.sh`) en cada paso.
Antes de empezar: push de los 7 commits pendientes (aprobado) y `docker compose down` del router.

## A. Emociones en la voz de Dalia
- El prompt de voz es el que mandó Kirito:
  - compañera con humor ácido ligero, nunca pesada;
  - etiqueta de emoción obligatoria al inicio;
  - máximo 25 palabras, sin markdown ni emojis, pausas con comas;
  - pantalla comentada «como si estuviera al lado»;
  - recuerdos usados con naturalidad.
- Se mantienen los límites de siempre (nada de «skill issue», no insistir).
- El ánimo va como «Energía: 70/100 | Paciencia | Cercanía» y tiñe el tono (con poca paciencia, más seca, pero
  nunca pesada).
- `server/pipeline/emocion.py` quita la etiqueta del stream aunque llegue partida («[bur» + «la]») y también
  las acotaciones cortas a media respuesta («[risas]»). La traduce a rate/pitch/volume de Dalia (tabla en el
  README).
- La caché de voz separa emociones y precarga las 15 aperturas con cada una (93 frases al arrancar, en segundo
  plano).
- El historial guarda la respuesta con su etiqueta, para que el LLM siga el formato.
- El cliente recibe `avatar.cmd` con la emoción.
- **Medido:**
  - 19 de 20 respuestas traían etiqueta y ninguna se leyó en voz alta.
  - El primer audio de la voz subió de 233 a 379 ms p50. Una prueba directa con frases nuevas (6 por emoción)
    dio neutral 580 ms y el resto entre 519 y 695: Microsoft iba lento para todas. **No hay penalización clara
    por la prosodia**; es variación del servicio.

## B. Cierre temprano por frase completa
- Si el texto adelantado (a los 150 ms de silencio) termina en «.», «?» o «!», con 2 palabras o más y sin
  puntos suspensivos, y no hubo más voz, el turno se cierra sin esperar el resto del silencio del VAD
  (`STT_CIERRE_TEMPRANO=1`).
- Cada turno registra con «⏩» cuándo se pidió y cuándo llegó el adelanto.
- **Medido: 0 cierres tempranos en 20 turnos.** El adelanto llega a 542 ms p50 de tu voz y el VAD ya cerró a
  los 321. El silencio del VAD **no está en el camino crítico**: lo que manda es Whisper (pedido a los 150 ms
  más ~400 ms de Groq).
- El ahorro de ~150 ms que estimé en el plan era un error: suponía que el VAD frenaba la respuesta.
- Lo dejo activo porque no cuesta nada. Permitiría alargar `VAD_SILENCIO_MS` (para cortar menos frases con
  pausas) sin perder velocidad en las frases completas.
- Lo único que de verdad adelantaría la respuesta con Whisper gratis es pedir el adelanto antes (100 ms ahorraría
  ~50 ms). Cuesta más peticiones del límite diario de Whisper (2000).

## C. Interrupciones sin perder frases
- **Pre-roll de 600 ms** (`VAD_PRE_ROLL_MS`, antes 360). Medición A/B: el adelanto llegó a 626 ms con 600 contra
  610 ms con 360. La diferencia está dentro del ruido.
- **Con `BARGE_IN=0`, la frase ya no se tira:**
  - se transcribe y se responde cuando ella termina, sin contar para la latencia;
  - si son varias, en orden (candado);
  - si el texto coincide en un 60 % o más con lo que ella decía, es su eco y se ignora.
  - Medido con 6 frases que se pisaban con su voz: **las 6 se respondieron**; antes se perdían todas.
- **Historial con lo que sonó:**
  - el orquestador guarda dónde empieza y acaba cada frase en el audio;
  - al interrumpirse, lo estima por el reloj y lo corrige con `playback.ack`;
  - una frase cortada se guarda en proporción, terminada en « —».
- **Protocolo:** `barge.in` (con `pts_played`, para que el servidor no reciba el ack antes de interrumpir). Lo
  pediste como `{"type": "barge_in"}`; usé `barge.in` para seguir el estilo de los demás mensajes
  (`playback.flush`, `presencia.update`).
- **Cliente** (`client/barge.py`):
  - `Reproduccion` sabe cuánto sonó;
  - `BargeCliente` la calla al instante si hablas encima; va por energía tras el AEC y está apagado
    (`BARGE_CLIENTE=0`).
  - No hice un VAD en el cliente: el VAD (Silero) está en el servidor y todo tu audio ya le llega entero.

## D. Memoria de largo plazo
- SQLite + FTS5 en `/datos/nix/nix/memoria.sqlite` (permisos 600). La búsqueda tarda ~1 ms y con 3000 hechos
  sigue por debajo de 15 ms (prueba).
- **Caducidad pedida por Kirito:** los estados pasajeros caducan a las 48 h y pesan la mitad cada 12 h; los
  gustos, proyectos, datos y bromas no caducan.
- **Extracción** en segundo plano, cada 3 turnos y al desconectarse, más un resumen de la sesión.
  - **`llama-3.1-8b-instant` ya no existe en Groq** (404 `model_not_found`): uso `openai/gpt-oss-20b`, que tiene
    su propio límite.
  - Probado de verdad con una conversación inventada: sacó «Está cansado» (estado), «Está haciendo un bot de
    Discord en Python» (proyecto)… y **no guardó la contraseña** que aparecía.
- **Recuerdos falsos de las pruebas:** la primera medición guardó en la memoria real hechos de las frases de
  prueba («Le gusta la pizza con piña»). Los borré con `--borrar-todo`; la base se había creado en esa misma
  medición. El medidor ahora usa su propia carpeta de datos (`~/.cache/nix-medir/datos`).
- **Groq colgado:** en una medición Qwen se colgó 20 s (`ReadTimeout`) y Nix tardó eso en decir «se me trabó el
  cerebro». Ahora, si no manda nada en `LLM_ESPERA_S` (6 s), ese turno responde el respaldo.

## E. VTube Studio
- `client/vtube.py`, apagado (`--vtube on` o `VTUBE=1`):
  - API pública de VTube Studio, con reconexión automática;
  - permiso del plugin la primera vez (el token va a `%APPDATA%\Nix\vtube_token`);
  - emoción → hotkey (`VTS_HOTKEYS`), sin pulsar dos veces una expresión toggle (la apagaría);
  - `MouthOpen` con el volumen de lo que suena, medido en el callback de la tarjeta de audio.
- Probado contra un VTube Studio falso (servidor WebSocket local). **Falta probarlo con VTube Studio de verdad
  en el PC.**

## Medición (20 turnos, voz sintética, servicio real)

| Paso | Total p50 / p95 | Whisper | LLM | Voz | Nota |
|---|---|---|---|---|---|
| Antes de la ronda (mañana) | 1203 / 1623 | 273 | 338 | 233 | |
| A emociones | 1308 / 1836 | 275 | 325 | 379 | Microsoft lento |
| B cierre temprano | 1298 / 1700 | 221 | 353 | 348 | 0 cierres tempranos |
| C pre-roll 600 + cola | 1455 / 1735 | 315 | 380 | 312 | |
| C con pre-roll 360 (A/B) | 1435 / 5865 | 273 | 366 | 393 | Groq colgado 20 s en 1 turno |
| D memoria | 1616 / 2124 | 367 | 598 | 257 | **19 de 20 con respaldo**: Qwen agotó su límite diario |
| E (5 turnos de humo) | 1610 / 2045 | 400 | 612 | 407 | 4 de 5 con respaldo |

**La latencia no bajó a ~1,0 s.** Con Qwen quedó entre 1,3 y 1,45 s; la mañana fue la mejor medición (1,2 s).
- Ninguno de los bloques ataca el camino crítico, que sigue siendo Whisper (~400 ms tras el adelanto), el primer
  token de Qwen (~350) y la primera frase de Microsoft (~250–400).
- La variación entre mediciones (Groq y Microsoft según la hora) es mayor que cualquier cambio de esta ronda.

**Límite diario de Qwen:** 200 000 tokens. El prompt de sistema ya mide ~1060 tokens antes del contexto, y con
el historial cada turno gasta ~1500–2000: **~100–130 turnos al día** con Qwen. Las 6 mediciones de hoy
(~125 turnos) lo agotaron, y desde ahí responde gpt-oss-120b.

# Menos tokens por turno (2026-11-03)

Cada turno manda a Groq la ficha de Nix, las reglas de voz, el ánimo, la memoria y el historial. El límite
diario de Qwen (200 000 tokens) se gastaba casi todo en eso.

- **Medición:** el servidor anota en el log los tokens de cada petición, que cuenta Groq (`📏 tokens ...: entrada N`),
  y `medir_turnos.sh` los resume.
- **Ficha de Nix** (`personajes/nix.md`): de 2603 a 1643 caracteres. Se quitó lo repetido con las reglas de voz y
  la sección VOZ (no sirve al LLM); se conservan todos los rasgos, gustos, límites y el aspecto.
- **Reglas de voz** (`INSTRUCCIONES_VOZ`): de 1646 a 863 caracteres, con las mismas reglas.
- **Historial:** `HISTORIAL_TURNOS` de 10 a 6 (también en el `.env` del servidor).
- **Memoria:** como máximo ~300 tokens (`MAX_TOKENS_MEMORIA`); si no cabe un recuerdo se queda fuera y el resumen
  de la sesión anterior se recorta.

| Medición (3 turnos, Qwen) | Turno 1 (sin historial) | Turno 2 |
|---|---|---|
| Antes | 1329 | 1373 |
| Después | 865 | 921 |

Con la conversación ya larga (cada intercambio suma ~45–60 tokens): antes ~1800–1900 por turno, ahora ~1150–1250.
