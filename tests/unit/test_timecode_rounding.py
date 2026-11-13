from fractions import Fraction

from aafton.timecode import quantize_samples


def test_fracao_exata_nao_gera_residuo():
    valor, residuo = quantize_samples(Fraction(1920))
    assert valor == 1920
    assert residuo == Fraction(0)


def test_fracao_inexata_reporta_o_residuo():
    valor, residuo = quantize_samples(Fraction(8008, 5))  # 1601.6
    assert valor == 1602
    assert residuo == Fraction(2, 5)


def test_arredondamento_e_metade_para_par():
    assert quantize_samples(Fraction(1, 2))[0] == 0
    assert quantize_samples(Fraction(3, 2))[0] == 2
    assert quantize_samples(Fraction(5, 2))[0] == 2


def test_residuo_e_sempre_valor_menos_original():
    original = Fraction(8008, 5)
    valor, residuo = quantize_samples(original)
    assert Fraction(valor) - residuo == original


def test_aceita_inteiro_direto():
    assert quantize_samples(48000) == (48000, Fraction(0))
