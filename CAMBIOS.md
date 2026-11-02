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
