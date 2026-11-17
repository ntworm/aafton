"""Comparacao entre duas listas de corte de imagem (CutIR) para reconformacao.

Calcula as pecas casadas por chave de origem e timecode de origem, identifica
ambiguidades e mudancas, e produz o ChangeList com o TimeMap associado.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .cut_ir import CutEvent, CutIR
from .reconform_map import TimeMap, build_time_map, uncovered_clip_spans
from .report import Report
from .timecode import TimecodeRate


@dataclass(frozen=True)
class Piece:
    """Trecho continuo casado entre um evento antigo e um evento novo."""

    old_in: int
    old_out: int
    new_in: int
    old_index: int
    new_index: int

    @property
    def new_out(self) -> int:
        """Frame final exclusivo no corte novo."""
        return self.new_in + (self.old_out - self.old_in)

    @property
    def delta(self) -> int:
        """Deslocamento em frames do corte antigo para o novo."""
        return self.new_in - self.old_in

    @property
    def duration_frames(self) -> int:
        """Duracao da peca em frames."""
        return self.old_out - self.old_in


@dataclass(frozen=True)
class _Match:
    """Associacao interna entre uma Piece e os dois eventos de origem."""

    piece: Piece
    old_event: CutEvent
    new_event: CutEvent


@dataclass(frozen=True)
class Change:
    """Entrada individual da lista de mudancas da reconformacao."""

    kind: str
    old_in: int | None
    old_out: int | None
    new_in: int | None
    new_out: int | None
    source: str | None
    delta_frames: int | None
    detail: str = ""


@dataclass(frozen=True)
class ChangeList:
    """Resultado completo da comparacao entre dois cortes."""

    old_path: str
    new_path: str
    rate: TimecodeRate
    changes: list[Change]
    counts: dict[str, int]
    ambiguous: bool
    time_map: TimeMap | None
    old_start_frames: int = 0
    new_start_frames: int = 0


def _rate_tuple(rate: TimecodeRate) -> tuple[int, int, bool]:
    """Tupla comparavel da taxa de frames (num, den, drop_frame)."""
    return (rate.num, rate.den, rate.drop_frame)


def _has_source_tc(ev: CutEvent) -> bool:
    """Verdadeiro quando o evento possui chave de origem e intervalo de TC."""
    return ev.source is not None and ev.source_in is not None and ev.source_out is not None


def _pair_piece(old_ev: CutEvent, new_ev: CutEvent) -> Piece | None:
    """Calcula a Piece da intersecao de source TC entre dois eventos de clip."""
    if old_ev.source is None or old_ev.source != new_ev.source:
        return None
    if old_ev.source_in is None or old_ev.source_out is None:
        return None
    if new_ev.source_in is None or new_ev.source_out is None:
        return None
    start = max(old_ev.source_in, new_ev.source_in)
    end = min(old_ev.source_out, new_ev.source_out)
    if end <= start:
        return None
    length = end - start
    old_in = old_ev.record_in + (start - old_ev.source_in)
    new_in = new_ev.record_in + (start - new_ev.source_in)
    return Piece(old_in, old_in + length, new_in, old_ev.index, new_ev.index)


def _index_new_clips(new: CutIR) -> dict[str, list[CutEvent]]:
    """Agrupa clips validos do corte novo pela chave de origem normalizada."""
    grouped: dict[str, list[CutEvent]] = {}
    for ev in new.events:
        if ev.kind == "clip" and _has_source_tc(ev) and ev.source is not None:
            grouped.setdefault(ev.source, []).append(ev)
    return grouped


def _match_sort_key(match: _Match) -> tuple[int, int, int, int, int]:
    """Chave deterministica para ordenar pecas casadas."""
    p = match.piece
    return (p.old_in, p.old_out, p.new_in, p.old_index, p.new_index)


def _collect_matches(old: CutIR, new: CutIR) -> list[_Match]:
    """Casa todos os pares de clips com intersecao nao vazia de source TC."""
    new_by_source = _index_new_clips(new)
    matches: list[_Match] = []
    for old_ev in old.events:
        if old_ev.kind != "clip" or not _has_source_tc(old_ev):
            continue
        assert old_ev.source is not None
        for new_ev in new_by_source.get(old_ev.source, ()):
            piece = _pair_piece(old_ev, new_ev)
            if piece is not None:
                matches.append(_Match(piece=piece, old_event=old_ev, new_event=new_ev))
    matches.sort(key=_match_sort_key)
    return matches


def _overlapping_indices(
    pieces: Sequence[Piece],
    bounds: Callable[[Piece], tuple[int, int]],
) -> set[int]:
    """Indices das pecas que se sobrepoem na linha de tempo projetada."""
    ordered = sorted(range(len(pieces)), key=lambda idx: bounds(pieces[idx]))
    active: list[tuple[int, int]] = []
    flagged: set[int] = set()
    for idx in ordered:
        start, end = bounds(pieces[idx])
        active = [(other_idx, other_end) for other_idx, other_end in active if other_end > start]
        if active:
            flagged.add(idx)
            for other_idx, _ in active:
                flagged.add(other_idx)
        active.append((idx, end))
    return flagged


def _ambiguous_indices(pieces: Sequence[Piece]) -> set[int]:
    """Indices das pecas ambiguas por sobreposicao no corte antigo ou novo."""
    old_hits = _overlapping_indices(pieces, lambda p: (p.old_in, p.old_out))
    new_hits = _overlapping_indices(pieces, lambda p: (p.new_in, p.new_out))
    return old_hits | new_hits


def _piece_change(kind: str, match: _Match, detail: str) -> Change:
    """Constroi um Change preenchido a partir de uma peca casada."""
    p = match.piece
    src = match.old_event.source
    return Change(kind, p.old_in, p.old_out, p.new_in, p.new_out, src, p.delta, detail)


def _uncovered_changes(
    events: Sequence[CutEvent],
    covered: Sequence[tuple[int, int]],
    is_delete: bool,
) -> list[Change]:
    """Gera mudancas 'delete' ou 'insert' para spans maximais sem cobertura."""
    kind = "delete" if is_delete else "insert"
    verb = "deleted" if is_delete else "inserted"
    changes: list[Change] = []
    for start, end, source in uncovered_clip_spans(events, covered):
        old_in, old_out = (start, end) if is_delete else (None, None)
        new_in, new_out = (None, None) if is_delete else (start, end)
        msg = f"{verb} {end - start} frames"
        changes.append(Change(kind, old_in, old_out, new_in, new_out, source, None, msg))
    return changes


def _group_matches(
    matches: Sequence[_Match],
) -> tuple[dict[int, list[_Match]], dict[int, list[_Match]]]:
    """Agrupa matches por objeto CutEvent antigo e novo."""
    by_old: dict[int, list[_Match]] = {}
    by_new: dict[int, list[_Match]] = {}
    for match in matches:
        by_old.setdefault(id(match.old_event), []).append(match)
        by_new.setdefault(id(match.new_event), []).append(match)
    for items in by_old.values():
        items.sort(key=lambda m: (m.piece.old_in, m.piece.old_out))
    for items in by_new.values():
        items.sort(key=lambda m: (m.piece.new_in, m.piece.new_out))
    return by_old, by_new


def _head_trim_change(
    old_ev: CutEvent,
    first_m: _Match,
    by_new: dict[int, list[_Match]],
) -> Change | None:
    """Detecta trim_head (perda ou ganho de cabeca) de um evento casado."""
    p = first_m.piece
    src = old_ev.source
    r_in = old_ev.record_in
    lost = p.old_in - r_in
    if lost > 0:
        msg = f"lost {lost} frames at head"
        return Change("trim_head", r_in, p.old_in, p.new_in, p.new_in, src, -lost, msg)
    new_matches = by_new[id(first_m.new_event)]
    if first_m is new_matches[0]:
        nr_in = first_m.new_event.record_in
        gained = p.new_in - nr_in
        if gained > 0:
            msg = f"gained {gained} frames at head"
            return Change("trim_head", r_in, r_in, nr_in, p.new_in, src, gained, msg)
    return None


def _tail_trim_change(
    old_ev: CutEvent,
    last_m: _Match,
    by_new: dict[int, list[_Match]],
) -> Change | None:
    """Detecta trim_tail (perda ou ganho de cauda) de um evento casado."""
    p = last_m.piece
    src = old_ev.source
    r_out = old_ev.record_out
    lost = r_out - p.old_out
    if lost > 0:
        msg = f"lost {lost} frames at tail"
        return Change("trim_tail", p.old_out, r_out, p.new_out, p.new_out, src, -lost, msg)
    new_matches = by_new[id(last_m.new_event)]
    if last_m is new_matches[-1]:
        nr_out = last_m.new_event.record_out
        gained = nr_out - p.new_out
        if gained > 0:
            msg = f"gained {gained} frames at tail"
            return Change("trim_tail", r_out, r_out, p.new_out, nr_out, src, gained, msg)
    return None


def _trim_changes(matches: Sequence[_Match]) -> list[Change]:
    """Gera mudancas trim_head e trim_tail para eventos casados."""
    by_old, by_new = _group_matches(matches)
    changes: list[Change] = []
    for items in by_old.values():
        old_ev = items[0].old_event
        head = _head_trim_change(old_ev, items[0], by_new)
        if head is not None:
            changes.append(head)
        tail = _tail_trim_change(old_ev, items[-1], by_new)
        if tail is not None:
            changes.append(tail)
    return changes


def _lis_positions(values: Sequence[int]) -> set[int]:
    """Indices da maior subsequencia estritamente crescente em values."""
    if not values:
        return set()
    tails: list[int] = []
    tail_idx: list[int] = []
    parent = [-1] * len(values)
    for idx, val in enumerate(values):
        pos = bisect_left(tails, val)
        if pos == len(tails):
            tails.append(val)
            tail_idx.append(idx)
        else:
            tails[pos] = val
            tail_idx[pos] = idx
        if pos > 0:
            parent[idx] = tail_idx[pos - 1]
    keep: set[int] = set()
    cur = tail_idx[-1]
    while cur != -1:
        keep.add(cur)
        cur = parent[cur]
    return keep


def _move_and_shift_changes(matches: Sequence[_Match]) -> list[Change]:
    """Classifica pecas fora da LIS como 'move' e mudancas de delta na LIS como 'shift'.

    A primeira peca e comparada com o delta zero: um corte novo todo
    deslocado marca um 'shift' no primeiro plano, e nao em nenhum.
    """
    lis = _lis_positions([m.piece.new_in for m in matches])
    changes: list[Change] = []
    prev_delta = 0
    for idx, match in enumerate(matches):
        p = match.piece
        if idx not in lis:
            msg = f"moved from {p.old_in} to {p.new_in} (delta {p.delta:+d})"
            changes.append(_piece_change("move", match, msg))
        elif p.delta != prev_delta:
            msg = f"shifted from delta {prev_delta:+d} to {p.delta:+d}"
            changes.append(_piece_change("shift", match, msg))
        prev_delta = p.delta
    return changes


def _transition_changes(matches: Sequence[_Match]) -> list[Change]:
    """Detecta mudanca de transition_frames mantendo o mesmo ponto de corte."""
    seen: set[tuple[int, int]] = set()
    changes: list[Change] = []
    for match in matches:
        o = match.old_event
        n = match.new_event
        key = (id(o), id(n))
        if key in seen:
            continue
        seen.add(key)
        if o.record_in == n.record_in and o.transition_frames != n.transition_frames:
            old_t, new_t = o.transition_frames, n.transition_frames
            oi, oo, ni, no = o.record_in, o.record_out, n.record_in, n.record_out
            msg = f"transition changed from {old_t} to {new_t} frames"
            changes.append(Change("transition", oi, oo, ni, no, o.source, new_t - old_t, msg))
    return changes


def _change_sort_key(change: Change) -> tuple[int, int, str]:
    """Chave de ordenacao por old_in (ou new_in quando ausente)."""
    if change.old_in is not None:
        primary = change.old_in
    elif change.new_in is not None:
        primary = change.new_in
    else:
        primary = 0
    secondary = change.new_in if change.new_in is not None else -1
    return (primary, secondary, change.kind)


def _count_changes(changes: Sequence[Change]) -> dict[str, int]:
    """Conta ocorrencias por tipo de mudanca."""
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    return counts


def _build_time_map(
    old: CutIR,
    new: CutIR,
    pieces: Sequence[Piece],
    report: Report,
) -> TimeMap | None:
    """Constroi o TimeMap e reporta o erro quando o mapa tem sobreposicao.

    Um mapa com segmentos sobrepostos nao e um mapa: o diff devolve a lista
    de mudancas sem TimeMap, e o apply recusa a lista.
    """
    try:
        return build_time_map(old, new, pieces)
    except ValueError as exc:
        report.error(
            "RECONFORM_MAP_OVERLAP",
            "the time map has overlapping segments",
            {"detail": str(exc)},
        )
        return None


def diff_cuts(old: CutIR, new: CutIR, report: Report | None = None) -> ChangeList | None:
    """Compara dois cortes (CutIR) e constroi o ChangeList e o TimeMap."""
    active_report = report if report is not None else Report()
    old_key, new_key = _rate_tuple(old.rate), _rate_tuple(new.rate)
    if old_key != new_key:
        ctx = {"old_rate": old_key, "new_rate": new_key}
        active_report.error(
            "RECONFORM_RATE_MISMATCH", "the frame rates of the two cuts differ", ctx
        )
        return None
    matches = _collect_matches(old, new)
    pieces = [match.piece for match in matches]
    flagged = _ambiguous_indices(pieces)
    ambiguous = bool(flagged)
    changes: list[Change] = []
    changes.extend(_uncovered_changes(old.events, [(p.old_in, p.old_out) for p in pieces], True))
    changes.extend(_uncovered_changes(new.events, [(p.new_in, p.new_out) for p in pieces], False))
    if ambiguous:
        for idx in sorted(flagged):
            changes.append(_piece_change("ambiguous", matches[idx], "ambiguous overlap"))
        time_map: TimeMap | None = None
    else:
        changes.extend(_trim_changes(matches))
        changes.extend(_move_and_shift_changes(matches))
        changes.extend(_transition_changes(matches))
        time_map = _build_time_map(old, new, pieces, active_report)
    changes.sort(key=_change_sort_key)
    counts = _count_changes(changes)
    return ChangeList(
        old.path,
        new.path,
        old.rate,
        changes,
        counts,
        ambiguous,
        time_map,
        old.start_frames,
        new.start_frames,
    )
