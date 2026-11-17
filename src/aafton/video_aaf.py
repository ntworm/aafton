"""Geometria do video lida da timeline de um AAF: inicio, duracao e trim."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import aaf2

from .report import Report

OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")

Geometria = tuple[Fraction | None, Fraction | None, int]


def is_aaf_file(path: Path) -> bool:
    """True quando o arquivo e um AAF: documento composto da Microsoft (OLE2)."""
    try:
        with open(path, "rb") as arquivo:
            return arquivo.read(8) == OLE2_MAGIC
    except OSError:
        return False


def read_aaf_video_geometry(
    aaf_path: Path, original_video_path: Path, sample_rate: int, report: Report
) -> Geometria:
    """Inicio e duracao em segundos e offset da fonte em amostras do video do AAF.

    So um AAF tem essa geometria; EDL e XML devolvem ``(None, None, 0)`` sem
    passar pelo aaf2.
    """
    if not is_aaf_file(aaf_path):
        return None, None, 0
    geometria: list = [None, None, 0]
    try:
        with aaf2.open(str(aaf_path), "r") as f:
            _varrer_slots_de_video(f, geometria, original_video_path, sample_rate, report)
    except Exception as exc:
        from .report import StrictModeError

        if isinstance(exc, StrictModeError):
            raise
        report.warning(
            "VIDEO_AAF_READ_FAILED", f"could not read the video geometry of the AAF: {exc}", {}
        )
    return geometria[0], geometria[1], geometria[2]


def _varrer_slots_de_video(
    f, geometria: list, original_video_path: Path, sample_rate: int, report: Report
) -> None:
    """Preenche ``geometria`` no lugar, para uma falha no meio manter o que ja foi lido.

    Com mais de uma faixa de video, a ultima com clip vence.
    """
    compositions = [m for m in f.content.mobs if isinstance(m, aaf2.mobs.CompositionMob)]
    for slot in compositions[0].slots if compositions else []:
        seg = getattr(slot, "segment", None)
        if getattr(seg, "media_kind", "") != "Picture":
            continue
        inicio, duracao, offset = _geometria_do_slot(
            slot, seg, original_video_path, sample_rate, report
        )
        if inicio is not None:
            geometria[0], geometria[1] = inicio, duracao
            geometria[2] = offset or geometria[2]


def _geometria_do_slot(
    slot, seg, original_video_path: Path, sample_rate: int, report: Report
) -> Geometria:
    from .aaf_read import _source_filename, _source_offset_samples
    from .timecode import exact_edit_rate

    rate = (
        exact_edit_rate(slot.edit_rate) if getattr(slot, "edit_rate", None) else exact_edit_rate(24)
    )
    curr = 0
    for c in getattr(seg, "components", None) or [seg]:
        if isinstance(c, aaf2.components.Filler):
            curr += c.length
            continue
        if not isinstance(c, (aaf2.components.NestedScope, aaf2.components.SourceClip)):
            continue
        offset = 0
        if isinstance(c, aaf2.components.SourceClip) and c.start:
            location = _source_filename(c)
            if location and Path(location).name.casefold() == original_video_path.name.casefold():
                offset = _source_offset_samples(c, sample_rate, rate, report)
            else:
                report.error(
                    "VIDEO_SOURCE_UNCONFIRMED",
                    "AAF asks for a video trim, but the selected file was not identified as the source; confirm the consolidated video",
                    {},
                )
        return curr / rate, c.length / rate, offset
    return None, None, 0
