"""Casamento de planos por frame, com a velocidade do plano.

O frame r do evento antigo casa com o frame r' do evento novo quando os dois
sao clip, tem a mesma origem e a mesma velocidade, e nenhum dos dois e um
plano inteiro: a origem de r em A e a origem de r' em N sao o mesmo frame do
material de origem.

Com velocidade inteira s, o frame r' do corte novo e
`N.record_in + (origem - N.source_in) / s`, e ele existe so quando
`(A.source_in - N.source_in) % s == 0`. Uma peca e a sequencia maxima de
frames consecutivos do corte antigo com r' consecutivo: com s = 1 e a regra de
hoje. Plano inteiro (whole_only) casa so com outro plano inteiro de mesma
origem, mesmo comeco, mesma velocidade e mesma duracao: a peca e o evento
inteiro. Mesma origem com velocidade diferente, ou com um so plano inteiro, nao
casa: o material antigo vira delete e o novo vira insert ("speed changed").
A etiqueta "speed changed" so vale quando as faixas de origem dos dois
eventos se cruzam: material de outra parte do arquivo de origem e um delete
e um insert comuns.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Sequence

from .cut_ir import CutEvent, CutIR

SPEED_CHANGED_DETAIL = "speed changed"


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


@dataclass(frozen=True)
class Match:
    """Associacao interna entre uma Piece e os dois eventos de origem."""

    piece: Piece
    old_event: CutEvent
    new_event: CutEvent


@dataclass(frozen=True)
class SpeedChanged:
    """Par de eventos da mesma origem que nao casa por causa da velocidade."""

    old_in: int
    old_out: int
    new_in: int
    new_out: int
    source: str | None


def _has_source(ev: CutEvent) -> bool:
    """Verdadeiro quando o evento possui chave de origem e intervalo de TC."""
    return ev.source is not None and ev.source_in is not None and ev.source_out is not None


def _speed_factor(speed: Fraction | int | None) -> int | None:
    """Velocidade do plano como inteiro positivo, ou None quando ela nao e."""
    if speed is None:
        return None
    value = Fraction(speed)
    if value.denominator != 1 or value <= 0:
        return None
    return int(value)


def _whole_piece(old_ev: CutEvent, new_ev: CutEvent) -> Piece | None:
    """Peca de um plano inteiro: o evento inteiro, quando os dois batem."""
    if not (old_ev.whole_only and new_ev.whole_only):
        return None
    if old_ev.source_in != new_ev.source_in or old_ev.speed != new_ev.speed:
        return None
    if old_ev.duration_frames != new_ev.duration_frames:
        return None
    if old_ev.duration_frames <= 0:
        return None
    return Piece(
        old_ev.record_in,
        old_ev.record_out,
        new_ev.record_in,
        old_ev.index,
        new_ev.index,
    )


def _frame_piece(old_ev: CutEvent, new_ev: CutEvent) -> Piece | None:
    """Peca do casamento por frame, com a velocidade dos dois planos."""
    factor = _speed_factor(old_ev.speed)
    if factor is None or _speed_factor(new_ev.speed) != factor:
        return None
    assert old_ev.source_in is not None and new_ev.source_in is not None
    if (old_ev.source_in - new_ev.source_in) % factor != 0:
        return None
    offset = (old_ev.source_in - new_ev.source_in) // factor
    start = max(old_ev.record_in, old_ev.record_in - offset)
    end = min(old_ev.record_out, old_ev.record_in + new_ev.duration_frames - offset)
    if end <= start:
        return None
    new_in = new_ev.record_in + (start - old_ev.record_in) + offset
    return Piece(start, end, new_in, old_ev.index, new_ev.index)


def pair_piece(old_ev: CutEvent, new_ev: CutEvent) -> Piece | None:
    """Peca da intersecao de origem entre dois eventos de clip."""
    if old_ev.source is None or old_ev.source != new_ev.source:
        return None
    if not _has_source(old_ev) or not _has_source(new_ev):
        return None
    if old_ev.whole_only or new_ev.whole_only:
        return _whole_piece(old_ev, new_ev)
    return _frame_piece(old_ev, new_ev)


def _index_new_clips(new: CutIR) -> dict[str, list[CutEvent]]:
    """Agrupa clips validos do corte novo pela chave de origem normalizada."""
    grouped: dict[str, list[CutEvent]] = {}
    for ev in new.events:
        if ev.kind == "clip" and _has_source(ev) and ev.source is not None:
            grouped.setdefault(ev.source, []).append(ev)
    return grouped


def match_sort_key(match: Match) -> tuple[int, int, int, int, int]:
    """Chave deterministica para ordenar pecas casadas."""
    p = match.piece
    return (p.old_in, p.old_out, p.new_in, p.old_index, p.new_index)


def _source_ranges_overlap(old_ev: CutEvent, new_ev: CutEvent) -> bool:
    """Verdadeiro quando as faixas de origem dos dois eventos se cruzam."""
    if old_ev.source_in is None or old_ev.source_out is None:
        return False
    if new_ev.source_in is None or new_ev.source_out is None:
        return False
    return old_ev.source_in < new_ev.source_out and new_ev.source_in < old_ev.source_out


def _speed_differs(old_ev: CutEvent, new_ev: CutEvent) -> bool:
    """Verdadeiro quando o par so nao casa por causa da velocidade ou do plano inteiro.

    As faixas de origem dos dois eventos precisam se cruzar: quando o
    material novo vem de outra parte do arquivo de origem, a diferenca nao
    e de velocidade e o par vira um delete e um insert comuns.
    """
    if not _source_ranges_overlap(old_ev, new_ev):
        return False
    if old_ev.whole_only != new_ev.whole_only:
        return True
    return old_ev.speed != new_ev.speed


def match_events(old: CutIR, new: CutIR) -> tuple[list[Match], list[SpeedChanged]]:
    """Casa os pares de clips da mesma origem e aponta os que nao casam por velocidade."""
    new_by_source = _index_new_clips(new)
    matches: list[Match] = []
    changed: list[SpeedChanged] = []
    for old_ev in old.events:
        if old_ev.kind != "clip" or not _has_source(old_ev):
            continue
        assert old_ev.source is not None
        for new_ev in new_by_source.get(old_ev.source, ()):
            piece = pair_piece(old_ev, new_ev)
            if piece is not None:
                matches.append(Match(piece=piece, old_event=old_ev, new_event=new_ev))
            elif _speed_differs(old_ev, new_ev):
                item = SpeedChanged(
                    old_ev.record_in,
                    old_ev.record_out,
                    new_ev.record_in,
                    new_ev.record_out,
                    old_ev.source,
                )
                changed.append(item)
    matches.sort(key=match_sort_key)
    return matches, changed


def _in_ranges(start: int | None, end: int | None, ranges: Sequence[tuple[int, int]]) -> bool:
    """Verdadeiro quando o intervalo informado cruza uma das faixas."""
    if start is None or end is None:
        return False
    return any(start < r_end and r_start < end for r_start, r_end in ranges)


def mark_speed_changed(
    changes: Sequence[Any],
    changed: Sequence[SpeedChanged],
) -> list[Any]:
    """Troca o detalhe das mudancas de material com a velocidade mudada.

    As mudancas do diff sao dataclasses congeladas com os campos kind, old_in,
    old_out, new_in, new_out e detail.
    """
    old_ranges = [(item.old_in, item.old_out) for item in changed]
    new_ranges = [(item.new_in, item.new_out) for item in changed]
    marked: list[Any] = []
    for change in changes:
        if change.kind == "delete" and _in_ranges(change.old_in, change.old_out, old_ranges):
            marked.append(replace(change, detail=SPEED_CHANGED_DETAIL))
        elif change.kind == "insert" and _in_ranges(change.new_in, change.new_out, new_ranges):
            marked.append(replace(change, detail=SPEED_CHANGED_DETAIL))
        else:
            marked.append(change)
    return marked
