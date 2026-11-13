from fractions import Fraction

import pytest
from aafton.timecode import (
    TimecodeRate,
    frames_to_samples_exact,
    label_to_samples,
    samples_to_frames_exact,
    samples_per_frame,
)

SR = 48000
FILM = TimecodeRate(24, 1)
PAL = TimecodeRate(25, 1)
NTSC_INT = TimecodeRate(30, 1)
FILM_PULLDOWN = TimecodeRate(24000, 1001)
NTSC = TimecodeRate(30000, 1001)
NTSC_DF = TimecodeRate(30000, 1001, drop_frame=True)


@pytest.mark.parametrize(
    "rate,expected",
    [
        (FILM, Fraction(2000)),
        (PAL, Fraction(1920)),
        (NTSC_INT, Fraction(1600)),
        (FILM_PULLDOWN, Fraction(2002)),
        (NTSC, Fraction(8008, 5)),
    ],
)
def test_samples_por_frame_conforme_tabela_do_spec(rate, expected):
    assert samples_per_frame(rate, SR) == expected


def test_taxas_que_fecham_em_sample_inteiro():
    assert samples_per_frame(PAL, SR).denominator == 1
    assert samples_per_frame(FILM_PULLDOWN, SR).denominator == 1


def test_29_97_nao_fecha_em_sample_inteiro():
    assert samples_per_frame(NTSC, SR).denominator != 1


def test_frames_para_samples_e_exato_e_nao_acumula_erro():
    # uma hora de PAL: 90000 frames
    assert frames_to_samples_exact(90000, PAL, SR) == Fraction(172800000)


def test_frames_para_samples_em_29_97_mantem_a_fracao():
    assert frames_to_samples_exact(5, NTSC, SR) == Fraction(8008)


def test_label_para_samples_usa_a_contagem_de_drop_frame():
    # 00:10:00:00 em drop frame A o frame 17982
    esperado = frames_to_samples_exact(17982, NTSC_DF, SR)
    assert label_to_samples("00:10:00:00", NTSC_DF, SR) == esperado


def test_samples_para_frames_e_o_inverso():
    for frame in (0, 1, 17982, 90000):
        samples = frames_to_samples_exact(frame, NTSC_DF, SR)
        assert samples_to_frames_exact(samples, NTSC_DF, SR) == frame


def test_uma_hora_em_pal_da_sample_inteiro_exato():
    samples = label_to_samples("01:00:00:00", PAL, SR)
    assert samples == Fraction(172800000)
    assert samples.denominator == 1
