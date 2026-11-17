"""Escolha da track [saiu] que recebe a automacao de um trecho removido.

Primeiro pela origem no corte antigo: a track cujas pecas vieram do mesmo
trecho. Sem peca no trecho, pela posicao nova: a track com o clip que cobre os
eventos; sem nenhum, a primeira track [saiu].
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .reconform_automation import _SaiuDest, _SaiuGroup, _SaiuTrack

# Uma amostra: a folga para dizer que um clip cobre os eventos.
_TOLERANCIA = 1


def _sobreposicao(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Quantas amostras os dois intervalos tem em comum."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def _destino_pela_origem(dest: _SaiuDest, group: _SaiuGroup) -> _SaiuTrack | None:
    """Track [saiu] que recebeu a peca de audio do mesmo trecho removido.

    O trecho e reconhecido no corte antigo: a track cujas pecas tem a maior
    sobreposicao com o trecho dos eventos. None quando nenhuma peca toca nele.
    """
    alvo = (group.old_in, group.old_out)
    melhor, maior = None, 0
    for track in dest.tracks:
        comum = sum(_sobreposicao(alvo, faixa) for faixa in track.old_ranges)
        if comum > maior:
            melhor, maior = track, comum
    return melhor


def _destino(dest: _SaiuDest, group: _SaiuGroup) -> _SaiuTrack | None:
    """Track [saiu] que tem o clip que cobre o trecho removido dos eventos.

    O clip que cobre o trecho inteiro vale mais do que o que so encosta nele;
    entre os que cobrem, vale o que comeca mais em cima do trecho. Quando
    nenhum clip cobre a posicao nova, o primeiro destino [saiu] recebe.
    """
    if not dest.tracks:
        return None
    pela_origem = _destino_pela_origem(dest, group)
    if pela_origem is not None:
        return pela_origem
    melhor: _SaiuTrack | None = None
    melhor_nota = (2, 0)
    for track in dest.tracks:
        for inicio, fim in track.ranges:
            if not (inicio - _TOLERANCIA <= group.new_in and fim + _TOLERANCIA >= group.new_out):
                continue
            nota = (0, -inicio)
            if nota < melhor_nota:
                melhor_nota = nota
                melhor = track
    return melhor if melhor is not None else dest.tracks[0]
