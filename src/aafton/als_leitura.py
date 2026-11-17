"""Amostra da midia que o Live le num clip com warp 1:1.

Medido no Live 12.4.25b1 a 48 kHz (15/11/2026, kits 1 a 3 da task
aafton-render-no-app-20261115): floor(segundos x taxa), com tolerancia de
1/1000 de amostra para o decimal de float do Live, mais 1 amostra em Complex
(4) e Complex Pro (6).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from fractions import Fraction

from .als_paths import AUDIO_CLIP_FIELDS
from .als_render import _fracao, segundos_no_beat
from .als_xml import get_value

TOLERANCIA_LEITURA = Fraction(1, 1000)
DESLOCAMENTO_DO_MODO = {"3": 0, "4": 1, "6": 1}


def amostra_lida(segundos: Fraction, taxa: int, modo: str) -> int:
    """A amostra lida; um modo fora de DESLOCAMENTO_DO_MODO soma 0 (o clip ja pede render)."""
    return math.floor(segundos * taxa + TOLERANCIA_LEITURA) + DESLOCAMENTO_DO_MODO.get(modo, 0)


def leitura_na_midia(clip_node: ET.Element, beat: Fraction, taxa: int) -> int | None:
    """Amostra da midia que o Live le num beat do clip com warp 1:1."""
    segundos = segundos_no_beat(clip_node, beat)
    modo = (get_value(clip_node, "WarpMode") or "").strip()
    return None if segundos is None else amostra_lida(segundos, taxa, modo)


def offset_do_warp(clip_node: ET.Element, taxa: int) -> int | None:
    """Offset na midia de um clip com warp 1:1, em amostras da sessao."""
    loop_start = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["loop_start"]))
    if loop_start is None:
        return None
    return leitura_na_midia(clip_node, loop_start, taxa)
