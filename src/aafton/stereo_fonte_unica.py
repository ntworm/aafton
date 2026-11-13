"""Estereo que vem de uma fonte unica: os dois canais do mesmo arquivo.

O Resolve grava o combiner com dois SourceClips que apontam para o mesmo
arquivo estereo, cada um com o seu proprio SourceMob. O leitor transforma
isso em dois clips, um por canal, que ocupam o mesmo tempo na mesma track.
No Live eles viram dois clips sobrepostos, e a exportacao para em
WRITE_OVERLAP. Como o arquivo ja e estereo, o par inteiro vira um clip so.
"""

from __future__ import annotations

import os

from .ir import ClipIR, MediaAsset, SessionIR, TrackIR
from .report import Report


def colapsar_fonte_unica(session: SessionIR, report: Report) -> int:
    """Colapsa os pares de canal que vem do mesmo arquivo estereo.

    Devolve quantos pares viraram um clip so, no total.
    """
    total = 0
    for track in session.tracks:
        total += _colapsar_track(session, track, report)
    return total


def _colapsar_track(session: SessionIR, track: TrackIR, report: Report) -> int:
    """Colapsa os pares de uma track e avisa quantos foram."""
    if track.channel_count != 2 or not track.clips:
        return 0

    colapsados = [
        clip_dir
        for clip_esq, clip_dir in _pares_do_evento(track)
        if _mesma_fonte(session, clip_esq, clip_dir)
    ]
    if not colapsados:
        return 0

    removidos = {id(clip) for clip in colapsados}
    track.clips = [clip for clip in track.clips if id(clip) not in removidos]
    report.info(
        "STEREO_SINGLE_SOURCE_COLLAPSED",
        f"in the track {track.name!r}, {len(colapsados)} channel pair(s) that came from the "
        "same stereo file became a single clip, to avoid leaving overlapping clips "
        "on the same track of Live",
        {"track": track.name, "count": len(colapsados)},
    )
    return len(colapsados)


def _pares_do_evento(track: TrackIR) -> list[tuple[ClipIR, ClipIR]]:
    """Pares (canal 0, canal 1) que descrevem o mesmo evento da track."""
    por_evento: dict[int, dict[int, ClipIR]] = {}
    for clip in track.clips:
        canais = por_evento.setdefault(clip.event_index, {})
        canais.setdefault(clip.channel_index, clip)
    return [(canais[0], canais[1]) for canais in por_evento.values() if 0 in canais and 1 in canais]


def _mesma_fonte(session: SessionIR, clip_esq: ClipIR, clip_dir: ClipIR) -> bool:
    """Os dois clips do par sao o mesmo arquivo estereo, no mesmo lugar."""
    asset_esq = session.media_by_id(clip_esq.media_id) if clip_esq.media_id else None
    asset_dir = session.media_by_id(clip_dir.media_id) if clip_dir.media_id else None
    if asset_esq is None or asset_dir is None:
        return False
    if asset_esq.channels != 2 or asset_dir.channels != 2:
        return False
    if not _mesmo_arquivo(asset_esq, asset_dir):
        return False
    if not _canais_um_e_dois(clip_esq, clip_dir):
        return False
    if not _mesmo_lugar(clip_esq, clip_dir):
        return False
    return _mesmos_fades(clip_esq, clip_dir)


def _canais_um_e_dois(clip_esq: ClipIR, clip_dir: ClipIR) -> bool:
    """O canal esquerdo le o canal 1 do arquivo e o direito o canal 2.

    Par que le o mesmo canal duas vezes (dual mono), canais trocados ou canal
    que o AAF nao declara nao vira estereo normal: colapsar tocaria o arquivo
    como L/R, que nao e o que a sessao de origem toca.
    """
    return clip_esq.source_channel == 1 and clip_dir.source_channel == 2


def _mesmo_lugar(clip_esq: ClipIR, clip_dir: ClipIR) -> bool:
    """Os dois clips do par comecam, terminam e leem no mesmo ponto da fonte."""
    if clip_esq.timeline_start_samples != clip_dir.timeline_start_samples:
        return False
    if clip_esq.timeline_end_samples != clip_dir.timeline_end_samples:
        return False
    if clip_esq.source_offset_samples != clip_dir.source_offset_samples:
        return False
    return True


def _mesmo_arquivo(asset_esq: MediaAsset, asset_dir: MediaAsset) -> bool:
    """Dois assets que apontam para o mesmo arquivo de audio."""
    if asset_esq.id == asset_dir.id:
        return True
    if asset_esq.resolved_path and asset_dir.resolved_path:
        return _mesmo_caminho(asset_esq.resolved_path, asset_dir.resolved_path)
    if asset_esq.filename != asset_dir.filename:
        return False
    if asset_esq.declared_location and asset_dir.declared_location:
        return _mesmo_caminho(asset_esq.declared_location, asset_dir.declared_location)
    return False


def _mesmo_caminho(um: str, outro: str) -> bool:
    """Dois caminhos que apontam para o mesmo arquivo."""
    return os.path.normcase(os.path.normpath(um)) == os.path.normcase(os.path.normpath(outro))


def _mesmos_fades(clip_esq: ClipIR, clip_dir: ClipIR) -> bool:
    """Os dois clips do par entram, saem e soam do mesmo jeito.

    Alem dos fades, ganho, mudo e envelope de ganho tem de ser iguais: ao
    colapsar, o clip do canal direito sai e so o do esquerdo fica, entao um
    ganho proprio do direito se perderia.
    """
    if not _mesmo_ganho(clip_esq, clip_dir):
        return False
    return _fade_igual(clip_esq.fade_in, clip_dir.fade_in) and _fade_igual(
        clip_esq.fade_out, clip_dir.fade_out
    )


def _mesmo_ganho(clip_esq: ClipIR, clip_dir: ClipIR) -> bool:
    """Ganho, mudo e envelope de ganho iguais nos dois clips do par."""
    return (
        clip_esq.gain_linear == clip_dir.gain_linear
        and clip_esq.muted == clip_dir.muted
        and getattr(clip_esq, "gain_points", None) == getattr(clip_dir, "gain_points", None)
    )


def _fade_igual(um, outro) -> bool:
    """Dois fades iguais: nenhum dos dois, ou os dois com o mesmo tipo e duracao."""
    if um is None or outro is None:
        return um is None and outro is None
    return (um.kind, um.duration_samples) == (outro.kind, outro.duration_samples)
