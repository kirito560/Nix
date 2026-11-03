import logging
import json

import logging
import httpx
import pytest

from server.config import ConfigServidor
from server.pipeline.llm import ErrorLLM, LLMGroq, LLMStub, crear_llm


def sse(*eventos) -> bytes:
    return "".join(f"data: {json.dumps(e) if isinstance(e, dict) else e}\n\n" for e in eventos).encode()


def groq_falso(cuerpo: bytes, estado: int = 200, capturado: dict | None = None):
    def manejar(request: httpx.Request) -> httpx.Response:
        if capturado is not None:
            capturado["auth"] = request.headers["Authorization"]
            capturado["json"] = json.loads(request.content)
        return httpx.Response(estado, content=cuerpo)
    return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


async def test_groq_en_streaming_solo_lee_el_contenido():
    capturado = {}
    cuerpo = sse({"choices": [{"delta": {"reasoning": "pienso..."}}]},
                 {"choices": [{"delta": {"content": "¡GG"}}]},
                 {"choices": [{"delta": {"content": "!"}}]},
                 {"choices": [{"delta": {}}], "x_groq": {"usage": {}}},
                 "[DONE]")
    llm = LLMGroq("CLAVE", "openai/gpt-oss-20b", cliente=groq_falso(cuerpo, capturado=capturado))
    trozos = [t async for t in llm.responder([{"role": "user", "content": "hola"}])]
    assert "".join(trozos) == "¡GG!"
    assert capturado["auth"] == "Bearer CLAVE"
    assert capturado["json"]["stream"] is True and capturado["json"]["reasoning_effort"] == "low"


def test_reasoning_effort_solo_en_gpt_oss():
    assert "reasoning_effort" not in LLMGroq("k", "llama-3.1-8b-instant", cliente=object()).cuerpo([])


async def test_error_de_groq():
    llm = LLMGroq("CLAVE", "m", cliente=groq_falso(b'{"error": "limite"}', estado=429))
    with pytest.raises(ErrorLLM, match="429"):
        [t async for t in llm.responder([])]


async def test_stub_y_eleccion():
    trozos = [t async for t in LLMStub().responder([{"role": "user", "content": "hola"}])]
    assert "hola" in "".join(trozos)
    assert crear_llm(ConfigServidor(token="x" * 40)).nombre == "stub"
    assert crear_llm(ConfigServidor(token="x" * 40, groq_api_key="k")).nombre == "groq"
    assert crear_llm(ConfigServidor(token="x" * 40, groq_api_key="k", llm_proveedor="stub")).nombre == "stub"


async def test_si_groq_retira_el_modelo_pasa_al_respaldo():
    modelos = []

    def manejar(request: httpx.Request) -> httpx.Response:
        modelo = json.loads(request.content)["model"]
        modelos.append(modelo)
        if modelo == "qwen/viejo":
            return httpx.Response(404, json={"error": {"code": "model_not_found"}})
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "hola"}}]}, "[DONE]"))

    llm = LLMGroq("CLAVE", "qwen/viejo", respaldo="openai/gpt-oss-120b",
                  cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    assert [t async for t in llm.responder([])] == ["hola"]
    assert [t async for t in llm.responder([])] == ["hola"]
    assert modelos == ["qwen/viejo", "openai/gpt-oss-120b", "openai/gpt-oss-120b"]


# --------------------------------------------------------------------------- respaldo por límite de uso
LIMITE_429 = {"error": {"message": "Rate limit reached for model `qwen/qwen3.8-27b` ... on input tokens per "
                                   "minute (ITPM): Limit 7000, Used 6374, Requested 1323.",
                        "type": "tokens", "code": "rate_limit_exceeded"}}


def groq_con_estados(estados: dict[str, int], pedidos: list):
    """Groq falso: responde con el estado HTTP que diga `estados` para cada modelo (200 = una respuesta normal)."""
    def manejar(request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        pedidos.append(cuerpo)
        estado = estados.get(cuerpo["model"], 200)
        if estado != 200:
            return httpx.Response(estado, json=LIMITE_429)
        texto = "Va, yo te ayudo." if cuerpo["model"].startswith("openai/") else "Hola desde Qwen."
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": texto}}]}),
                              headers={"content-type": "text/event-stream"})
    return httpx.AsyncClient(transport=httpx.MockTransport(manejar))


def llm_con_respaldo(estados, pedidos):
    return LLMGroq("CLAVE", "qwen/qwen3.8-27b", max_tokens=90, respaldo="openai/gpt-oss-120b",
                   cliente=groq_con_estados(estados, pedidos))


@pytest.mark.parametrize("estado", [429, 503, 498])
async def test_limite_de_uso_responde_el_respaldo(estado, caplog):
    pedidos = []
    llm = llm_con_respaldo({"qwen/qwen3.8-27b": estado}, pedidos)
    assert "".join([t async for t in llm.responder([{"role": "user", "content": "hola"}])]) == "Va, yo te ayudo."
    assert [p["model"] for p in pedidos] == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    assert pedidos[1]["messages"] == pedidos[0]["messages"]               # la misma conversación
    assert pedidos[1]["reasoning_effort"] == "low" and pedidos[1]["max_completion_tokens"] == 90 + 100
    assert "RESPALDO" in caplog.text and str(estado) in caplog.text


async def test_el_respaldo_es_solo_para_ese_turno():
    pedidos = []
    estados = {"qwen/qwen3.8-27b": 429}
    llm = llm_con_respaldo(estados, pedidos)
    await _todo(llm)
    del estados["qwen/qwen3.8-27b"]                                       # pasó el minuto: Qwen ya responde
    assert await _todo(llm) == "Hola desde Qwen."
    assert llm.modelo == "qwen/qwen3.8-27b"
    assert [p["model"] for p in pedidos] == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]


