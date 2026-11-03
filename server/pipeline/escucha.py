"""
Escucha de una conexión: audio del cliente -> VAD -> STT -> Orquestador.

Por cada frase que detecta el VAD se lanza una tarea que abre la sesión STT y le va pasando el audio
por una cola (así el audio no se pierde mientras se abre la conexión con el proveedor). Al cerrar la
frase, la tarea pide el texto final y se lo pasa al Orquestador, que responde.

Transcripción adelantada: con STT que no es streaming (Groq), en cuanto el usuario lleva STT_ADELANTO_MS
callado se empieza a transcribir lo que hay. Si la frase termina sin más voz, ese resultado se usa y se
ahorra la diferencia con VAD_SILENCIO_MS (~200 ms); si sigue hablando, se descarta.

Cierre temprano (STT_CIERRE_TEMPRANO=1): si el texto adelantado ya es una frase completa (termina en «.», «?»
o «!», sin puntos suspensivos y con 2 palabras o más) y no hubo más voz, el turno se cierra en ese momento sin
esperar el resto del silencio del VAD. Cada turno registra cuándo se pidió y cuándo llegó el adelanto («⏩»),
para ver qué etapa decide de verdad cuándo empieza la respuesta.

Frases mientras el personaje habla: solo cuentan cuando juntan BARGE_MIN_VOZ_MS de voz; si terminan antes,
se toman por eco de su propia voz (o ruido) y se descartan sin transcribirlas.
  - BARGE_IN=1: se interrumpe al personaje y la frase sigue como cualquier otra.
  - BARGE_IN=0: no se le interrumpe, pero la frase NO se pierde: se transcribe y se responde en cuanto ella
    termina de hablar (o en cuanto el cliente la calla con barge.in). Si el texto es casi igual a lo que ella
    estaba diciendo, era su propio eco y se descarta.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING

from server.latencia import Span
from server.orquestador import FRASE_NO_ENTENDI, FRASE_SIN_CEREBRO
from server.pipeline.filtros_whisper import es_texto_vacio, poco_claro
from server.pipeline.stt import ProveedorSTT, a_pcm16
from server.pipeline.vad import DetectorVoz, Reagrupador
from shared.texto import normalizar

if TYPE_CHECKING:
    from server.orquestador import Orquestador

log = logging.getLogger("nix.escucha")


ESPERA_MAX_S = 60.0          # lo más que una frase espera a que el personaje termine de hablar
UMBRAL_ECO = 0.6             # fracción de palabras que ya estaban en lo que ella decía


def es_eco(texto: str, dicho: str) -> bool:
    """¿La transcripción es su propia voz que volvió por el micrófono? Casi todas sus palabras están en lo que
    ella estaba diciendo."""
    palabras = normalizar(texto).split()
    if len(palabras) < 2 or not dicho:
        return False
    suyas = set(normalizar(dicho).split())
    return sum(p in suyas for p in palabras) / len(palabras) >= UMBRAL_ECO


def frase_completa(texto: str | None) -> bool:
    """¿El texto ya parece una frase terminada? Whisper puntúa: «.», «?» o «!» al final. Los puntos
    suspensivos («Creo que...») son una pausa a media frase, no un final."""
    if not texto:
        return False
    t = texto.strip()
    return (t.endswith((".", "?", "!")) and not t.endswith(("...", "…")) and len(t.split()) >= 2)


class Escucha:
    def __init__(self, detector: DetectorVoz, stt: ProveedorSTT, orquestador: "Orquestador",
                 barge_in: bool = True, barge_min_voz_ms: float = 400.0, adelanto_ms: float = 0.0,
                 cierre_temprano: bool = False):
        self.detector, self.stt, self.orquestador = detector, stt, orquestador
        self.barge_in, self.barge_min_voz_ms = barge_in, barge_min_voz_ms
        self.adelanto_ms, self.cierre_temprano = adelanto_ms, cierre_temprano
        self.adelanto_de: float | None = None    # t_ultima_voz de la pausa ya adelantada
        self.reagrupador = Reagrupador()
        self.cola: asyncio.Queue | None = None
        self.tareas: set[asyncio.Task] = set()
        self.pendiente_de_barge = False      # la frase en curso empezó mientras el personaje hablaba
        self.voces_detectadas = 0            # frases que el VAD empezó (para el medidor del gateway)
        self.fila = asyncio.Lock()           # frases en cola: se responden una tras otra, en orden

    async def recibir_audio(self, pcm16: bytes) -> None:
        for ventana in self.reagrupador.agregar(pcm16):
            for ev in self.detector.procesar(ventana, time.monotonic()):
                self._evento(ev)

    def _evento(self, ev) -> None:
        if ev.tipo == "inicio":
            self.voces_detectadas += 1
            self.pendiente_de_barge = self.orquestador.hablando
            self.cola = asyncio.Queue()
            self.cola.put_nowait(a_pcm16(ev.audio))
            self._lanzar(self._frase(self.cola, str(uuid.uuid4()), self.pendiente_de_barge), "frase")
        elif ev.tipo == "voz" and self.cola is not None:
            self.cola.put_nowait(a_pcm16(ev.audio))
            d = self.detector
            if (self.adelanto_ms and d.en_frase and d.ms_silencio >= self.adelanto_ms
                    and self.adelanto_de != d.t_ultima_voz and not self.pendiente_de_barge):
                self.adelanto_de = d.t_ultima_voz
                self.cola.put_nowait(("adelantar", d.t_ultima_voz))
            if self.pendiente_de_barge and self.detector.ms_voz >= self.barge_min_voz_ms:
                self.pendiente_de_barge = False
                if self.barge_in:
                    self._lanzar(self.orquestador.interrumpir("barge-in"), "barge-in")
                else:
                    log.info("⏳ hablas mientras ella habla: tu frase se responde cuando termine")
        elif ev.tipo in ("fin", "descartada") and self.cola is not None:
            if self.pendiente_de_barge:
                log.info("🔇 frase durante su voz, demasiado corta para interrumpir: la tomo por eco")
                ev.tipo = "descartada"
            self.pendiente_de_barge = False
            self.cola.put_nowait((ev.tipo, ev.t_ultima_voz, time.monotonic()))
            self.cola = None

    async def _en_cola(self, turno: str, texto: str, span: Span) -> None:
        """Frase dicha mientras ella hablaba (BARGE_IN=0): se responde cuando termine, salvo que sea su eco."""
        if es_eco(texto, self.orquestador.ultimo_dicho):
            log.info("🔇 era su propia voz (eco), no la respondo: %s", texto)
            return
        log.info("🗣️ usuario (mientras ella hablaba): %s", texto)
        inicio = time.monotonic()
        async with self.fila:                # si hay varias, en el orden en que las dijiste
            while self.orquestador.hablando and time.monotonic() - inicio < ESPERA_MAX_S:
                await asyncio.sleep(0.05)
            if time.monotonic() - inicio > 0.05:
                log.info("⏳ respondo tu frase tras esperar %.1f s a que terminara", time.monotonic() - inicio)
            # No cuenta para la latencia: esperó a propósito.
            await self.orquestador.al_texto_usuario(turno, texto, span, medir=False)

    def _vigilar_adelanto(self, sesion, cola: asyncio.Queue, t_voz: float, tiempos: dict) -> None:
        tiempos.clear()
        tiempos["pedido"] = time.monotonic()
        tarea = getattr(sesion, "adelanto", None)
        if tarea is not None:
            tarea.add_done_callback(lambda t: self._adelanto_listo(t, cola, t_voz, tiempos))

    def _adelanto_listo(self, tarea: asyncio.Task, cola: asyncio.Queue, t_voz: float, tiempos: dict) -> None:
        ahora = time.monotonic()
        tiempos["listo"] = ahora
        if (not self.cierre_temprano or tarea.cancelled() or tarea.exception() is not None
                or self.cola is not cola or self.pendiente_de_barge):
            return
        d = self.detector
        if not d.en_frase or d.t_ultima_voz != t_voz or not frase_completa(tarea.result().texto):
            return
        if d.cerrar_ya():
            tiempos["temprano"] = ahora
            log.info("⏩ cierre temprano: la frase ya está completa a %.0f ms de tu voz", (ahora - t_voz) * 1000)
            cola.put_nowait(("fin", t_voz, ahora))
            self.cola = None

    @staticmethod
    def _registrar_adelanto(tiempos: dict, t_voz: float, t_vad: float, usado: bool) -> None:
        if not usado or "pedido" not in tiempos:
            return
        ms = lambda t: (t - t_voz) * 1000  # noqa: E731
        listo = f"listo a {ms(tiempos['listo']):.0f} ms" if "listo" in tiempos else "sin terminar"
        cierre = "temprano" if "temprano" in tiempos else "por el VAD"
        log.info("⏩ adelanto: pedido a %.0f ms de tu voz, %s; turno cerrado a %.0f ms (%s)",
                 ms(tiempos["pedido"]), listo, ms(t_vad), cierre)

    def _lanzar(self, corrutina, nombre: str) -> None:
        tarea = asyncio.create_task(corrutina, name=nombre)
        self.tareas.add(tarea)
        tarea.add_done_callback(self.tareas.discard)

    async def _frase(self, cola: asyncio.Queue, turno: str, durante_su_voz: bool = False) -> None:
        try:
            sesion = await self.stt.abrir()
        except Exception:
            log.exception("❌ no pude abrir el STT (%s)", self.stt.nombre)
            return
        terminada = False
        adelantado_de = None
        tiempos: dict[str, float] = {}       # pedido / listo / temprano del adelanto (reloj del servidor)
        try:
            while True:
                item = await cola.get()
                if isinstance(item, bytes):
                    await sesion.enviar(item)
                    continue
                if item[0] == "adelantar":
                    adelantado_de = item[1]
                    sesion.adelantar()
                    self._vigilar_adelanto(sesion, cola, item[1], tiempos)
                    continue
                tipo, t_voz_fin, t_vad_fin = item
                if tipo == "descartada":
                    log.debug("frase descartada")
                    return
                span = Span(turno)
                span.marcar("voz_fin", t_voz_fin)
                span.marcar("vad_fin", t_vad_fin)
                try:
                    resultado = await sesion.finalizar(usar_adelanto=adelantado_de == t_voz_fin)
                except Exception:
                    # Groq caído, sin internet, límite de uso...: avisarle en vez de quedarse callada.
                    log.exception("❌ el STT (%s) falló", self.stt.nombre)
                    await self.orquestador.decir(turno, FRASE_SIN_CEREBRO)
                    return
                terminada = True
                span.marcar("stt_final")
                self._registrar_adelanto(tiempos, t_voz_fin, t_vad_fin, adelantado_de == t_voz_fin)
                if resultado.repetir:
                    log.info("🙉 no se entendió bien (%s): pido que lo repita", resultado.motivo)
                    await self.orquestador.decir(turno, FRASE_NO_ENTENDI)
                    return
                if resultado.texto is None or es_texto_vacio(resultado.texto):
                    log.info("🙉 frase descartada: %s", resultado.motivo or f"texto vacío ({resultado.texto!r})")
                    return
                if (motivo := poco_claro(resultado.texto)) is not None:
                    log.info("🙉 %s: pido que lo repita", motivo)
                    await self.orquestador.decir(turno, FRASE_NO_ENTENDI)
                    return
                if durante_su_voz and not self.barge_in:
                    await self._en_cola(turno, resultado.texto, span)
                    return
                log.info("🗣️ usuario: %s", resultado.texto)
                await self.orquestador.al_texto_usuario(turno, resultado.texto, span)
                return
        except Exception:
            log.exception("❌ error en el STT (%s)", self.stt.nombre)
        finally:
            if not terminada:
                await sesion.cancelar()

    async def cerrar(self) -> None:
        for tarea in list(self.tareas):
            tarea.cancel()
        await asyncio.gather(*self.tareas, return_exceptions=True)
        await self.orquestador.cerrar()
