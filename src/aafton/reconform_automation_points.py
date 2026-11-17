"""Pontos dos envelopes de automacao do reconform: leitura e avaliacao.

Separa os sentinelas, converte o tempo de cada ponto para samples absolutos e
avalia o valor do envelope numa fronteira, interpolando FloatEvent e segurando
o valor nos eventos discretos.
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .als_read_clips import _beats_to_samples
from .als_xml import format_value
from .sidecar import Anchor


@dataclass(frozen=True)
class _EnvPoint:
    """Evento nao-sentinela com tempo original em beats e samples absolutos."""

    t_beats: float
    abs_samples: int
    element: ET.Element


def _interp_float(pt_a: _EnvPoint, pt_b: _EnvPoint, b_samples: int, b_beats: float) -> str:
    """Interpolacao linear entre dois pontos FloatEvent."""
    v_a = float(pt_a.element.get("Value", "0"))
    v_b = float(pt_b.element.get("Value", "0"))
    denom = pt_b.t_beats - pt_a.t_beats
    if denom > 0.0:
        frac = (b_beats - pt_a.t_beats) / denom
    else:
        s_denom = pt_b.abs_samples - pt_a.abs_samples
        frac = (b_samples - pt_a.abs_samples) / s_denom if s_denom > 0 else 0.0
    return format_value(v_a + (v_b - v_a) * min(1.0, max(0.0, frac)))


def _eval_right(points: list[_EnvPoint], b_samples: int, b_beats: float, bp_tag: str) -> str:
    """Valor do envelope no inicio de um trecho em b_samples."""
    if b_samples < points[0].abs_samples:
        return points[0].element.get("Value", "0")
    if b_samples >= points[-1].abs_samples:
        return points[-1].element.get("Value", "0")
    prev_idx = 0
    for idx, pt in enumerate(points):
        if pt.abs_samples <= b_samples:
            prev_idx = idx
        else:
            break
    if points[prev_idx].abs_samples == b_samples or bp_tag != "FloatEvent":
        return points[prev_idx].element.get("Value", "0")
    return _interp_float(points[prev_idx], points[prev_idx + 1], b_samples, b_beats)


def _eval_left(points: list[_EnvPoint], b_samples: int, b_beats: float, bp_tag: str) -> str:
    """Valor do envelope ao aproximar b_samples pela esquerda."""
    if b_samples <= points[0].abs_samples:
        return points[0].element.get("Value", "0")
    if b_samples > points[-1].abs_samples:
        return points[-1].element.get("Value", "0")
    next_idx = len(points) - 1
    for idx, pt in enumerate(points):
        if pt.abs_samples >= b_samples:
            next_idx = idx
            break
    if bp_tag != "FloatEvent":
        return points[next_idx - 1].element.get("Value", "0")
    if points[next_idx].abs_samples == b_samples:
        return points[next_idx].element.get("Value", "0")
    return _interp_float(points[next_idx - 1], points[next_idx], b_samples, b_beats)


def _parse_events(
    events_box: ET.Element, anchor: Anchor
) -> tuple[list[ET.Element], list[_EnvPoint]]:
    """Separa eventos sentinela (Time < 0) dos pontos ordenados por Time."""
    sentinels: list[ET.Element] = []
    points: list[_EnvPoint] = []
    for ev in events_box:
        raw_t = ev.get("Time")
        if raw_t is None:
            continue
        try:
            t_beats = float(raw_t)
        except ValueError:
            continue
        if not math.isfinite(t_beats):
            continue
        if t_beats < 0.0:
            sentinels.append(copy.deepcopy(ev))
        else:
            abs_s = anchor.live_origin_samples + _beats_to_samples(
                t_beats, anchor.sample_rate, anchor.bpm
            )
            points.append(_EnvPoint(t_beats, abs_s, ev))
    points.sort(key=lambda pt: pt.t_beats)
    return sentinels, points


def _cloned_at(ev: ET.Element, t_beats: float) -> ET.Element:
    """Copia um evento original ajustando apenas o atributo Time."""
    cloned = copy.deepcopy(ev)
    cloned.set("Time", format_value(t_beats))
    return cloned
