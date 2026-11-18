"""Testes unitarios da comparacao de cortes e mapa temporal da reconformacao."""

from __future__ import annotations

from fractions import Fraction

import pytest

from aafton.cut_ir import CutEvent, CutIR
from aafton.reconform_diff import diff_cuts
from aafton.reconform_map import Lacuna, Saiu, Segment, TimeMap
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

RATE_24 = TimecodeRate(24, 1)


def _clip(
    idx: int,
    source: str | None,
    rec_in: int,
    rec_out: int,
    src_in: int,
    src_out: int,
    reel: str | None = None,
    trans: int = 0,
    speed: Fraction | None = Fraction(1),
    whole: bool = False,
) -> CutEvent:
    """Cria um CutEvent de clip em frames para testes."""
    return CutEvent(
        idx, "clip", source, reel, src_in, src_out, rec_in, rec_out, None, trans, speed, whole
    )


def _gap(idx: int, rec_in: int, rec_out: int) -> CutEvent:
    """Cria um CutEvent de lacuna em V1 para testes."""
    return CutEvent(idx, "gap", None, None, None, None, rec_in, rec_out)


def _cut(events: list[CutEvent], path: str = "cut.edl", start: int = 0) -> CutIR:
    """Constroi um CutIR direto em memoria na taxa de 24 fps."""
    return CutIR(path=path, rate=RATE_24, start_frames=start, events=events)


def test_identical_cuts_identity_map() -> None:
    """Cortes identicos nao geram mudancas e preservam mapa identidade."""
    events = [
        _clip(1, "A", 0, 100, 1000, 1100),
        _gap(2, 100, 120),
        _clip(3, "B", 120, 220, 2000, 2100),
    ]
    cl = diff_cuts(_cut(events, path="old.edl"), _cut(events, path="new.edl"), Report())
    assert cl is not None
    assert cl.changes == []
    assert cl.counts == {}
    assert not cl.ambiguous
    assert cl.time_map is not None
    samples = (-10, 0, 50, 110, 150, 250)
    assert [cl.time_map.map_frame(f) for f in samples] == list(samples)
    assert (cl.time_map.leader_delta, cl.time_map.tail_delta) == (0, 0)


def test_swap_of_two_shots_and_three_shot_move() -> None:
    """Troca de ordem gera move e TimeMap nao monotonico valido."""
    old2 = _cut([_clip(1, "A", 0, 100, 100, 200), _clip(2, "B", 100, 200, 300, 400)])
    new2 = _cut([_clip(1, "B", 0, 100, 300, 400), _clip(2, "A", 100, 200, 100, 200)])
    cl2 = diff_cuts(old2, new2, Report())
    assert cl2 is not None
    assert not cl2.ambiguous
    assert cl2.counts.get("move") == 1
    assert cl2.time_map is not None
    assert (cl2.time_map.map_frame(20), cl2.time_map.map_frame(120)) == (120, 20)
    old_ev3 = [
        _clip(1, "A", 0, 100, 1, 101),
        _clip(2, "B", 100, 200, 201, 301),
        _clip(3, "C", 200, 300, 401, 501),
    ]
    new_ev3 = [
        _clip(1, "B", 0, 100, 201, 301),
        _clip(2, "C", 100, 200, 401, 501),
        _clip(3, "A", 200, 300, 1, 101),
    ]
    cl3 = diff_cuts(_cut(old_ev3), _cut(new_ev3), Report())
    assert cl3 is not None
    assert [c.source for c in cl3.changes if c.kind == "move"] == ["A"]


def test_insert_in_middle_shift_and_lacuna() -> None:
    """Insercao no meio desloca planos posteriores como shift e cria Lacuna."""
    old_ev = [_clip(1, "A", 0, 100, 100, 200), _clip(2, "B", 100, 200, 300, 400)]
    new_ev = [
        _clip(1, "A", 0, 100, 100, 200),
        _clip(2, "X", 100, 160, 500, 560),
        _clip(3, "B", 160, 260, 300, 400),
    ]
    cl = diff_cuts(_cut(old_ev), _cut(new_ev), Report())
    assert cl is not None
    assert cl.counts == {"insert": 1, "shift": 1}
    assert cl.time_map is not None
    assert cl.time_map.lacunas == [Lacuna(100, 160)]
    assert cl.time_map.tail_delta == 60
    assert cl.time_map.map_frame(150) == 210


