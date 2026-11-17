"""Resolucao da ancora de timecode do set para o fluxo de reconformacao."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from . import locators_write
from .als_ancora import StartSpec, _locator_tc, ancora_sem_sidecar, start_da_linha
from .ir import PRODUCTION_LAYOUT
from .report import Report
from .sidecar import (
    Anchor,
    SidecarMissingError,
    anchor_locator_label,
    read_anchor,
    sidecar_path_for,
)
from .timecode import TimecodeRate


def _rate_tuple(rate: TimecodeRate) -> tuple[int, int, bool]:
    """Tupla comparavel da taxa de frames."""
    return (rate.num, rate.den, rate.drop_frame)


def _anchor_from_sidecar(set_p: Path, report: Report, cut_rate: TimecodeRate) -> Anchor | None:
    """Le a ancora do sidecar e valida layout e taxa contra a lista de corte."""
    sidecar_p = sidecar_path_for(set_p)
    try:
        anchor = read_anchor(set_p)
    except (SidecarMissingError, KeyError, json.JSONDecodeError, ValueError) as exc:
        report.error("SIDECAR_ERROR", str(exc), {"path": str(sidecar_p)})
        return None
    if anchor.layout in (PRODUCTION_LAYOUT, "production_blocks"):
        report.error(
            "RECONFORM_PRODUCTION_SET",
            "Sets built from production audio cannot be reconformed",
            {"path": str(set_p)},
        )
        return None
    if _rate_tuple(anchor.timecode_rate) != _rate_tuple(cut_rate):
        ctx = {"set_rate": _rate_tuple(anchor.timecode_rate), "cut_rate": _rate_tuple(cut_rate)}
        report.error(
            "RECONFORM_RATE_MISMATCH",
            "set timecode rate differs from cut list frame rate",
            ctx,
        )
        return None
    return anchor


def _start_without_sidecar(
    set_p: Path,
    root: ET.Element,
    report: Report,
    cut_rate: TimecodeRate,
    start_tc: str | None,
) -> StartSpec | None:
    """Monta o StartSpec sem sidecar a partir de start_tc ou do locator TC."""
    if start_tc is not None:
        if not start_tc.strip():
            report.error(
                "ALS_START_TC_INVALID",
                f"invalid start timecode: {start_tc!r}",
                {"start_tc": start_tc},
            )
            return None
        return start_da_linha(start_tc, cut_rate)
    locator = _locator_tc(root)
    if locator is None:
        report.error(
            "RECONFORM_ANCHOR_MISSING",
            "set has no sidecar, no TC anchor locator, and no start_tc",
            {"path": str(set_p)},
        )
        return None
    rotulo = anchor_locator_label(locator[0])
    if rotulo is None:
        report.error(
            "RECONFORM_ANCHOR_MISSING",
            "set has no sidecar, no TC anchor locator, and no start_tc",
            {"path": str(set_p)},
        )
        return None
    return StartSpec(
        start_tc=rotulo,
        frame_rate=Fraction(cut_rate.num, cut_rate.den),
        drop_frame=cut_rate.drop_frame,
        assumed=True,
    )


def _anchor_without_sidecar(
    set_p: Path,
    root: ET.Element,
    report: Report,
    cut_rate: TimecodeRate,
    start_tc: str | None,
) -> Anchor | None:
    """Resolve a ancora sem sidecar usando a taxa do corte e o tempo do set."""
    start = _start_without_sidecar(set_p, root, report, cut_rate, start_tc)
    if start is None:
        return None
    try:
        bpm = locators_write._tempo_do_set(root)
        return ancora_sem_sidecar(set_p, root, report, start, bpm)
    except (SidecarMissingError, ValueError) as exc:
        report.error("ALS_START_TC_INVALID", str(exc), {"start_tc": start.start_tc})
        return None


def resolve_reconform_anchor(
    set_path: str | Path,
    root: ET.Element,
    report: Report,
    cut_rate: TimecodeRate,
    start_tc: str | None = None,
) -> Anchor | None:
    """Resolve a ancora do set para reconformacao e valida tempo fixo no set inteiro."""
    set_p = Path(set_path)
    if sidecar_path_for(set_p).exists():
        anchor = _anchor_from_sidecar(set_p, report, cut_rate)
    else:
        anchor = _anchor_without_sidecar(set_p, root, report, cut_rate, start_tc)
    if anchor is None:
        return None
    locators_write._check_tempo(anchor, root, report)
    locators_write._check_tempo_automation(anchor, root, report)
    if report.incomplete:
        return None
    return anchor
