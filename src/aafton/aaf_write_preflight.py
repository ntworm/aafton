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


def _transition_conflicts_with_automation(
    previous_clip, clip, overlap: int, gain_points: list[tuple[int, float]]
) -> bool:
    """Verifica se cortes de automacao quebram a geometria da transicao.

    Um crossfade de comprimento ``overlap`` exige que o trecho do clipe
    anterior adjacente a transicao e o trecho inicial do clipe seguinte tenham
    comprimento >= ``overlap``, sem subdivisao da regiao de overlap.
    """
    if not gain_points or not _is_supported_transition(previous_clip, clip, overlap):
        return False

    prev_dur = previous_clip.timeline_end_samples - previous_clip.timeline_start_samples
    prev_start = previous_clip.timeline_start_samples
    prev_cuts = sorted({t - prev_start for t, _ in gain_points if 0 < t - prev_start < prev_dur})
    prev_adj = prev_dur - prev_cuts[-1] if prev_cuts else prev_dur
    if prev_adj < overlap:
        return True

    cur_dur = clip.timeline_end_samples - clip.timeline_start_samples
    cur_start = clip.timeline_start_samples
    cur_cuts = sorted({t - cur_start for t, _ in gain_points if 0 < t - cur_start < cur_dur})
    cur_adj = cur_cuts[0] if cur_cuts else cur_dur
    if cur_adj < overlap:
        return True

    return False


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
            if _transition_conflicts_with_automation(
                previous_clip, clip, overlap, track.gain_points
            ):
                report.error(
                    "WRITE_TRANSITION_SEGMENTED_AUTOMATION_UNSUPPORTED",
                    f"transition between {previous_clip.name!r} and {clip.name!r} conflicts with "
                    f"automation segmentation in the overlap region ({overlap} samples)",
                    {
                        "track": track.name,
                        "previous_clip": previous_clip.name,
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
