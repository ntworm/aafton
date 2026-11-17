"""Resolucao dos nomes de track da revisao na disposicao final.

Desmarcar uma track de destino faz a proposta ser montada de novo: a
compactacao reempacota o bloco e as chaves das tracks de destino mudam. Os
nomes digitados na revisao ficam presos a chave antiga, entao este modulo
resolve cada nome na disposicao final: a ancora do nome (as origens aceitas
da track quando o nome foi digitado) diz com quais clips o nome viaja.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .organize_home import _HomeBuilder
from .organize_units import OriginKey

if TYPE_CHECKING:
    from .organize_plan import Edits

# Nome com ancora, a ancora e o tipo da track que a ancora.
Pendente = tuple[str, str, set[OriginKey], str | None]


def _origens_aceitas(home: _HomeBuilder) -> set[OriginKey]:
    """Chaves de origem das unidades aceitas de uma track de destino."""
    return {chave for unidade in home.units if unidade.accepted for chave in unidade.origin_keys}


def _ancora(
    chave: str,
    edits: Edits,
    primeira_por_chave: dict[str, _HomeBuilder],
) -> set[OriginKey] | None:
    """Origens que ancoram o nome: as declaradas ou as da primeira disposicao."""
    declaradas = edits.track_name_origins.get(chave)
    # Ancora vazia (track so com clips desmarcados) vale como sem ancora.
    if declaradas:
        return set(declaradas)
    home = primeira_por_chave.get(chave)
    if home is None:
        return None
    return _origens_aceitas(home) or None


def _tipo_da_ancora(
    chave: str,
    ancora: set[OriginKey],
    primeira_por_chave: dict[str, _HomeBuilder],
    final_por_chave: dict[str, _HomeBuilder],
) -> str | None:
    """Tipo da track que ancorou o nome: o da primeira ou o da disposicao final."""
    home = primeira_por_chave.get(chave)
    if home is None:
        home = final_por_chave.get(chave)
    if home is not None:
        return home.kind
    # Sem track de mesma chave: o tipo vem das tracks que carregam a ancora.
    tipos = {h.kind for h in final_por_chave.values() if _origens_aceitas(h) & ancora}
    return tipos.pop() if len(tipos) == 1 else None


def _sobreposicao(ancora: set[OriginKey], tipo: str | None, homes: list[_HomeBuilder]) -> int:
    """Maior numero de origens aceitas em comum entre a ancora e uma track do tipo."""
    if tipo is None:
        return 0
    return max((len(ancora & _origens_aceitas(h)) for h in homes if h.kind == tipo), default=0)


def _fica_na_chave(
    home: _HomeBuilder | None,
    ancora: set[OriginKey],
    tipo: str | None,
    homes: list[_HomeBuilder],
) -> bool:
    """A track de mesma chave fica com o nome so se e a que mais carrega a ancora.

    Com a compactacao a chave pode sobreviver com outros clips: dividir uma
    origem so nao basta, ela precisa ter tantas origens da ancora quanto a
    melhor track do mesmo tipo.
    """
    if home is None:
        return False
    comuns = len(_origens_aceitas(home) & ancora)
    return comuns > 0 and comuns >= _sobreposicao(ancora, tipo, homes)


def _ordem(pendentes: list[Pendente], homes: list[_HomeBuilder]) -> list[Pendente]:
    """Ordena os nomes pendentes pela maior sobreposicao e, no empate, pela chave."""
    return sorted(
        pendentes,
        key=lambda item: (-_sobreposicao(item[2], item[3], homes), item[0]),
    )


def _melhor_destino(
    ancora: set[OriginKey],
    tipo: str | None,
    homes: list[_HomeBuilder],
    nomes: dict[str, str],
) -> str | None:
    """Chave da track do mesmo tipo com mais origens em comum entre as sem nome."""
    if tipo is None:
        return None
    melhor: str | None = None
    maior = 0
    for home in homes:
        if home.kind != tipo or home.key in nomes:
            continue
        comuns = len(ancora & _origens_aceitas(home))
        if comuns > maior:
            melhor, maior = home.key, comuns
    return melhor


def resolver_nomes(
    homes_primeira: list[_HomeBuilder],
    homes_finais: list[_HomeBuilder],
    edits: Edits,
) -> Edits:
    """Resolve os nomes das tracks de destino na disposicao final.

    O nome ancorado fica na track de mesma chave quando ela divide uma origem
    aceita com a ancora. Os nomes que sobram vao para a track do mesmo tipo
    (familia inclusa) com mais origens aceitas em comum com a ancora, entre as
    tracks ainda sem nome; sem origem em comum, o nome cai. Uma track recebe
    no maximo um nome.
    """
    primeira_por_chave = {home.key: home for home in homes_primeira}
    final_por_chave = {home.key: home for home in homes_finais}
    nomes: dict[str, str] = {}
    ancoras: dict[str, tuple[OriginKey, ...]] = {}
    pendentes: list[Pendente] = []
    for chave, nome in sorted(edits.track_names.items()):
        ancora = _ancora(chave, edits, primeira_por_chave)
        if ancora is None:
            # Sem ancora o nome fica na chave, se ela existir na disposicao final.
            if chave in final_por_chave:
                nomes[chave] = nome
            continue
        tipo = _tipo_da_ancora(chave, ancora, primeira_por_chave, final_por_chave)
        if _fica_na_chave(final_por_chave.get(chave), ancora, tipo, homes_finais):
            nomes[chave] = nome
            ancoras[chave] = tuple(sorted(ancora))
            continue
        pendentes.append((chave, nome, ancora, tipo))
    livres = [home for home in homes_finais if home.key not in nomes]
    for _chave, nome, ancora, tipo in _ordem(pendentes, livres):
        destino = _melhor_destino(ancora, tipo, homes_finais, nomes)
        if destino is not None:
            nomes[destino] = nome
            ancoras[destino] = tuple(sorted(ancora))
    # Nomes e ancoras ficam nas chaves da disposicao final: a pagina adota
    # esse mapa e as proximas edicoes partem das chaves que ela mostra.
    return replace(edits, track_names=nomes, track_name_origins=ancoras)
