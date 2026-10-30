"""
Cliente HTTP para Groq (STT y LLM comparten conexiones).

Medido el 2026-10-30: una petición con la conexión TLS ya abierta tarda ~130 ms; si hay que abrirla, ~200 ms.
httpx cierra por defecto las conexiones a los 5 s sin uso, y entre turnos de una conversación casi siempre
pasan más, así que aquí duran 5 minutos y `mantener_viva()` hace una petición barata (la lista de modelos)
mientras haya alguien conectado.
"""
from __future__ import annotations

import logging

log = logging.getLogger("nix.groq")

URL_MODELOS = "https://api.groq.com/openai/v1/models"
_cliente = None


def cliente():
    global _cliente
    if _cliente is None:
        import httpx
        _cliente = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0),
                                     limits=httpx.Limits(keepalive_expiry=300.0))
    return _cliente


async def mantener_viva(api_key: str) -> None:
    try:
        await cliente().get(URL_MODELOS, headers={"Authorization": f"Bearer {api_key}"})
    except Exception as e:  # noqa: BLE001 - solo es para tener la conexión abierta
        log.debug("no pude mantener viva la conexión con Groq: %s", e)
