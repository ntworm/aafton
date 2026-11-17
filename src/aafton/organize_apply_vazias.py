"""Tracks do editor que a organizacao esvaziou (P4).

Depois de aplicar a proposta, a track do editor que tinha clip e ficou sem
nenhum nao tem mais o que levar para o set do Live: ela sai da sessao. A track
que ja estava vazia antes de organizar fica como esta, e a track esvaziada que
tem envelope proprio tambem fica, com aviso no relatorio.

As tracks fora do alcance da organizacao (video, LTC, mais de dois canais e as
irmas do som direto) nunca perdem clip: elas entram no mesmo teste e ficam.
"""

from __future__ import annotations

from .ir import SessionIR, TrackIR
from .report import Report


def tracks_com_clips(session: SessionIR) -> set[str]:
    """Os ids das tracks que tem clip antes de organizar."""
    return {track.id for track in session.tracks if track.clips}


def _tem_envelope(track: TrackIR) -> bool:
    """Diz se a track tem envelope de automacao proprio."""
    return bool(track.gain_points or track.pan_points)


def _relatar_envelope(track: TrackIR, report: Report | None) -> None:
    """Avisa que a track esvaziada ficou no set por ter envelope proprio."""
    if report is None:
        return
    report.info(
        "ORGANIZE_EMPTY_TRACK_KEPT",
        f"the track {track.name!r} was emptied by the organization and stays: "
        "it has an envelope of its own",
        {"track": track.name},
    )


def remover_tracks_esvaziadas(
    session: SessionIR,
    com_clips: set[str],
    report: Report | None = None,
) -> list[TrackIR]:
    """Tira da sessao as tracks do editor que a organizacao esvaziou.

    ``com_clips`` traz os ids das tracks que tinham clip antes de organizar: a
    track que ja estava vazia fica como esta. A track esvaziada que tem
    envelope proprio tambem fica, e o relatorio diz o nome dela. Devolve as
    tracks que sairam da sessao.
    """
    ficam: list[TrackIR] = []
    removidas: list[TrackIR] = []
    for track in session.tracks:
        if track.id not in com_clips or track.clips:
            ficam.append(track)
            continue
        if _tem_envelope(track):
            ficam.append(track)
            _relatar_envelope(track, report)
            continue
        removidas.append(track)
    session.tracks = ficam
    return removidas
