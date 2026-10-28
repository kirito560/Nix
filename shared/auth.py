"""
Autenticación mutua por desafío-respuesta con HMAC-SHA256. El token (NIX_TOKEN en el .env) nunca se envía.

  1. servidor -> AuthChallenge(nonce)
  2. cliente  -> AuthResponse(mac = HMAC(token, "cliente|" + nonce), nonce_cliente)
  3. servidor -> AuthOk(mac_servidor = HMAC(token, "servidor|" + nonce_cliente))   (si el mac es correcto)

Así el servidor sabe que el cliente tiene el token y el cliente sabe que habla con el servidor real.
Los prefijos "cliente|"/"servidor|" impiden reenviar una respuesta en sentido contrario.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

LARGO_MINIMO_TOKEN = 32


def nuevo_nonce() -> str:
    return secrets.token_hex(32)


def firmar(token: str, rol: str, nonce: str) -> str:
    return hmac.new(token.encode(), f"{rol}|{nonce}".encode(), hashlib.sha256).hexdigest()


def verificar(token: str, rol: str, nonce: str, mac: str) -> bool:
    return hmac.compare_digest(firmar(token, rol, nonce), mac)


def validar_token(token: str) -> str:
    if len(token or "") < LARGO_MINIMO_TOKEN:
        raise ValueError(f"NIX_TOKEN debe tener al menos {LARGO_MINIMO_TOKEN} caracteres "
                         "(genera uno con: python -c \"import secrets; print(secrets.token_urlsafe(32))\")")
    return token
