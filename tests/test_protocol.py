import uuid

import pytest

from shared import protocol as p


def test_audio_in_ida_y_vuelta():
    pcm = bytes(range(256)) * 2 + bytes(128)          # 640 bytes = 20 ms
    trama = p.codificar_audio_in(1_700_000_000_123, pcm)
    assert len(trama) == 8 + p.BYTES_TRAMA
    assert p.decodificar_audio_in(trama) == (1_700_000_000_123, pcm)


@pytest.mark.parametrize("trama", [b"", b"1234567", b"12345678" + b"\x01"])
def test_audio_in_mal_formado(trama):
    with pytest.raises(p.ErrorProtocolo):
        p.decodificar_audio_in(trama)


def test_audio_in_rechaza_pcm_impar():
    with pytest.raises(p.ErrorProtocolo):
        p.codificar_audio_in(0, b"\x00\x01\x02")


def test_audio_out_ida_y_vuelta():
    turno = str(uuid.uuid4())
    trama = p.codificar_audio_out(turno, 1234, 24000, b"\x01\x00" * 10)
    assert p.decodificar_audio_out(trama) == (turno, 1234, 24000, b"\x01\x00" * 10)


MENSAJES = [
    p.AuthChallenge(nonce="ab"), p.AuthResponse(mac="cd", nonce_cliente="ef"),
    p.AuthOk(mac_servidor="12", sesion="s", personaje="Nix"),
    p.VisionDelta(ts=1, phash_dist=12), p.VisionDelta(ts=1, phash_dist=12, caption="un menú"),
    p.VisionFrame(ts=2, jpeg_b64="AAAA"), p.UserState(ts=3, valence=-0.5, attention=0.9),
    p.PlaybackAck(turn_id="t", pts_played=100), p.SttText(turn_id="t", texto="hola"),
    p.AvatarCmd(turn_id="t", pts=0, expression="feliz", gaze=(0.1, -0.2), mouth_form=0.5),
    p.PlaybackFlush(turn_id="t"), p.StateSync(emotion={"valence": 0.3}, mood_label="contenta"),
    p.LlmText(turn_id="t", texto="¡GG!"),
    p.Error(codigo="x", detalle="y"),
]


@pytest.mark.parametrize("msg", MENSAJES, ids=lambda m: m.type)
def test_mensajes_ida_y_vuelta(msg):
    assert p.parsear_mensaje(msg.a_json()) == msg


def test_todos_los_tipos_estan_cubiertos():
    assert {m.type for m in MENSAJES} == {c.model_fields["type"].default
                                         for c in p.MENSAJES_DEL_CLIENTE + p.MENSAJES_DEL_SERVIDOR}


@pytest.mark.parametrize("texto", [
    '{"type": "desconocido"}', '{"nonce": "sin tipo"}', "no es json",
    '{"type": "stt.text", "turn_id": "t", "texto": "x", "campo_extra": 1}',
    '{"type": "user.state", "ts": 1, "valence": 2.0, "attention": 0.5}',
])
def test_mensajes_invalidos(texto):
    with pytest.raises(p.ErrorProtocolo):
        p.parsear_mensaje(texto)
