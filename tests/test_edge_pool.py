import asyncio
from contextlib import aclosing

import pytest

from server.pipeline.edge_pool import PoolEdge


class ConexionFalsa:
    creadas = 0

    def __init__(self, falla_antes=False, falla_despues=False):
        ConexionFalsa.creadas += 1
        self.id = ConexionFalsa.creadas
        self.falla_antes, self.falla_despues = falla_antes, falla_despues
        self.cerrada = False
        self.ultimo_uso = asyncio.get_event_loop().time()
        self.frases = []

    async def pedir(self, texto):
        if self.falla_antes:
            raise ConnectionError("cerrada por Microsoft")
        self.frases.append(texto)
        for i in range(3):
            await asyncio.sleep(0)
            yield f"{texto}:{i}".encode()
            if self.falla_despues and i == 0:
                raise ConnectionError("cortada")

    async def cerrar(self):
        self.cerrada = True


def pool(fabrica, **kw):
    async def conectar():
        await asyncio.sleep(0.01)              # abrir una conexión tarda (aquí 10 ms)
        return fabrica()
    return PoolEdge("v", conectar=conectar, **kw)


async def frase(p, texto):
    return [d async for d in p.mp3(texto)]


async def test_reusa_conexiones_y_repone_las_reservas():
    creadas = []
    p = pool(lambda: creadas.append(ConexionFalsa()) or creadas[-1], tamano=2)
    await p.calentar()
    await asyncio.sleep(0.05)
    assert len(p.libres) == 2
    assert await frase(p, "hola") == [b"hola:0", b"hola:1", b"hola:2"]
    await frase(p, "otra")
    await asyncio.sleep(0.05)
    usadas = [c for c in creadas if c.frases]
    assert len(p.libres) == 2 and len(creadas) <= 4 and all(not c.cerrada for c in usadas)
    await p.cerrar()


async def test_si_la_conexion_del_pool_esta_muerta_abre_otra():
    fabricas = iter([ConexionFalsa(falla_antes=True), ConexionFalsa(), ConexionFalsa(), ConexionFalsa()])
    p = pool(lambda: next(fabricas), tamano=1)
    await p.calentar()
    await asyncio.sleep(0.05)
    muerta = p.libres[0]
    assert await frase(p, "hola") == [b"hola:0", b"hola:1", b"hola:2"]
    await asyncio.sleep(0.01)
    assert muerta.cerrada
    await p.cerrar()


async def test_sin_conexion_usa_el_respaldo():
    async def no_conecta():
        raise OSError("sin red")

    async def respaldo(texto):
        yield b"respaldo"

    p = PoolEdge("v", conectar=no_conecta, respaldo=respaldo)
    assert await frase(p, "hola") == [b"respaldo"]
    await p.cerrar()


async def test_una_frase_cortada_cierra_su_conexion():
    p = pool(ConexionFalsa, tamano=1)
    await p.calentar()
    await asyncio.sleep(0.05)
    conexion = p.libres[0]
    async with aclosing(p.mp3("larga")) as audio:
        async for _ in audio:
            break                                # barge-in: se deja de leer a mitad
    await asyncio.sleep(0.01)
    assert conexion.cerrada and conexion not in p.libres
    await p.cerrar()


async def test_si_falla_a_mitad_no_repite_la_frase():
    p = pool(lambda: ConexionFalsa(falla_despues=True), tamano=1)
    with pytest.raises(ConnectionError):
        await frase(p, "hola")
    await p.cerrar()


async def test_sin_actividad_cierra_las_reservas():
    p = pool(ConexionFalsa, tamano=2, ventana_s=0.1, max_ocio_s=5)
    await p.calentar()
    await asyncio.sleep(0.05)
    reservas = list(p.libres)
    assert len(reservas) == 2
    await asyncio.sleep(2.2)                     # el vigilante revisa cada 0,5 s (max_ocio_s / 10)
    assert p.libres == [] and all(c.cerrada for c in reservas) and p.vigilante.done()
    await p.cerrar()
