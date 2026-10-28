from server.pipeline.frases import Segmentador, limpiar_para_voz


def trocear(texto: str, n: int = 3) -> list[str]:
    return [texto[i:i + n] for i in range(0, len(texto), n)]


def segmentar(texto: str, **kw) -> list[str]:
    s, frases = Segmentador(**kw), []
    for trozo in trocear(texto):
        frases += s.agregar(trozo)
    return frases + s.terminar()


def test_corta_en_fin_de_oracion():
    assert segmentar("¡GG! Eso estuvo roto. ¿Otra?") == ["¡GG!", "Eso estuvo roto.", "¿Otra?"]


def test_la_primera_frase_sale_en_la_primera_coma_larga():
    frases = segmentar("Oye, la neta eso fue un clutch tremendo, pero la próxima te gano. Va.")
    assert frases[0] == "Oye, la neta eso fue un clutch tremendo,"
    assert frases[1:] == ["pero la próxima te gano.", "Va."]


def test_solo_la_primera_frase_corta_en_comas():
    frases = segmentar("Bueno, mira. Uno, dos, tres, cuatro, cinco, seis y siete.")
    assert frases == ["Bueno, mira.", "Uno, dos, tres, cuatro, cinco, seis y siete."]


def test_frases_muy_largas_se_cortan_en_un_espacio():
    frases = segmentar("Bueno. " + "palabra " * 40, max_largo=60)
    assert all(len(f) <= 60 for f in frases) and len(frases) > 2


def test_limpieza_para_voz():
    assert limpiar_para_voz("*se ríe* ¡**GG**! 🎮🔥") == "¡GG!"
    assert segmentar("😂😂. ... ¡Ya!") == ["¡Ya!"]
