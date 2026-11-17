"""Reconformacao em memoria dos envelopes de automacao das tracks do Live.

Desloca eventos de AutomationEnvelope de AudioTrack, GroupTrack, ReturnTrack,
MainTrack e MasterTrack pelo SampleMap, insere breakpoints nas fronteiras
cruzadas, preserva sentinelas e trata trechos removidos (saiu).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .als_paths import TRACK_FIELDS
from .als_write_base import TIMELINE, AnchorError, to_beats
from .als_xml import (
    IdAllocator,
    classify_id_tags,
    clone,
    format_value,
    get_value,
    renumber_siblings,
    unique_ids,
)
from .reconform_automation_points import (
    _cloned_at,
    _EnvPoint,
    _eval_left,
    _eval_right,
    _parse_events,
)
from .reconform_clips import _read_clip_range
from .reconform_saiu_routing import _destino
from .reconform_samples import SampleMap
from .reconform_targets import _PathKey, _TargetInfo, _index_targets
from .reconform_tracks import SaiuTracks
from .report import Report
from .sidecar import Anchor

_TRACK_TAGS = ("AudioTrack", "GroupTrack", "ReturnTrack", "MainTrack", "MasterTrack")


@dataclass(frozen=True)
class _PlacedEvent:
    """Evento pronto para ordenacao na timeline (0=fim, 1=meio, 2=inicio)."""

    t_beats: float
    role: int
    element: ET.Element


@dataclass
class _CloneCtx:
    """Estado de clonagem compartilhado para envelopes copiados para tracks [saiu]."""

    allocator: IdAllocator
    positional_tags: frozenset[str]
    unique: frozenset[int]
    used: bool = False


@dataclass(frozen=True)
class _SaiuTrack:
    """Track [saiu] de destino, com os alvos e o alcance dos seus clips."""

    node: ET.Element
    by_path: dict[_PathKey, str]
    ranges: tuple[tuple[int, int], ...]
    old_ranges: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class _SaiuGroup:
    """Trecho removido e os eventos de automacao que ficaram nele."""

    new_in: int
    new_out: int
    events: list[_PlacedEvent]
    old_in: int = 0
    old_out: int = 0


@dataclass(frozen=True)
class _SaiuDest:
    """Destinos [saiu] e o estado de clonagem para envelopes movidos."""

    tracks: tuple[_SaiuTrack, ...]
    clone_ctx: _CloneCtx


@dataclass(frozen=True)
class _EnvCtx:
    """Contexto de conversao e diagnostico de um AutomationEnvelope."""

    points: list[_EnvPoint]
    anchor: Anchor
    report: Report
    track_label: str
    param_name: str
    bp_tag: str


def _track_label(track_node: ET.Element) -> str:
    """Nome legivel da track ou nome da tag quando ausente."""
    return (
        get_value(track_node, TRACK_FIELDS["effective_name"])
        or get_value(track_node, TRACK_FIELDS["name"])
        or track_node.tag
    )


def _old_beats(abs_samples: int, anchor: Anchor) -> float:
    """Converte sample absoluto antigo para beats relativos a ancora."""
    return (abs_samples - anchor.live_origin_samples) * anchor.bpm / (60.0 * anchor.sample_rate)


def _to_beats_checked(ctx: _EnvCtx, abs_samples: int) -> float | None:
    """Converte sample absoluto novo para beats ou registra RECONFORM_BEFORE_ORIGIN."""
    try:
        return to_beats(
            abs_samples,
            ctx.anchor.live_origin_samples,
            ctx.anchor.sample_rate,
            ctx.anchor.bpm,
            TIMELINE,
        )
    except AnchorError:
        ctx.report.error(
            "RECONFORM_BEFORE_ORIGIN",
            f"automation of {ctx.param_name!r} in track {ctx.track_label!r} falls before origin",
            {"track": ctx.track_label, "parameter": ctx.param_name, "abs_samples": abs_samples},
        )
        return None


def _emit_breakpoint(
    out: list[_PlacedEvent], ctx: _EnvCtx, new_s: int, role: int, val: str
) -> None:
    """Cria e anexa um evento sintetico de fronteira quando new_s e valido."""
    new_beats = _to_beats_checked(ctx, new_s)
    if new_beats is not None:
        ev = ET.Element(ctx.bp_tag, {"Id": "0", "Time": format_value(new_beats), "Value": val})
        out.append(_PlacedEvent(new_beats, role, ev))


def _saiu_splice_sample(smap: SampleMap, abs_sample: int) -> int:
    """Posicao de destino na track [saiu] para um sample em saiu."""
    for item in smap.saiu:
        if item.old_in <= abs_sample < item.old_out:
            return item.splice_new + (abs_sample - item.old_in)
    return abs_sample


def _map_degenerate(
    ctx: _EnvCtx, smap: SampleMap
) -> tuple[list[_PlacedEvent], list[_SaiuGroup], int]:
    """Mapeia envelope cujos pontos compartilham o mesmo sample absoluto."""
    abs_s = ctx.points[0].abs_samples
    mapped = smap.map_sample(abs_s)
    if mapped is not None:
        new_b = _to_beats_checked(ctx, mapped)
        if new_b is None:
            return [], [], 0
        return [_PlacedEvent(new_b, 1, _cloned_at(pt.element, new_b)) for pt in ctx.points], [], 0
    splice = _saiu_splice_sample(smap, abs_s)
    new_b = _to_beats_checked(ctx, splice)
    if new_b is None:
        return [], [], len(ctx.points)
    moved = [_PlacedEvent(new_b, 1, _cloned_at(pt.element, new_b)) for pt in ctx.points]
    grupo = _grupo_saiu((abs_s, abs_s + 1, "saiu", splice), moved)
    return [], [grupo], len(ctx.points)


def _point_role(abs_s: int, lo: int, hi: int) -> int:
    """Papel de ordenacao do ponto dentro do span (0=fim, 1=meio, 2=inicio)."""
    if abs_s >= hi:
        return 0
    return 2 if abs_s <= lo else 1


def _points_in_span(points: list[_EnvPoint], lo: int, hi: int, is_last: bool) -> list[_EnvPoint]:
    """Pontos dentro do span; o ultimo span inclui a borda final."""
    if is_last:
        return [pt for pt in points if lo <= pt.abs_samples <= hi]
    return [pt for pt in points if lo <= pt.abs_samples < hi]


def _span_target(span: tuple[int, int, str, int], abs_s: int) -> int:
    """Posicao nova de um sample do span: delta no map, emenda + offset no saiu."""
    lo, _hi, kind, val = span
    return abs_s + val if kind == "map" else val + (abs_s - lo)


def _collect_span(
    span_idx: int,
    total_spans: int,
    span: tuple[int, int, str, int],
    ctx: _EnvCtx,
) -> tuple[list[_PlacedEvent], int]:
    """Gera eventos mapeados e breakpoints de fronteira para um span."""
    lo, hi, kind, val = span
    is_last = span_idx == total_spans - 1
    span_pts = _points_in_span(ctx.points, lo, hi, is_last)
    out: list[_PlacedEvent] = []
    if span_idx > 0 and not any(pt.abs_samples == lo for pt in span_pts):
        lo_val = _eval_right(ctx.points, lo, _old_beats(lo, ctx.anchor), ctx.bp_tag)
        _emit_breakpoint(out, ctx, _span_target(span, lo), 2, lo_val)
    for pt in span_pts:
        new_s = _span_target(span, pt.abs_samples)
        pt_beats = _to_beats_checked(ctx, new_s)
        if pt_beats is not None:
            role = _point_role(pt.abs_samples, lo, hi)
            out.append(_PlacedEvent(pt_beats, role, _cloned_at(pt.element, pt_beats)))
    if not is_last:
        hi_val = _eval_left(ctx.points, hi, _old_beats(hi, ctx.anchor), ctx.bp_tag)
        _emit_breakpoint(out, ctx, _span_target(span, hi), 0, hi_val)
    return out, len(span_pts)


def _grupo_saiu(span: tuple[int, int, str, int], events: list[_PlacedEvent]) -> _SaiuGroup:
    """Grupo dos eventos de automacao que caem num trecho removido.

    O grupo guarda o alcance dos eventos no corte novo: e ele que diz qual
    track [saiu] tem o clip que cobre o trecho removido.
    """
    lo, hi, _kind, val = span
    return _SaiuGroup(val, val + (hi - lo), events, lo, hi)


def _map_envelope(
    ctx: _EnvCtx, smap: SampleMap
) -> tuple[list[_PlacedEvent], list[_SaiuGroup], int]:
    """Separa os eventos do envelope em trecho mantido e trechos saiu."""
    if ctx.points[-1].abs_samples == ctx.points[0].abs_samples:
        return _map_degenerate(ctx, smap)
    spans = smap.spans(ctx.points[0].abs_samples, ctx.points[-1].abs_samples)
    kept: list[_PlacedEvent] = []
    groups: list[_SaiuGroup] = []
    saiu_count = 0
    for idx, span in enumerate(spans):
        events, orig_count = _collect_span(idx, len(spans), span, ctx)
        if span[2] == "map":
            kept.extend(events)
        else:
            groups.append(_grupo_saiu(span, events))
            saiu_count += orig_count
    return kept, groups, saiu_count


def _write_events(
    events_box: ET.Element, sentinels: list[ET.Element], placed: list[_PlacedEvent]
) -> None:
    """Reescreve Automation/Events ordenado por Time e renumera Ids posicionais."""
    placed_sorted = sorted(placed, key=lambda item: (item.t_beats, item.role))
    for child in list(events_box):
        events_box.remove(child)
    tags: set[str] = set()
    for node in [*sentinels, *(item.element for item in placed_sorted)]:
        events_box.append(node)
        tags.add(node.tag)
    for tag in sorted(tags):
        renumber_siblings(events_box, tag)


def _ensure_sub(parent: ET.Element, tag: str) -> ET.Element:
    """Retorna o filho tag ou cria um novo se ausente."""
    found = parent.find(tag)
    return found if found is not None else ET.SubElement(parent, tag)


def _append_saiu_envelope(
    saiu_track: ET.Element,
    source_env: ET.Element,
    new_target_id: str,
    sentinels: list[ET.Element],
    saiu_events: list[_PlacedEvent],
    clone_ctx: _CloneCtx,
) -> None:
    """Clona o envelope para a track [saiu] de destino remapeando PointeeId."""
    envelopes_box = _ensure_sub(_ensure_sub(saiu_track, "AutomationEnvelopes"), "Envelopes")
    env_copy = clone(source_env, clone_ctx.allocator, clone_ctx.positional_tags, clone_ctx.unique)
    clone_ctx.used = True
    pointee = _ensure_sub(_ensure_sub(env_copy, "EnvelopeTarget"), "PointeeId")
    pointee.set("Value", new_target_id)
    events_box = _ensure_sub(_ensure_sub(env_copy, "Automation"), "Events")
    _write_events(events_box, sentinels, saiu_events)
    envelopes_box.append(env_copy)
    renumber_siblings(envelopes_box, "AutomationEnvelope")


def _moved_to_saiu(
    env: ET.Element,
    target_info: _TargetInfo | None,
    sentinels: list[ET.Element],
    groups: list[_SaiuGroup],
    dest: _SaiuDest,
) -> tuple[bool, int, int]:
    """Move o trecho saiu para as tracks [saiu], ignorando Speaker.

    Devolve (tratado, movidos, descartados): 'tratado' diz que o envelope nao
    precisa de aviso de descarte.
    """
    if target_info is None:
        return False, 0, 0
    if target_info.is_speaker:
        return True, 0, 0
    if not dest.tracks:
        return False, 0, 0
    por_destino: dict[str, tuple[_SaiuTrack, list[_PlacedEvent]]] = {}
    for group in groups:
        track = _destino(dest, group)
        if track is None:
            continue
        chave = str(id(track.node))
        if chave not in por_destino:
            por_destino[chave] = (track, [])
        por_destino[chave][1].extend(group.events)
    movidos = 0
    descartados = 0
    for track, events in por_destino.values():
        if not events:
            continue
        new_tid = track.by_path.get(target_info.path)
        if new_tid is None:
            descartados += len(events)
            continue
        _append_saiu_envelope(track.node, env, new_tid, sentinels, events, dest.clone_ctx)
        movidos += len(events)
    return movidos > 0, movidos, descartados


def _route_saiu(
    track_node: ET.Element,
    ctx: _EnvCtx,
    env: ET.Element,
    target_info: _TargetInfo | None,
    pointee_id: str,
    sentinels: list[ET.Element],
    groups: list[_SaiuGroup],
    saiu_orig: int,
    dest: _SaiuDest,
) -> None:
    """Encaminha eventos em saiu para [saiu] (AudioTrack) ou descarta com aviso."""
    if not groups:
        return
    descartados = 0
    if track_node.tag == "AudioTrack":
        tratado, _movidos, descartados = _moved_to_saiu(env, target_info, sentinels, groups, dest)
        if tratado and not descartados:
            return
        if not descartados:
            descartados = saiu_orig
    else:
        descartados = saiu_orig
    if descartados <= 0:
        return
    msg = (
        f"automation of {ctx.param_name!r} in track {ctx.track_label!r} "
        "fell in a removed span and was dropped"
    )
    info = {
        "track": ctx.track_label,
        "kind": track_node.tag,
        "parameter": ctx.param_name,
        "pointee_id": pointee_id,
        "dropped_events": descartados,
    }
    ctx.report.warning("RECONFORM_AUTOMATION_DROPPED", msg, info)


def _process_track(
    track_node: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    dest: _SaiuDest,
    report: Report,
) -> None:
    """Reconformam todos os AutomationEnvelopes de uma track."""
    envelopes_box = track_node.find("AutomationEnvelopes/Envelopes")
    if envelopes_box is None:
        return
    track_label = _track_label(track_node)
    targets_by_id, _ = _index_targets(track_node)
    for env in envelopes_box.findall("AutomationEnvelope"):
        events_box = env.find("Automation/Events")
        if events_box is None:
            continue
        sentinels, points = _parse_events(events_box, anchor)
        if not points:
            continue
        ponteiro = env.find("EnvelopeTarget/PointeeId")
        pointee_id = ponteiro.get("Value", "") if ponteiro is not None else ""
        target_info = targets_by_id.get(pointee_id)
        param_name = target_info.param_name if target_info else f"target {pointee_id}"
        ctx = _EnvCtx(points, anchor, report, track_label, param_name, points[0].element.tag)
        kept, groups, saiu_orig = _map_envelope(ctx, smap)
        _write_events(events_box, sentinels, kept)
        _route_saiu(
            track_node, ctx, env, target_info, pointee_id, sentinels, groups, saiu_orig, dest
        )


def _saiu_lookup(
    saiu_tracks: SaiuTracks | Mapping[str, Sequence[ET.Element] | ET.Element] | None,
) -> tuple[Mapping[str, Sequence[ET.Element] | ET.Element], set[int]]:
    """Extrai o mapeamento por origem e o conjunto de id() das tracks [saiu]."""
    if saiu_tracks is None:
        return {}, set()
    by_source = saiu_tracks.by_source if isinstance(saiu_tracks, SaiuTracks) else saiu_tracks
    ids: set[int] = set()
    for entry in by_source.values():
        if isinstance(entry, ET.Element):
            ids.add(id(entry))
        else:
            ids.update(id(node) for node in entry)
    return by_source, ids


def _saiu_destinos(
    by_source: Mapping[str, Sequence[ET.Element] | ET.Element],
    tid: str,
    anchor: Anchor,
    old_ranges: Mapping[int, Sequence[tuple[int, int]]] | None = None,
) -> tuple[_SaiuTrack, ...]:
    """Tracks [saiu] criadas para a track de origem com o Id informado."""
    old_ranges = old_ranges or {}
    entry = by_source.get(tid)
    if entry is None:
        return ()
    nodes = [entry] if isinstance(entry, ET.Element) else list(entry)
    saida: list[_SaiuTrack] = []
    for node in nodes:
        _by_id, by_path = _index_targets(node)
        events = node.find(TRACK_FIELDS["arrangement_events"])
        ranges: list[tuple[int, int]] = []
        if events is not None:
            for clip in events.findall("AudioClip"):
                bounds = _read_clip_range(clip, anchor)
                if bounds is not None and bounds[1] > bounds[0]:
                    ranges.append(bounds)
        antigos = tuple(sorted(old_ranges.get(id(node), ())))
        saida.append(_SaiuTrack(node, by_path, tuple(sorted(ranges)), antigos))
    return tuple(saida)


def shift_track_automation(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    saiu_tracks: SaiuTracks | Mapping[str, Sequence[ET.Element] | ET.Element] | None,
    report: Report,
) -> None:
    """Desloca os AutomationEnvelopes das tracks de acordo com smap."""
    by_source, saiu_ids = _saiu_lookup(saiu_tracks)
    antigos = saiu_tracks.old_ranges if isinstance(saiu_tracks, SaiuTracks) else {}
    positional_tags, _ = classify_id_tags(root)
    clone_ctx = _CloneCtx(
        allocator=IdAllocator.from_root(root),
        positional_tags=positional_tags,
        unique=unique_ids(root),
    )
    tracks = [node for tag in _TRACK_TAGS for node in root.iter(tag) if id(node) not in saiu_ids]
    for track_node in tracks:
        destinos = (
            _saiu_destinos(by_source, track_node.get("Id", ""), anchor, antigos)
            if track_node.tag == "AudioTrack"
            else ()
        )
        _process_track(track_node, anchor, smap, _SaiuDest(destinos, clone_ctx), report)
    if clone_ctx.used:
        next_node = root.find(".//NextPointeeId")
        if next_node is not None:
            raw = next_node.get("Value", "")
            current = int(raw) if raw.lstrip("-").isdigit() else 0
            if current <= clone_ctx.allocator._cursor:
                clone_ctx.allocator.write_next_pointee_id(root)
