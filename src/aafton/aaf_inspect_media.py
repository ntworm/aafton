"""Media of an AAF for the inspect with resolved media and for the apply.

The PhaseSlip standalone reads the inspect JSON and only then plays the media,
so the inspect has to say, per media, where the file is, how many channels it
has and whether the AAF carries it inside. The apply needs the same reading to
write the edited AAF back with the media as it came in.
"""

from __future__ import annotations

import wave
from pathlib import Path

import aaf2

from .aaf_read import read_aaf
from .ir import SessionIR
from .media_extract import read_audio_metadata, resolve_asset
from .pipeline import media_roots
from .report import Report

# Code of the issue of an AAF that the reader could not open.
AAF_UNREADABLE = "AAF_UNREADABLE"


def embedded_media_ids(path: str | Path, session: SessionIR) -> set[str]:
    """Ids of the media whose SourceMob carries essence inside the AAF.

    The reader fills ``embedded_mob_id`` for linked media too (it keeps the id
    of the file SourceMob), so that id alone does not say the media is inside
    the AAF: only a SourceMob with essence does, the same test the extraction
    makes.
    """
    wanted = {
        asset.embedded_mob_id.lower(): asset.id
        for asset in session.media
        if asset.embedded_mob_id is not None
    }
    found: set[str] = set()
    if not wanted:
        return found
    with aaf2.open(str(path), "r") as f:
        for mob in f.content.mobs:
            if type(mob).__name__ != "SourceMob":
                continue
            asset_id = wanted.get(str(getattr(mob, "mob_id", "") or "").lower())
            if asset_id is not None and getattr(mob, "essence", None) is not None:
                found.add(asset_id)
    return found


def complete_media(session: SessionIR, roots: list[Path], report: Report) -> None:
    """Resolves the media that is still missing and fills its metadata.

    A media that does not resolve stays missing, with its issue in the
    report, and does not stop the others. Every resolved media takes its
    channels, rate and length from its file, the extracted WAV included, as
    ``resolve_asset`` does for the linked one: the reader fills the rate with
    the one of the session and the length with the one the AAF declares.
    """
    for asset in session.media:
        if asset.status != "resolved":
            try:
                resolve_asset(asset, roots, report, taxa_tc=session.timecode_rate)
            except Exception as exc:  # noqa: BLE001 - one media must not stop the others
                asset.status = "missing"
                report.error(
                    "MEDIA_RESOLVE_ERROR",
                    f"could not resolve the media {asset.filename!r}: {exc}",
                    {"media_id": asset.id},
                )
                continue
        if asset.status != "resolved" or not asset.resolved_path:
            continue
        try:
            meta = read_audio_metadata(Path(asset.resolved_path))
        except (wave.Error, OSError, ValueError) as exc:
            report.warning(
                "MEDIA_METADATA_UNREADABLE",
                f"could not read the metadata of {asset.resolved_path}: {exc}",
                {"media_id": asset.id},
            )
            continue
        asset.channels = meta["channels"]
        asset.sample_rate = meta["sample_rate"]
        asset.duration_samples = meta["duration_samples"]


def read_with_media(
    path: str | Path,
    report: Report,
    *,
    extract_dir: str | Path | None,
    audio_dir: str | None = None,
) -> tuple[SessionIR | None, int]:
    """Reads the AAF, extracts the embedded media and resolves the linked one.

    Returns the session, or None when the AAF does not open, and how many
    issues the reading itself left: the issues after that index come from
    the media resolution. A missing file still raises ``FileNotFoundError``,
    as every other command of the line does.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(2, "file not found", str(path))
    try:
        session = read_aaf(path, report, extract_dir=extract_dir)
    except Exception as exc:  # noqa: BLE001 - an AAF that does not open is an error of the reading
        report.error(AAF_UNREADABLE, f"the AAF could not be read: {exc}", {"path": str(path)})
        return None, len(report.issues)
    read_issues = len(report.issues)
    complete_media(session, media_roots(str(path), audio_dir), report)
    return session, read_issues


def errors_by_stage(report: Report, read_issues: int) -> list[dict]:
    """The error issues of the report, each with the stage it came from."""
    return [
        {
            "code": issue.code,
            "message": issue.message,
            "stage": "read" if index < read_issues else "media",
        }
        for index, issue in enumerate(report.issues)
        if issue.severity == "error"
    ]


def clip_to_dict(clip, rotulo) -> dict:
    """One clip of the inspect JSON, with the ids the PhaseSlip standalone edits by."""
    return {
        "id": clip.id,
        "name": clip.name,
        "track_id": clip.track_id,
        "channel_index": clip.channel_index,
        "source_channel": clip.source_channel,
        "muted": clip.muted,
        "media_id": clip.media_id,
        "start_samples": clip.timeline_start_samples,
        "end_samples": clip.timeline_end_samples,
        "duration_samples": clip.duration_samples,
        "source_offset_samples": clip.source_offset_samples,
        "gain_linear": clip.gain_linear,
        "tc_in": rotulo(clip.timeline_start_samples),
        "tc_out": rotulo(clip.timeline_end_samples),
    }
