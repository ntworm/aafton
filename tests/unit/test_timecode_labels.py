import pytest
from aafton.timecode import (
    TimecodeRate,
    frames_to_label,
    label_to_frames,
    parse_label,
)

NDF_2997 = TimecodeRate(30000, 1001, drop_frame=False)
DF_2997 = TimecodeRate(30000, 1001, drop_frame=True)
PAL = TimecodeRate(25, 1)


def test_parse_label_aceita_dois_pontos_e_ponto_e_virgula():
    assert parse_label("01:00:12:04") == (1, 0, 12, 4)
    assert parse_label("00:10:00;00") == (0, 10, 0, 0)


def test_parse_label_recusa_formato_errado():
    with pytest.raises(ValueError, match="invalid timecode label"):
        parse_label("01:00:12")


def test_referencia_do_spec_dez_minutos_em_drop_frame():
    assert label_to_frames("00:10:00:00", DF_2997) == 17982


def test_sem_drop_frame_dez_minutos_conta_cheio():
    assert label_to_frames("00:10:00:00", NDF_2997) == 18000


def test_primeiro_frame_apos_o_pulo_do_minuto_um():
    # 00:01:00:00 e 00:01:00:01 nAo existem em drop frame
    assert label_to_frames("00:01:00:02", DF_2997) == 1800


def test_rotulo_invalido_em_drop_frame_recusado():
    with pytest.raises(ValueError, match="does not exist in drop frame"):
        label_to_frames("00:01:00:00", DF_2997)
    with pytest.raises(ValueError, match="does not exist in drop frame"):
        label_to_frames("00:01:00:01", DF_2997)


def test_minuto_multiplo_de_dez_nao_pula():
    assert label_to_frames("00:10:00;00", DF_2997) == 17982
    assert frames_to_label(17982, DF_2997) == "00:10:00;00"


def test_ida_e_volta_drop_frame_em_toda_hora():
    for frame in range(0, 17982 * 6, 997):
        label = frames_to_label(frame, DF_2997)
        assert label_to_frames(label, DF_2997) == frame


def test_ida_e_volta_pal():
    for frame in (0, 1, 24, 25, 1499, 90000, 5400000):
        label = frames_to_label(frame, PAL)
        assert label_to_frames(label, PAL) == frame


def test_formato_do_rotulo_usa_ponto_e_virgula_em_drop_frame():
    assert frames_to_label(1800, DF_2997) == "00:01:00;02"


def test_comparacao_samples_para_rotulo_drop_vs_nondrop():
    from aafton.timecode import samples_to_label

    # Ambos a 29.97 (30000/1001) e sample rate 48000
    NDF = TimecodeRate(30000, 1001, drop_frame=False)
    DF = TimecodeRate(30000, 1001, drop_frame=True)

    # 172972800 samples
    assert samples_to_label(172972800, NDF, 48000) == "01:00:00:00"
    assert samples_to_label(172972800, DF, 48000) == "01:00:03;18"

    # 203214211 samples
    assert samples_to_label(203214211, NDF, 48000) == "01:10:29:12"
    assert samples_to_label(203214211, DF, 48000) == "01:10:33;18"
