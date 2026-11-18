"""Leitura do efeito de velocidade (time warp) no leitor de corte de imagem.

O Media Composer grava o efeito de velocidade como um OperationGroup com
IsTimeWarp e um parametro de velocidade ('Speed Ratio'). O leitor de corte
transforma esse efeito em dois dados do CutEvent: `speed`, a constante quando
ela existe, e `whole_only`, que marca o plano que so pode ser movido inteiro.

Velocidade constante inteira positiva, com um unico SourceClip de entrada e
sem transicao encostada, e um plano normal: o frame de gravacao `r` vem do
frame de origem `source_in + (r - record_in) * s`. Todo o resto (rampa,
constante fracionaria, zero, negativo, parametro ausente, entrada composta,
transicao encostada) e um plano inteiro, que so pode ser movido como um todo.

O parametro de velocidade vem gravado com o par cru: o Avid escreve o 2x
como 2000000/1000000. A leitura reduz o par, para que o denominador diga
se a velocidade cabe em frames inteiros.

Transicao encostada e a que cobre o plano, mesmo sem apara-lo: o material
passa a andar junto com a gravacao, e o plano perde a chance de ser normal.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction

from .aaf_read_components import _filhos_do_componente, _type_name
from .aaf_read_points import is_varying, point_list_of
from .aaf_read_timewarp import (
    _filhos_do_time_warp,
    _parametro_por_nome,
    _tem_is_time_warp,
)
from .cut_ir import CutEvent

PROFUNDIDADE_MAXIMA = 16
NOME_DA_VELOCIDADE = "speedratio"


@dataclass(frozen=True)
class Velocidade:
    """Velocidade de um efeito de warping lido no AAF."""

    speed: Fraction | None
    constante: bool
    primeiro_clip: object | None
    entrada_unica: bool

    @property
    def inteira_positiva(self) -> bool:
        """True quando a velocidade e uma constante inteira positiva."""
        return (
            self.constante
            and self.speed is not None
            and self.speed > 0
            and self.speed.denominator == 1
        )


def _velocidade_do_efeito(componente) -> Velocidade | None:
    """Velocidade do efeito de warping de `componente`, ou None sem efeito.

    O efeito pode estar no proprio OperationGroup ou dentro dele, como no
    'Audio Gain' que embrulha o warp.
    """
    grupos = _grupos_de_warp(componente, set(), 0)
    if not grupos:
        return None
    speed, constante = _velocidade_dos_grupos(grupos)
    clips = _source_clips(_entrada_do_efeito(grupos[0]))
    return Velocidade(speed, constante, clips[0] if clips else None, len(clips) == 1)


def _grupos_de_warp(no, visitados: set[int], profundidade: int) -> list:
    """Grupos de warping de `no`, do mais externo para o mais interno.

    Uma Sequence com mais de um componente e uma lista de planos, nao um
    embrulho de um plano so: o efeito de velocidade de um dos planos nao
    vale para os outros, e por isso ela nao e aberta aqui.
    """
    if no is None or profundidade > PROFUNDIDADE_MAXIMA or id(no) in visitados:
        return []
    visitados.add(id(no))
    achados: list = []
    if _grupo_e_efeito(no):
        achados.append(no)
    for filho in _filhos_do_time_warp(no):
        if _sequence_de_varios_planos(filho):
            continue
        achados.extend(_grupos_de_warp(filho, visitados, profundidade + 1))
    return achados


def _grupo_e_efeito(no) -> bool:
    """True quando o no e o OperationGroup do efeito de velocidade."""
    return _type_name(no) == "OperationGroup" and _tem_is_time_warp(no)


def _sequence_de_varios_planos(no) -> bool:
    """True quando `no` e uma Sequence com mais de um componente."""
    if _type_name(no) != "Sequence":
        return False
    componentes = list(getattr(no, "components", None) or [])
    return len(componentes) > 1


def _entrada_do_efeito(grupo) -> list:
    """Segmentos de entrada do grupo de warping."""
    return list(getattr(grupo, "segments", None) or [])


def _velocidade_dos_grupos(grupos: list) -> tuple[Fraction | None, bool]:
    """Velocidade do efeito e se ela existe.

    Warps encaixados multiplicam as constantes; uma parte sem constante
    (rampa ou parametro ausente) deixa o efeito sem velocidade constante.
    """
    speed = Fraction(1)
    for grupo in grupos:
        valor = _constante_do_grupo(grupo)
        if valor is None:
            return None, False
        speed *= valor
    return speed, True


def _constante_do_grupo(grupo) -> Fraction | None:
    """Constante da velocidade de um grupo, ou None quando ela nao existe."""
    parametro = _parametro_por_nome(getattr(grupo, "parameters", []) or [], NOME_DA_VELOCIDADE)
    if parametro is None:
        return None
    if is_varying(parametro):
        valores = _valores_dos_pontos(parametro)
        if valores is None or not valores or len(set(valores)) > 1:
            return None
        return valores[0]
    return _fracao(getattr(parametro, "value", None))


def _valores_dos_pontos(parametro) -> list[Fraction] | None:
    """Valores dos pontos de um VaryingValue; None quando algum nao da para ler.

    Um ponto sem valor legivel tira a constante da velocidade: nao se sabe
    o que ele valia.
    """
    valores: list[Fraction] = []
    for ponto in point_list_of(parametro) or []:
        valor = _valor_do_ponto(ponto)
        if valor is None:
            return None
        valores.append(valor)
    return valores


def _valor_do_ponto(ponto) -> Fraction | None:
    """Valor de um ponto de controle, como fracao."""
    bruto = getattr(ponto, "value", None)
    if bruto is None and hasattr(ponto, "__getitem__"):
        try:
            bruto = ponto["Value"]
        except Exception:
            bruto = None
    if bruto is not None and hasattr(bruto, "value"):
        bruto = bruto.value
    return _fracao(bruto)


def _fracao(valor) -> Fraction | None:
    """Valor racional do AAF como fracao reduzida, ou None quando nao da para ler.

    O Media Composer grava o Speed Ratio como 2000000/1000000, e o pyaaf2
    devolve esse par sem reduzir. A fracao e sempre remontada a partir do
    numerador e do denominador, para que 2000000/1000000 vire 2 e o plano
    de 2x volte a ser um plano normal.
    """
    if valor is None:
        return None
    if isinstance(valor, int):
        return Fraction(int(valor), 1)
    numerador, denominador = _numerador_e_denominador(valor)
    if numerador is None or denominador is None:
        try:
            fracao = Fraction(valor)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return Fraction(fracao.numerator, fracao.denominator)
    try:
        return Fraction(int(numerador), int(denominador))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _numerador_e_denominador(valor) -> tuple[int | None, int | None]:
    """Numerador e denominador do valor, como estao no AAF."""
    for chave_num, chave_den in (("num", "den"), ("numerator", "denominator")):
        numerador = getattr(valor, chave_num, None)
        denominador = getattr(valor, chave_den, None)
        if numerador is not None and denominador is not None:
            return numerador, denominador
    return None, None


def _source_clips(no) -> list:
    """SourceClips alcancaveis a partir de `no`, em ordem de leitura."""
    achados: list = []
    _coletar_clips(no, achados, set(), 0)
    return achados


def _coletar_clips(no, achados: list, visitados: set[int], profundidade: int) -> None:
    """Coleta os SourceClips de `no` e dos filhos dele."""
    if no is None or profundidade > PROFUNDIDADE_MAXIMA:
        return
    if isinstance(no, (list, tuple)):
        for item in no:
            _coletar_clips(item, achados, visitados, profundidade + 1)
        return
    if id(no) in visitados:
        return
    visitados.add(id(no))
    if _type_name(no) == "SourceClip":
        achados.append(no)
        return
    for filho in _filhos_do_componente(no):
        _coletar_clips(filho, achados, visitados, profundidade + 1)


def _quadro_da_velocidade(
    velocidade: Velocidade,
    raw_src: int,
    head_trim: int,
    duracao: int,
    transicao: bool,
) -> tuple[Fraction | None, bool, int, int]:
    """Velocidade, plano inteiro, origem e fim de um evento com velocidade.

    `duracao` e o comprimento do evento na gravacao, em frames. `transicao`
    avisa que uma transicao encosta no evento.
    """
    speed = velocidade.speed
    if velocidade.inteira_positiva and velocidade.entrada_unica and not transicao:
        fator = int(speed)
        src_in = raw_src + head_trim * fator
        return speed, False, src_in, src_in + duracao * fator
    return speed, True, raw_src, raw_src + duracao


def _plano_inteiro(prev: CutEvent, raw_src: int | None, record_out: int) -> CutEvent:
    """Marca o evento como plano inteiro e refaz o intervalo de origem.

    Uma transicao encostada num plano com velocidade tira dele a chance de
    ser um plano normal: o material passa a andar junto com a gravacao.
    """
    src_in = raw_src if raw_src is not None else prev.source_in
    src_out = src_in + (record_out - prev.record_in) if src_in is not None else None
    return replace(prev, record_out=record_out, source_out=src_out, whole_only=True)


def _plano_inteiro_por_transicao(
    prev: CutEvent, raw_src: int | None, transition_start: int, record_out_antes: int
) -> CutEvent:
    """Marca o plano anterior como inteiro quando a transicao encosta nele.

    A transicao que cobre o plano, mesmo sem apara-lo, ja tira dele a chance
    de ser um plano normal. A comparacao e com o fim do plano antes do
    apara: a transicao encosta nele mesmo quando o corte ja aparou o plano
    antes dela. O intervalo de origem passa a ser o do material inteiro: o
    comeco do SourceClip de entrada mais o comprimento de gravacao do plano.
    """
    if transition_start >= record_out_antes:
        return prev
    return _plano_inteiro(prev, raw_src, prev.record_out)
