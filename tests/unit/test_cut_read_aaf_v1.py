"""Testes da escolha da camada de imagem (V1) dentro de um NestedScope."""

from __future__ import annotations

from pathlib import Path

from aafton import cut_read_aaf
from aafton.cut_read_aaf import read_cut_aaf
from aafton.report import Report
from tests.cut_factory import (
    build_scope_cut_aaf,
    build_scope_in_sequence_cut_aaf,
    build_scope_with_direct_clip_cut_aaf,
    build_slot_without_sequence_cut_aaf,
)


def _codigos(report: Report) -> list[str]:
    """Codigos de todas as ocorrencias registradas no relatorio."""
    return [issue.code for issue in report.issues]


def _eventos(corte) -> list[tuple]:
    """Resumo dos eventos do corte, para comparar com o esperado."""
    return [
        (e.kind, e.reel, e.source_in, e.source_out, e.record_in, e.record_out) for e in corte.events
    ]


def test_escopo_do_slot_escolhe_a_primeira_camada_com_clip(tmp_path: Path):
    """O V1 vem primeiro; a camada de graficos de cima fica de fora."""
    v1 = [{"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 48}]
    graficos = [{"kind": "clip", "reel": "GRAF001", "tape_start": 172800, "start": 0, "length": 24}]
    caminho = build_scope_cut_aaf(tmp_path / "escopo.aaf", [v1, graficos])
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    assert _eventos(corte) == [("clip", "R001", 86400, 86448, 86400, 86448)]


def test_escopo_dentro_da_sequence_escolhe_a_primeira_camada_com_clip(tmp_path: Path):
    """Dentro da Sequence, o primeiro slot com clip vence o slot so de Filler."""
    camadas = [
        [{"kind": "gap", "length": 24}],
        [{"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 12, "length": 36}],
    ]
    caminho = build_scope_in_sequence_cut_aaf(tmp_path / "escopo_seq.aaf", camadas)
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    assert _eventos(corte) == [("clip", "R002", 172812, 172848, 86400, 86436)]


def test_slot_sem_sequence_e_lido_como_esta(tmp_path: Path):
    """Sem Sequence, o segmento do slot vira o unico componente lido."""
    ev = {"kind": "clip", "reel": "R003", "tape_start": 86400, "start": 0, "length": 24}
    caminho = build_slot_without_sequence_cut_aaf(tmp_path / "sem_sequence.aaf", ev)
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    assert _eventos(corte) == [("clip", "R003", 86400, 86424, 86400, 86424)]


def test_camada_de_cima_com_clip_sobre_vazio_gera_info(tmp_path: Path):
    """Clip de outra camada sobre um vazio da escolhida vira info com o TC."""
    v1 = [
        {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24},
        {"kind": "gap", "length": 24},
    ]
    graficos = [{"kind": "clip", "reel": "GRAF001", "tape_start": 172800, "start": 0, "length": 48}]
    caminho = build_scope_cut_aaf(tmp_path / "camadas.aaf", [v1, graficos])
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None
    assert "RECONFORM_UPPER_LAYER_IGNORED" in _codigos(report)
    info = next(i for i in report.issues if i.code == "RECONFORM_UPPER_LAYER_IGNORED")
    assert info.context["timecode"] == "01:00:01:00"


def test_camada_de_cima_no_escopo_dentro_da_sequence_gera_info(tmp_path: Path):
    """O NestedScope dentro da Sequence tambem avisa a camada de cima."""
    camadas = [
        [
            {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24},
            {"kind": "gap", "length": 24},
        ],
        [{"kind": "clip", "reel": "GRAF001", "tape_start": 172800, "start": 0, "length": 48}],
    ]
    caminho = build_scope_in_sequence_cut_aaf(tmp_path / "camadas_seq.aaf", camadas)
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None
    assert "RECONFORM_UPPER_LAYER_IGNORED" in _codigos(report)
    info = next(i for i in report.issues if i.code == "RECONFORM_UPPER_LAYER_IGNORED")
    assert info.context["timecode"] == "01:00:01:00"


def test_transicao_desloca_o_vazio_da_camada_escolhida(tmp_path: Path):
    """A transicao recua o cursor: o vazio da escolhida comeca antes.

    A transicao cobre o fim do plano anterior, e o leitor de corte recua o
    cursor pelo comprimento dela; o aviso da camada de cima tem de usar a
    mesma conta.
    """
    v1 = [
        {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 48},
        {"kind": "transition", "length": 24, "cutpoint": 12},
        {"kind": "gap", "length": 24},
    ]
    graficos = [{"kind": "clip", "reel": "GRAF001", "tape_start": 172800, "start": 0, "length": 48}]
    caminho = build_scope_cut_aaf(tmp_path / "camadas_transicao.aaf", [v1, graficos])
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None
    assert "RECONFORM_UPPER_LAYER_IGNORED" in _codigos(report)
    info = next(i for i in report.issues if i.code == "RECONFORM_UPPER_LAYER_IGNORED")
    assert info.context["timecode"] == "01:00:01:00"


def test_sem_camada_de_cima_nao_gera_info(tmp_path: Path):
    """Uma camada so nao tem nada de cima para avisar."""
    v1 = [{"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24}]
    caminho = build_scope_cut_aaf(tmp_path / "uma_camada.aaf", [v1])
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None
    assert "RECONFORM_UPPER_LAYER_IGNORED" not in _codigos(report)


def test_escopo_apos_grupo_comum_avisa_a_camada_de_cima(tmp_path: Path):
    """O NestedScope dentro de um grupo comum tambem avisa a camada de cima."""
    v1 = [
        {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 24},
        {"kind": "gap", "length": 24},
    ]
    graficos = [{"kind": "clip", "reel": "GRAF001", "tape_start": 172800, "start": 0, "length": 48}]
    caminho = build_scope_cut_aaf(tmp_path / "escopo_grupo.aaf", [v1, graficos], em_grupo=True)
    report = Report(strict=True)
    corte = read_cut_aaf(caminho, report)
    assert corte is not None
    assert _eventos(corte) == [
        ("clip", "R001", 86400, 86424, 86400, 86424),
        ("gap", None, None, None, 86424, 86448),
    ]
    assert "RECONFORM_UPPER_LAYER_IGNORED" in _codigos(report)
    info = next(i for i in report.issues if i.code == "RECONFORM_UPPER_LAYER_IGNORED")
    assert info.context["timecode"] == "01:00:01:00"


def test_aviso_da_camada_de_cima_sai_uma_vez_por_escopo(tmp_path: Path, monkeypatch):
    """O aviso das camadas de cima e calculado uma vez por escopo.

    Sem Sequence no slot, a descida do slot nao acha nada e o leitor cai
    no fallback: o aviso do mesmo escopo nao pode sair duas vezes.
    """
    chamadas: list = []
    original = cut_read_aaf._avisar_camadas_superiores

    def contando(*args, **kwargs) -> None:
        chamadas.append(1)
        original(*args, **kwargs)

    monkeypatch.setattr(cut_read_aaf, "_avisar_camadas_superiores", contando)
    ev = {"kind": "clip", "reel": "R004", "tape_start": 86400, "start": 0, "length": 24}
    caminho = build_scope_with_direct_clip_cut_aaf(tmp_path / "escopo_sem_sequence.aaf", ev)
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    assert _eventos(corte) == [("clip", "R004", 86400, 86424, 86400, 86424)]
    assert len(chamadas) == 1
