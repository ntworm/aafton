"""Track de video do AAF dentro do SessionIR.

O bloco que monta a track de video saiu do modulo video.py: video.py decide
qual arquivo entra e com que ancora, video_track.py monta a track, o asset e
o clip que apontam para o video.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .ir import ClipIR, MediaAsset, SessionIR, TrackIR
from .report import Report


def add_video_track(
    session: SessionIR,
    video_path: Path,
    start_samples: int,
    duration_sec: Optional[float],
    final_duration_sec: float,
    aaf_source_offset: int,
    anchor_source: Optional[str],
    report: Report,
) -> None:
    """Cria a track de video mutada, o asset e o clip que apontam para o video."""
    track_name = video_path.stem
    for sufixo in ("_LiveProxy_TC", "_LiveProxy"):
        if track_name.endswith(sufixo):
            track_name = track_name.removesuffix(sufixo)
    track = TrackIR(
        id="t_video",
        source_index=-1,
        original_source_indices=[-1],
        name=track_name,
        kind="audio",
        channel_count=1,
    )
    track.is_video = True
    track.muted = True

    report.info(
        "VIDEO_TRACK_MUTED", "The video track was muted so it does not overlap the AAF audio", {}
    )

    asset = MediaAsset(
        id="m_video",
        filename=video_path.name,
        channels=2,
        sample_rate=44100,
        duration_samples=int((duration_sec or final_duration_sec) * 44100),
        status="resolved",
        declared_location=str(video_path),
        resolved_path=str(video_path),
    )
    session.media.append(asset)

    clip = ClipIR(
        id="c_video",
        track_id=track.id,
        channel_index=0,
        event_index=0,
        name=video_path.stem,
        media_id=asset.id,
        timeline_start_samples=start_samples,
        timeline_end_samples=start_samples + int(final_duration_sec * session.sample_rate),
        source_offset_samples=aaf_source_offset,
        gain_linear=1.0,
        anchor_source=anchor_source,
    )
    track.clips.append(clip)
    session.tracks.insert(0, track)
