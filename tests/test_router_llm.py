"""Router local de LLM (freellmapi): elección por LLM_PROVEEDOR y respaldo con Groq directo."""
import json

import httpx2
import pytest

from server.config import ConfigServidor
from server.pipeline.llm import LLMConRespaldo, LLMGroq, LLMRouter, crear_llm
from tests.test_llm import groq_falso, sse

TOKEN = "x" * 40
URL = "http://127.0.0.1:3001/v1"


def trozo(texto: str, modelo: str = "gemini-2.5-flash") -> dict:
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": modelo,
            "choices": [{"index": 0, "delta": {"content": texto}, "finish_reason": None}]}


def router_falso(estado: int = 200, cuerpo: bytes = b"", pedidos: list | None = None, falla=None):
    def manejar(request: httpx2.Request) -> httpx2.Response:
        if pedidos is not None:
            pedidos.append({"url": str(request.url), "auth": request.headers.get("Authorization"),
                            "json": json.loads(request.content)})
        if falla is not None:
            raise falla(request)
        return httpx2.Response(estado, content=cuerpo, headers={"content-type": "text/event-stream"})
    return LLMRouter(URL, "freellmapi-prueba", max_tokens=90, temperatura=0.8, espera_s=0.5,
                     http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(manejar)))


def groq_que_responde(texto: str = "Hola desde Groq.") -> LLMGroq:
    return LLMGroq("k", "qwen/qwen3.8-27b", cliente=groq_falso(sse(
        {"choices": [{"delta": {"content": texto}}]}, "[DONE]")))


async def _todo(llm) -> str:
    return "".join([t async for t in llm.responder([{"role": "user", "content": "hola"}])])


# ---------------------------------------------------------------- elección
def test_sin_la_variable_se_usa_groq():
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": TOKEN, "GROQ_API_KEY": "k"})
    assert cfg.llm_proveedor == "auto"
    assert isinstance(crear_llm(cfg), LLMGroq)


def test_con_la_variable_se_usa_el_router_con_groq_de_respaldo():
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": TOKEN, "GROQ_API_KEY": "k", "LLM_PROVEEDOR": "freellmapi",
                                        "FREELLMAPI_URL": URL, "FREELLMAPI_CLAVE": "freellmapi-prueba"})
    llm = crear_llm(cfg)
    assert isinstance(llm, LLMConRespaldo) and llm.nombre == "freellmapi"
    assert isinstance(llm.principal, LLMRouter) and isinstance(llm.respaldo, LLMGroq)
    assert str(llm.principal.cliente.base_url).rstrip("/") == URL


def test_router_sin_url_usa_groq(caplog):
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": TOKEN, "GROQ_API_KEY": "k", "LLM_PROVEEDOR": "freellmapi"})
    assert isinstance(crear_llm(cfg), LLMGroq)
    assert "falta FREELLMAPI_URL" in caplog.text


def test_router_sin_clave_de_groq_va_solo():
    cfg = ConfigServidor.desde_entorno({"NIX_TOKEN": TOKEN, "LLM_PROVEEDOR": "freellmapi", "FREELLMAPI_URL": URL})
    assert isinstance(crear_llm(cfg), LLMRouter)


# ---------------------------------------------------------------- router
async def test_el_router_responde_en_streaming_con_model_auto():
    pedidos = []
    llm = router_falso(cuerpo=sse(trozo("¡Hola! "), trozo(""), trozo("¿Qué tal?"), "[DONE]"), pedidos=pedidos)
    assert [t async for t in llm.responder([{"role": "user", "content": "hola"}])] == ["¡Hola! ", "¿Qué tal?"]
    p = pedidos[0]
    assert p["url"] == f"{URL}/chat/completions"
    assert p["auth"] == "Bearer freellmapi-prueba"
    assert p["json"]["model"] == "auto" and p["json"]["stream"] is True and p["json"]["max_tokens"] == 90
    assert llm.ultimo_modelo == "gemini-2.5-flash"


