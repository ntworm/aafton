"""Mapa temporal da reconformacao entre duas listas de corte (CutIR).

Opera exclusivamente em frames inteiros na taxa do corte, sem I/O nem
integracao com o Ableton Live.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .cut_ir import CutEvent, CutIR
from .timecode import TimecodeRate

_MIXED_SOURCE = object()


@dataclass(frozen=True)
class Segment:
    """Trecho continuo mapeado do corte antigo para o corte novo."""

    old_in: int
    old_out: int
    new_in: int

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
        """Duracao do segmento em frames."""
        return self.old_out - self.old_in


@dataclass(frozen=True)
class Saiu:
    """Trecho do corte antigo removido no corte novo."""

    old_in: int
    old_out: int
    splice_new: int
    # A cauda e o material depois do ultimo evento do corte antigo: o mapa nao
    # sabe o tamanho dela, e o set define.
    cauda: bool = False


@dataclass(frozen=True)
class Lacuna:
    """Trecho de material novo sem cobertura de pecas antigas."""

    new_in: int
    new_out: int


@dataclass(frozen=True)
class _Cauda:
    """Regiao do corte novo que a cauda do corte antigo ocupa."""

    inicio: int
    # Fim da regiao; None quando a cauda pode ocupar o resto do corte novo.
    fim: int | None


def _check_non_empty(segments: Sequence[Segment]) -> None:
    """Garante que todo segmento tem duracao positiva."""
    for seg in segments:
        if seg.old_out <= seg.old_in:
            raise ValueError("RECONFORM_MAP_OVERLAP")


def _check_side_overlap(
    segments: Sequence[Segment],
    bounds: Callable[[Segment], tuple[int, int]],
) -> None:
    """Garante que os intervalos projetados de um lado nao se sobrepoem."""
    ordered = sorted((bounds(seg) for seg in segments), key=lambda span: (span[0], span[1]))
    for prev, curr in zip(ordered, ordered[1:]):
        if curr[0] < prev[1]:
            raise ValueError("RECONFORM_MAP_OVERLAP")


def _old_bounds(segments: Sequence[Segment], saiu: Sequence[Saiu]) -> tuple[int, int]:
    """Menor old_in e maior old_out entre segmentos e trechos removidos."""
    starts = [seg.old_in for seg in segments] + [item.old_in for item in saiu]
    ends = [seg.old_out for seg in segments] + [item.old_out for item in saiu]
    return min(starts), max(ends)


def _in_saiu(saiu: Sequence[Saiu], old_frame: int) -> bool:
    """Verdadeiro quando o frame cai dentro de um trecho removido."""
    return any(item.old_in <= old_frame < item.old_out for item in saiu)


def _map_segment(segments: Sequence[Segment], old_frame: int) -> int | None:
    """Mapeia o frame pelo segmento que o contem, se houver."""
    for seg in segments:
        if seg.old_in <= old_frame < seg.old_out:
            return seg.new_in + (old_frame - seg.old_in)
    return None


def _preceding_delta(segments: Sequence[Segment], old_frame: int, fallback: int) -> int:
    """Delta do ultimo segmento anterior ao frame na ordem antiga."""
    best = _preceding_segment(segments, old_frame)
    return best.delta if best is not None else fallback


@dataclass(frozen=True)
class TimeMap:
    """Mapa de tempo entre o corte antigo e o corte novo, em frames."""

    rate: TimecodeRate
    segments: list[Segment] = field(default_factory=list)
    saiu: list[Saiu] = field(default_factory=list)
    lacunas: list[Lacuna] = field(default_factory=list)
    leader_delta: int = 0
    tail_delta: int = 0

    def verify(self) -> None:
        """Valida ausencia de segmentos vazios e de sobreposicoes."""
        _check_non_empty(self.segments)
        _check_side_overlap(self.segments, lambda seg: (seg.old_in, seg.old_out))
        _check_side_overlap(self.segments, lambda seg: (seg.new_in, seg.new_out))

    def map_frame(self, old_frame: int) -> int | None:
        """Mapeia um frame do corte antigo para o novo, ou None se saiu."""
        if not self.segments and not self.saiu:
            return old_frame + self.leader_delta
        min_old_in, max_old_out = _old_bounds(self.segments, self.saiu)
        if old_frame < min_old_in:
            return old_frame + self.leader_delta
        if old_frame >= max_old_out:
            return old_frame + self.tail_delta
        if _in_saiu(self.saiu, old_frame):
            return None
        mapped = _map_segment(self.segments, old_frame)
        if mapped is not None:
            return mapped
        return old_frame + _preceding_delta(self.segments, old_frame, self.leader_delta)


def _merge_covered(covered: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Normaliza e funde intervalos cobertos ordenados."""
    ordered = sorted((span for span in covered if span[1] > span[0]), key=lambda s: (s[0], s[1]))
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_from_clip(
    clip: CutEvent,
    merged_cov: Sequence[tuple[int, int]],
) -> list[tuple[int, int, str | None]]:
    """Subtrai intervalos cobertos do trecho de um evento de clip."""
    raw: list[tuple[int, int, str | None]] = []
    cursor = clip.record_in
    for cov_in, cov_out in merged_cov:
        if cov_out <= cursor:
            continue
        if cov_in >= clip.record_out:
            break
        if cov_in > cursor:
            raw.append((cursor, cov_in, clip.source))
        cursor = max(cursor, cov_out)
    if cursor < clip.record_out:
        raw.append((cursor, clip.record_out, clip.source))
    return raw


