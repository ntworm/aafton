"""Parte de aaf_write: transicoes, canais, clips e sobreposicoes.

Na fase 1, _validate_session_overlaps foi dividida em etapas (os helpers novos
saem dela); as demais definicoes vieram de aaf_write sem mudanca.
"""

from .ir import SessionIR

from .report import Report


def _is_supported_transition(previous_clip, clip, overlap: int) -> bool:
    return (
        previous_clip is not None
        and clip.fade_in is not None
        and previous_clip.fade_out is not None
        and clip.fade_in.duration_samples == overlap
        and previous_clip.fade_out.duration_samples == overlap
    )


def _canais_da_track(track, track_channels: int) -> list[int]:
    """Escolhe os canais que precisam ser conferidos numa track."""
    if track_channels == 2:
        return [0]
    if track.clips and all(c.channel_index == 0 for c in track.clips) and track_channels > 1:
        return list(range(track_channels))
    return sorted(list(set(c.channel_index for c in track.clips) or {0}))


def _clips_do_canal(track, canal: int, track_channels: int):
    """Seleciona e ordena por inicio os clips que pertencem a um canal da track."""
    if track_channels == 2:
        has_ch0 = any(c.channel_index == 0 for c in track.clips)
        return sorted(
            (c for c in track.clips if (c.channel_index == 0 if has_ch0 else True)),
            key=lambda x: x.timeline_start_samples,
        )
    if track_channels > 1 and all(c.channel_index == 0 for c in track.clips):
        return sorted(track.clips, key=lambda x: x.timeline_start_samples)
    return sorted(
        (c for c in track.clips if c.channel_index == canal),
        key=lambda x: x.timeline_start_samples,
    )


def _conferir_clipes_do_canal(session: SessionIR, track, clips_do_canal, report: Report) -> None:
    """Percorre os clips de um canal e reporta as sobreposicoes nao suportadas."""
    cursor = 0
    previous_clip = None
    for clip in clips_do_canal:
        start_local = clip.timeline_start_samples - session.start_timecode_samples
        if start_local > cursor:
            gap = start_local - cursor
            cursor += gap
        if start_local < cursor:
            overlap = cursor - start_local
            if not _is_supported_transition(previous_clip, clip, overlap):
                report.error(
                    "WRITE_OVERLAP",
                    f"Unsupported overlap of {overlap} samples in the track {track.name!r}, clip {clip.name!r}",
                    {
                        "track": track.name,
                        "clip": clip.name,
                        "overlap_samples": overlap,
                    },
                )
                continue
            cursor -= overlap

        duration = clip.timeline_end_samples - clip.timeline_start_samples
        cursor += duration
        previous_clip = clip


def _validate_session_overlaps(session: SessionIR, report: Report) -> None:
    for track in session.tracks:
        if getattr(track, "is_video", False):
            continue
        track_channels = getattr(track, "channel_count", 1) or 1
        for canal in _canais_da_track(track, track_channels):
            clips_do_canal = _clips_do_canal(track, canal, track_channels)
            _conferir_clipes_do_canal(session, track, clips_do_canal, report)
