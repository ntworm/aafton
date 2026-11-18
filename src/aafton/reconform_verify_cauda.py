"""Oraculo da cauda no verify do reconform, pelos dois cortes.

A cauda (o material depois do ultimo evento do corte antigo) segue a ultima
peca do verify, mas so ate o espaco livre no corte novo: o que nao cabe vai
para a track [saiu], na emenda. A regra e a mesma do apply, calculada aqui sem
o mapa de tempo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .reconform_verify import (
    _Ctx,
    _base_saiu,
    _frames,
    _para_samples,
    _samples,
)

# Uma amostra: a tolerancia da posicao conferida.
_TOLERANCIA = 1


@dataclass(frozen=True)
class Cauda:
    """Comeco da cauda no corte antigo e no novo, e o espaco livre, em frames."""

    fim_antigo: int
    inicio_novo: int
    livre: int | None


def _delta_em_frames(ctx: _Ctx) -> int | None:
    """Deslocamento do ultimo frame coberto do ultimo plano antigo, em frames.

    A cauda segue a ultima peca do verify, como o mapa de tempo segue a
    ultima peca casada: e o deslocamento dela que leva o material depois do
    ultimo evento antigo para o corte novo.
    """
    return ctx.pecas.delta_da_ultima


def _espaco_livre(ctx: _Ctx, inicio: int) -> int | None:
    """Espaco livre do corte novo a partir do frame informado, em frames.

    O espaco livre e o que sobra depois do material que o corte novo casa com o
    corte antigo: None quando nenhuma peca comeca depois do frame (a cauda
    pode ocupar o resto do corte novo) e zero quando uma peca ja cobre esse
    frame (a cauda nao cabe).
    """
    livre: int | None = None
    for peca in ctx.pecas.pecas:
        if peca.new_in <= inicio < peca.new_out:
            return 0
        if peca.new_in > inicio and (livre is None or peca.new_in - inicio < livre):
            livre = peca.new_in - inicio
    return livre


def cauda(ctx: _Ctx) -> Cauda | None:
    """Onde a cauda comeca nos dois cortes e quanto dela cabe no corte novo."""
    if not ctx.old.events:
        return None
    delta = _delta_em_frames(ctx)
    if delta is None:
        return None
    fim_antigo = max(ev.record_out for ev in ctx.old.events)
    inicio_novo = fim_antigo + delta
    return Cauda(fim_antigo, inicio_novo, _espaco_livre(ctx, inicio_novo))


def _deslocamento(ctx: _Ctx, dados: Cauda) -> int:
    """Deslocamento, em samples, da cauda do corte antigo para o novo."""
    return _samples(ctx, dados.inicio_novo) - _para_samples(ctx, dados.fim_antigo)


def e_cauda(ctx: _Ctx, inicio: int) -> bool:
    """Verdadeiro quando o sample esta depois do ultimo evento do corte antigo."""
    if not ctx.old.events:
        return False
    return _frames(ctx, inicio) >= max(ev.record_out for ev in ctx.old.events)


def oraculo_cauda(ctx: _Ctx, inicio: int, frame) -> tuple[str, int | None]:
    """('map', posicao nova) ou ('saiu', posicao na emenda) para um sample da cauda."""
    dados = cauda(ctx)
    if dados is None:
        return "fora", None
    posicao = inicio + _deslocamento(ctx, dados)
    if dados.livre is not None and frame - dados.fim_antigo >= dados.livre:
        return "saiu", posicao
    return "map", posicao


def fronteira_da_cauda(ctx: _Ctx) -> int | None:
    """Sample onde a cauda deixa de caber no corte novo, quando deixa."""
    dados = cauda(ctx)
    if dados is None or dados.livre is None:
        return None
    return _para_samples(ctx, dados.fim_antigo + dados.livre)


def conferir_cauda(ctx: _Ctx, nome: str, pedaco: tuple[int, int, int], aplicado: dict) -> None:
    """Confere a peca da cauda que nao cabe: ela fica na track [saiu]."""
    inicio, fim, loop = pedaco
    dados = cauda(ctx)
    if dados is None:
        return
    esperado = inicio + _deslocamento(ctx, dados)
    duracao = fim - inicio
    # O modulo de saiu importa este aqui: a importacao fica dentro da funcao
    # para nao fechar o ciclo entre os dois.
    from .reconform_verify_saiu import _clips_saiu, _cobre

    achou = any(
        abs(clip.start + (loop - clip.loop_start) - esperado) <= _TOLERANCIA
        and _cobre(clip, loop, duracao)
        for clip in _clips_saiu(aplicado, _base_saiu(nome) or nome)
    )
    if not achou:
        ctx.problema(
            "RECONFORM_VERIFY_POSITION", nome, f"tail piece at {inicio} expected at {esperado}"
        )
