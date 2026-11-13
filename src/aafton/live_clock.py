"""Relogio do Live: batidas viram samples e rotulos SMPTE.

O Live manda a posicao da song em batidas. O relogio traduz essa posicao
para samples, a partir da ancora do sidecar, e de volta para rotulos.
"""

from __future__ import annotations

import math
import struct
from fractions import Fraction

from .sidecar import Anchor
from .timecode import frames_to_label, label_to_samples

TOLERANCIA_FRAME = 1e-9
DIFERENCA_MAXIMA_TEMPO = 0.001
# Erro maximo de um float32 em relacao ao valor exato: um ulp.
ULP_FLOAT32 = 2**-23


def beats_to_samples(beats: float, anchor: Anchor) -> Fraction:
    """Posicao em samples de uma posicao em batidas do Live."""
    duracao = Fraction(beats) * 60 / Fraction(anchor.bpm)
    return anchor.live_origin_samples + duracao * anchor.sample_rate


def beats_to_label(beats: float, anchor: Anchor) -> str:
    """Rotulo SMPTE do frame que contem a posicao em batidas.

    O Live manda a posicao da song em float32: o valor volta um fio abaixo
    do comeco do frame e o relogio mostraria o frame anterior. A tolerancia
    e o dobro do erro de um ulp do valor das batidas, em frames.
    """
    amostras = beats_to_samples(beats, anchor)
    taxa = anchor.timecode_rate
    frames_por_beat = 60 / anchor.bpm * (taxa.num / taxa.den)
    tolerancia = max(TOLERANCIA_FRAME, 2 * abs(beats) * ULP_FLOAT32 * frames_por_beat)
    frames = math.floor(amostras * taxa.num / (anchor.sample_rate * taxa.den) + tolerancia)
    return frames_to_label(frames, taxa)


def beats_float32(beats: float) -> float:
    """Menor float32 que nao e menor que as batidas dadas.

    O Live recebe a posicao em float32: mandar o valor exato faz ele
    arredondar para baixo e comecar o frame anterior. Aqui o valor sobe
    para o proximo float32, e o Live comeca no frame ou depois dele.
    """
    try:
        bruto = struct.pack(">f", beats)
    except (OverflowError, struct.error):
        return beats
    (valor,) = struct.unpack(">f", bruto)
    if valor >= beats:
        return valor
    bits = int.from_bytes(bruto, "big")
    # O bit de sinal e a magnitude: nos negativos, o numero seguinte esta
    # um bit abaixo.
    bits += 1 if beats >= 0 else -1
    (valor,) = struct.unpack(">f", bits.to_bytes(4, "big"))
    return valor


def label_to_beats(label: str, anchor: Anchor) -> float:
    """Posicao em batidas do Live de um rotulo SMPTE."""
    amostras = label_to_samples(label, anchor.timecode_rate, anchor.sample_rate)
    return float((amostras - anchor.live_origin_samples) / anchor.sample_rate * anchor.bpm / 60)


def tempo_warning(live_tempo: float, anchor: Anchor) -> str | None:
    """Aviso quando o tempo do Live difere do bpm guardado no sidecar."""
    if abs(live_tempo - anchor.bpm) <= DIFERENCA_MAXIMA_TEMPO:
        return None
    return (
        f"the Live tempo {live_tempo} differs from the sidecar bpm {anchor.bpm}; "
        "the clock assumes a fixed tempo"
    )
