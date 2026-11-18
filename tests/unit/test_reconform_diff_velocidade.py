"""Testes unitarios do casamento de planos por frame, com a velocidade do plano.

O material de origem do plano a 2x anda dois frames de origem por frame de
gravacao: a origem do frame de gravacao r e `source_in + (r - record_in) * s`.
"""

from __future__ import annotations

from fractions import Fraction

from aafton.cut_ir import CutIR
from aafton.reconform_diff import diff_cuts
from aafton.reconform_diff_velocidade import pair_piece
from aafton.reconform_map import Segment
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_reconform_diff import _clip

RATE_24 = TimecodeRate(24, 1)


def _cut(events: list) -> CutIR:
    """Constroi um CutIR direto em memoria na taxa de 24 fps."""
    return CutIR(path="cut.edl", rate=RATE_24, start_frames=0, events=events)


def test_2x_com_apo_em_ambas_as_pontas_casa_o_trecho_comum() -> None:
    """Plano a 2x com aparo na cabeca e na cauda casa so o material comum."""
    old = _cut([_clip(1, "A", 0, 100, 1000, 1200, speed=Fraction(2))])
    new = _cut([_clip(1, "A", 0, 100, 1020, 1220, speed=Fraction(2))])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(10, 100, 0) in cl.time_map.segments
    heads = [c for c in cl.changes if c.kind == "trim_head"]
    tails = [c for c in cl.changes if c.kind == "trim_tail"]
    assert len(heads) == 1 and "lost 10 frames at head" in heads[0].detail
    assert len(tails) == 1 and "gained 10 frames at tail" in tails[0].detail
    assert cl.counts == {"delete": 1, "insert": 1, "shift": 1, "trim_head": 1, "trim_tail": 1}


def test_2x_contra_1x_vira_delete_e_insert_com_velocidade_mudada() -> None:
    """Mesma origem com velocidades diferentes nao casa e vira delete e insert."""
    old = _cut([_clip(1, "A", 0, 100, 1000, 1200, speed=Fraction(2))])
    new = _cut([_clip(1, "A", 0, 100, 1000, 1100, speed=Fraction(1))])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {"delete": 1, "insert": 1}
    assert [c.kind for c in cl.changes] == ["delete", "insert"]
    assert [c.detail for c in cl.changes] == ["speed changed", "speed changed"]


def test_origem_sem_cruzamento_nao_diz_velocidade_mudada() -> None:
    """Mesma origem com material de outra parte do arquivo nao vira 'speed changed'."""
    old = _cut([_clip(1, "A004", 0, 100, 1000, 1100)])
    new = _cut([_clip(1, "A004", 0, 100, 5000, 5200, speed=Fraction(2))])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {"delete": 1, "insert": 1}
    assert [c.detail for c in cl.changes] == ["deleted 100 frames", "inserted 100 frames"]


def test_meio_x_movido_inteiro_casa_o_plano_inteiro() -> None:
    """Plano a 0.5x so pode ser movido inteiro e casa como plano inteiro."""
    meio = Fraction(1, 2)
    old = _cut([_clip(1, "A", 0, 100, 1000, 1100, speed=meio, whole=True)])
    new = _cut([_clip(1, "A", 50, 150, 1000, 1100, speed=meio, whole=True)])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(0, 100, 50) in cl.time_map.segments
    assert cl.counts == {"shift": 1}


def test_rampa_movida_inteira_casa_e_rampa_apara_vira_delete_e_insert() -> None:
    """Rampa movida inteira casa como plano; rampa aparada nao casa com nada."""
    old = _cut([_clip(1, "A", 0, 100, 1000, 1100, speed=None, whole=True)])
    movida = _cut([_clip(1, "A", 50, 150, 1000, 1100, speed=None, whole=True)])

    cl = diff_cuts(old, movida, Report())
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(0, 100, 50) in cl.time_map.segments
    assert cl.counts == {"shift": 1}

    aparada = _cut([_clip(1, "A", 0, 80, 1000, 1080, speed=None, whole=True)])
    cl2 = diff_cuts(old, aparada, Report())
    assert cl2 is not None
    assert cl2.counts == {"delete": 1, "insert": 1}
    assert [c.detail for c in cl2.changes] == ["deleted 100 frames", "inserted 80 frames"]


def test_plano_inteiro_contra_plano_normal_vira_delete_e_insert() -> None:
    """Par com um so plano inteiro nao casa e a lista diz 'speed changed'."""
    old = _cut([_clip(1, "A", 0, 100, 1000, 1100)])
    new = _cut([_clip(1, "A", 0, 100, 1000, 1100, speed=None, whole=True)])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {"delete": 1, "insert": 1}
    assert [c.detail for c in cl.changes] == ["speed changed", "speed changed"]


def test_4x_identico_casa_o_plano_e_nao_muda_nada() -> None:
    """O mesmo plano a 4x nos dois cortes casa inteiro e nao gera mudanca."""
    old = _cut([_clip(1, "A", 0, 100, 0, 400, speed=Fraction(4))])
    new = _cut([_clip(1, "A", 0, 100, 0, 400, speed=Fraction(4))])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {}
    assert cl.time_map is not None
    assert cl.time_map.segments == [Segment(0, 100, 0)]
    assert cl.time_map.segments[0].delta == 0


def test_plano_inteiro_a_2x_identico_casa_o_plano_inteiro() -> None:
    """O mesmo plano inteiro a 2x nos dois cortes casa o plano inteiro."""
    old = _cut([_clip(1, "A", 0, 100, 0, 200, speed=Fraction(2), whole=True)])
    new = _cut([_clip(1, "A", 0, 100, 0, 200, speed=Fraction(2), whole=True)])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {}
    assert cl.time_map is not None
    assert Segment(0, 100, 0) in cl.time_map.segments


def test_plano_inteiro_a_2x_contra_plano_normal_a_2x_vira_delete_e_insert() -> None:
    """Plano inteiro a 2x contra plano normal a 2x nao casa e vira delete e insert."""
    old = _cut([_clip(1, "A", 0, 100, 0, 200, speed=Fraction(2), whole=True)])
    new = _cut([_clip(1, "A", 0, 100, 0, 200, speed=Fraction(2))])

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.counts == {"delete": 1, "insert": 1}
    assert [c.detail for c in cl.changes] == ["speed changed", "speed changed"]


def test_plano_inteiro_sem_duracao_nao_casa() -> None:
    """Plano inteiro com duracao zero nao casa: a peca seria vazia."""
    old = _cut([_clip(1, "A", 0, 0, 1000, 1000, speed=None, whole=True)])
    new = _cut([_clip(1, "A", 0, 0, 1000, 1000, speed=None, whole=True)])

    assert pair_piece(old.events[0], new.events[0]) is None
    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.time_map is not None
    assert not any(iss.code == "RECONFORM_MAP_OVERLAP" for iss in report.issues)