# ---------------------------------------------------------------- respaldo
@pytest.mark.parametrize("estado", [429, 500, 502, 503])
async def test_si_el_router_da_error_responde_groq(estado, caplog):
    pedidos = []
    llm = LLMConRespaldo(router_falso(estado, b'{"error": {"message": "todos ocupados"}}', pedidos),
                         groq_que_responde())
    assert await _todo(llm) == "Hola desde Groq."
    assert len(pedidos) == 1                       # sin reintentos de la librería: el respaldo es Groq
    assert "RESPALDO" in caplog.text and str(estado) in caplog.text


async def test_si_el_router_no_esta_encendido_responde_groq(caplog):
    llm = LLMConRespaldo(router_falso(falla=lambda r: httpx2.ConnectError("conexión rechazada", request=r)),
                         groq_que_responde())
    assert await _todo(llm) == "Hola desde Groq."
    assert "RESPALDO" in caplog.text and "APIConnectionError" in caplog.text


async def test_si_el_router_tarda_demasiado_responde_groq(caplog):
    import asyncio
    from server.pipeline.llm import ProveedorLLM

    class Lento(ProveedorLLM):
        nombre = "freellmapi"

        async def responder(self, mensajes):
            await asyncio.wait_for(asyncio.sleep(10), 0.05)
            yield "nunca"

    llm = LLMConRespaldo(Lento(), groq_que_responde())
    assert await _todo(llm) == "Hola desde Groq."
    assert "RESPALDO" in caplog.text and "tiempo agotado" in caplog.text


async def test_el_router_sin_primer_texto_a_tiempo_es_tiempo_agotado():
    """El router acepta la conexión pero no manda nada: pasado espera_s se rinde (y el respaldo responde)."""
    import asyncio

    class FlujoLento(httpx2.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield b""

    def manejar(request):
        return httpx2.Response(200, stream=FlujoLento(), headers={"content-type": "text/event-stream"})

    router = LLMRouter(URL, "c", espera_s=0.1,
                       http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(manejar)))
    llm = LLMConRespaldo(router, groq_que_responde())
    assert await asyncio.wait_for(_todo(llm), 2) == "Hola desde Groq."


async def test_el_siguiente_turno_vuelve_a_probar_el_router():
    estados = iter([503, 200])
    pedidos = []

    def manejar(request):
        pedidos.append(1)
        estado = next(estados)
        cuerpo = sse(trozo("Desde el router."), "[DONE]") if estado == 200 else b"{}"
        return httpx2.Response(estado, content=cuerpo, headers={"content-type": "text/event-stream"})

    router = LLMRouter(URL, "c", espera_s=0.5, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(manejar)))
    llm = LLMConRespaldo(router, groq_que_responde())
    assert await _todo(llm) == "Hola desde Groq."
    assert await _todo(llm) == "Desde el router."
    assert len(pedidos) == 2


async def test_si_tambien_falla_groq_sube_el_error():
    from server.pipeline.llm import ErrorLLM
    groq = LLMGroq("k", "qwen/qwen3.8-27b", cliente=groq_falso(b"{}", 500))
    with pytest.raises(ErrorLLM):
        await _todo(LLMConRespaldo(router_falso(503, b"{}"), groq))


async def test_con_el_orquestador_responde_groq_y_registra_respaldo(caplog):
    from shared.protocol import LlmText
    from tests.test_orquestador import orquestador, span

    o, enviados, _ = orquestador(LLMConRespaldo(router_falso(429, b"{}"), groq_que_responde("Va, aquí estoy.")))
    await o.al_texto_usuario("00000000-0000-0000-0000-000000000001", "hola", span())
    await o.turno.tarea
    assert " ".join(m.texto for m in enviados if isinstance(m, LlmText) and m.texto) == "Va, aquí estoy."
    assert "RESPALDO" in caplog.text
