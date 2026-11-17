"""Cue sheet CSV export from Ableton Live set locators."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .locators_annotation import is_adr_locator, parse_adr_annotation_data
from .locators_write import read_set_for_locators
from .report import Report
from .timecode import TimecodeRate, samples_to_label

CUE_SHEET_COLUMNS: list[str] = [
    "cue",
    "tc_in",
    "tc_out",
    "character",
    "line",
    "name",
    "comment",
]


@dataclass
class CueSheetResult:
    """Result of exporting a cue sheet CSV."""

    report: Report
    output: Path | None
    written: bool
    rows: list[dict[str, str]] = field(default_factory=list)


def _locator_to_row(
    name: str,
    pos_samples: int,
    annotation: str,
    rate: TimecodeRate,
    sample_rate: int,
) -> dict[str, str]:
    tc_in = samples_to_label(pos_samples, rate, sample_rate)
    if is_adr_locator(name, annotation):
        cue = name.split()[0] if name else ""
        adr_data = parse_adr_annotation_data(annotation)
        return {
            "cue": cue,
            "tc_in": tc_in,
            "tc_out": adr_data.get("out", ""),
            "character": adr_data.get("character", ""),
            "line": adr_data.get("line", ""),
            "name": "",
            "comment": adr_data.get("note", ""),
        }
    return {
        "cue": "",
        "tc_in": tc_in,
        "tc_out": "",
        "character": "",
        "line": "",
        "name": name,
        "comment": annotation or "",
    }


def _check_duplicate_cues(rows: list[dict[str, str]], report: Report) -> None:
    seen: set[str] = set()
    for row in rows:
        cue = row.get("cue")
        if cue:
            if cue in seen:
                report.warning("CUE_DUPLICATE", f"duplicate cue in set: {cue}", {"cue": cue})
            else:
                seen.add(cue)


def _write_cue_sheet_csv(out_p: Path, rows: list[dict[str, str]], report: Report) -> bool:
    try:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CUE_SHEET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return True
    except OSError as exc:
        report.error("WRITE_ERROR", f"could not write cue sheet CSV: {exc}", {"path": str(out_p)})
        return False


def export_cue_sheet(
    set_path: str | Path,
    output: str | Path,
    *,
    only_cues: bool = False,
    start_tc: str | None = None,
    frame_rate: TimecodeRate | None = None,
) -> CueSheetResult:
    """Export locators from Live set to cue sheet CSV."""
    report = Report(strict=False)
    set_p = Path(set_path)
    out_p = Path(output)

    if out_p.exists():
        report.error(
            "OUTPUT_EXISTS", f"output file already exists: {out_p.name}", {"output": str(out_p)}
        )
        return CueSheetResult(report=report, output=out_p, written=False, rows=[])

    set_info = read_set_for_locators(set_p, report, start_tc, frame_rate)
    if set_info is None:
        return CueSheetResult(report=report, output=out_p, written=False, rows=[])
    _root, anchor, _locs_container, user_locs = set_info

    rows: list[dict[str, str]] = []
    for name, pos_samples, annotation in user_locs:
        row = _locator_to_row(
            name, pos_samples, annotation, anchor.timecode_rate, anchor.sample_rate
        )
        rows.append(row)

    _check_duplicate_cues(rows, report)

    if only_cues:
        rows = [r for r in rows if r["cue"]]

    written = _write_cue_sheet_csv(out_p, rows, report)
    return CueSheetResult(report=report, output=out_p, written=written, rows=rows)
