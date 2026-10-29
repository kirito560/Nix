import json

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
