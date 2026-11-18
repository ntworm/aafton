"""Revisao de entrega 13 do reconform: a cauda conta frames inteiros de material.

O detalhe da remocao da cauda mede o material real do set. O material e
contado em frames inteiros e o arredondamento e para cima: um material que
nao completa um frame ainda tira um frame do set, e reportar zero frames
esconderia a remocao.

Cada extremo do material foi arredondado para o sample mais proximo, e a
diferenca entre eles pode errar ate um sample. Esse sample e arredondamento e
nao material: um material de N frames mais um sample ainda conta N frames.
"""

from __future__ import annotations

from typing import cast

from aafton.reconform_apply_locators import _detalhe_da_cauda
from aafton.reconform_diff import ChangeList
from aafton.reconform_samples import SampleMap
from aafton.sidecar import Anchor
from aafton.timecode import TimecodeRate, frames_to_samples_exact

RATE_25 = TimecodeRate(25, 1)
RATE_23_976 = TimecodeRate(24000, 1001)
RATE_29_97 = TimecodeRate(30000, 1001)
SAMPLE_RATE = 48000


class _MapaDaCauda:
    """SampleMap de mentira: so o material da cauda importa aqui."""

    def __init__(self, cauda: tuple[int, int, int]) -> None:
        self._cauda = cauda

    def cauda_material(self) -> tuple[int, int, int]:
        """Trecho de cauda com o material medido: (inicio antigo, emenda, fim)."""
        return self._cauda


class _MudancasDaCauda:
    """ChangeList de mentira: so a taxa de frames importa aqui."""

    def __init__(self, rate: TimecodeRate) -> None:
        self.rate = rate


class _AncoraDaCauda:
    """Anchor de mentira: so a taxa de amostragem importa aqui."""

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate


def _detalhe_de(cauda: tuple[int, int, int], rate: TimecodeRate) -> str | None:
    """Detalhe da remocao da cauda para o trecho de cauda dado."""
    smap = cast(SampleMap, _MapaDaCauda(cauda))
    cl = cast(ChangeList, _MudancasDaCauda(rate))
    anchor = cast(Anchor, _AncoraDaCauda(SAMPLE_RATE))
    return _detalhe_da_cauda(smap, cl, anchor)


def _detalhe(samples: int, rate: TimecodeRate) -> str | None:
    """Detalhe da remocao da cauda para um material do tamanho dado."""
    return _detalhe_de((0, 1000, 1000 + samples), rate)


def _samples_de(frames: int, rate: TimecodeRate) -> int:
    """Samples inteiros de um numero de frames na taxa dada."""
    exatos = frames_to_samples_exact(frames, rate, SAMPLE_RATE)
    assert exatos.denominator == 1
    return int(exatos)


def test_cauda_de_um_sample_conta_um_frame() -> None:
    """Material de menos de um frame ainda remove um frame do set."""
    assert _detalhe(1, RATE_25) == "deleted 1 frames"
    assert _detalhe(1, RATE_23_976) == "deleted 1 frames"


def test_cauda_de_dez_frames_conta_dez_frames() -> None:
    """Material de dez frames inteiros remove dez frames."""
    assert _detalhe(_samples_de(10, RATE_25), RATE_25) == "deleted 10 frames"
    assert _detalhe(_samples_de(10, RATE_23_976), RATE_23_976) == "deleted 10 frames"


def test_cauda_de_dez_frames_e_um_sample_conta_dez_frames() -> None:
    """O sample a mais e arredondamento dos extremos, e nao material."""
    assert _detalhe(_samples_de(10, RATE_25) + 1, RATE_25) == "deleted 10 frames"
    assert _detalhe(_samples_de(10, RATE_23_976) + 1, RATE_23_976) == "deleted 10 frames"


def test_cauda_de_dez_frames_e_dois_samples_conta_onze_frames() -> None:
    """Dois samples a mais ja sao material: contam um frame a mais."""
    assert _detalhe(_samples_de(10, RATE_25) + 2, RATE_25) == "deleted 11 frames"
    assert _detalhe(_samples_de(10, RATE_23_976) + 2, RATE_23_976) == "deleted 11 frames"


def test_cauda_de_29_97_nao_conta_frame_a_mais() -> None:
    """A 29.97 o frame nao fecha em samples: o arredondamento nao vira frame."""
    # A 29.97 e 48 kHz o frame tem 1601.6 samples: o material vai do frame 2
    # (arredondado para 3203) ate o frame 5 (8008), e sao tres frames.
    assert _detalhe_de((3203, 3203, 8008), RATE_29_97) == "deleted 3 frames"


def test_cauda_de_29_97_de_um_sample_conta_um_frame() -> None:
    """A 29.97 um sample de material ainda remove um frame do set."""
    assert _detalhe_de((0, 3203, 3204), RATE_29_97) == "deleted 1 frames"
