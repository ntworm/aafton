"""Casamento de planos por frame no verify do reconform, com a velocidade.

O verify nao importa o diff: ele refaz, com codigo proprio, a regra de
casamento por frame, para saber que trecho do corte antigo casa com que
trecho do corte novo. A regra e a mesma do diff: o frame r do evento antigo
casa com o frame r' do evento novo quando os dois sao clip, tem a mesma
origem e a mesma velocidade, e nenhum dos dois e um plano inteiro: a origem de
r em A e a origem de r' em N sao o mesmo frame do material de origem.

Com velocidade inteira s, o frame r' do corte novo e
`N.record_in + (origem - N.source_in) / s`, e ele existe so quando
`(A.source_in - N.source_in) % s == 0`. Uma peca e a sequencia maxima de
frames consecutivos do corte antigo com r' consecutivo: com s = 1 e a regra de
hoje. Plano inteiro (whole_only) casa so com outro plano inteiro de mesma
origem, mesmo comeco, mesma velocidade e mesma duracao: a peca e o evento
inteiro.

As pecas sao montadas em frames inteiros: a posicao em samples e levada pelo
delta da peca, e o TC de origem nunca e dividido.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .cut_ir import CutEvent, CutIR


@dataclass(frozen=True)
class Peca:
    """Trecho continuo do corte antigo casado com um trecho do corte novo."""

    old_in: int
    old_out: int
    new_in: int
    evento_antigo: CutEvent
    evento_novo: CutEvent

    @property
    def new_out(self) -> int:
        """Frame final exclusivo no corte novo."""
        return self.new_in + (self.old_out - self.old_in)

    @property
    def delta(self) -> int:
        """Deslocamento em frames do corte antigo para o novo."""
        return self.new_in - self.old_in

    @property
    def source(self) -> str | None:
        """Chave de origem do material casado."""
        return self.evento_antigo.source

    @property
    def fator(self) -> int:
        """Frames de origem que o plano consome por frame de gravacao."""
        return fator_tc(self.evento_antigo)

    @property
    def tc_in(self) -> int | Fraction:
        """TC de origem do primeiro frame coberto."""
        return tc_do_frame(self.evento_antigo, self.old_in)

    @property
    def tc_out(self) -> int | Fraction:
        """TC de origem depois do ultimo frame coberto."""
        return tc_do_frame(self.evento_antigo, self.old_out)


def _tem_origem(evento: CutEvent) -> bool:
    """Verdadeiro quando o evento tem chave de origem e faixa de TC."""
    return (
        evento.kind == "clip"
        and evento.source is not None
        and evento.source_in is not None
        and evento.source_out is not None
    )


def fator_tc(evento: CutEvent) -> int:
    """Frames de origem que o plano consome por frame de gravacao.

    O plano inteiro tem a faixa de origem do tamanho da gravacao: o TC anda um
    por frame. O plano normal anda com a velocidade do plano.
    """
    if evento.whole_only or evento.speed is None:
        return 1
    valor = Fraction(evento.speed)
    if valor.denominator != 1 or valor <= 0:
        return 1
    return int(valor)


def tc_do_frame(evento: CutEvent, frame: int | Fraction) -> int | Fraction:
    """TC de origem do frame de gravacao do evento."""
    assert evento.source_in is not None
    return evento.source_in + (frame - evento.record_in) * fator_tc(evento)


def frame_do_tc(evento: CutEvent, tc: int | Fraction) -> int | Fraction:
    """Frame de gravacao do evento que tem o TC de origem dado."""
    assert evento.source_in is not None
    return evento.record_in + (tc - evento.source_in) // fator_tc(evento)


def _fator(speed: Fraction | int | None) -> int | None:
    """Velocidade do plano como inteiro positivo, ou None quando ela nao e."""
    if speed is None:
        return None
    valor = Fraction(speed)
    if valor.denominator != 1 or valor <= 0:
        return None
    return int(valor)


def _peca_inteira(antigo: CutEvent, novo: CutEvent) -> Peca | None:
    """Peca de um plano inteiro: o evento inteiro, quando os dois batem."""
    if not (antigo.whole_only and novo.whole_only):
        return None
    if antigo.source_in != novo.source_in or antigo.speed != novo.speed:
        return None
    if antigo.duration_frames != novo.duration_frames:
        return None
    if antigo.duration_frames <= 0:
        return None
    return Peca(antigo.record_in, antigo.record_out, novo.record_in, antigo, novo)


def _peca_de_frames(antigo: CutEvent, novo: CutEvent) -> Peca | None:
    """Peca do casamento por frame, com a velocidade dos dois planos."""
    fator = _fator(antigo.speed)
    if fator is None or _fator(novo.speed) != fator:
        return None
    assert antigo.source_in is not None and novo.source_in is not None
    if (antigo.source_in - novo.source_in) % fator != 0:
        return None
    deslocamento = (antigo.source_in - novo.source_in) // fator
    inicio = max(antigo.record_in, antigo.record_in - deslocamento)
    fim = min(antigo.record_out, antigo.record_in + novo.duration_frames - deslocamento)
    if fim <= inicio:
        return None
    novo_in = novo.record_in + (inicio - antigo.record_in) + deslocamento
    return Peca(inicio, fim, novo_in, antigo, novo)


def casar(antigo: CutEvent, novo: CutEvent) -> Peca | None:
    """Peca que casa um evento do corte antigo com um do corte novo."""
    if antigo.source is None or antigo.source != novo.source:
        return None
    if not _tem_origem(antigo) or not _tem_origem(novo):
        return None
    if antigo.whole_only or novo.whole_only:
        return _peca_inteira(antigo, novo)
    return _peca_de_frames(antigo, novo)


def chave(peca: Peca) -> tuple[int, int, int, int, int]:
    """Chave deterministica para ordenar as pecas casadas."""
    return (
        peca.old_in,
        peca.old_out,
        peca.new_in,
        peca.evento_antigo.index,
        peca.evento_novo.index,
    )


def casar_eventos(old: CutIR, new: CutIR) -> list[Peca]:
    """Candidatas do casamento: um par de eventos com a mesma origem."""
    novos = [evento for evento in new.events if _tem_origem(evento)]
    pecas: list[Peca] = []
    for antigo in old.events:
        if not _tem_origem(antigo):
            continue
        for novo in novos:
            peca = casar(antigo, novo)
            if peca is not None:
                pecas.append(peca)
    pecas.sort(key=chave)
    return pecas
