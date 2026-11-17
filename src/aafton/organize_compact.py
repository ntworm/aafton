"""Compactacao das tracks de destino da organizacao de sessao.

Junta as unidades de cada bloco (mesmo tipo e mesma contagem de canais) no
menor numero de tracks, sem track vazia entre clips que tocam juntos, e mantem
os monos antes dos estereos dentro de cada grupo. O modulo e puro: conhece
apenas as homes e a ordem do editor, nao o relatorio nem a sessao.
"""

from __future__ import annotations

from .organize_home import _HomeBuilder, _unit_fits
from .organize_units import Unit

# Posicao de cada track do editor na ordem das agrupagens das unidades.
OrdemEditor = dict[tuple[int, ...], int]


def _blocos(homes: list[_HomeBuilder]) -> list[tuple[str, int, list[_HomeBuilder]]]:
    """Agrupa as homes por tipo e, dentro de cada tipo, mono antes de estereo."""
    por_tipo: dict[str, list[_HomeBuilder]] = {}
    for home in homes:
        por_tipo.setdefault(home.kind, []).append(home)
    blocos: list[tuple[str, int, list[_HomeBuilder]]] = []
    for kind, do_tipo in por_tipo.items():
        por_canais: dict[int, list[_HomeBuilder]] = {}
        for home in do_tipo:
            por_canais.setdefault(home.channel_count, []).append(home)
        for canais in sorted(por_canais):
            blocos.append((kind, canais, por_canais[canais]))
    return blocos


def _posicao_editor(home: _HomeBuilder, ordem_editor: OrdemEditor) -> int:
    """Posicao da home na ordem do editor: a da track de origem, quando ela existe."""
    if home.source_indices in ordem_editor:
        return ordem_editor[home.source_indices]
    if not home.units:
        return len(ordem_editor)
    return min(ordem_editor[u.source_tracks] for u in home.units)


def _identidades(
    bloco: list[_HomeBuilder], ordem_editor: OrdemEditor
) -> list[tuple[str | None, tuple[int, ...]]]:
    """Identidades (source_track, source_indices) das homes do bloco, na ordem do editor."""
    ordenadas = sorted(bloco, key=lambda h: _posicao_editor(h, ordem_editor))
    return [(h.source_track, h.source_indices) for h in ordenadas]


def _primeira_track_que_cabe(spans: list[list[tuple[int, int]]], unit: Unit) -> int | None:
    """Indice da primeira track onde a unidade cabe, ou None quando nenhuma cabe."""
    for idx, track_spans in enumerate(spans):
        if _unit_fits(track_spans, unit):
            return idx
    return None


def _empacotar(unidades: list[Unit], ordem_editor: OrdemEditor) -> list[list[Unit]]:
    """Empacota as unidades no menor numero de tracks, sem track vazia entre elas."""
    # A ordem do editor desempata as unidades que comecam no mesmo sample.
    ordenadas = sorted(
        unidades,
        key=lambda u: (u.start_samples, ordem_editor[u.source_tracks], u.origin_keys),
    )
    tracks: list[list[Unit]] = []
    spans: list[list[tuple[int, int]]] = []
    for unit in ordenadas:
        alvo = _primeira_track_que_cabe(spans, unit)
        if alvo is None:
            tracks.append([unit])
            spans.append([(unit.start_samples, unit.end_samples)])
            continue
        tracks[alvo].append(unit)
        spans[alvo].append((unit.start_samples, unit.end_samples))
    return tracks


def compactar_homes(
    homes: list[_HomeBuilder], ordem_editor: OrdemEditor
) -> tuple[list[_HomeBuilder], dict[str, tuple[int, int]]]:
    """Compacta as homes: menos tracks por bloco, com os monos antes dos estereos.

    Devolve as homes novas e, por tipo, quantas tracks havia antes e quantas
    ficaram depois da compactacao. Home sem nenhuma unidade some do resultado.
    """
    cheias = [h for h in homes if h.units]
    novas: list[_HomeBuilder] = []
    contagem: dict[str, tuple[int, int]] = {}
    for kind, canais, bloco in _blocos(cheias):
        unidades = [u for home in bloco for u in home.units]
        tracks = _empacotar(unidades, ordem_editor)
        identidades = _identidades(bloco, ordem_editor)
        antes, depois = contagem.get(kind, (0, 0))
        contagem[kind] = (antes + len(bloco), depois + len(tracks))
        for idx, track in enumerate(tracks):
            # A track i herda a identidade da home i do bloco; as novas nao tem.
            source_track, source_indices = (
                identidades[idx] if idx < len(identidades) else (None, ())
            )
            novas.append(
                _HomeBuilder(
                    kind,
                    source_track,
                    source_indices,
                    canais,
                    units=list(track),
                    spans=[(u.start_samples, u.end_samples) for u in track],
                )
            )
    return novas, contagem
