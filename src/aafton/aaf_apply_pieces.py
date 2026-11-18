"""The pieces of the apply: the JSON of the edits and the swap of the clips.

Pure functions over the SessionIR, without any AAF: the apply calls them
between the reading and the writing, and the tests call them on a synthetic
session.

A piece is ``(start, end, offset)``: start and end absolute on the timeline,
offset absolute in the source, all in samples. The pieces replace the clip;
the part of the clip that no piece covers becomes empty.
"""

from __future__ import annotations

import copy
import dataclasses

from .ir import ClipIR, FadeIR, SessionIR, TrackIR
from .report import Report

Piece = tuple[int, int, int]


class ApplyRefused(Exception):
    """A refusal of the apply, with the code of the report."""

    def __init__(
        self, code: str, message: str, context: dict | None = None, *, reported: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context = context or {}
        # The issue is already in the report (the reader wrote it): not added twice.
        self.reported = reported


def _whole(value) -> bool:
    """An int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_piece(clip_id: str, item) -> Piece:
    """One piece of the JSON, checked on its own."""
    if not isinstance(item, dict) or not all(
        _whole(item.get(k)) for k in ("start", "end", "offset")
    ):
        raise ApplyRefused(
            "APPLY_BAD_EDITS",
            f"the clip {clip_id!r} has a piece without integer start, end and offset",
        )
    start, end, offset = item["start"], item["end"], item["offset"]
    if start >= end or offset < 0:
        raise ApplyRefused(
            "APPLY_BAD_EDITS",
            f"the clip {clip_id!r} has a piece with start {start}, end {end} and offset {offset}",
        )
    return start, end, offset


def parse_edits(data) -> dict[str, list[Piece]]:
    """The edits of the JSON, by clip id, each list in timeline order."""
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("clips"), dict)
    ):
        raise ApplyRefused("APPLY_BAD_EDITS", 'the edits must be {"version": 1, "clips": {...}}')
    edits: dict[str, list[Piece]] = {}
    for clip_id, items in data["clips"].items():
        if not isinstance(items, list) or not items:
            raise ApplyRefused("APPLY_BAD_EDITS", f"the clip {clip_id!r} has no pieces")
        pieces = [_parse_piece(clip_id, item) for item in items]
        for before, after in zip(pieces, pieces[1:]):
            if after[0] < before[1]:
                raise ApplyRefused(
                    "APPLY_BAD_EDITS",
                    f"the pieces of the clip {clip_id!r} are out of order or overlap",
                )
        edits[clip_id] = pieces
    return edits


def _check_clip(session: SessionIR, track: TrackIR, clip: ClipIR, pieces: list[Piece]) -> None:
    """Refuses the clip that the apply cannot edit and the piece outside the media."""
    if track.channel_count != 1:
        raise ApplyRefused(
            "APPLY_TRACK_NOT_MONO", f"the clip {clip.id!r} is on a track that is not mono"
        )
    asset = session.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None or asset.status != "resolved" or asset.duration_samples is None:
        raise ApplyRefused(
            "APPLY_MEDIA_UNRESOLVED", f"the media of the clip {clip.id!r} is not resolved"
        )
    if asset.sample_rate != session.sample_rate:
        raise ApplyRefused(
            "APPLY_MEDIA_RATE",
            f"the media of the clip {clip.id!r} is at {asset.sample_rate} Hz, "
            f"the session at {session.sample_rate} Hz",
        )
    for start, end, offset in pieces:
        if offset + (end - start) > asset.duration_samples:
            raise ApplyRefused(
                "APPLY_OUTSIDE_MEDIA",
                f"a piece of the clip {clip.id!r} goes past the end of its media",
            )


def _fit(fade: FadeIR | None, room: int) -> FadeIR | None:
    """The fade shortened to ``room`` samples; None when nothing is left."""
    if fade is None or room <= 0:
        return None
    if fade.duration_samples <= room:
        return fade
    return dataclasses.replace(fade, duration_samples=room)


def _pieces_of(clip: ClipIR, pieces: list[Piece], next_event: int, report: Report) -> list[ClipIR]:
    """The clips that replace ``clip``: fade in on the first, fade out on the last."""
    novos: list[ClipIR] = []
    for n, (start, end, offset) in enumerate(pieces, start=1):
        novos.append(
            dataclasses.replace(
                clip,
                id=clip.id if len(pieces) == 1 else f"{clip.id}/{n}",
                event_index=clip.event_index if n == 1 else next_event + n - 2,
                timeline_start_samples=start,
                timeline_end_samples=end,
                source_offset_samples=offset,
                fade_in=None,
                fade_out=None,
                # Only the first piece can be the entry of the crossfade of the clip.
                crossfade_in_state=clip.crossfade_in_state and n == 1,
                raw=copy.deepcopy(clip.raw),
            )
        )
    _place_fades(clip, novos[0], novos[-1], report)
    return novos


def _place_fades(clip: ClipIR, first: ClipIR, last: ClipIR, report: Report) -> None:
    """Fade in on the first piece, fade out on the last, each fitted to its piece."""
    first.fade_in = _fit(clip.fade_in, first.duration_samples)
    used = first.fade_in.duration_samples if first.fade_in and first is last else 0
    last.fade_out = _fit(clip.fade_out, last.duration_samples - used)
    for antigo, novo in ((clip.fade_in, first.fade_in), (clip.fade_out, last.fade_out)):
        if antigo is None:
            continue
        final = novo.duration_samples if novo else 0
        if final != antigo.duration_samples:
            report.warning(
                "APPLY_FADE_SHORTENED",
                f"a fade of the clip {clip.id!r} went from {antigo.duration_samples} "
                f"to {final} samples to fit its piece",
                {"clip": clip.id},
            )


def _overlapping_pairs(track: TrackIR) -> list[tuple[ClipIR, ClipIR]]:
    """Every pair of clips of the same channel that overlap, earlier clip first.

    Not only neighbours: in a chain of crossfades a clip can come to overlap
    one that is two clips away, which no neighbour pair shows.
    """
    pares: list[tuple[ClipIR, ClipIR]] = []
    for channel in {c.channel_index for c in track.clips}:
        ordenados = sorted(
            (c for c in track.clips if c.channel_index == channel),
            key=lambda c: c.timeline_start_samples,
        )
        for i, anterior in enumerate(ordenados):
            for seguinte in ordenados[i + 1 :]:
                if seguinte.timeline_start_samples >= anterior.timeline_end_samples:
                    break
                pares.append((anterior, seguinte))
    return pares


def _crossfade_pieces(antes: list[ClipIR], depois: list[ClipIR]) -> list[tuple[ClipIR, ClipIR]]:
    """The pieces of two crossfaded clips that still overlap, earlier piece first."""
    return [
        (a, b) if a.timeline_start_samples <= b.timeline_start_samples else (b, a)
        for a in antes
        for b in depois
        if min(a.timeline_end_samples, b.timeline_end_samples)
        > max(a.timeline_start_samples, b.timeline_start_samples)
    ]


def _remove_crossfade(a: ClipIR, b: ClipIR, report: Report) -> None:
    """Drops the fades of a crossfade whose clips no longer overlap."""
    a.fade_out, b.fade_in = None, None
    report.warning(
        "APPLY_CROSSFADE_REMOVED",
        f"the crossfade between {a.name!r} and {b.name!r} was removed: they no longer overlap",
        {"clips": [a.id, b.id]},
    )


def _resize_crossfade(a: ClipIR, b: ClipIR, report: Report) -> None:
    """Sets both fades to the overlap; a piece lying under the other clip is refused.

    A crossfade runs from the start of the later clip to the end of the
    earlier one, so the earlier piece has to start first and end no later
    than the later clip; otherwise one clip lies under the other.
    """
    if not (
        a.timeline_start_samples < b.timeline_start_samples
        and a.timeline_end_samples <= b.timeline_end_samples
    ):
        raise ApplyRefused(
            "APPLY_CROSSFADE_TOO_LONG",
            f"the crossfade between {a.name!r} and {b.name!r} would be longer than a clip",
        )
    overlap = a.timeline_end_samples - b.timeline_start_samples
    saida = a.fade_out.duration_samples if a.fade_out else 0
    entrada = b.fade_in.duration_samples if b.fade_in else 0
    a.fade_out = dataclasses.replace(
        a.fade_out or FadeIR(kind="out", duration_samples=0), duration_samples=overlap
    )
    b.fade_in = dataclasses.replace(
        b.fade_in or FadeIR(kind="in", duration_samples=0), duration_samples=overlap
    )
    if saida != overlap or entrada != overlap:
        report.warning(
            "APPLY_CROSSFADE_RESIZED",
            f"the crossfade between {a.name!r} and {b.name!r} is now {overlap} samples",
            {"clips": [a.id, b.id], "samples": overlap},
        )


def _follow_crossfades(
    antes: list[tuple[ClipIR, ClipIR]], trocas: dict[int, list[ClipIR]], report: Report
) -> set[tuple[int, int]]:
    """Follows the crossfades of the edited clips; returns the overlaps they keep.

    Every piece of the two clips is checked against every piece of the
    other: splitting a clip inside its crossfade leaves the crossfade on
    whichever piece still overlaps the neighbour.
    """
    mantidos: set[tuple[int, int]] = set()
    for anterior, seguinte in antes:
        if id(anterior) not in trocas and id(seguinte) not in trocas:
            mantidos.add((id(anterior), id(seguinte)))
            continue
        de_a = trocas.get(id(anterior), [anterior])
        de_b = trocas.get(id(seguinte), [seguinte])
        pares = _crossfade_pieces(de_a, de_b)
        if not pares:
            _remove_crossfade(de_a[-1], de_b[0], report)
        for a, b in pares:
            _resize_crossfade(a, b, report)
            mantidos.add((id(a), id(b)))
        _drop_moved_halves(pares, de_a[-1], de_b[0])
    return mantidos


def _drop_moved_halves(
    pares: list[tuple[ClipIR, ClipIR]], ultimo: ClipIR, primeiro: ClipIR
) -> None:
    """Drops the old crossfade half from the edge piece that no longer holds it."""
    if not pares:
        return
    if all(a is not ultimo for a, _ in pares):
        ultimo.fade_out = None
    if all(b is not primeiro for _, b in pares):
        primeiro.fade_in = None


def _swap_track(
    session: SessionIR, track: TrackIR, edits: dict[str, list[Piece]], report: Report
) -> None:
    """Swaps the edited clips of one track and follows its crossfades."""
    antes = _overlapping_pairs(track)
    trocas: dict[int, list[ClipIR]] = {}
    next_event = max(c.event_index for c in track.clips) + 1
    novos: list[ClipIR] = []
    for clip in track.clips:
        if clip.id not in edits:
            novos.append(clip)
            continue
        _check_clip(session, track, clip, edits[clip.id])
        pedacos = _pieces_of(clip, edits[clip.id], next_event, report)
        next_event += len(pedacos) - 1
        trocas[id(clip)] = pedacos
        novos.extend(pedacos)
    track.clips = novos

    mantidos = _follow_crossfades(antes, trocas, report)
    for anterior, seguinte in _overlapping_pairs(track):
        if (id(anterior), id(seguinte)) not in mantidos:
            raise ApplyRefused(
                "APPLY_OVERLAP_CREATED",
                f"the edit makes {anterior.name!r} and {seguinte.name!r} overlap on the "
                f"track {track.name!r}",
            )


def swap_clips(session: SessionIR, edits: dict[str, list[Piece]], report: Report) -> list[TrackIR]:
    """Swaps every edited clip for its pieces; returns the tracks that changed."""
    por_id = {clip.id: track for track in session.tracks for clip in track.clips}
    faltam = sorted(set(edits) - set(por_id))
    if faltam:
        raise ApplyRefused(
            "APPLY_UNKNOWN_CLIP", f"no clip with the id {faltam[0]!r} in the AAF", {"ids": faltam}
        )
    tracks: list[TrackIR] = []
    for track in session.tracks:
        if any(clip.id in edits for clip in track.clips):
            _swap_track(session, track, edits, report)
            tracks.append(track)
    return tracks
