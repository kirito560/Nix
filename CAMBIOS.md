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