def test_delete_in_middle_saiu_with_splice_point() -> None:
    """Remocao no meio gera delete e Saiu com splice_new no fim do segmento anterior."""
    old_ev = [
        _clip(1, "A", 0, 100, 100, 200),
        _clip(2, "X", 100, 150, 200, 250),
        _clip(3, "B", 150, 250, 300, 400),
    ]
    new_ev = [_clip(1, "A", 0, 100, 100, 200), _clip(2, "B", 100, 200, 300, 400)]
    cl = diff_cuts(_cut(old_ev), _cut(new_ev), Report())
    assert cl is not None
    assert cl.counts["delete"] == 1
    assert cl.time_map is not None
    assert cl.time_map.saiu == [Saiu(100, 150, 100)]
    assert cl.time_map.map_frame(120) is None
    assert cl.time_map.map_frame(175) == 125


def test_delete_first_shot_keeps_leader_delta_zero() -> None:
    """Remocao do primeiro plano preserva leader_delta=0 sem mapeamento negativo."""
    old = _cut([_clip(1, "A", 100, 200, 100, 200), _clip(2, "B", 200, 300, 300, 400)])
    new = _cut([_clip(1, "B", 0, 100, 300, 400)])
    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.time_map is not None
    assert cl.time_map.leader_delta == 0
    assert cl.time_map.saiu == [Saiu(100, 200, 0)]
    assert cl.time_map.map_frame(50) == 50
    assert cl.time_map.map_frame(150) is None
    assert cl.time_map.map_frame(250) == 50


def test_trim_head_and_trim_tail_lost_and_gained() -> None:
    """Aparos de cabeca e cauda registram lost/gained e contagem de frames."""
    old = _cut([_clip(1, "A", 0, 100, 100, 200), _clip(2, "B", 100, 200, 500, 600)])
    new = _cut([_clip(1, "A", 0, 80, 120, 200), _clip(2, "B", 80, 210, 500, 630)])
    cl = diff_cuts(old, new, Report())
    assert cl is not None
    heads = [c for c in cl.changes if c.kind == "trim_head"]
    tails = [c for c in cl.changes if c.kind == "trim_tail"]
    assert (len(heads), "lost 20 frames" in heads[0].detail) == (1, True)
    assert (len(tails), "gained 30 frames" in tails[0].detail) == (1, True)
    assert cl.time_map is not None
    assert cl.time_map.saiu == [Saiu(0, 20, 0)]
    assert cl.time_map.lacunas == [Lacuna(180, 210)]


def test_shot_split_by_inserted_coverage_not_ambiguous() -> None:
    """Plano dividido por insercao no meio produz pecas contiguas sem ambiguidade."""
    old_ev = [_clip(1, "A", 0, 100, 100, 200)]
    new_ev = [
        _clip(1, "A", 0, 40, 100, 140),
        _clip(2, "COV", 40, 70, 800, 830),
        _clip(3, "A", 70, 130, 140, 200),
    ]
    cl = diff_cuts(_cut(old_ev), _cut(new_ev), Report())
    assert cl is not None
    assert not cl.ambiguous
    assert cl.counts == {"insert": 1, "shift": 1}
    assert cl.time_map is not None
    assert cl.time_map.lacunas == [Lacuna(40, 70)]
    assert cl.time_map.map_frame(20) == 20
    assert cl.time_map.map_frame(60) == 90


def test_same_shot_used_twice_is_resolved_by_order() -> None:
    """Mesmo trecho de origem reutilizado no corte novo e resolvido pela ordem."""
    old = _cut([_clip(1, "A", 0, 100, 100, 200)])
    new = _cut([_clip(1, "A", 0, 100, 100, 200), _clip(2, "A", 100, 200, 100, 200)])
    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.ambiguous is False
    assert cl.counts == {"insert": 1}
    assert cl.time_map is not None


