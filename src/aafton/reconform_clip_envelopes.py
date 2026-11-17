"""Corte e rebase dos ClipEnvelope de um clip fatiado pelo reconform.

Cada pedaco guarda os eventos dentro do seu trecho, rebaseados para o inicio
do pedaco, com pontos de quebra nas bordas; o evento sentinela de tempo
negativo fica em todo pedaco.
"""

from __future__ import annotations

import copy
import math
import xml.etree.ElementTree as ET

from .als_xml import format_value, renumber_siblings

_ENV_TOL = 1e-9


def _snapshot_envelopes(clip_node: ET.Element) -> list[list[ET.Element]]:
    """Copia os eventos originais de cada ClipEnvelope antes da mutacao."""
    snapshots: list[list[ET.Element]] = []
    for env in clip_node.findall("Envelopes/Envelopes/ClipEnvelope"):
        ev_box = env.find("Automation/Events")
        snapshots.append(copy.deepcopy(list(ev_box)) if ev_box is not None else [])
    return snapshots


def _parse_envelope_events(
    orig_events: list[ET.Element],
) -> tuple[list[ET.Element], list[tuple[float, ET.Element]]]:
    """Separa eventos sentinela (Time < 0) dos pontos ordenados por Time."""
    sentinels: list[ET.Element] = []
    points: list[tuple[float, ET.Element]] = []
    for ev in orig_events:
        raw_t = ev.get("Time")
        if raw_t is None:
            continue
        try:
            t_val = float(raw_t)
        except ValueError:
            continue
        if not math.isfinite(t_val):
            continue
        if t_val < 0.0:
            sentinels.append(copy.deepcopy(ev))
        else:
            points.append((t_val, ev))
    points.sort(key=lambda item: item[0])
    return sentinels, points


def _eval_envelope(points: list[tuple[float, ET.Element]], t_q: float, bp_tag: str) -> str:
    """Calcula o valor do envelope em t_q (linear para FloatEvent, hold caso contrario)."""
    if t_q <= points[0][0]:
        return points[0][1].get("Value", "0")
    if t_q >= points[-1][0]:
        return points[-1][1].get("Value", "0")
    prev_idx = 0
    for idx, (t_val, _ev) in enumerate(points):
        if t_val <= t_q:
            prev_idx = idx
        else:
            break
    if bp_tag != "FloatEvent":
        return points[prev_idx][1].get("Value", "0")
    t_a, ev_a = points[prev_idx]
    t_b, ev_b = points[prev_idx + 1]
    v_a = float(ev_a.get("Value", "0"))
    v_b = float(ev_b.get("Value", "0"))
    frac = (t_q - t_a) / (t_b - t_a)
    return format_value(v_a + (v_b - v_a) * frac)


def _has_time_near(kept: list[tuple[float, ET.Element]], target: float) -> bool:
    """Verdadeiro se ja existe evento em kept dentro da tolerancia de target."""
    return any(abs(t_val - target) <= _ENV_TOL for t_val, _ev in kept)


def _make_breakpoint(tag: str, rebased_time: float, value_str: str) -> ET.Element:
    """Cria um elemento de evento de envelope com Id provisorio."""
    return ET.Element(tag, {"Id": "0", "Time": format_value(rebased_time), "Value": value_str})


def _build_sliced_events(
    orig_events: list[ET.Element],
    p_start: float,
    p_end: float,
) -> list[ET.Element]:
    """Filtra, rebasa e interpola os breakpoints do ClipEnvelope para uma fatia."""
    sentinels, points = _parse_envelope_events(orig_events)
    if not points:
        return sentinels
    kept = [(t_val, ev) for t_val, ev in points if p_start - _ENV_TOL <= t_val <= p_end + _ENV_TOL]
    bp_tag = points[0][1].tag
    duration = max(0.0, p_end - p_start)
    out = list(sentinels)
    if not _has_time_near(kept, p_start):
        out.append(_make_breakpoint(bp_tag, 0.0, _eval_envelope(points, p_start, bp_tag)))
    for t_val, ev in kept:
        rebased = t_val - p_start
        if abs(rebased) <= _ENV_TOL:
            rebased = 0.0
        elif abs(t_val - p_end) <= _ENV_TOL:
            rebased = duration
        new_ev = copy.deepcopy(ev)
        new_ev.set("Time", format_value(rebased))
        out.append(new_ev)
    if duration > _ENV_TOL and not _has_time_near(kept, p_end):
        out.append(_make_breakpoint(bp_tag, duration, _eval_envelope(points, p_end, bp_tag)))
    return out


def _slice_clip_envelopes(
    piece_node: ET.Element,
    orig_envelopes: list[list[ET.Element]],
    p_start: float,
    p_end: float,
) -> None:
    """Atualiza todos os ClipEnvelopes da peca fatiada e renumera eventos."""
    envelopes = piece_node.findall("Envelopes/Envelopes/ClipEnvelope")
    for env, orig_events in zip(envelopes, orig_envelopes):
        ev_box = env.find("Automation/Events")
        if ev_box is None:
            continue
        new_children = _build_sliced_events(orig_events, p_start, p_end)
        for child in list(ev_box):
            ev_box.remove(child)
        tags: set[str] = set()
        for child in new_children:
            ev_box.append(child)
            tags.add(child.tag)
        for tag in sorted(tags):
            renumber_siblings(ev_box, tag)