def _merge_uncovered(
    spans: Sequence[tuple[int, int, str | None]],
) -> list[tuple[int, int, str | None]]:
    """Funde trechos descobertos contiguos ou sobrepostos em spans maximais."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda item: (item[0], item[1]))
    merged: list[list[Any]] = [[ordered[0][0], ordered[0][1], ordered[0][2]]]
    for start, end, source in ordered[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
            if source != last[2]:
                last[2] = _MIXED_SOURCE
        else:
            merged.append([start, end, source])
    return [(item[0], item[1], None if item[2] is _MIXED_SOURCE else item[2]) for item in merged]


def uncovered_clip_spans(
    events: Sequence[CutEvent],
    covered: Sequence[tuple[int, int]],
) -> list[tuple[int, int, str | None]]:
    """Retorna spans maximais de clips nao cobertos pelos intervalos dados."""
    merged_cov = _merge_covered(covered)
    raw: list[tuple[int, int, str | None]] = []
    for ev in events:
        if ev.kind == "clip" and ev.record_out > ev.record_in:
            raw.extend(_subtract_from_clip(ev, merged_cov))
    return _merge_uncovered(raw)


def _preceding_segment(segments: Sequence[Segment], frame: int) -> Segment | None:
    """Ultimo segmento na ordem antiga com old_out <= frame."""
    best: Segment | None = None
    for seg in segments:
        if seg.old_out <= frame and (best is None or seg.old_out > best.old_out):
            best = seg
    return best


def _next_new_limit(segments: Sequence[Segment], min_new_in: int) -> int | None:
    """Menor new_in >= min_new_in entre os segmentos existentes."""
    candidates = [seg.new_in for seg in segments if seg.new_in >= min_new_in]
    return min(candidates) if candidates else None


def _map_one_gap(
    gap: CutEvent,
    segments: Sequence[Segment],
    leader_delta: int,
    new_start: int,
    cauda: _Cauda | None,
) -> tuple[Segment | None, Saiu | None]:
    """Mapeia um evento de gap do corte antigo no espaco livre do corte novo.

    O trecho de gap entra no corte novo onde o plano anterior cai, ate o
    proximo plano. Ele nao passa da regiao da cauda: a cauda vem depois de
    todo o material antigo, e um gap que caisse nela entraria por cima do
    material dela.
    """
    prev = _preceding_segment(segments, gap.record_in)
    if prev is not None:
        delta = prev.delta
        limit = _next_new_limit(segments, prev.new_out)
        fallback_splice = prev.new_out
    else:
        delta = leader_delta
        limit = min((seg.new_in for seg in segments), default=None)
        fallback_splice = new_start
    gap_len = gap.record_out - gap.record_in
    gap_new_in = gap.record_in + delta
    available = gap_len if limit is None else max(0, limit - gap_new_in)
    espaco_cauda = _espaco_ate_a_cauda(cauda, gap_new_in)
    if espaco_cauda is not None:
        available = min(available, espaco_cauda)
    fit = min(gap_len, available)
    if fit == gap_len:
        return Segment(gap.record_in, gap.record_out, gap_new_in), None
    if fit > 0:
        seg = Segment(gap.record_in, gap.record_in + fit, gap_new_in)
        return seg, Saiu(gap.record_in + fit, gap.record_out, seg.new_out)
    return None, Saiu(gap.record_in, gap.record_out, fallback_splice)


def _build_gap_segments(
    old_events: Sequence[CutEvent],
    segments: list[Segment],
    leader_delta: int,
    new_start: int,
    cauda: _Cauda | None,
) -> tuple[list[Segment], list[Saiu]]:
    """Processa eventos de gap do corte antigo em ordem cronologica."""
    ordered = sorted(old_events, key=lambda ev: (ev.record_in, ev.record_out, ev.index))
    all_segments = list(segments)
    gap_saiu: list[Saiu] = []
    for ev in ordered:
        if ev.kind == "gap" and ev.record_out > ev.record_in:
            seg, overflow = _map_one_gap(ev, all_segments, leader_delta, new_start, cauda)
            if seg is not None:
                all_segments.append(seg)
                all_segments.sort(key=lambda item: (item.old_in, item.old_out))
            if overflow is not None:
                gap_saiu.append(overflow)
    return all_segments, gap_saiu


# Alcance da cauda quando ela precisa ser limitada: o material depois do ultimo
# evento antigo e finito no set, e este alcance (cerca de 4 dias a 25 fps) o
# cobre com folga. O alcance do material e o do set: quem aplica a reconformacao
# mede a cauda pelo material que ele tem.
_CAUDA_FRAMES = 10_000_000


def _espaco_livre(segmentos: Sequence[Segment], inicio: int) -> int | None:
    """Espaco livre do corte novo a partir do frame informado, em frames.

    None quando nenhum segmento comeca depois do frame: a cauda pode ocupar o
    resto do corte novo. Zero quando um segmento ja cobre esse frame: a cauda
    nao cabe.
    """
    livre: int | None = None
    for seg in segmentos:
        if seg.new_in <= inicio < seg.new_out:
            return 0
        if seg.new_in > inicio and (livre is None or seg.new_in - inicio < livre):
            livre = seg.new_in - inicio
    return livre


def _regiao_da_cauda(segmentos: Sequence[Segment], inicio: int) -> _Cauda | None:
    """Regiao do corte novo que a cauda ocupa, ou None quando ela nao cabe.

    A cauda entra no corte novo no frame informado e ocupa o espaco livre que
    encontra: ate o proximo segmento, ou o resto do corte novo quando nenhum
    segmento comeca depois. Quando um segmento ja cobre esse frame, a cauda nao
    cabe e nao ocupa nada.
    """
    livre = _espaco_livre(segmentos, inicio)
    if livre is None:
        return _Cauda(inicio, None)
    if livre == 0:
        return None
    return _Cauda(inicio, inicio + livre)


def _espaco_ate_a_cauda(cauda: _Cauda | None, inicio: int) -> int | None:
    """Espaco livre antes da cauda, a partir do frame informado, ou None.

    A cauda vem depois de todo o material antigo: um trecho que caia nela
    entraria por cima do material da cauda. None quando nao ha cauda, ou quando
    o trecho ja passa dela; zero quando o trecho cai dentro da regiao dela.
    """
    if cauda is None:
        return None
    if cauda.fim is not None and inicio >= cauda.fim:
        return None
    if inicio >= cauda.inicio:
        return 0
    return cauda.inicio - inicio


def _limitar_cauda(
    segments: list[Segment],
    saiu: list[Saiu],
    tail_delta: int,
    pecas: Sequence[Segment],
    fim_antigo: int,
) -> None:
    """A cauda segue o ultimo segmento so ate o espaco livre no corte novo.

    O material depois do ultimo evento antigo cai onde o ultimo segmento cai
    no corte novo. Quando o corte novo ja tem material nesse lugar, a cauda nao
    cabe: ela vai para [saiu] pela regra da emenda, como as lacunas de V1; sem
    isso a cauda cairia por cima daquele material.

    A cauda e o unico trecho removido cujo alcance o mapa nao conhece: o
    material do set depois do ultimo evento e que diz onde ela acaba.
    """
    if not segments:
        return
    inicio_novo = fim_antigo + tail_delta
    livre = _espaco_livre(pecas, inicio_novo)
    if livre is None:
        return
    if livre > 0:
        segments.append(Segment(fim_antigo, fim_antigo + livre, inicio_novo))
    fim = fim_antigo + _CAUDA_FRAMES
    saiu.append(Saiu(fim_antigo + livre, fim, inicio_novo + livre, cauda=True))
    saiu.sort(key=lambda item: (item.old_in, item.old_out))


def _fim_do_corte_antigo(old: CutIR) -> int:
    """Ultimo frame do corte antigo, onde o material da cauda do set comeca."""
    return max((ev.record_out for ev in old.events), default=0)


def build_time_map(old: CutIR, new: CutIR, pieces: Sequence[Any]) -> TimeMap:
    """Constroi e valida o TimeMap a partir das pecas casadas e dos dois cortes."""
    piece_segments = [Segment(p.old_in, p.old_out, p.new_in) for p in pieces]
    piece_segments.sort(key=lambda seg: (seg.old_in, seg.old_out))
    leader_delta = new.start_frames - old.start_frames
    new_start = new.start_frames
    # A cauda segue o ultimo plano casado do corte antigo: e por esse
    # deslocamento que ela entra no corte novo.
    tail_delta = piece_segments[-1].delta if piece_segments else leader_delta
    fim_antigo = _fim_do_corte_antigo(old)
    # A regiao da cauda e o espaco do corte novo que o material depois do
    # ultimo evento antigo ocupa: as lacunas de V1 do corte antigo nao podem
    # entrar nela, e o que sobra delas vai para [saiu].
    cauda = _regiao_da_cauda(piece_segments, fim_antigo + tail_delta)
    segments, gap_saiu = _build_gap_segments(
        old.events, piece_segments, leader_delta, new_start, cauda
    )
    old_cov = [(seg.old_in, seg.old_out) for seg in piece_segments]
    clip_saiu: list[Saiu] = []
    for start, end, _source in uncovered_clip_spans(old.events, old_cov):
        # A emenda de um trecho removido fica no fim da ultima peca casada que
        # vem antes dele no corte antigo: a lacuna de V1 no meio nao conta,
        # porque o material dela nao pertence ao trecho removido.
        prev = _preceding_segment(piece_segments, start)
        splice = prev.new_out if prev is not None else new_start
        clip_saiu.append(Saiu(start, end, splice))
    saiu = sorted(clip_saiu + gap_saiu, key=lambda item: (item.old_in, item.old_out))
    new_cov = [(seg.new_in, seg.new_out) for seg in piece_segments]
    new_uncovered = uncovered_clip_spans(new.events, new_cov)
    lacunas = [Lacuna(start, end) for start, end, _source in new_uncovered]
    _limitar_cauda(segments, saiu, tail_delta, piece_segments, fim_antigo)
    time_map = TimeMap(old.rate, segments, saiu, lacunas, leader_delta, tail_delta)
    time_map.verify()
    return time_map