async def test_si_el_respaldo_tambien_falla_sube_el_error(caplog):
    pedidos = []
    llm = llm_con_respaldo({"qwen/qwen3.8-27b": 429, "openai/gpt-oss-120b": 429}, pedidos)
    with pytest.raises(ErrorLLM, match="429"):
        await _todo(llm)
    assert len(pedidos) == 2
    assert "RESPALDO: openai/gpt-oss-120b también falló" in caplog.text


async def test_sin_respaldo_el_429_sube_igual():
    pedidos = []
    llm = LLMGroq("CLAVE", "qwen/qwen3.8-27b", cliente=groq_con_estados({"qwen/qwen3.8-27b": 429}, pedidos))
    with pytest.raises(ErrorLLM):
        await _todo(llm)
    assert len(pedidos) == 1


async def test_otros_errores_no_usan_el_respaldo():
    pedidos = []
    llm = llm_con_respaldo({"qwen/qwen3.8-27b": 401}, pedidos)            # clave mala: el respaldo no ayudaría
    with pytest.raises(ErrorLLM, match="401"):
        await _todo(llm)
    assert len(pedidos) == 1


async def test_con_el_orquestador_el_usuario_no_nota_el_cambio():
    """429 en Qwen: Nix responde con el respaldo. Luego los dos en 429: dice la frase de error. Y luego, con
    Qwen de vuelta, sigue conversando normal."""
    from server.orquestador import FRASE_SIN_CEREBRO
    from shared.protocol import LlmText
    from tests.test_orquestador import orquestador, span

    pedidos = []
    estados = {"qwen/qwen3.8-27b": 429}
    o, enviados, _ = orquestador(llm_con_respaldo(estados, pedidos))

    async def turno(i, texto):
        enviados.clear()
        await o.al_texto_usuario(f"00000000-0000-0000-0000-00000000000{i}", texto, span())
        await o.turno.tarea
        return " ".join(m.texto for m in enviados if isinstance(m, LlmText) and m.texto)

    assert await turno(1, "hola") == "Va, yo te ayudo."
    estados["openai/gpt-oss-120b"] = 429
    assert await turno(2, "¿sigues?") == FRASE_SIN_CEREBRO
    estados.clear()
    assert await turno(3, "¿y ahora?") == "Hola desde Qwen."
    # El historial tiene lo que dijo el respaldo, como si lo hubiera dicho ella (el usuario no nota el cambio).
    assert {"role": "assistant", "content": "Va, yo te ayudo."} in o.historial.mensajes


async def _todo(llm) -> str:
    return "".join([t async for t in llm.responder([{"role": "user", "content": "hola"}])])


async def test_si_groq_se_cuelga_responde_el_respaldo(caplog):
    """Groq a veces se queda colgado (medido: 20 s sin respuesta). Pasado LLM_ESPERA_S, responde el respaldo."""
    def manejar(request):
        modelo = json.loads(request.content)["model"]
        if modelo == "qwen/qwen3.8-27b":
            raise httpx.ReadTimeout("sin respuesta", request=request)
        return httpx.Response(200, content=sse({"choices": [{"delta": {"content": "Aquí estoy."}}]}, "[DONE]"))

    llm = LLMGroq("k", "qwen/qwen3.8-27b", respaldo="openai/gpt-oss-120b", espera_s=0.1,
                  cliente=httpx.AsyncClient(transport=httpx.MockTransport(manejar)))
    assert await _todo(llm) == "Aquí estoy."
    assert "RESPALDO" in caplog.text and "no respondió" in caplog.text


async def test_si_groq_se_cuelga_a_media_respuesta_sube_el_error():
    class Corte(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse({"choices": [{"delta": {"content": "Hola"}}]})
            raise httpx.ReadTimeout("se colgó")

    llm = LLMGroq("k", "qwen/qwen3.8-27b", respaldo="openai/gpt-oss-120b", espera_s=0.1,
                  cliente=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Corte()))))
    trozos = []
    with pytest.raises(ErrorLLM, match="media frase"):
        async for t in llm.responder([{"role": "user", "content": "hola"}]):
            trozos.append(t)
    assert trozos == ["Hola"]


async def test_anota_los_tokens_de_cada_turno(caplog):
    caplog.set_level(logging.INFO, "nix.llm")
    capturado = {}
    cuerpo = sse({"choices": [{"delta": {"content": "Va."}}]},
                 {"choices": [], "usage": {"prompt_tokens": 812, "completion_tokens": 9,
                                           "prompt_tokens_details": {"cached_tokens": 512}}},
                 "[DONE]")
    llm = LLMGroq("CLAVE", "qwen/qwen3.8-27b", cliente=groq_falso(cuerpo, capturado=capturado))
    assert "".join([t async for t in llm.responder([{"role": "user", "content": "hola"}])]) == "Va."
    assert capturado["json"]["stream_options"] == {"include_usage": True}
    assert llm.ultimo_uso["prompt_tokens"] == 812
    assert "📏 tokens qwen3.8-27b: entrada 812 (en caché 512), salida 9" in caplog.text
