"""Pure serialization of the engine objects into the JSON of the app page.

The functions here read the IR of a session and the issues of a report and
give back plain dictionaries and lists. They never touch the disk and never
call the engine, so the page always has the same shape to render.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from fractions import Fraction

from ..ir import ClipIR, SessionIR, TrackIR
from ..report import ConversionIssue
from ..timecode import TimecodeRate, samples_to_label

# The signs the page shows are the typographic ones, not the ASCII ones.
MINUS = "\u2212"
INFINITY = "\u221e"
MIDDLE_DOT = "\u00b7"
EN_DASH = "\u2013"
NBSP = "\u00a0"
TIMES = "\u00d7"

# How many groups of informational notes the page shows at once.
INFO_LIMIT = 60

_RATE_LABELS: dict[Fraction, str] = {
    Fraction(24, 1): "24 fps",
    Fraction(25, 1): "25 fps",
    Fraction(30, 1): "30 fps",
    Fraction(24000, 1001): "23.976 fps",
    Fraction(30000, 1001): "29.97 fps",
}


def tc(samples: int, session: SessionIR) -> str:
    """Timecode label of an absolute sample position of the session."""
    return samples_to_label(samples, session.timecode_rate, session.sample_rate)


def rate_label(rate: TimecodeRate) -> str:
    """Label of a frame rate: "25 fps", "23.976 fps", "29.97 fps DF"."""
    label = _RATE_LABELS.get(rate.fps)
    if label is None:
        label = f"{float(rate.fps):.3f}".rstrip("0").rstrip(".") + " fps"
    if rate.drop_frame:
        return f"{label} DF"
    return label


def hz_label(sample_rate: int) -> str:
    """Sample rate as a label: "48 000 Hz", with a non-breaking space."""
    return f"{sample_rate:,}".replace(",", NBSP) + " Hz"


def timeline(session: SessionIR) -> dict:
    """Start and end of the session over every clip it holds."""
    starts = [clip.timeline_start_samples for track in session.tracks for clip in track.clips]
    ends = [clip.timeline_end_samples for track in session.tracks for clip in track.clips]
    if not starts:
        start = end = session.start_timecode_samples
    else:
        start = min(starts)
        end = max(ends)
    return {
        "start_samples": start,
        "end_samples": end,
        "start_tc": tc(start, session),
        "end_tc": tc(end, session),
    }


def channels_label(track: TrackIR) -> str:
    """Channel count of a track as a short label: "mono", "stereo", "5.1"."""
    if track.is_video:
        return "video"
    if track.channel_count == 1:
        return "mono"
    if track.channel_count == 2:
        return "stereo"
    if track.channel_count == 6:
        return "5.1"
    return f"{track.channel_count} ch"


def _closest_gap_index(merged: list[list[int]]) -> int:
    """Index of the neighbour pair with the smallest gap between them."""
    best = 0
    best_gap = merged[1][0] - merged[0][1]
    for index in range(1, len(merged) - 1):
        gap = merged[index + 1][0] - merged[index][1]
        if gap < best_gap:
            best = index
            best_gap = gap
    return best


def spans(track: TrackIR, limit: int = 400) -> list[list[int]]:
    """Time spans of the clips of a track, merged down to the limit.

    The neighbours with the smallest gap are merged first, so the lane keeps
    the widest spaces of the track. A merged span never leaves the bounds of
    the clips it comes from.
    """
    pairs = sorted(
        ([clip.timeline_start_samples, clip.timeline_end_samples] for clip in track.clips),
        key=lambda pair: (pair[0], pair[1]),
    )
    merged = [list(pair) for pair in pairs]
    while len(merged) > max(limit, 1):
        index = _closest_gap_index(merged)
        merged[index] = [merged[index][0], max(merged[index][1], merged[index + 1][1])]
        del merged[index + 1]
    return merged


def gain_db(linear: float) -> str:
    """Gain of a clip as decibels: "0.0 dB", "+1.5 dB", "\u22123.0 dB"."""
    if linear <= 0:
        return f"{MINUS}{INFINITY} dB"
    decibels = round(20 * math.log10(linear), 1)
    if decibels > 0:
        return f"+{decibels:.1f} dB"
    if decibels < 0:
        return f"{MINUS}{abs(decibels):.1f} dB"
    return "0.0 dB"


def _milliseconds(samples: int, sample_rate: int) -> str:
    """Duration in samples as milliseconds."""
    if sample_rate <= 0:
        return "0 ms"
    return f"{round(samples * 1000 / sample_rate)} ms"


def fades_label(clip: ClipIR, sample_rate: int) -> str:
    """Fades of a clip as a short label: "in 240 ms \u00b7 out 800 ms".

    A clip without fades gets the en dash, and a fade of no samples is not
    a fade.
    """
    parts = []
    if clip.fade_in is not None and clip.fade_in.duration_samples > 0:
        parts.append(f"in {_milliseconds(clip.fade_in.duration_samples, sample_rate)}")
    if clip.fade_out is not None and clip.fade_out.duration_samples > 0:
        parts.append(f"out {_milliseconds(clip.fade_out.duration_samples, sample_rate)}")
    if not parts:
        return EN_DASH
    return f" {MIDDLE_DOT} ".join(parts)


def clip_rows(track: TrackIR, session: SessionIR, limit: int = 300) -> tuple[list[dict], int]:
    """Rows of the clip table of a track and how many clips were left out."""
    rows = [
        {
            "name": clip.name,
            "in": tc(clip.timeline_start_samples, session),
            "out": tc(clip.timeline_end_samples, session),
            "gain": gain_db(clip.gain_linear),
            "fades": fades_label(clip, session.sample_rate),
        }
        for clip in track.clips[: max(limit, 0)]
    ]
    return rows, max(len(track.clips) - len(rows), 0)


def notes(issues: Iterable[ConversionIssue], limit: int = 40) -> dict:
    """Notes of a report: the errors and the warnings first, the info aside.

    The issues of the same code and severity are one group, in the order they
    appear, and the group keeps the first message and the count.
    """
    items = list(issues)
    groups: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for issue in items:
        key = (issue.severity, issue.code)
        if key not in groups:
            groups[key] = {
                "sev": issue.severity,
                "code": issue.code,
                "count": 0,
                "text": issue.message,
            }
            order.append(key)
        groups[key]["count"] += 1
    main = [groups[key] for key in order if key[0] == "error"]
    main += [groups[key] for key in order if key[0] == "warning"]
    info = [groups[key] for key in order if key[0] == "info"]
    return {
        "main": main[:limit],
        "info": info[:INFO_LIMIT],
        "info_total": sum(1 for issue in items if issue.severity == "info"),
    }


def issues_by_track(issues: Iterable[ConversionIssue]) -> dict[str, list[ConversionIssue]]:
    """Issues grouped by the track name they carry in their context.

    A track with no name at all is a name too: the issues that carry it stay
    under the empty name, so the sheet can still attribute them.
    """
    grouped: dict[str, list[ConversionIssue]] = {}
    for issue in issues:
        name = issue.context.get("track")
        if name is None:
            continue
        grouped.setdefault(str(name), []).append(issue)
    return grouped


def issue_state(issues: list[ConversionIssue]) -> dict:
    """Short state of a list of issues: an error wins over a warning."""
    errors = sum(1 for issue in issues if issue.severity == "error")
    if errors:
        return {"st": "err", "label": plural(errors, "error", "errors")}
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    if warnings:
        return {"st": "warn", "label": plural(warnings, "note", "notes")}
    return {"st": "ok", "label": "ok"}


def reasons(issues: list[ConversionIssue], limit: int = 6) -> list[str]:
    """The distinct messages of the issues, with the count of the repeated ones.

    The messages come in the order they first appear and each one is written
    once. A message that happens more than once carries its count, and a last
    line says how many distinct messages did not fit in the limit.
    """
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.message] = counts.get(issue.message, 0) + 1
    mensagens = list(counts)
    teto = max(limit, 0)
    linhas = [_com_contagem(mensagem, counts[mensagem]) for mensagem in mensagens[:teto]]
    if len(mensagens) > teto:
        linhas.append(f"+ {len(mensagens) - teto} more")
    return linhas


def _com_contagem(mensagem: str, contagem: int) -> str:
    """The message with its count, when it happens more than once."""
    if contagem > 1:
        return f"{mensagem} ({TIMES}{contagem})"
    return mensagem


def fact(k: str, v: str, sub: str | None = None, st: str | None = None) -> dict:
    """One fact of the sheet: a key, a value, a sub line and a state."""
    return {"k": k, "v": v, "sub": sub, "st": st}


def track_row(
    index: int,
    track: TrackIR,
    session: SessionIR,
    state: dict,
    why: list[str],
    *,
    kind: str | None = None,
    pick: bool | None = None,
    audio_index: int | None = None,
) -> dict:
    """One row of the track sheet, as the page expects it."""
    rows, more = clip_rows(track, session)
    return {
        "index": index,
        "audio_index": audio_index,
        "name": track.name,
        "kind": kind or channels_label(track),
        "clips": len(track.clips),
        "spans": spans(track),
        "pick": pick,
        "state": state,
        "reasons": list(why),
        "clip_rows": rows,
        "clips_more": more,
    }


def plural(n: int, one: str, many: str) -> str:
    """Count with the right noun: "1 note", "3 notes"."""
    return f"{n} {one if n == 1 else many}"
