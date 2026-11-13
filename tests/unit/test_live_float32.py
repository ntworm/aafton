"""Testes do relogio do Live com a posicao da song em float32.

O AbletonOSC manda e recebe a posicao da song em float32: um valor um fio
abaixo do comeco do frame faz o relogio mostrar o frame anterior. O relogio
tem de tolerar esse erro, e o goto tem de mandar um float32 que nunca caia
antes do comeco do frame.
"""

import math
import struct

import pytest

from aafton import live_clock
from aafton.live_clock import beats_to_label, label_to_beats
from aafton.sidecar import Anchor
from aafton.timecode import TimecodeRate, label_to_samples

SR = 48000
TAXAS = [
    TimecodeRate(24000, 1001),
    TimecodeRate(24, 1),
    TimecodeRate(25, 1),
    TimecodeRate(30000, 1001),
    TimecodeRate(30000, 1001, drop_frame=True),
    TimecodeRate(30, 1),
]
BPMS = [60.0, 120.0]
ORIGEM = "00:59:50:00"
ORIGEM_DF = "00:59:50;00"


def _float32(valor: float) -> float:
    """O valor exato que um float32 de 32 bits guarda."""
    (dado,) = struct.unpack(">f", struct.pack(">f", valor))
    return dado


def _float32_anterior(valor: float) -> float:
    """O float32 logo abaixo do valor dado."""
    bits = int.from_bytes(struct.pack(">f", valor), "big")
    (anterior,) = struct.unpack(">f", (bits - 1).to_bytes(4, "big"))
    return anterior


def _rotulos(taxa: TimecodeRate) -> list[str]:
    """Os tres rotulos de teste, com o separador da taxa."""
    sep = ";" if taxa.drop_frame else ":"
    return [f"01:00:00{sep}00", f"01:00:00{sep}01", f"01:10:00{sep}00"]


def _ancora(taxa: TimecodeRate, bpm: float) -> Anchor:
    """Ancora com o Live comecando no rotulo de origem."""
    rotulo = ORIGEM_DF if taxa.drop_frame else ORIGEM
    origem = math.ceil(label_to_samples(rotulo, taxa, SR))
    return Anchor(
        live_origin_samples=origem,
        session_start_samples=0,
        sample_rate=SR,
        timecode_rate=taxa,
        bpm=bpm,
    )


def _casos():
    """Cada taxa, cada bpm e cada rotulo de teste."""
    casos = []
    for taxa in TAXAS:
        for bpm in BPMS:
            for rotulo in _rotulos(taxa):
                marca = "df" if taxa.drop_frame else "nd"
                limpo = rotulo.replace(":", "").replace(";", "-")
                nome = f"{taxa.num}-{taxa.den}-{marca}-{int(bpm)}-{limpo}"
                casos.append(pytest.param(taxa, bpm, rotulo, id=nome))
    return casos


@pytest.mark.parametrize("taxa,bpm,rotulo", _casos())
def test_rotulo_volta_mesmo_com_a_posicao_em_float32(taxa, bpm, rotulo):
    """O relogio mostra o proprio frame com a posicao em float32."""
    ancora = _ancora(taxa, bpm)
    exatas = label_to_beats(rotulo, ancora)
    assert beats_to_label(_float32(exatas), ancora) == rotulo
    assert beats_to_label(live_clock.beats_float32(exatas), ancora) == rotulo


@pytest.mark.parametrize("taxa,bpm,rotulo", _casos())
def test_meio_frame_dentro_do_frame_nao_pula(taxa, bpm, rotulo):
    """Meio frame dentro do frame mostra o mesmo frame."""
    ancora = _ancora(taxa, bpm)
    beats = label_to_beats(rotulo, ancora)
    frames_por_beat = 60 / bpm * (taxa.num / taxa.den)
    assert beats_to_label(beats + 0.5 / frames_por_beat, ancora) == rotulo


@pytest.mark.parametrize(
    "valor",
    [0.0, 0.1, 18.339966666666668, 7200.066666666666, 1234.5678, -3.25, -0.5],
)
def test_float32_vai_para_cima_e_nunca_para_baixo(valor):
    """O float32 do Live nunca e menor que as batidas exatas."""
    ajustado = live_clock.beats_float32(valor)
    assert ajustado >= valor
    assert _float32(ajustado) == ajustado
    if ajustado != valor:
        assert _float32_anterior(ajustado) < valor
