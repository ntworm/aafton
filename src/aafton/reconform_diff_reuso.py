"""Resolucao por ordem do material repetido: o mesmo plano usado mais de uma vez.

Duas pecas candidatas que se sobrepoem no corte antigo ou no corte novo sao um
conflito, e as pecas de um grupo se resolvem pela ordem dos planos: a ancora
valida mais proxima da peca da o deslocamento de referencia, e vence a peca
mais proxima desse deslocamento, depois a mais longa. O vencedor nao tira os
outros inteiros: cada candidato que se sobrepoe a ele e aparado, no antigo e
no novo juntos, ate a parte que nao se sobrepoe, e a parte que sobra volta
para o grupo. Candidatos empatados nos dois criterios e sobrepostos entre si
nao vencem: o trecho deles fica sem correspondencia, e listado como nao
resolvido, e todo candidato que se sobrepoe a ele e aparado.

O trecho empatado e listado por regiao: cada regiao do empate, no antigo e no
novo, vira um 'unresolved' e um aviso. Nunca uma caixa que va da primeira
regiao a ultima, porque essa caixa cobre o material que ja casou. As pecas
descartadas sao contadas pelas faixas que o vencedor cobriu, e nao pelo par de
eventos do candidato.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Sequence

from .cut_ir import CutIR
from .reconform_diff_velocidade import Match, Piece, match_sort_key
from .report import Report
from .timecode import TimecodeRate, frames_to_label

UNRESOLVED_DETAIL = "same shot used more than once: not matched"


@dataclass(frozen=True)
class Unresolved:
    """Trecho que a ordem dos planos nao resolveu, em uma regiao do empate."""

    old_in: int
    old_out: int
    new_in: int
    new_out: int
    source: str | None


@dataclass(frozen=True)
class Resolution:
    """Resultado da resolucao por ordem do material repetido."""

    matches: list[Match]
    unresolved: list[Unresolved]
    discarded: list[Match]
    groups: int


def lis_positions(values: Sequence[int]) -> set[int]:
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


def _overlaps(a: Piece, b: Piece) -> bool:
    """Verdadeiro quando as pecas se sobrepoem no corte antigo ou no novo."""
    antigo = a.old_in < b.old_out and b.old_in < a.old_out
    novo = a.new_in < b.new_out and b.new_in < a.new_out
    return antigo or novo


def _span(match: Match, side: str) -> tuple[int, int]:
    """Intervalo da peca no lado escolhido da linha do tempo."""
    if side == "old":
        return match.piece.old_in, match.piece.old_out
    return match.piece.new_in, match.piece.new_out


def _conflicting_pairs(matches: Sequence[Match], side: str) -> list[tuple[int, int]]:
    """Pares de pecas que se sobrepoem no lado escolhido da linha do tempo."""
    indices = sorted(range(len(matches)), key=lambda i: _span(matches[i], side)[0])
    pairs: list[tuple[int, int]] = []
    active: list[tuple[int, int]] = []
    for idx in indices:
        start, end = _span(matches[idx], side)
        active = [(other, other_end) for other, other_end in active if other_end > start]
        for other, _ in active:
            pairs.append((other, idx))
        active.append((idx, end))
    return pairs


def _root(parent: list[int], idx: int) -> int:
    """Raiz do conjunto do indice, com compressao de caminho."""
    while parent[idx] != idx:
        parent[idx] = parent[parent[idx]]
        idx = parent[idx]
    return idx


def _union(parent: list[int], a: int, b: int) -> None:
    """Junta os conjuntos dos dois indices."""
    root_a, root_b = _root(parent, a), _root(parent, b)
    if root_a != root_b:
        parent[max(root_a, root_b)] = min(root_a, root_b)


def _groups(matches: Sequence[Match]) -> list[list[Match]]:
    """Grupos de conflito: componentes conexas do grafo de sobreposicao."""
    parent = list(range(len(matches)))
    for side in ("old", "new"):
        for a, b in _conflicting_pairs(matches, side):
            _union(parent, a, b)
    groups: dict[int, list[Match]] = {}
    for idx, match in enumerate(matches):
        groups.setdefault(_root(parent, idx), []).append(match)
    return list(groups.values())


def _anchors(
    matches: Sequence[Match],
    groups: Sequence[list[Match]],
) -> tuple[list[Match], list[Match]]:
    """Ancoras da ordem e as ancoras validas (as da maior sequencia crescente)."""
    in_group = {id(m) for group in groups if len(group) > 1 for m in group}
    anchors = [m for m in matches if id(m) not in in_group]
    ordered = sorted(anchors, key=match_sort_key)
    lis = lis_positions([m.piece.new_in for m in ordered])
    valid = [m for idx, m in enumerate(ordered) if idx in lis]
    return anchors, valid


def _reference(anchors: Sequence[Match], piece: Piece, default_delta: int) -> int:
    """Deslocamento de referencia da peca: a ancora valida mais proxima dela."""
    before = [a for a in anchors if a.piece.old_out <= piece.old_in]
    if before:
        return max(before, key=lambda a: a.piece.old_out).piece.delta
    after = [a for a in anchors if a.piece.old_in >= piece.old_in]
    if after:
        return min(after, key=lambda a: a.piece.old_in).piece.delta
    return default_delta


def _score(match: Match, anchors: Sequence[Match], default_delta: int) -> tuple[int, int]:
    """Distancia do deslocamento a referencia e duracao da peca, para a escolha."""
    reference = _reference(anchors, match.piece, default_delta)
    piece = match.piece
    return (abs(piece.delta - reference), -(piece.old_out - piece.old_in))


def _best(pending: Sequence[Match], anchors: Sequence[Match], default_delta: int) -> list[Match]:
    """Candidatos com a melhor pontuacao do grupo, na ordem do grupo."""
    scores = [(_score(m, anchors, default_delta), m) for m in pending]
    best = min(score for score, _ in scores)
    return [m for score, m in scores if score == best]


def _blocked(tied: Sequence[Match]) -> list[Match]:
    """Empatados que se sobrepoem entre si: a ordem nao decide entre eles."""
    blocked: list[Match] = []
    for m in tied:
        if any(_overlaps(m.piece, o.piece) for o in tied if o is not m):
            blocked.append(m)
    return blocked


def _merge_spans(spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Funde faixas que se tocam ou se sobrepoem."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _touching(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Verdadeiro quando as faixas se tocam ou se sobrepoem."""
    return a[0] <= b[1] and b[0] <= a[1]


