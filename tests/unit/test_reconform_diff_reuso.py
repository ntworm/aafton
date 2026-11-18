"""Testes unitarios da resolucao por ordem do material repetido.

O mesmo plano usado mais de uma vez nao recusa o corte: a ordem dos planos
decide qual das ocorrencias casa, o que sobra vira delete e insert, e o que a
ordem nao decide fica listado como nao resolvido.
"""

from __future__ import annotations

from aafton.cut_ir import CutIR
from aafton.reconform_diff import diff_cuts
from aafton.reconform_map import Lacuna, Saiu, Segment
from aafton.report import Report
from aafton.timecode import TimecodeRate, frames_to_label
from tests.unit.test_reconform_diff import _clip

RATE_24 = TimecodeRate(24, 1)


def _cut(*clips: tuple) -> CutIR:
    """Corte com os clips dados: (origem, record_in, record_out, src_in, src_out)."""
    eventos = [_clip(idx + 1, *clip) for idx, clip in enumerate(clips)]
    return CutIR(path="cut.edl", rate=RATE_24, start_frames=0, events=eventos)


def test_plano_duas_vezes_no_antigo_e_uma_no_novo_perde_a_segunda() -> None:
    """O mesmo plano duas vezes no corte antigo e uma no novo perde a segunda vez."""
    old = _cut(("A", 0, 100, 0, 100), ("A", 100, 200, 0, 100))
    new = _cut(("A", 0, 100, 0, 100))

    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.ambiguous is False
    assert cl.counts == {"delete": 1}
    assert cl.time_map is not None
    assert Segment(0, 100, 0) in cl.time_map.segments
    assert cl.time_map.saiu == [Saiu(100, 200, 100)]
    assert any(iss.code == "RECONFORM_REUSE_RESOLVED" for iss in report.issues)


def test_faixa_de_origem_sobreposta_casa_o_resto_do_trecho() -> None:
    """Faixa de origem sobreposta: o pedaco antigo [50,100) casa com [220,270)."""
    old = _cut(("A", 0, 100, 1000, 1100))
    new = _cut(("A", 0, 50, 1000, 1050), ("A", 200, 270, 1030, 1100))

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(0, 50, 0) in cl.time_map.segments
    assert Segment(50, 100, 220) in cl.time_map.segments
    assert cl.time_map.lacunas == [Lacuna(200, 220)]
    assert cl.counts.get("insert") == 1


def test_grafico_tres_vezes_nos_dois_cortes_ganha_tres_vencedores() -> None:
    """O mesmo grafico tres vezes nos dois cortes casa as tres vezes."""
    gfx = (("G", 0, 100, 0, 100), ("G", 100, 200, 0, 100), ("G", 200, 300, 0, 100))
    old = _cut(*gfx)
    new = _cut(*gfx)

    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.counts == {}
    assert cl.time_map is not None
    assert cl.time_map.map_frame(150) == 150
    assert any(iss.code == "RECONFORM_REUSE_RESOLVED" for iss in report.issues)


def test_ancora_movida_fora_da_lis_nao_da_a_referencia() -> None:
    """A ancora que saiu da LIS nao serve de referencia para o grupo do plano repetido."""
    old = _cut(
        ("A", 0, 100, 0, 100),
        ("B", 100, 200, 0, 100),
        ("C", 200, 300, 0, 100),
        ("G", 300, 400, 0, 100),
        ("G", 400, 500, 0, 100),
    )
    new = _cut(
        ("A", 0, 100, 0, 100),
        ("C", 100, 200, 0, 100),
        ("G", 200, 300, 0, 100),
        ("G", 300, 400, 0, 100),
        ("B", 400, 500, 0, 100),
    )

    cl = diff_cuts(old, new, Report())
    assert cl is not None
    assert cl.time_map is not None
    # A ancora valida e a que ficou na LIS (A e C): o grupo casa G com G.
    assert Segment(300, 400, 200) in cl.time_map.segments
    assert Segment(400, 500, 300) in cl.time_map.segments


