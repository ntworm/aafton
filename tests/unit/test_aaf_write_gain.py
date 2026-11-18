"""Testes do envelope de ganho gravado no AAF.

O keyframe de fim do clip vale o ganho da ultima amostra do clip: o fim do
clip e a amostra seguinte, e um degrau da curva nesse ponto ja e o ganho do
clip que vem depois.
"""

from __future__ import annotations

import pytest

from aafton.aaf_write_gain import _ganho_em, _keyframes_automacao


def test_keyframe_de_fim_usa_o_ganho_dentro_do_clip() -> None:
    """O ultimo keyframe do clip nao pega o ganho do clip seguinte.

    A curva da track da um degrau na amostra seguinte ao fim do clip: o
    keyframe de fim do clip vale o ganho do proprio clip.
    """
    curva = [(0, 1.0), (999, 1.0), (1000, 0.1), (2000, 0.1)]
    keyframes = _keyframes_automacao(curva, 1.0, 0, 1000, 1000)
    assert keyframes[-1] == (1.0, 1.0)


def test_clip_de_uma_amostra_mantem_o_valor_da_amostra_seguinte() -> None:
    """O clip de uma amostra mantem o valor antigo: o da amostra seguinte."""
    curva = [(0, 1.0), (999, 0.0), (1000, 0.1)]
    valor_seguinte = _ganho_em(curva, 1)
    valor_do_clip = _ganho_em(curva, 0)
    assert valor_seguinte != valor_do_clip
    keyframes = _keyframes_automacao(curva, 1.0, 0, 1, 1)
    assert keyframes[-1][1] == pytest.approx(valor_seguinte, abs=1e-12)
