"""
Banco de retos del día. Para agregar uno basta con añadirlo a BANCO con un id único.
`respuestas`: formas aceptadas (sin importar mayúsculas ni acentos). Vacío = no se califica solo.
`opinion`: el "take" del personaje en las preguntas polémicas.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reto:
    id: str
    tipo: str
    enunciado: str
    respuestas: tuple[str, ...] = ()
    opinion: str = ""


BANCO: tuple[Reto, ...] = (
    # Trivia de anime
    Reto("anime-ryuk", "trivia_anime", "¿Cómo se llama el shinigami que deja caer la Death Note en el mundo humano?",
         ("Ryuk",)),
    Reto("anime-kamehameha", "trivia_anime", "¿Cómo se llama la técnica de energía más famosa de Goku?",
         ("kamehameha",)),
    Reto("anime-pikachu", "trivia_anime", "¿Qué número tiene Pikachu en la Pokédex nacional?", ("25", "veinticinco")),
    Reto("anime-kurama", "trivia_anime", "En Naruto, ¿cómo se llama el zorro de nueve colas sellado dentro de Naruto?",
         ("Kurama", "Kyubi", "Kyuubi")),
    Reto("anime-sunny", "trivia_anime",
         "En One Piece, ¿cómo se llama el barco de los Sombrero de Paja que reemplazó al Going Merry?",
         ("Thousand Sunny", "Sunny")),
    Reto("anime-maria", "trivia_anime", "En Attack on Titan, ¿cómo se llama la muralla más externa de las tres?",
         ("Maria", "María")),
    Reto("anime-ghibli", "trivia_anime", "¿Qué estudio de animación hizo El viaje de Chihiro?", ("Ghibli",)),
    # Trivia de juegos
    Reto("juego-obsidiana", "trivia_juegos", "¿Con qué bloque se construye el marco de un portal al Nether en Minecraft?",
         ("obsidiana", "obsidian")),
    Reto("juego-luigi", "trivia_juegos", "¿Cómo se llama el hermano de Mario?", ("Luigi",)),
    Reto("juego-link", "trivia_juegos", "¿Cómo se llama el protagonista de la saga The Legend of Zelda?", ("Link",)),
    Reto("juego-golem", "trivia_juegos", "En Minecraft, ¿cuántos bloques de hierro necesitas para un golem de hierro?",
         ("4", "cuatro")),
    Reto("juego-sega", "trivia_juegos", "¿Qué compañía creó a Sonic?", ("Sega",)),
    Reto("juego-chief", "trivia_juegos", "¿Cómo le dicen al protagonista de Halo, el de la armadura verde?",
         ("Jefe Maestro", "Master Chief")),
    Reto("juego-impostor", "trivia_juegos",
         "En Among Us, ¿cómo se le llama al jugador que sabotea y elimina a la tripulación?", ("impostor",)),
    # Adivinanzas
    Reto("adiv-reloj", "adivinanza", "Tengo agujas y no sé coser, tengo números y no sé leer. ¿Qué soy?", ("reloj",)),
    Reto("adiv-platano", "adivinanza", "Oro parece, plata no es. ¿Qué es?", ("platano", "plátano")),
    Reto("adiv-pera", "adivinanza", "Blanca por dentro, verde por fuera; si quieres que te lo diga, espera.",
         ("pera",)),
    Reto("adiv-creeper", "adivinanza",
         "Soy verde, no tengo brazos, me acerco en silencio y hago ¡bum! en tu casa. ¿Quién soy?", ("creeper",)),
    Reto("adiv-hoyo", "adivinanza", "Cuanto más le quitas, más grande es. ¿Qué es?", ("hoyo", "agujero", "pozo")),
    Reto("adiv-ajo", "adivinanza", "Tiene dientes y no come, tiene cabeza y no es hombre. ¿Qué es?", ("ajo",)),
    # Récords
    Reto("record-pokemon", "record", "Nombra 15 Pokémon distintos en 30 segundos sin repetir."),
    Reto("record-ppt", "record", "Gana 3 partidas seguidas de !ppt contra mí."),
    Reto("record-tigres", "record", "Di 'tres tristes tigres tragaban trigo en un trigal' tres veces seguidas sin trabarte."),
    Reto("record-dado", "record", "Saca la suma más alta posible en tres tiradas de !dado."),
    Reto("record-ingles", "record", "Cuenta del 1 al 20 en inglés en menos de 10 segundos."),
    # Polémicas
    Reto("pol-pina", "polemica", "¿Pizza con piña sí o no?", (),
         "Sí, y quien diga que no tiene skill issue de sabor."),
    Reto("pol-doblaje", "polemica", "¿Anime con doblaje latino o subtitulado?", (),
         "Doblaje latino para comedia, subtítulos para shonen serio."),
    Reto("pol-survival", "polemica", "Minecraft: ¿survival o creativo?", (),
         "Survival. Creativo es para los que le tienen miedo a los creepers."),
    Reto("pol-saitama", "polemica", "¿Goku le gana a Saitama?", (), "Saitama, de un golpe. Ni modo."),
    Reto("pol-tortillas", "polemica", "¿Tortillas de maíz o de harina?", (),
         "Maíz para tacos, harina para burritos; pero si hay que elegir, maíz."),
    Reto("pol-terror", "polemica", "¿Los juegos de terror se juegan solo o con amigos?", (),
         "Con amigos, para que alguien grite más que yo (yo no grito, eh)."),
)
