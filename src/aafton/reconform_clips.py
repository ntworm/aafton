"""Reconformacao em memoria dos AudioClips do arranjo de um Live set.

Move, fatia e separa trechos removidos (saiu) nos AudioClips das AudioTracks
sobre a arvore XML carregada, sem fazer I/O em disco e sem criar tracks novas.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from .als_read_clips import _beats_to_samples
from .als_write_base import TIMELINE, WriteOptions, _clip_times, samples_to_beats, source_seconds
from .als_xml import (
    IdAllocator,
    classify_id_tags,
    clone,
    format_value,
    get_value,
    renumber_siblings,
    set_value,
    unique_ids,
)
from .reconform_clip_envelopes import _slice_clip_envelopes, _snapshot_envelopes
from .reconform_samples import SampleMap
from .report import Report
from .sidecar import Anchor


@dataclass(frozen=True)
class SaiuPiece:
    """Peca de AudioClip retirada do corte para posicionamento posterior."""

    track_node: ET.Element
    clip_element: ET.Element
    new_abs_start: int
    # Inicio da peca no corte antigo: diz de qual trecho removido ela veio.
    old_abs_start: int = -1
    # Trecho removido de onde a peca veio: (inicio no corte antigo, que o
    # identifica, e inicio e fim no corte novo). As pecas do mesmo trecho vao
    # para a mesma track [saiu].
    region_new: tuple[int, int, int] | None = None


@dataclass
class ClipsResult:
    """Contadores e pecas removidas da reconformacao dos AudioClips."""

    moved: int = 0
    sliced: int = 0
    saiu: list[SaiuPiece] = field(default_factory=list)


@dataclass(frozen=True)
class _ClipPlan:
    """Plano validado de transformacao para um AudioClip do arranjo."""

    clip_node: ET.Element
    abs_start: int
    abs_end: int
    is_warped: bool
    spans: list[tuple[int, int, str, int]]


def _flag_true(node: ET.Element, field_key: str) -> bool:
    """Verdadeiro quando o campo booleano XML do clip vale 'true'."""
    raw = get_value(node, AUDIO_CLIP_FIELDS[field_key])
    return raw is not None and raw.strip().lower() == "true"


def _read_float(node: ET.Element, field_key: str, default: float = 0.0) -> float:
    """Le um campo float do AudioClip com fallback seguro."""
    raw = get_value(node, AUDIO_CLIP_FIELDS[field_key])
    if raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return val if math.isfinite(val) else default


def _set_optional_value(
    node: ET.Element,
    field_key: str,
    value: float | int | str | bool,
) -> None:
    """Escreve um campo opcional do AudioClip ignorando se ausente no XML."""
    path = AUDIO_CLIP_FIELDS.get(field_key)
    if path is None:
        return
    try:
        set_value(node, path, value)
    except KeyError:
        pass


def _read_clip_range(clip_node: ET.Element, anchor: Anchor) -> tuple[int, int] | None:
    """Intervalo absoluto [inicio, fim) em samples do AudioClip pela ancora."""
    raw_start = get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"])
    raw_end = get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"])
    if raw_start is None or raw_end is None:
        return None
    try:
        start_beats = float(raw_start)
        end_beats = float(raw_end)
    except ValueError:
        return None
    if not (math.isfinite(start_beats) and math.isfinite(end_beats)):
        return None
    abs_start = anchor.live_origin_samples + _beats_to_samples(
        start_beats, anchor.sample_rate, anchor.bpm
    )
    abs_end = anchor.live_origin_samples + _beats_to_samples(
        end_beats, anchor.sample_rate, anchor.bpm
    )
    return abs_start, abs_end


def _has_map_before_origin(spans: list[tuple[int, int, str, int]], origin: int) -> bool:
    """Verdadeiro se alguma peca 'map' cai antes da origem do Live."""
    for sp_in, _sp_out, kind, val in spans:
        if kind == "map" and sp_in + val < origin:
            return True
    return False


def _clip_ctx(track_name: str, clip_node: ET.Element) -> dict[str, object]:
    """Contexto minimo de erro para um AudioClip."""
    return {
        "track": track_name,
        "clip_id": clip_node.get("Id"),
        "clip_name": get_value(clip_node, AUDIO_CLIP_FIELDS["name"]) or "",
    }


def _track_label(track_node: ET.Element) -> str:
    """Nome legivel da AudioTrack para mensagens de erro."""
    return (
        get_value(track_node, TRACK_FIELDS["effective_name"])
        or get_value(track_node, TRACK_FIELDS["name"])
        or ""
    )


def _validate_clip(
    track_name: str,
    clip_node: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> _ClipPlan | None:
    """Valida um AudioClip e devolve seu plano de reconformacao, ou None."""
    ctx = _clip_ctx(track_name, clip_node)
    if _flag_true(clip_node, "loop_on"):
        report.error("RECONFORM_LOOPED_CLIP", "a clip with LoopOn cannot be reconformed", ctx)
        return None
    bounds = _read_clip_range(clip_node, anchor)
    if bounds is None:
        report.error("RECONFORM_CLIP_UNREADABLE", "clip without valid CurrentStart/CurrentEnd", ctx)
        return None
    abs_start, abs_end = bounds
    if abs_end <= abs_start:
        return None
    spans = smap.spans(abs_start, abs_end)
    is_warped = _flag_true(clip_node, "is_warped")
    if is_warped and len(spans) > 1:
        report.error("RECONFORM_WARPED_CLIP", "a warped clip cannot be split", ctx)
        return None
    if _has_map_before_origin(spans, anchor.live_origin_samples):
        report.error("RECONFORM_BEFORE_ORIGIN", "the reconformed clip falls before the origin", ctx)
        return None
    return _ClipPlan(clip_node, abs_start, abs_end, is_warped, spans)


def _collect_plans(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> list[tuple[ET.Element, ET.Element, list[_ClipPlan]]]:
    """Valida todas as AudioTracks e monta planos antes de qualquer mutacao."""
    planned: list[tuple[ET.Element, ET.Element, list[_ClipPlan]]] = []
    for track_node in root.findall("./LiveSet/Tracks/AudioTrack"):
        events = track_node.find(TRACK_FIELDS["arrangement_events"])
        if events is None:
            continue
        t_name = _track_label(track_node)
        track_plans: list[_ClipPlan] = []
        for clip_node in events.findall("AudioClip"):
            plan = _validate_clip(t_name, clip_node, anchor, smap, report)
            if plan is not None:
                track_plans.append(plan)
        if track_plans:
            planned.append((track_node, events, track_plans))
    return planned


def _set_timeline_times(
    node: ET.Element,
    abs_start: int,
    abs_end: int,
    anchor: Anchor,
) -> None:
    """Atualiza Time e CurrentStart/CurrentEnd pela grade de clips do Live.

    Um double fora da grade (o to_beats) anda uma amostra quando o Live salva
    o set de novo.
    """
    start_beats, end_beats = _clip_times(
        abs_start,
        abs_end - abs_start,
        WriteOptions(
            sample_rate=anchor.sample_rate,
            bpm=anchor.bpm,
            live_origin_samples=anchor.live_origin_samples,
        ),
    )
    node.set("Time", format_value(start_beats))
    set_value(node, AUDIO_CLIP_FIELDS["current_start"], start_beats)
    set_value(node, AUDIO_CLIP_FIELDS["current_end"], end_beats)


def _update_unwarped_loop(
    node: ET.Element,
    orig_loop_start: float,
    offset_samples: int,
    duration_samples: int,
    sample_rate: int,
) -> None:
    """Atualiza LoopStart, LoopEnd e HiddenLoopStart/End em segundos da fonte."""
    orig_f = orig_loop_start * sample_rate
    orig_i = round(orig_f)
    if abs(orig_f - orig_i) < 1e-6:
        loop_start = (
            orig_loop_start
            if offset_samples == 0
            else source_seconds(orig_i + offset_samples, sample_rate)
        )
        loop_end = source_seconds(orig_i + offset_samples + duration_samples, sample_rate)
    else:
        delta_sec = source_seconds(offset_samples, sample_rate) if offset_samples else 0.0
        loop_start = orig_loop_start + delta_sec
        loop_end = loop_start + source_seconds(duration_samples, sample_rate)
    set_value(node, AUDIO_CLIP_FIELDS["loop_start"], loop_start)
    set_value(node, AUDIO_CLIP_FIELDS["loop_end"], loop_end)
    _set_optional_value(node, "hidden_loop_start", loop_start)
    _set_optional_value(node, "hidden_loop_end", loop_end)


def _reset_zero_curves(node: ET.Element, zero_in: bool, zero_out: bool) -> None:
    """Zera skew/slope quando o fade correspondente foi zerado na fatia."""
    if zero_in:
        _set_optional_value(node, "fade_in_curve_skew", 0.0)
        _set_optional_value(node, "fade_in_curve_slope", 0.0)
    if zero_out:
        _set_optional_value(node, "fade_out_curve_skew", 0.0)
        _set_optional_value(node, "fade_out_curve_slope", 0.0)


def _apply_piece_fades(
    node: ET.Element,
    idx: int,
    total: int,
    piece_dur: int,
    orig_in: float,
    orig_out: float,
    orig_xfade: str | None,
    anchor: Anchor,
) -> None:
    """Aplica fades e CrossfadeInState nas pontas originais e zera nas emendas."""
    piece_len_beats = samples_to_beats(piece_dur, anchor.sample_rate, anchor.bpm, TIMELINE)
    if idx == 0:
        fade_in = min(max(0.0, orig_in), piece_len_beats)
        xfade_val = orig_xfade if orig_xfade is not None else "0"
    else:
        fade_in = 0.0
        xfade_val = "0"
    fade_out = min(max(0.0, orig_out), piece_len_beats) if idx == total - 1 else 0.0
    _set_optional_value(node, "fade_in_length", fade_in)
    _set_optional_value(node, "fade_out_length", fade_out)
    _set_optional_value(node, "crossfade_in_state", xfade_val)
    _reset_zero_curves(node, fade_in == 0.0, fade_out == 0.0)


def _apply_whole_plan(
    track_node: ET.Element,
    events_node: ET.Element,
    plan: _ClipPlan,
    anchor: Anchor,
    result: ClipsResult,
) -> bool:
    """Aplica o plano de um clip nao fatiado; devolve se houve mudanca na track."""
    sp_in, sp_out, kind, val = plan.spans[0]
    dur = sp_out - sp_in
    if kind == "map":
        if val == 0:
            return False
        _set_timeline_times(plan.clip_node, sp_in + val, sp_out + val, anchor)
        if not plan.is_warped:
            orig_ls = _read_float(plan.clip_node, "loop_start", 0.0)
            _update_unwarped_loop(plan.clip_node, orig_ls, 0, dur, anchor.sample_rate)
        result.moved += 1
        return True
    if val >= anchor.live_origin_samples:
        _set_timeline_times(plan.clip_node, val, val + dur, anchor)
    if not plan.is_warped:
        orig_ls = _read_float(plan.clip_node, "loop_start", 0.0)
        _update_unwarped_loop(plan.clip_node, orig_ls, 0, dur, anchor.sample_rate)
    events_node.remove(plan.clip_node)
    result.saiu.append(SaiuPiece(track_node, plan.clip_node, val, sp_in))
    return True


def _configure_sliced_piece(
    piece_node: ET.Element,
    idx: int,
    plan: _ClipPlan,
    orig_meta: tuple[float, float, float, str | None, list[list[ET.Element]]],
    anchor: Anchor,
) -> tuple[str, int]:
    """Configura tempos, loop, fades e envelopes de uma fatia de clip."""
    orig_ls, orig_in, orig_out, orig_xfade, orig_envelopes = orig_meta
    sp_in, sp_out, kind, val = plan.spans[idx]
    piece_offset = sp_in - plan.abs_start
    piece_dur = sp_out - sp_in
    new_abs_start = sp_in + val if kind == "map" else val
    if kind == "map" or new_abs_start >= anchor.live_origin_samples:
        _set_timeline_times(piece_node, new_abs_start, new_abs_start + piece_dur, anchor)
    _update_unwarped_loop(piece_node, orig_ls, piece_offset, piece_dur, anchor.sample_rate)
    _apply_piece_fades(
        piece_node, idx, len(plan.spans), piece_dur, orig_in, orig_out, orig_xfade, anchor
    )
    p_start = samples_to_beats(piece_offset, anchor.sample_rate, anchor.bpm, TIMELINE)
    p_end = samples_to_beats(sp_out - plan.abs_start, anchor.sample_rate, anchor.bpm, TIMELINE)
    _slice_clip_envelopes(piece_node, orig_envelopes, p_start, p_end)
    return kind, new_abs_start


def _apply_sliced_plan(
    track_node: ET.Element,
    events_node: ET.Element,
    plan: _ClipPlan,
    anchor: Anchor,
    allocator: IdAllocator,
    positional_tags: frozenset[str],
    unique: frozenset[int],
    result: ClipsResult,
) -> None:
    """Clona pecas novas, muta cada fatia e separa as pecas saiu."""
    orig_meta = (
        _read_float(plan.clip_node, "loop_start", 0.0),
        _read_float(plan.clip_node, "fade_in_length", 0.0),
        _read_float(plan.clip_node, "fade_out_length", 0.0),
        get_value(plan.clip_node, AUDIO_CLIP_FIELDS["crossfade_in_state"]),
        _snapshot_envelopes(plan.clip_node),
    )
    reuse_idx = next((i for i, sp in enumerate(plan.spans) if sp[2] == "map"), 0)
    piece_nodes = [
        plan.clip_node
        if i == reuse_idx
        else clone(plan.clip_node, allocator, positional_tags, unique)
        for i in range(len(plan.spans))
    ]
    for idx, piece_node in enumerate(piece_nodes):
        kind, new_abs_start = _configure_sliced_piece(piece_node, idx, plan, orig_meta, anchor)
        if kind == "map":
            if idx != reuse_idx:
                events_node.append(piece_node)
        else:
            if idx == reuse_idx:
                events_node.remove(piece_node)
            result.saiu.append(SaiuPiece(track_node, piece_node, new_abs_start, plan.spans[idx][0]))
    result.sliced += 1


def _clip_sort_key(clip: ET.Element) -> float:
    """Chave numerica de ordenacao de AudioClips por Time."""
    raw = clip.get("Time") or get_value(clip, AUDIO_CLIP_FIELDS["current_start"]) or "0"
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _sort_and_renumber_events(
    events_node: ET.Element,
    positional_tags: frozenset[str],
) -> None:
    """Reordena os AudioClips restantes por Time e atualiza Ids posicionais."""
    clips = [child for child in events_node if child.tag == "AudioClip"]
    other = [child for child in events_node if child.tag != "AudioClip"]
    clips.sort(key=_clip_sort_key)
    for child in list(events_node):
        events_node.remove(child)
    for child in other:
        events_node.append(child)
    for clip in clips:
        events_node.append(clip)
    if "AudioClip" in positional_tags:
        renumber_siblings(events_node, "AudioClip")


def _update_next_pointee_id(root: ET.Element, allocator: IdAllocator) -> None:
    """Atualiza NextPointeeId se existir e o alocador tiver avancado alem dele."""
    next_node = root.find(".//NextPointeeId")
    if next_node is None:
        return
    raw = next_node.get("Value", "")
    current = int(raw) if raw.lstrip("-").isdigit() else 0
    if current <= allocator._cursor:
        allocator.write_next_pointee_id(root)


def reconform_clips(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
    allocator: IdAllocator,
) -> ClipsResult:
    """Reconformam todos os AudioClips do arranjo segundo smap sem alterar o disco."""
    errors_before = report.count("error")
    planned = _collect_plans(root, anchor, smap, report)
    if report.count("error") > errors_before:
        return ClipsResult()
    positional_tags, _ = classify_id_tags(root)
    unique = unique_ids(root)
    result = ClipsResult()
    for track_node, events_node, track_plans in planned:
        track_changed = False
        for plan in track_plans:
            if len(plan.spans) == 1:
                if _apply_whole_plan(track_node, events_node, plan, anchor, result):
                    track_changed = True
            else:
                _apply_sliced_plan(
                    track_node,
                    events_node,
                    plan,
                    anchor,
                    allocator,
                    positional_tags,
                    unique,
                    result,
                )
                track_changed = True
        if track_changed:
            _sort_and_renumber_events(events_node, positional_tags)
    if result.sliced > 0:
        _update_next_pointee_id(root, allocator)
    return result