def _linked(a: Match, b: Match) -> bool:
    """Verdadeiro quando os dois candidatos se tocam no antigo e no novo."""
    antigo = _touching((a.piece.old_in, a.piece.old_out), (b.piece.old_in, b.piece.old_out))
    novo = _touching((a.piece.new_in, a.piece.new_out), (b.piece.new_in, b.piece.new_out))
    return antigo and novo


def _clusters(blocked: Sequence[Match]) -> list[list[Match]]:
    """Candidatos empatados ligados nos dois cortes: componentes conexas."""
    parent = list(range(len(blocked)))
    for i in range(len(blocked)):
        for j in range(i + 1, len(blocked)):
            if _linked(blocked[i], blocked[j]):
                _union(parent, i, j)
    groups: dict[int, list[Match]] = {}
    for idx, match in enumerate(blocked):
        groups.setdefault(_root(parent, idx), []).append(match)
    return list(groups.values())


def _regions(blocked: Sequence[Match]) -> list[Unresolved]:
    """Trechos do empate: uma regiao por grupo de candidatos ligados.

    Cada regiao tem uma faixa no corte antigo e uma no corte novo, e as faixas
    nao tem buraco: o trecho listado nunca cobre material que ja casou.
    """
    regions: list[Unresolved] = []
    for cluster in _clusters(blocked):
        old_range = _merge_spans([_span(m, "old") for m in cluster])
        new_range = _merge_spans([_span(m, "new") for m in cluster])
        sources = {m.old_event.source for m in cluster}
        source = sources.pop() if len(sources) == 1 else None
        regions.append(
            Unresolved(
                old_range[0][0],
                old_range[-1][1],
                new_range[0][0],
                new_range[-1][1],
                source,
            )
        )
    return regions


def _blocks(blocked: Sequence[Match], side: str) -> list[tuple[int, int]]:
    """Faixas cobertas pelos candidatos empatados, no lado escolhido."""
    return _merge_spans([_span(m, side) for m in blocked])