def test_empate_fica_listado_como_nao_resolvido() -> None:
    """Candidatos empatados e sobrepostos ficam sem correspondencia e sao listados."""
    old = _cut(("X", 0, 10, 0, 10), ("Z", 100, 200, 0, 100), ("Z", 400, 500, 0, 100))
    new = _cut(("X", 250, 260, 0, 10), ("Z", 300, 400, 0, 100), ("Z", 400, 500, 0, 100))

    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.ambiguous is False
    unresolved = [c for c in cl.changes if c.kind == "unresolved"]
    assert len(unresolved) == 1
    assert (unresolved[0].old_in, unresolved[0].old_out) == (100, 200)
    assert (unresolved[0].new_in, unresolved[0].new_out) == (300, 500)
    assert unresolved[0].source == "Z"
    assert unresolved[0].detail == "same shot used more than once: not matched"

    avisos = [i for i in report.issues if i.code == "RECONFORM_REUSE_UNRESOLVED"]
    assert len(avisos) == 1
    assert avisos[0].severity == "warning"
    assert avisos[0].context["source"] == "Z"
    antigo = f"{frames_to_label(100, RATE_24)} -> {frames_to_label(200, RATE_24)}"
    novo = f"{frames_to_label(300, RATE_24)} -> {frames_to_label(500, RATE_24)}"
    assert avisos[0].context["old_timecode"] == antigo
    assert avisos[0].context["new_timecode"] == novo

    # O terceiro candidato, pior e sobreposto ao empate, nao vence.
    assert cl.time_map is not None
    assert Segment(400, 500, 400) not in cl.time_map.segments
    assert cl.counts == {"delete": 2, "insert": 1, "shift": 1, "unresolved": 1}


def test_empate_com_material_casado_no_meio_nao_cobre_o_material_casado() -> None:
    """O empate lista as regioes do empate e nao uma caixa que cobre o material casado."""
    old = _cut(("Z", 100, 200, 0, 100), ("X", 200, 300, 0, 100), ("Z", 500, 600, 0, 100))
    new = _cut(("X", 200, 300, 0, 100), ("Z", 300, 400, 0, 100))

    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.ambiguous is False

    unresolved = [c for c in cl.changes if c.kind == "unresolved"]
    assert len(unresolved) == 2
    assert [(c.old_in, c.old_out) for c in unresolved] == [(100, 200), (500, 600)]
    assert [(c.new_in, c.new_out) for c in unresolved] == [(300, 400), (300, 400)]
    assert [c.source for c in unresolved] == ["Z", "Z"]
    assert all(c.detail == "same shot used more than once: not matched" for c in unresolved)

    avisos = [i for i in report.issues if i.code == "RECONFORM_REUSE_UNRESOLVED"]
    assert len(avisos) == 2
    assert [i.context["source"] for i in avisos] == ["Z", "Z"]
    antigo = [
        f"{frames_to_label(100, RATE_24)} -> {frames_to_label(200, RATE_24)}",
        f"{frames_to_label(500, RATE_24)} -> {frames_to_label(600, RATE_24)}",
    ]
    novo = f"{frames_to_label(300, RATE_24)} -> {frames_to_label(400, RATE_24)}"
    assert [i.context["old_timecode"] for i in avisos] == antigo
    assert [i.context["new_timecode"] for i in avisos] == [novo, novo]

    # O X do meio casou e nao aparece em nenhum dos trechos listados.
    assert cl.time_map is not None
    assert Segment(200, 300, 200) in cl.time_map.segments
    assert cl.counts == {"delete": 2, "insert": 1, "unresolved": 2}


def test_peca_descartada_e_contada_pela_faixa_coberta() -> None:
    """A peca descartada conta pela faixa que o vencedor cobriu, nao pelo par de eventos."""
    old = _cut(("A", 0, 100, 0, 100), ("A", 100, 200, 50, 150))
    new = _cut(("A", 0, 150, 0, 150))

    report = Report()
    cl = diff_cuts(old, new, report)
    assert cl is not None
    assert cl.time_map is not None
    assert Segment(0, 100, 0) in cl.time_map.segments
    assert Segment(150, 200, 100) in cl.time_map.segments

    resolvidos = [i for i in report.issues if i.code == "RECONFORM_REUSE_RESOLVED"]
    assert len(resolvidos) == 1
    # O pedaco [100,150) antigo foi coberto pelo vencedor e nao casou: e uma
    # peca descartada, mesmo que o par de eventos do candidato tenha casado.
    assert resolvidos[0].context["discarded"] == 1
    assert resolvidos[0].context["pieces"] == [
        "old "
        f"{frames_to_label(100, RATE_24)} -> {frames_to_label(150, RATE_24)}"
        " -> new "
        f"{frames_to_label(50, RATE_24)} -> {frames_to_label(100, RATE_24)}"
    ]
