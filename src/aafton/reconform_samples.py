"""Mapa temporal em samples para reconformacao de clips do Live.

Converte um TimeMap (em frames na taxa do corte) para fronteiras e intervalos
em samples inteiros, preservando a semantica de leader, segmentos, trechos
removidos (saiu) e tail.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from .reconform_map import Saiu, Segment, TimeMap
from .timecode import TimecodeRate, frames_to_samples_exact, quantize_samples


@dataclass(frozen=True)
class SampleSegment:
    """Segmento continuo mapeado do corte antigo para o novo, em samples."""

    old_in: int
    old_out: int
    new_in: int

    @property
    def delta(self) -> int:
        """Deslocamento em samples do corte antigo para o novo."""
        return self.new_in - self.old_in


@dataclass(frozen=True)
class SampleSaiu:
    """Trecho removido do corte antigo, em samples."""

    old_in: int
    old_out: int
    splice_new: int
    # A cauda e o material depois do ultimo evento antigo: o seu alcance real
    # e o do material do set.
    cauda: bool = False


@dataclass(frozen=True)
class _Interval:
    """Intervalo elementar disjunto dentro do dominio coberto pelo mapa."""

    old_in: int
    old_out: int
    kind: str
    value: int
    saiu_origin: int = 0


def _frame_to_samples(frame: int, rate: TimecodeRate, sample_rate: int) -> int:
    """Converte indice ou deslocamento de frames para samples inteiros."""
    samples, _residuo = quantize_samples(frames_to_samples_exact(frame, rate, sample_rate))
    return samples


def _convert_segments(
    segments: Sequence[Segment],
    rate: TimecodeRate,
    sample_rate: int,
) -> tuple[SampleSegment, ...]:
    """Converte e ordena segmentos de frames para samples."""
    out: list[SampleSegment] = []
    for seg in segments:
        old_in = _frame_to_samples(seg.old_in, rate, sample_rate)
        old_out = _frame_to_samples(seg.old_out, rate, sample_rate)
        if old_out <= old_in:
            continue
        new_in = _frame_to_samples(seg.new_in, rate, sample_rate)
        out.append(SampleSegment(old_in, old_out, new_in))
    out.sort(key=lambda item: (item.old_in, item.old_out))
    return tuple(out)


def _convert_saiu(
    saiu: Sequence[Saiu],
    rate: TimecodeRate,
    sample_rate: int,
) -> tuple[SampleSaiu, ...]:
    """Converte e ordena trechos removidos de frames para samples."""
    out: list[SampleSaiu] = []
    for item in saiu:
        old_in = _frame_to_samples(item.old_in, rate, sample_rate)
        old_out = _frame_to_samples(item.old_out, rate, sample_rate)
        if old_out <= old_in:
            continue
        splice_new = _frame_to_samples(item.splice_new, rate, sample_rate)
        out.append(SampleSaiu(old_in, old_out, splice_new, item.cauda))
    out.sort(key=lambda item: (item.old_in, item.old_out))
    return tuple(out)


def _preceding_delta(segments: Sequence[SampleSegment], pos: int, fallback: int) -> int:
    """Delta do ultimo segmento com old_out <= pos, ou fallback."""
    best: SampleSegment | None = None
    for seg in segments:
        if seg.old_out <= pos and (best is None or seg.old_out > best.old_out):
            best = seg
    return best.delta if best is not None else fallback


def _classify_interval(
    start: int,
    end: int,
    segments: Sequence[SampleSegment],
    saiu: Sequence[SampleSaiu],
    leader_delta: int,
) -> _Interval:
    """Classifica um intervalo elementar dentro dos limites do mapa."""
    for item in saiu:
        if item.old_in <= start and end <= item.old_out:
            return _Interval(start, end, "saiu", item.splice_new, item.old_in)
    for seg in segments:
        if seg.old_in <= start and end <= seg.old_out:
            return _Interval(start, end, "map", seg.delta)
    return _Interval(start, end, "map", _preceding_delta(segments, start, leader_delta))


def _build_intervals(
    segments: Sequence[SampleSegment],
    saiu: Sequence[SampleSaiu],
    leader_delta: int,
) -> tuple[_Interval, ...]:
    """Constroi particao ordenada e disjunta entre o inicio e o fim do mapa."""
    bounds: set[int] = set()
    for seg in segments:
        bounds.add(seg.old_in)
        bounds.add(seg.old_out)
    for item in saiu:
        bounds.add(item.old_in)
        bounds.add(item.old_out)
    ordered = sorted(bounds)
    return tuple(
        _classify_interval(a, b, segments, saiu, leader_delta)
        for a, b in zip(ordered, ordered[1:])
        if b > a
    )


def _append_span(
    out: list[tuple[int, int, str, int]],
    abs_in: int,
    abs_out: int,
    kind: str,
    value: int,
) -> None:
    """Anexa um span, fundindo trechos 'map' contiguos com o mesmo delta."""
    if abs_out <= abs_in:
        return
    if out and kind == "map":
        prev_in, prev_out, prev_kind, prev_val = out[-1]
        if prev_kind == "map" and prev_val == value and prev_out == abs_in:
            out[-1] = (prev_in, abs_out, "map", value)
            return
    out.append((abs_in, abs_out, kind, value))


@dataclass(frozen=True)
class SampleMap:
    """Mapa temporal de reconformacao convertido para samples inteiros."""

    sample_rate: int
    segments: tuple[SampleSegment, ...]
    saiu: tuple[SampleSaiu, ...]
    leader_delta: int
    tail_delta: int
    _intervals: tuple[_Interval, ...]
    _starts: tuple[int, ...]
    _min_old_in: int | None
    _max_old_out: int | None
    # Fim do material do set, em samples: e ele que diz ate onde vai a cauda.
    fim_material: int | None = None

    @classmethod
    def from_time_map(
        cls,
        tm: TimeMap,
        sample_rate: int,
        fim_material: int | None = None,
    ) -> SampleMap:
        """Converte um TimeMap em frames para SampleMap na taxa de amostragem."""
        if sample_rate <= 0:
            raise ValueError(f"sample_rate must be positive, got {sample_rate}")
        tm.verify()
        segments = _convert_segments(tm.segments, tm.rate, sample_rate)
        saiu = _convert_saiu(tm.saiu, tm.rate, sample_rate)
        leader_delta = _frame_to_samples(tm.leader_delta, tm.rate, sample_rate)
        tail_delta = _frame_to_samples(tm.tail_delta, tm.rate, sample_rate)
        intervals = _build_intervals(segments, saiu, leader_delta)
        min_old = intervals[0].old_in if intervals else None
        max_old = intervals[-1].old_out if intervals else None
        return cls(
            sample_rate=sample_rate,
            segments=segments,
            saiu=saiu,
            leader_delta=leader_delta,
            tail_delta=tail_delta,
            _intervals=intervals,
            _starts=tuple(iv.old_in for iv in intervals),
            _min_old_in=min_old,
            _max_old_out=max_old,
            fim_material=fim_material,
        )

    def cauda_material(self) -> tuple[int, int, int] | None:
        """Regiao da cauda com material do set: (inicio antigo, emenda, fim novo).

        None quando o set nao tem material depois do ultimo evento do corte
        antigo: a cauda nao tira material do set, e so o material do set diz
        ate onde ela vai.
        """
        if self.fim_material is None:
            return None
        for item in self.saiu:
            if item.cauda and self.fim_material > item.old_in:
                fim = item.splice_new + (self.fim_material - item.old_in)
                return item.old_in, item.splice_new, fim
        return None

    def saiu_region(self, abs_sample: int) -> tuple[int, int, int] | None:
        """Trecho removido que contem o sample antigo: (inicio antigo, inicio e fim novos).

        O inicio antigo identifica o trecho; dois trechos diferentes podem ter a
        mesma emenda e a mesma duracao no corte novo. A cauda tem o alcance do
        material do set, e nao o do mapa.
        """
        for item in self.saiu:
            if item.old_in <= abs_sample < item.old_out:
                fim = item.splice_new + (item.old_out - item.old_in)
                if item.cauda:
                    cauda = self.cauda_material()
                    if cauda is not None:
                        fim = cauda[2]
                return item.old_in, item.splice_new, fim
        return None

    def map_sample(self, abs_sample: int) -> int | None:
        """Mapeia um sample absoluto antigo para o novo, ou None se caiu em saiu."""
        if self._min_old_in is None or self._max_old_out is None:
            return abs_sample + self.leader_delta
        if abs_sample < self._min_old_in:
            return abs_sample + self.leader_delta
        if abs_sample >= self._max_old_out:
            return abs_sample + self.tail_delta
        idx = bisect_right(self._starts, abs_sample) - 1
        iv = self._intervals[idx]
        if iv.kind == "saiu":
            return None
        return abs_sample + iv.value

    def _collect_middle_spans(
        self,
        out: list[tuple[int, int, str, int]],
        abs_start: int,
        abs_end: int,
    ) -> None:
        """Preenche os spans que interceptam o dominio entre o primeiro e o ultimo limite."""
        idx = max(0, bisect_right(self._starts, abs_start) - 1)
        while idx < len(self._intervals):
            iv = self._intervals[idx]
            if iv.old_in >= abs_end:
                break
            lo = max(abs_start, iv.old_in)
            hi = min(abs_end, iv.old_out)
            if hi > lo:
                val = iv.value if iv.kind == "map" else iv.value + (lo - iv.saiu_origin)
                _append_span(out, lo, hi, iv.kind, val)
            idx += 1

    def spans(self, abs_start: int, abs_end: int) -> list[tuple[int, int, str, int]]:
        """Divide [abs_start, abs_end) nas fronteiras de segmentos e trechos saiu."""
        if abs_end <= abs_start:
            return []
        if self._min_old_in is None or self._max_old_out is None:
            return [(abs_start, abs_end, "map", self.leader_delta)]
        out: list[tuple[int, int, str, int]] = []
        if abs_start < self._min_old_in:
            _append_span(out, abs_start, min(abs_end, self._min_old_in), "map", self.leader_delta)
        self._collect_middle_spans(out, abs_start, abs_end)
        if abs_end > self._max_old_out:
            _append_span(out, max(abs_start, self._max_old_out), abs_end, "map", self.tail_delta)
        return out
