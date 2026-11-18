"""As pecas do verify: o unico predicado de correspondencia dos dois cortes.

O verify nao importa o diff nem o mapa de tempo: ele monta as proprias pecas a
partir dos dois cortes, casando por frame (reconform_verify_velocidade) e
resolvendo por ordem o material repetido (reconform_verify_reuso). Esse
conjunto de pecas e o unico predicado de correspondencia do verify: para onde
vai um frame do corte antigo, que material o corte novo nao cobre e qual e o
deslocamento do trecho sao perguntas respondidas por ele.

As pecas sao montadas em frames inteiros: a posicao em samples e levada pelo
delta da peca, e o TC de origem nunca e dividido.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .cut_ir import CutIR
from .reconform_verify_reuso import NaoResolvido, resolver_reuso
from .reconform_verify_velocidade import Peca, casar_eventos


@dataclass(frozen=True)
class Pecas:
    """As pecas do verify e as perguntas que elas respondem."""

    pecas: tuple[Peca, ...]
    # Os trechos que a ordem dos planos nao resolveu: nao tem correspondencia.
    nao_resolvidos: tuple[NaoResolvido, ...] = ()

    def peca_de(self, frame: int | Fraction) -> Peca | None:
        """A peca que cobre um frame do corte antigo."""
        for peca in self.pecas:
            if peca.old_in <= frame < peca.old_out:
                return peca
        return None

    def novo_de(self, frame: int | Fraction) -> int | Fraction | None:
        """O frame do corte novo que casa com um frame do corte antigo."""
        peca = self.peca_de(frame)
        if peca is None:
            return None
        return peca.new_in + (frame - peca.old_in)

    def peca_antes(self, frame: int | Fraction) -> Peca | None:
        """A ultima peca que termina antes do frame no corte antigo."""
        melhor: Peca | None = None
        for peca in self.pecas:
            if peca.old_out <= frame and (melhor is None or peca.old_out > melhor.old_out):
                melhor = peca
        return melhor

    def ultima_coberta(self, frame: int | Fraction) -> int | None:
        """O ultimo frame do corte antigo coberto antes do frame informado."""
        peca = self.peca_antes(frame)
        return None if peca is None else peca.old_out - 1

    @property
    def delta_da_ultima(self) -> int | None:
        """O deslocamento da ultima peca do verify, em frames."""
        if not self.pecas:
            return None
        return self.pecas[-1].delta


def montar_pecas(old: CutIR, new: CutIR) -> Pecas:
    """Monta as pecas do verify a partir dos dois cortes.

    As pecas candidatas vem do casamento por frame; a resolucao por ordem
    escolhe as que casam e apara as perdedoras, e o que sobra sem
    correspondencia e o material que o corte novo nao cobre.
    """
    resolucao = resolver_reuso(casar_eventos(old, new), old, new)
    return Pecas(tuple(resolucao.casados), tuple(resolucao.nao_resolvidos))