def _intersection(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    """Parte comum de dois intervalos, ou None quando eles nao se cruzam."""
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    return (start, end) if start < end else None


def _subtract(
    span: tuple[int, int],
    conflicts: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Partes do intervalo que nao estao em nenhum dos conflitos."""
    remaining = [span]
    for start, end in conflicts:
        kept: list[tuple[int, int]] = []
        for begin, finish in remaining:
            if end <= begin or start >= finish:
                kept.append((begin, finish))
                continue
            if begin < start:
                kept.append((begin, start))
            if end < finish:
                kept.append((end, finish))
        remaining = kept
    return remaining


def _trim(
    match: Match,
    old_blocks: Sequence[tuple[int, int]],
    new_blocks: Sequence[tuple[int, int]],
) -> tuple[list[Match], list[Match]]:
    """Apara o candidato: devolve a parte que sobra e a parte que foi coberta."""
    piece = match.piece
    conflicts: list[tuple[int, int]] = []
    for block in old_blocks:
        overlap = _intersection((piece.old_in, piece.old_out), block)
        if overlap is not None:
            conflicts.append(overlap)
    for block in new_blocks:
        overlap = _intersection((piece.new_in, piece.new_out), block)
        if overlap is not None:
            conflicts.append((overlap[0] - piece.delta, overlap[1] - piece.delta))
    if not conflicts:
        return [match], []
    kept = [
        Match(
            Piece(begin, end, begin + piece.delta, piece.old_index, piece.new_index),
            match.old_event,
            match.new_event,
        )
        for begin, end in _subtract((piece.old_in, piece.old_out), conflicts)
    ]
    covered = [
        Match(
            Piece(begin, end, begin + piece.delta, piece.old_index, piece.new_index),
            match.old_event,
            match.new_event,
        )
        for begin, end in _merge_spans(conflicts)
    ]
    return kept, covered


def _trim_all(
    pending: Sequence[Match],
    old_blocks: Sequence[tuple[int, int]],
    new_blocks: Sequence[tuple[int, int]],
) -> tuple[list[Match], list[Match]]:
    """Apara os candidatos que conflitam com o bloqueio."""
    kept: list[Match] = []
    covered: list[Match] = []
    for match in pending:
        sobra, corte = _trim(match, old_blocks, new_blocks)
        kept.extend(sobra)
        covered.extend(corte)
    return kept, covered


def _without(pending: Sequence[Match], removed: Sequence[Match]) -> list[Match]:
    """Candidatos que ficaram depois de remover os indicados."""
    ids = {id(m) for m in removed}
    return [m for m in pending if id(m) not in ids]


def _resolve_group(
    group: Sequence[Match],
    anchors: Sequence[Match],
    default_delta: int,
) -> tuple[list[Match], list[Unresolved], list[Match]]:
    """Escolhe os vencedores do grupo pela ordem e apara as perdedoras."""
    pending = list(group)
    winners: list[Match] = []
    unresolved: list[Unresolved] = []
    discarded: list[Match] = []
    while pending:
        tied = _best(pending, anchors, default_delta)
        blocked = _blocked(tied)
        if blocked:
            unresolved.extend(_regions(blocked))
            discarded.extend(blocked)
            pending = _without(pending, blocked)
            old_blocks = _blocks(blocked, "old")
            new_blocks = _blocks(blocked, "new")
            pending, covered = _trim_all(pending, old_blocks, new_blocks)
            discarded.extend(covered)
            pending.sort(key=match_sort_key)
            continue
        winner = tied[0]
        winners.append(winner)
        pending = _without(pending, [winner])
        old_block = ((winner.piece.old_in, winner.piece.old_out),)
        new_block = ((winner.piece.new_in, winner.piece.new_out),)
        pending, covered = _trim_all(pending, old_block, new_block)
        discarded.extend(covered)
        pending.sort(key=match_sort_key)
    return winners, unresolved, discarded


def resolve_reuse(
    matches: Sequence[Match],
    old: CutIR,
    new: CutIR,
    report: Report,
) -> Resolution:
    """Resolve por ordem as pecas que se conflitam e devolve as que casam."""
    groups = [group for group in _groups(matches) if len(group) > 1]
    anchors, valid_anchors = _anchors(matches, groups)
    default_delta = new.start_frames - old.start_frames
    winners: list[Match] = []
    unresolved: list[Unresolved] = []
    discarded: list[Match] = []
    for group in groups:
        found = _resolve_group(group, valid_anchors, default_delta)
        winners.extend(found[0])
        unresolved.extend(found[1])
        discarded.extend(found[2])
    winners.extend(anchors)
    winners.sort(key=match_sort_key)
    resolution = Resolution(winners, unresolved, discarded, len(groups))
    _report(resolution, report, old.rate)
    return resolution


def _timecode(start: int, end: int, rate: TimecodeRate) -> str:
    """Intervalo de frames escrito com rotulos de timecode."""
    return f"{frames_to_label(start, rate)} -> {frames_to_label(end, rate)}"


def _piece_text(match: Match, rate: TimecodeRate) -> str:
    """Peca descartada escrita com os rotulos de timecode dos dois cortes."""
    piece = match.piece
    old = _timecode(piece.old_in, piece.old_out, rate)
    new = _timecode(piece.new_in, piece.new_out, rate)
    return f"old {old} -> new {new}"


def _report(resolution: Resolution, report: Report, rate: TimecodeRate) -> None:
    """Escreve no relatorio o resultado da resolucao por ordem."""
    if resolution.groups:
        report.info(
            "RECONFORM_REUSE_RESOLVED",
            "the repeated shots were resolved by the order of the shots",
            {
                "groups": resolution.groups,
                "discarded": len(resolution.discarded),
                "pieces": [_piece_text(d, rate) for d in resolution.discarded],
            },
        )
    for item in resolution.unresolved:
        report.warning(
            "RECONFORM_REUSE_UNRESOLVED",
            "the same shot is used more than once and the order does not resolve it",
            {
                "old_timecode": _timecode(item.old_in, item.old_out, rate),
                "new_timecode": _timecode(item.new_in, item.new_out, rate),
                "source": item.source or "",
            },
        )
