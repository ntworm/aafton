"""Testes do tempo exato do FCPXML."""

from fractions import Fraction

import pytest

from aafton.formats.fcpxml_fix import tempo_fcpxml


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("3600s", Fraction(3600)),
        ("0s", Fraction(0)),
        ("100/2500s", Fraction(1, 25)),
        ("36001/1s", Fraction(36001)),
    ],
)
def test_tempo_fcpxml_vira_segundos_exatos(texto, esperado):
    assert tempo_fcpxml(texto) == esperado


@pytest.mark.parametrize("texto", ["12", "abc", "1/0s"])
def test_tempo_fcpxml_recusa_valor_fora_do_padrao(texto):
    with pytest.raises(ValueError) as erro:
        tempo_fcpxml(texto)
    assert texto in str(erro.value)
