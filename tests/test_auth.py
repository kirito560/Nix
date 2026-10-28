import pytest

from shared import auth

TOKEN = "x" * 40


def test_firma_y_verificacion():
    nonce = auth.nuevo_nonce()
    mac = auth.firmar(TOKEN, "cliente", nonce)
    assert auth.verificar(TOKEN, "cliente", nonce, mac)
    assert not auth.verificar("y" * 40, "cliente", nonce, mac)
    assert not auth.verificar(TOKEN, "cliente", auth.nuevo_nonce(), mac)


def test_la_firma_de_un_rol_no_sirve_para_el_otro():
    nonce = auth.nuevo_nonce()
    assert not auth.verificar(TOKEN, "servidor", nonce, auth.firmar(TOKEN, "cliente", nonce))


def test_el_token_no_aparece_en_la_firma():
    assert TOKEN not in auth.firmar(TOKEN, "cliente", "abc")


def test_token_corto_rechazado():
    with pytest.raises(ValueError):
        auth.validar_token("corto")
