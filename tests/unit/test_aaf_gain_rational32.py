"""D1 do R07.6: o keyframe de ganho tem de caber no Rational32 do AAF.

O writer limitava so o denominador, entao ganho de automacao acima de 1.0 saia
com numerador acima de 2**31-1 e o arquivo fechava com struct.error. Aqui o
ganho passa pela conversao limitada que ja existia e o limite e conferido de
forma independente (struct.pack).
"""

from __future__ import annotations

import struct
from fractions import Fraction

import pytest

from aafton.aaf_write_defs import AafBuilder
from aafton.report import Report

LIMITE_RATIONAL32 = 2**31 - 1

# Ganhos representaveis: zero, fracao exata, acima de 1.0 e inteiro grande.
GANHOS = [0.0, 0.25, 1.0, 1.244514610618353, 2.125, 10.123456789, 2_000_000_000.0]

# (ganho, fracao exata que o Rational32 deve receber)
GANHOS_EXATOS = [(0.0, (0, 1)), (0.25, (1, 4)), (1.0, (1, 1)), (2.125, (17, 8))]


class _VaryingCaptura:
    """VaryingValue de mentira: guarda o keyframe ja serializado."""

    def __init__(self) -> None:
        self.keyframes: list[tuple[object, object]] = []

    def add_keyframe(self, tempo, valor) -> None:
        self.keyframes.append((tempo, valor))


def _fracao(valor) -> Fraction:
    """Le a fracao do AAFRational serializado, sem depender do tipo."""
    return Fraction(int(valor.numerator), int(valor.denominator))


def _keyframe(gain: float) -> tuple[Fraction, Fraction]:
    """Devolve (fracao do tempo, fracao do ganho) do keyframe gravado."""
    builder = object.__new__(AafBuilder)
    builder.report = Report(strict=False)
    varying = _VaryingCaptura()
    builder._add_gain_keyframe(varying, 0.5, gain)
    assert len(varying.keyframes) == 1, "o keyframe nao foi gravado"
    tempo, valor = varying.keyframes[0]
    return _fracao(tempo), _fracao(valor)


@pytest.mark.parametrize("gain", GANHOS)
def test_ganho_de_automacao_cabe_no_rational32(gain):
    numerador, denominador = _keyframe(gain)[1].as_integer_ratio()
    struct.pack(">i", numerador)
    struct.pack(">i", denominador)
    assert abs(numerador) <= LIMITE_RATIONAL32
    assert 0 < denominador <= LIMITE_RATIONAL32
    escrito = Fraction(numerador, denominador)
    assert abs(float(escrito) - gain) <= max(1e-9, abs(gain) * 1e-6), (
        f"o ganho gravado {float(escrito)} nao aproxima o pedido {gain}"
    )


@pytest.mark.parametrize("gain,esperado", GANHOS_EXATOS)
def test_ganho_exato_sai_exato(gain, esperado):
    assert _keyframe(gain)[1].as_integer_ratio() == esperado


@pytest.mark.parametrize("gain", [g for g in GANHOS if g > 1.0])
def test_ganho_acima_de_um_nao_e_recortado(gain):
    escrito = _keyframe(gain)[1]
    assert escrito > 1, f"o ganho {gain} foi recortado para {float(escrito)}"


@pytest.mark.parametrize("gain", [1e12, 5_000_000_000.0])
def test_ganho_fora_do_rational32_recusa_com_erro_claro(gain):
    builder = object.__new__(AafBuilder)
    builder.report = Report(strict=False)
    varying = _VaryingCaptura()
    with pytest.raises(ValueError, match="Rational32"):
        builder._add_gain_keyframe(varying, 0.5, gain)
    assert not varying.keyframes, "um keyframe invalido foi gravado"


def test_clamp_negativo_e_tempo_normalizado():
    """O ganho negativo vira zero e o tempo do keyframe continua normalizado."""
    assert _keyframe(-3.0)[1] == Fraction(0, 1)
    assert _keyframe(1.5)[0] == Fraction(1, 2)
