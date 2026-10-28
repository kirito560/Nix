"""
Consola para probar el marcador de rivalidad y el reto del día del personaje del .env (PERSONAJE).

    uv run python -m server.juegos reto
    uv run python -m server.juegos responder telegram:123 Kirito "creo que es Ryuk"
    uv run python -m server.juegos registrar twitch:amigo Amigo ppt gana_persona
    uv run python -m server.juegos marcador [persona]
"""
from __future__ import annotations

import argparse

from server.config import ConfigServidor
from server.juegos.reto_del_dia import RetoDelDia
from server.juegos.rivalidad import RESULTADOS, Rivalidad
from server.personaje import cargar_personaje
from shared.config_base import cargar_env


def main() -> None:
    cargar_env()
    cfg = ConfigServidor.desde_entorno()
    p = cargar_personaje(cfg.personaje, cfg.carpeta_personajes, cfg.datos_dir)
    ap = argparse.ArgumentParser(description=f"Juegos de {p.nombre}")
    sub = ap.add_subparsers(dest="orden", required=True)
    sub.add_parser("reto", help="reto de hoy")
    r = sub.add_parser("responder", help="responder el reto de hoy")
    r.add_argument("persona")
    r.add_argument("nombre")
    r.add_argument("texto")
    g = sub.add_parser("registrar", help="anotar una partida")
    g.add_argument("persona")
    g.add_argument("nombre")
    g.add_argument("juego")
    g.add_argument("resultado", choices=RESULTADOS)
    m = sub.add_parser("marcador", help="marcador de una persona o la tabla general")
    m.add_argument("persona", nargs="?")
    args = ap.parse_args()

    rivalidad = Rivalidad(p.crear_carpeta() / "juegos.sqlite")
    reto = RetoDelDia(p.datos / "juegos.sqlite", rivalidad)
    if args.orden == "reto":
        print(reto.texto_para_prompt())
    elif args.orden == "responder":
        print(reto.responder(args.persona, args.nombre, args.texto))
        print(rivalidad.texto_para_prompt(args.persona, p.nombre))
    elif args.orden == "registrar":
        rivalidad.registrar(args.persona, args.nombre, args.juego, args.resultado)
        print(rivalidad.texto_para_prompt(args.persona, p.nombre))
    elif args.persona:
        print(rivalidad.texto_para_prompt(args.persona, p.nombre) or "Todavía no han jugado.")
    else:
        for marcador in rivalidad.tabla():
            print(rivalidad.texto_para_prompt(marcador.persona, p.nombre))


if __name__ == "__main__":
    main()
