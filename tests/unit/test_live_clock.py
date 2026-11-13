"""Testes do relogio do Live: batidas do Live viram samples e rotulos SMPTE.

O Live manda a posicao da song em batidas. O relogio traduz essa posicao
para samples, a partir da ancora do sidecar, e de volta para rotulos.
"""

import math
from fractions import Fraction

import pytest

from aafton.live_clock import (
    beats_to_label,
    beats_to_samples,
    label_to_beats,
    tempo_warning,
)
from aafton.sidecar import Anchor
from aafton.timecode import TimecodeRate, label_to_samples

SR = 48000
TAXA_23976 = TimecodeRate(24000, 1001)
TAXA_24 = TimecodeRate(24, 1)
TAXA_25 = TimecodeRate(25, 1)
TAXA_2997 = TimecodeRate(30000, 1001)
TAXA_2997DF = TimecodeRate(30000, 1001, drop_frame=True)
TAXA_30 = TimecodeRate(30, 1)
TAXAS = [TAXA_23976, TAXA_24, TAXA_25, TAXA_2997, TAXA_2997DF, TAXA_30]
BPMS = [60.0, 120.0]
ORIGEM = "00:59:50:00"
ORIGEM_DF = "00:59:50;00"


def _rotulos(taxa):
    """Quatro rotulos de teste, com o separador e o ultimo frame de cada taxa."""
    sep = ";" if taxa.drop_frame else ":"
    ultimo = taxa.nominal_fps - 1
    return [
        f"01:00:00{sep}00",
        f"01:00:00{sep}01",
        f"01:10:00{sep}00",
        f"01:00:59{sep}{ultimo:02d}",
    ]


def _ancora(taxa, bpm):
    """Ancora com o Live comecando no rotulo de origem."""
    rotulo = ORIGEM_DF if taxa.drop_frame else ORIGEM
    # O comeco de um frame nao cai em sample inteiro na taxa 29.97 drop.
    # Arredonda para cima para o beat zero cair dentro do rotulo de origem.
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
def test_volta_de_rotulo_para_beats_e_de_volta(taxa, bpm, rotulo):
    """O rotulo volta para os beats e para o mesmo lugar em samples."""
    ancora = _ancora(taxa, bpm)
    beats = label_to_beats(rotulo, ancora)
    amostras = beats_to_samples(beats, ancora)
    esperado = label_to_samples(rotulo, taxa, SR)
    assert abs(amostras - esperado) < 1e-6, rotulo
    # O beats em ponto flutuante pode ficar um fio abaixo da fronteira; o
    # relogio tolera esse erro e mostra o proprio frame, nunca o anterior.
    assert beats_to_label(beats, ancora) == rotulo


@pytest.mark.parametrize("taxa", TAXAS)
def test_beat_zero_da_o_rotulo_de_origem(taxa):
    ancora = _ancora(taxa, 120.0)
    esperado = ORIGEM_DF if taxa.drop_frame else ORIGEM
    assert beats_to_label(0.0, ancora) == esperado


@pytest.mark.parametrize(
    "taxa,bpm,esperado,anterior",
    [
        (TAXA_30, 120.0, "00:59:50:15", "00:59:50:14"),
        (TAXA_24, 60.0, "00:59:51:00", "00:59:50:23"),
    ],
)
def test_inicio_do_frame_e_o_frame_anterior(taxa, bpm, esperado, anterior):
    """O comeco do frame da o proprio frame, e um sample antes da o anterior."""
    ancora = _ancora(taxa, bpm)
    inicio = label_to_samples(esperado, taxa, SR)
    beats = float((inicio - ancora.live_origin_samples) * bpm / (60 * SR))
    assert beats_to_label(beats, ancora) == esperado
    antes = float((inicio - 1 - ancora.live_origin_samples) * bpm / (60 * SR))
    assert beats_to_label(antes, ancora) == anterior


def test_beats_para_samples_e_exato():
    """A 120 bpm e 48000 Hz, duas batidas valem 48000 samples."""
    ancora = _ancora(TAXA_30, 120.0)
    amostras = beats_to_samples(2.0, ancora)
    assert isinstance(amostras, Fraction)
    assert amostras == ancora.live_origin_samples + 48000


def test_aviso_de_tempo():
    ancora = _ancora(TAXA_30, 120.0)
    assert tempo_warning(120.0, ancora) is None
    assert tempo_warning(120.0005, ancora) is None
    aviso = tempo_warning(120.002, ancora)
    assert isinstance(aviso, str)
    assert "120" in aviso
    aviso = tempo_warning(119.0, ancora)
    assert isinstance(aviso, str)
    assert "119" in aviso
    assert "120" in aviso
