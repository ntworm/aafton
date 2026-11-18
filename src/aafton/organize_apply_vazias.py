"""Tracks do editor que a organizacao esvaziou (P4).

Depois de aplicar a proposta, a track do editor que tinha clip e ficou sem
nenhum nao tem mais o que levar para o set do Live: ela sai da sessao. A
envelope de automacao dela tambem sai: o volume da track de origem viaja com
o clip para a track de destino (organize_envelope), e a track vazia nao tem
mais clip que ganha-lo. A track que ja estava vazia antes de organizar fica
como esta.

As tracks fora do alcance da organizacao (video, LTC, mais de dois canais e as
irmas do som direto) nunca perdem clip: elas entram no mesmo teste e ficam.
"""

from __future__ import annotations

from .ir import SessionIR, TrackIR
from .report import Report


def tracks_com_clips(session: SessionIR) -> set[str]:
    """Os ids das tracks que tem clip antes de organizar."""
    return {track.id for track in session.tracks if track.clips}


def remover_tracks_esvaziadas(
    session: SessionIR,
    com_clips: set[str],
    report: Report | None = None,
) -> list[TrackIR]:
    """Tira da sessao as tracks do editor que a organizacao esvaziou.

    ``com_clips`` traz os ids das tracks que tinham clip antes de organizar: a
    track que ja estava vazia fica como esta. A track que a organizacao esvaziou
    sai sempre, mesmo com envelope proprio, porque o envelope foi com os clips
    para as tracks de destino. O ``report`` fica na assinatura para o chamador,
    mas a track esvaziada nao gera mais aviso. Devolve as tracks que sairam da
    sessao.
    """
    ficam: list[TrackIR] = []
    removidas: list[TrackIR] = []
    for track in session.tracks:
        if track.id not in com_clips or track.clips:
            ficam.append(track)
            continue
        removidas.append(track)
    session.tracks = ficam
    return removidas