def test_gap_kept_and_shrunk_overflow_to_saiu() -> None:
    """Gap mantido vira Segment e gap encolhido envia excedente para Saiu sem overlap."""
    old_ev = [
        _clip(1, "A", 0, 100, 100, 200),
        _gap(2, 100, 150),
        _clip(3, "B", 150, 250, 300, 400),
    ]
    kept = diff_cuts(_cut(old_ev), _cut(old_ev), Report())
    assert kept is not None
    assert kept.time_map is not None
    assert Segment(100, 150, 100) in kept.time_map.segments
    new_ev = [
        _clip(1, "A", 0, 100, 100, 200),
        _gap(2, 100, 120),
        _clip(3, "B", 120, 220, 300, 400),
    ]
    cl = diff_cuts(_cut(old_ev), _cut(new_ev), Report())
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(100, 120, 100) in cl.time_map.segments
    assert cl.time_map.saiu == [Saiu(120, 150, 120)]
    assert (cl.time_map.map_frame(110), cl.time_map.map_frame(135)) == (110, None)
    assert (cl.time_map.map_frame(160), cl.time_map.map_frame(300)) == (130, 270)


def test_leading_gap_and_tail_after_last_event() -> None:
    """Gap inicial usa leader_delta e cauda apos ultimo evento usa tail_delta."""
    old_ev = [_gap(1, 0, 50), _clip(2, "A", 50, 150, 100, 200)]
    new_ev = [_gap(1, 100, 130), _clip(2, "A", 130, 230, 100, 200)]
    cl = diff_cuts(_cut(old_ev, start=0), _cut(new_ev, start=100), Report())
    assert cl is not None
    assert cl.time_map is not None
    assert cl.time_map.leader_delta == 100
    assert Segment(0, 30, 100) in cl.time_map.segments
    assert cl.time_map.saiu == [Saiu(30, 50, 130)]
    assert (cl.time_map.map_frame(10), cl.time_map.map_frame(40)) == (110, None)
    assert cl.time_map.map_frame(200) == 280


def test_transition_only_and_source_key_matching() -> None:
    """Mudanca de transition_frames no mesmo record_in casa somente por source key."""
    old = _cut([_clip(1, "CAM1", 10, 110, 100, 200, reel="R1", trans=0)])
    new = _cut([_clip(1, "CAM1", 10, 110, 100, 200, reel="R9", trans=24)])
    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {"transition": 1}
    none_old = _cut([_clip(1, None, 0, 40, 0, 40)])
    none_new = _cut([_clip(1, None, 0, 40, 0, 40)])
    cl_none = diff_cuts(none_old, none_new, Report())
    assert cl_none is not None
    assert cl_none.counts == {"delete": 1, "insert": 1}


def test_rate_mismatch_reports_error_and_returns_none() -> None:
    """Taxas divergentes emitem RECONFORM_RATE_MISMATCH e retornam None em modo permissivo."""
    old = _cut([_clip(1, "A", 0, 100, 100, 200)])
    new = CutIR("new.edl", TimecodeRate(25, 1), 0, [_clip(1, "A", 0, 100, 100, 200)])
    permissive = Report(strict=False)
    assert diff_cuts(old, new, permissive) is None
    assert permissive.incomplete
    assert permissive.issues[0].code == "RECONFORM_RATE_MISMATCH"
    df = TimecodeRate(30000, 1001, drop_frame=True)
    ndf = TimecodeRate(30000, 1001, drop_frame=False)
    with pytest.raises(StrictModeError, match="RECONFORM_RATE_MISMATCH"):
        diff_cuts(CutIR("a.edl", df, 0, []), CutIR("b.edl", ndf, 0, []), Report(strict=True))


def test_timemap_verify_overlap_and_valid_reorder() -> None:
    """verify() falha em segmento vazio ou sobreposicao e aceita reordenacao valida."""
    with pytest.raises(ValueError, match="RECONFORM_MAP_OVERLAP"):
        TimeMap(RATE_24, segments=[Segment(10, 10, 10)]).verify()
    with pytest.raises(ValueError, match="RECONFORM_MAP_OVERLAP"):
        TimeMap(RATE_24, segments=[Segment(0, 50, 0), Segment(40, 80, 100)]).verify()
    with pytest.raises(ValueError, match="RECONFORM_MAP_OVERLAP"):
        TimeMap(RATE_24, segments=[Segment(0, 50, 0), Segment(60, 100, 30)]).verify()
    TimeMap(RATE_24, segments=[Segment(0, 50, 100), Segment(50, 100, 0)]).verify()
