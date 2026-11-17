"""Desmarque por chave de track de destino na revisao da organizacao.

Desmarcar uma track de destino pela chave faz a proposta ser montada de
novo: a compactacao reempacota o bloco e as chaves das tracks de destino
mudam. A chave desmarcada pode aparecer so numa disposicao ja reempacotada,
entao este modulo repete a disposicao ate um ponto fixo: enquanto aparecer uma
chave desmarcada nova na disposicao corrente, as origens aceitas dela entram
como desmarcadas. O teto de rodadas corta a repeticao e mantem o resultado
deterministico.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, NamedTuple

from .organize_home import _HomeBuilder
from .organize_units import OriginKey, Unit

if TYPE_CHECKING:
    from .ir import TrackIR
    from .organize_plan import Edits, LeftTrack

MAX_RODADAS = 4


class DisposicaoDesmarque(NamedTuple):
    """Resultado do desmarque por chave: a primeira disposicao e a final."""

    primeira: list[_HomeBuilder]
    final: list[_HomeBuilder]
    left_tracks: list[LeftTrack]
    compactacao: dict[str, tuple[int, int]]
    disabled_origins: set[OriginKey]


def _origens_aceitas(home: _HomeBuilder) -> set[OriginKey]:
    """Chaves de origem das unidades aceitas de uma track de destino."""
    return {chave for unidade in home.units if unidade.accepted for chave in unidade.origin_keys}


def _par_de_processamento(home: _HomeBuilder) -> tuple[str, tuple[OriginKey, ...]]:
    """Identidade do processamento: a chave e as origens aceitas da track."""
    return (home.key, tuple(sorted(_origens_aceitas(home))))


def _desmarcadas_novas(
    homes: list[_HomeBuilder],
    edits: Edits,
    processados: set[tuple[str, tuple[OriginKey, ...]]],
) -> list[_HomeBuilder]:
    """Tracks desmarcadas da disposicao corrente que ainda nao entraram."""
    achadas: list[_HomeBuilder] = []
    for home in homes:
        if edits.tracks.get(home.key) is not False:
            continue
        if _par_de_processamento(home) in processados:
            continue
        achadas.append(home)
    return achadas


def desmarcar_por_chave(
    raw_units: list[Unit],
    ordered_kinds: list[str],
    empty_tracks: list[TrackIR],
    edits: Edits,
    compact: bool,
) -> DisposicaoDesmarque:
    """Repete a disposicao ate as chaves desmarcadas encontrarem o seu par.

    Devolve a primeira disposicao, a disposicao final, as tracks mantidas, a
    contagem da compactacao e as origens acumuladas como desmarcadas. Uma
    chave de edits.tracks sem correspondencia em nenhuma rodada e ignorada.
    """
    from .organize_plan import _apply_unit_override, _layout_units

    def _disposicao(
        edits_atual: Edits,
    ) -> tuple[list[_HomeBuilder], list[LeftTrack], dict[str, tuple[int, int]]]:
        unidades = [_apply_unit_override(u, edits_atual) for u in raw_units]
        return _layout_units(unidades, ordered_kinds, empty_tracks, compact=compact)

    primeira, left_tracks, compactacao = _disposicao(edits)
    atual = primeira
    disabled: set[OriginKey] = set()
    processados: set[tuple[str, tuple[OriginKey, ...]]] = set()
    for _ in range(MAX_RODADAS):
        novas = _desmarcadas_novas(atual, edits, processados)
        if not novas:
            break
        for home in novas:
            processados.add(_par_de_processamento(home))
            disabled |= _origens_aceitas(home)
        merged = {**edits.origins, **{chave: False for chave in disabled}}
        atual, left_tracks, compactacao = _disposicao(replace(edits, origins=merged))
    return DisposicaoDesmarque(primeira, atual, left_tracks, compactacao, disabled)
