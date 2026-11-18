"""Testes da leitura do efeito de velocidade (time warp) no leitor de corte."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import aaf2

from aafton.aaf_read_components import _filhos_do_componente
from aafton.aaf_read_timewarp import _parametro_por_nome
from aafton.cut_read_aaf import read_cut_aaf
from aafton.cut_read_aaf_velocidade import NOME_DA_VELOCIDADE, _constante_do_grupo
from aafton.report import Report
from tests.cut_factory import SPEED_RATIO_DO_VID, build_picture_cut_aaf


def _warp(reel: str, length: int, velocidade=(2, 1), **extra) -> dict:
    """Evento de fabrica: um plano com efeito de velocidade."""
    evento = {
        "kind": "warp",
        "reel": reel,
        "tape_start": 86400,
        "start": 0,
        "length": length,
        "velocidade": velocidade,
    }
    evento.update(extra)
    return evento


def _corte(tmp_path: Path, nome: str, eventos):
    """Le o corte de um AAF de fabrica e devolve o CutIR."""
    caminho = build_picture_cut_aaf(tmp_path / nome, eventos, rate=(24, 1), start_tc_frames=86400)
    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    return corte


def test_velocidade_2x_e_plano_normal(tmp_path: Path):
    """2x constantes com um SourceClip de entrada e um plano normal."""
    corte = _corte(tmp_path, "2x.aaf", [_warp("R001", 48)])
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is False
    assert (evento.record_in, evento.record_out) == (86400, 86448)
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 96)


def test_entrada_do_warp_em_grupo_comum_e_plano_normal(tmp_path: Path):
    """O Avid embrulha a entrada do warp num OperationGroup comum."""
    corte = _corte(tmp_path, "wrapper.aaf", [_warp("R001", 48, entrada_em_grupo=True)])
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is False
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 96)


def test_grupo_comum_por_cima_do_warp_nao_perde_a_velocidade(tmp_path: Path):
    """Um OperationGroup comum por cima do warp nao esconde a velocidade.

    O grupo de fora e um embrulho comum, e o grupo de warping esta dentro
    dele: o leitor abre o embrulho e acha a velocidade de 2x.
    """
    corte = _corte(
        tmp_path,
        "grupo_sobre_warp.aaf",
        [
            {
                "kind": "grupo_warp",
                "reel": "R001",
                "tape_start": 86400,
                "start": 0,
                "length": 48,
                "velocidade": (2, 1),
                "entrada_em_sequence": True,
                "fonte_comprimento": 96,
            }
        ],
    )
    evento = corte.events[0]
    assert len(corte.events) == 1
    assert evento.speed == Fraction(2)
    assert evento.whole_only is False
    assert (evento.record_in, evento.record_out) == (86400, 86448)
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 96)


def test_2x_com_trim_nas_duas_pontas_vira_plano_inteiro(tmp_path: Path):
    """Com transicao antes e depois, o plano de 2x so pode ser movido inteiro."""
    eventos = [
        {"kind": "clip", "reel": "R000", "tape_start": 0, "start": 0, "length": 48},
        {"kind": "transition", "length": 24, "cutpoint": 12},
        _warp("R001", 48),
        {"kind": "transition", "length": 24, "cutpoint": 12},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 0, "length": 48},
    ]
    corte = _corte(tmp_path, "2x_trim.aaf", eventos)
    evento = corte.events[1]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is True
    assert (evento.record_in, evento.record_out) == (86436, 86460)
    assert (evento.source_in, evento.source_out) == (86400, 86424)


def test_transicao_encostada_em_2x_vira_plano_inteiro(tmp_path: Path):
    """Uma transicao no fim do plano de 2x tira a chance de ser plano normal."""
    eventos = [
        _warp("R001", 48),
        {"kind": "transition", "length": 24, "cutpoint": 12},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 0, "length": 48},
    ]
    corte = _corte(tmp_path, "transicao.aaf", eventos)
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is True
    assert (evento.record_in, evento.record_out) == (86400, 86436)
    assert (evento.source_in, evento.source_out) == (86400, 86436)


def test_transicao_que_cobre_o_plano_de_2x_vira_plano_inteiro(tmp_path: Path):
    """A transicao que cobre o plano de 2x, sem apara-lo, tambem o torna inteiro."""
    eventos = [
        _warp("R001", 48),
        {"kind": "transition", "length": 24, "cutpoint": 24},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 0, "length": 48},
    ]
    corte = _corte(tmp_path, "transicao_cobre.aaf", eventos)
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is True
    assert (evento.record_in, evento.record_out) == (86400, 86448)
    assert (evento.source_in, evento.source_out) == (86400, 86448)


def test_transicao_com_cutpoint_zero_apos_2x_vira_plano_inteiro(tmp_path: Path):
    """Transicao que comeca no fim do plano de 2x ainda encosta no plano.

    O corte apara o plano antes da transicao; a transicao encosta nele
    assim mesmo, e o plano perde a chance de ser plano normal.
    """
    eventos = [
        _warp("R001", 48),
        {"kind": "transition", "length": 24, "cutpoint": 0},
        {"kind": "clip", "reel": "R002", "tape_start": 172800, "start": 0, "length": 48},
    ]
    corte = _corte(tmp_path, "cutpoint_zero.aaf", eventos)
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is True
    assert (evento.record_in, evento.record_out) == (86400, 86424)
    assert (evento.source_in, evento.source_out) == (86400, 86424)


def test_grupo_comum_com_varios_planos_nao_vira_um_plano(tmp_path: Path):
    """OperationGroup comum com uma Sequence de planos nao os junta.

    O grupo embrulha dois planos: um clip e um plano de 2x. O leitor tem de
    ler os dois, e nao fazer do grupo um plano so de 2x.
    """
    eventos = [
        {
            "kind": "grupo",
            "length": 96,
            "internos": [
                {"kind": "clip", "reel": "R001", "tape_start": 86400, "start": 0, "length": 48},
                _warp("R002", 48, tape_start=172800),
            ],
        }
    ]
    corte = _corte(tmp_path, "grupo_comum.aaf", eventos)
    assert len(corte.events) == 2
    primeiro, segundo = corte.events
    assert primeiro.reel == "R001"
    assert primeiro.speed == Fraction(1)
    assert (primeiro.record_in, primeiro.record_out) == (86400, 86448)
    assert segundo.reel == "R002"
    assert segundo.speed == Fraction(2)
    assert segundo.whole_only is False
    assert (segundo.record_in, segundo.record_out) == (86448, 86496)
    assert (segundo.source_in, segundo.source_out) == (172800, 172800 + 96)


def test_meia_velocidade_vira_plano_inteiro(tmp_path: Path):
    """Velocidade fracionaria nao cabe em frames inteiros: e plano inteiro."""
    corte = _corte(tmp_path, "meio.aaf", [_warp("R001", 48, (1, 2))])
    evento = corte.events[0]
    assert evento.speed == Fraction(1, 2)
    assert evento.whole_only is True
    assert (evento.source_in, evento.source_out) == (86400, 86448)


def test_velocidade_zero_e_negativa_viram_plano_inteiro(tmp_path: Path):
    """Zero e negativo nao sao velocidade de plano normal."""
    corte = _corte(tmp_path, "zero.aaf", [_warp("R001", 48, (0, 1))])
    evento = corte.events[0]
    assert evento.speed == Fraction(0)
    assert evento.whole_only is True

    corte = _corte(tmp_path, "negativa.aaf", [_warp("R001", 48, (-2, 1))])
    evento = corte.events[0]
    assert evento.speed == Fraction(-2)
    assert evento.whole_only is True


def test_rampa_de_velocidade_vira_plano_inteiro(tmp_path: Path):
    """Rampa nao tem constante: o plano perde a velocidade e vira inteiro."""
    pontos = [((0, 1), (1, 1)), ((1, 1), (2, 1))]
    corte = _corte(tmp_path, "rampa.aaf", [_warp("R001", 48, pontos=pontos)])
    evento = corte.events[0]
    assert evento.speed is None
    assert evento.whole_only is True
    assert (evento.source_in, evento.source_out) == (86400, 86448)


def test_varying_value_de_pontos_iguais_e_constante(tmp_path: Path):
    """Pontos com o mesmo valor sao uma constante e valem como plano normal."""
    pontos = [((0, 1), (2, 1)), ((1, 1), (2, 1))]
    corte = _corte(tmp_path, "iguais.aaf", [_warp("R001", 48, pontos=pontos)])
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.whole_only is False
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 96)


class _Ponto:
    """Ponto de controle de um parametro variavel."""

    def __init__(self, valor) -> None:
        self.value = valor


class _ParametroVariavel:
    """Parametro com PointList, como o pyaaf2 le do AAF."""

    def __init__(self, pontos) -> None:
        self.name = "Speed Ratio"
        self.point_list = pontos


class _GrupoComVelocidade:
    """OperationGroup com o parametro de velocidade."""

    def __init__(self, pontos) -> None:
        self.parameters = [_ParametroVariavel(pontos)]


def test_ponto_sem_valor_nao_vira_constante_no_grupo():
    """Um ponto sem valor legivel tira a constante da velocidade.

    O AAF nao deixa gravar um ponto sem valor, porque a propriedade do
    valor e obrigatoria; o ponto chega aqui como o pyaaf2 nao le, sem
    valor nenhum.
    """
    grupo = _GrupoComVelocidade([_Ponto(Fraction(2)), _Ponto(None)])
    assert _constante_do_grupo(grupo) is None


def test_warp_aninhado_multiplica_as_constantes(tmp_path: Path):
    """2x dentro de 2x e 4x."""
    corte = _corte(tmp_path, "aninhado.aaf", [_warp("R001", 48, aninhado=True)])
    evento = corte.events[0]
    assert evento.speed == Fraction(4)
    assert evento.whole_only is False
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 192)


def test_parametro_de_velocidade_com_e_sem_espaco(tmp_path: Path):
    """'Speed Ratio' e 'SpeedRatio' sao o mesmo parametro."""
    corte = _corte(tmp_path, "com_espaco.aaf", [_warp("R001", 48, parametro="Speed Ratio")])
    assert corte.events[0].speed == Fraction(2)

    corte = _corte(tmp_path, "sem_espaco.aaf", [_warp("R001", 48, parametro="SpeedRatio")])
    assert corte.events[0].speed == Fraction(2)


def _componentes(no, vistos=None, prof=0):
    """Componentes do AAF, em profundidade."""
    vistos = vistos if vistos is not None else set()
    if no is None or prof > 12 or id(no) in vistos:
        return
    vistos.add(id(no))
    yield no
    for filho in _filhos_do_componente(no):
        yield from _componentes(filho, vistos, prof + 1)


def _speed_ratio_do_arquivo(caminho: Path) -> tuple[int, int] | None:
    """Par do Speed Ratio gravado no arquivo, como o pyaaf2 le do AAF."""
    with aaf2.open(str(caminho), "r") as f:
        for mob in f.content.mobs:
            for slot in getattr(mob, "slots", None) or []:
                for no in _componentes(getattr(slot, "segment", None)):
                    parametros = getattr(no, "parameters", None) or []
                    parametro = _parametro_por_nome(parametros, NOME_DA_VELOCIDADE)
                    if parametro is None:
                        continue
                    valor = getattr(parametro, "value", None)
                    return (valor.numerator, valor.denominator)
    return None


def test_velocidade_2x_como_o_avid_grava(tmp_path: Path):
    """O Avid grava o Speed Ratio como 2000000/1000000; a leitura reduz para 2."""
    caminho = build_picture_cut_aaf(
        tmp_path / "avid.aaf",
        [_warp("R001", 48, velocidade=SPEED_RATIO_DO_VID)],
        rate=(24, 1),
        start_tc_frames=86400,
    )
    assert _speed_ratio_do_arquivo(caminho) == SPEED_RATIO_DO_VID

    corte = read_cut_aaf(caminho, Report(strict=True))
    assert corte is not None
    evento = corte.events[0]
    assert evento.speed == Fraction(2)
    assert evento.speed.denominator == 1
    assert evento.whole_only is False
    assert (evento.source_in, evento.source_out) == (86400, 86400 + 96)
