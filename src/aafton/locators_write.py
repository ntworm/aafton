"""Import locators and ADR cues from CSV into an Ableton Live set."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .als_ancora import ancora_sem_sidecar, start_da_linha
from .als_paths import find_prototype
from .als_read import _check_tempo, _check_tempo_automation, _tempo_do_set
from .als_read_clips import _beats_to_samples
from .als_write_base import to_beats
from .als_xml import (
    DocumentInvalid,
    classify_id_tags,
    format_value,
    get_value,
    load_als,
    renumber_siblings,
    save_als,
    validate_document,
)
from .bundled import default_template
from .ir import PRODUCTION_LAYOUT
from .locators_csv import (
    read_and_process_csv,
)
from .report import Report
from .sidecar import (
    Anchor,
    SidecarMissingError,
    read_anchor,
    sidecar_path_for,
)
from .timecode import _LABEL_RE, TimecodeRate, samples_to_label

SET_LEVEL_CODES = {
    "NO_LOCATORS",
    "ALS_TEMPO_CHANGED",
    "ALS_TEMPO_AUTOMATION_UNSUPPORTED",
    "LOCATORS_PRODUCTION_SET",
    "LOCATORS_START_INCOMPLETE",
    "LOCATORS_OUTPUT_FOLDER",
    "OUTPUT_EXISTS",
    "CSV_NO_TC",
    "LOCATOR_PROTOTYPE_IDS",
    "DOCUMENT_INVALID",
    "SIDECAR_ERROR",
    "SIDECAR_MISSING",
    "ALS_LOAD_ERROR",
    "CSV_READ_ERROR",
    "NO_LOCATOR_PROTOTYPE",
    "WRITE_ERROR",
}


@dataclass
class LocatorsResult:
    """Result of importing locators into a Live set."""

    report: Report
    output: Path | None
    written: bool
    added: int
    skipped: list[dict] = field(default_factory=list)


def _load_root(set_path: Path, report: Report) -> ET.Element | None:
    try:
        return load_als(set_path)
    except Exception as exc:
        report.error("ALS_LOAD_ERROR", f"could not load set: {exc}", {"path": str(set_path)})
        return None


def _resolve_anchor(
    set_path: Path,
    root: ET.Element,
    report: Report,
    start_tc: str | None,
    frame_rate: TimecodeRate | None,
) -> Anchor | None:
    sidecar_p = sidecar_path_for(set_path)
    if sidecar_p.exists():
        try:
            anchor = read_anchor(set_path)
        except (SidecarMissingError, KeyError, json.JSONDecodeError, ValueError) as exc:
            report.error("SIDECAR_ERROR", str(exc), {"path": str(sidecar_p)})
            return None
        if anchor.layout in (PRODUCTION_LAYOUT, "production_blocks"):
            report.error(
                "LOCATORS_PRODUCTION_SET",
                "Sets built from production audio cannot receive locators",
                {"path": str(set_path)},
            )
            return None
        return anchor

    if frame_rate is not None and start_tc is None:
        report.error(
            "LOCATORS_START_INCOMPLETE",
            "--frame-rate given without --start-tc for set without sidecar",
            {"frame_rate": str(frame_rate)},
        )
        return None

    start_spec = None
    if start_tc is not None:
        if frame_rate is None:
            rate = TimecodeRate(24, 1)
            report.warning(
                "LOCATORS_FRAME_RATE_ASSUMED",
                "frame rate not specified for set without sidecar; assuming 24 fps",
                {"start_tc": start_tc, "frame_rate": "24"},
            )
        else:
            rate = frame_rate
        try:
            start_spec = start_da_linha(start_tc, rate)
        except ValueError as exc:
            report.error("ALS_START_TC_INVALID", str(exc), {"start_tc": start_tc})
            return None

    try:
        bpm = _tempo_do_set(root)
        return ancora_sem_sidecar(set_path, root, report, start_spec, bpm)
    except (SidecarMissingError, ValueError) as exc:
        report.error("SIDECAR_MISSING", str(exc), {"path": str(set_path)})
        return None


def _read_user_markers(
    locators_container: ET.Element, anchor: Anchor
) -> list[tuple[str, int, str]]:
    user_locs: list[tuple[str, int, str]] = []
    for loc in locators_container.findall("Locator"):
        name_node = loc.find("Name")
        time_node = loc.find("Time")
        if name_node is None or time_node is None:
            continue
        name = name_node.get("Value", "")
        if name.startswith("TC ") or _LABEL_RE.match(name):
            continue
        time_beats = float(time_node.get("Value", "0"))
        samples = anchor.live_origin_samples + _beats_to_samples(
            time_beats, anchor.sample_rate, anchor.bpm
        )
        annotation = get_value(loc, "Annotation") or ""
        user_locs.append((name, samples, annotation))
    user_locs.sort(key=lambda item: item[1])
    return user_locs


def read_set_for_locators(
    set_path: Path,
    report: Report,
    start_tc: str | None = None,
    frame_rate: TimecodeRate | None = None,
) -> tuple[ET.Element, Anchor, ET.Element, list[tuple[str, int, str]]] | None:
    """Load set XML, resolve anchor, verify tempo, and return user locators."""
    root = _load_root(set_path, report)
    if root is None:
        return None

    locs_container = root.find(".//LiveSet/Locators/Locators")
    if locs_container is None:
        report.error(
            "NO_LOCATORS",
            "set missing LiveSet/Locators/Locators container",
            {"path": str(set_path)},
        )
        return None

    anchor = _resolve_anchor(set_path, root, report, start_tc, frame_rate)
    if anchor is None:
        return None

    _check_tempo(anchor, root, report)
    _check_tempo_automation(anchor, root, report)
    if report.incomplete:
        return None

    report.metadata["anchor"] = _anchor_summary(anchor)
    user_locs = _read_user_markers(locs_container, anchor)
    return root, anchor, locs_container, user_locs


def _anchor_summary(anchor: Anchor) -> dict:
    """A ancora usada na conversao, para o operador conferir no relatorio."""
    rate = anchor.timecode_rate
    return {
        "live_origin_timecode": samples_to_label(
            anchor.live_origin_samples, rate, anchor.sample_rate
        ),
        "frame_rate": f"{rate.num}/{rate.den}" + (" drop" if rate.drop_frame else ""),
        "sample_rate": anchor.sample_rate,
        "bpm": anchor.bpm,
    }


def _get_prototype(root: ET.Element, report: Report) -> ET.Element | None:
    proto = find_prototype(root, "Locator")
    if proto is None:
        try:
            tmpl_path = default_template()
            tmpl_root = load_als(tmpl_path)
            proto = find_prototype(tmpl_root, "Locator")
        except Exception as exc:
            report.error("NO_LOCATOR_PROTOTYPE", f"could not load template prototype: {exc}")
            return None

    if proto is None:
        report.error("NO_LOCATOR_PROTOTYPE", "no Locator prototype found in set or template")
        return None

    has_nested_ids = any(elem.get("Id") is not None for elem in proto.iter() if elem is not proto)
    if has_nested_ids:
        report.error(
            "LOCATOR_PROTOTYPE_IDS",
            "prototype locator contains nested Id attributes",
        )
        return None

    return proto


def _build_locator_element(
    proto: ET.Element,
    time_beats: float,
    name: str,
    annotation: str,
) -> ET.Element:
    elem = copy.deepcopy(proto)
    fields = [
        ("LomId", "0"),
        ("Time", format_value(time_beats)),
        ("Name", name),
        ("Annotation", annotation),
        ("IsSongStart", "false"),
    ]
    for tag, val in fields:
        sub = elem.find(tag)
        if sub is None:
            sub = ET.SubElement(elem, tag)
        sub.set("Value", val)
    return elem


def _document_problems(root: ET.Element) -> set[str]:
    """Os problemas que validate_document acha no documento, um por linha."""
    try:
        validate_document(root, classify_id_tags(root)[0])
    except DocumentInvalid as exc:
        return {linha.strip() for linha in str(exc).split("\n  - ")[1:]}
    return set()


def _save_set_atomic(
    root: ET.Element, out_path: Path, set_path: Path, report: Report, before: set[str]
) -> bool:
    # Um set salvo pelo proprio Live pode ja ter o que validate_document acusa
    # (WarpMarker com Id grande, medido num set real): so o problema novo recusa.
    novos = sorted(_document_problems(root) - before)
    if novos:
        report.error(
            "DOCUMENT_INVALID",
            "writing the locators would break the set: " + "; ".join(novos),
        )
        return False

    temp_fd, temp_str = tempfile.mkstemp(dir=out_path.parent, prefix="aafton_loc_", suffix=".tmp")
    os.close(temp_fd)
    temp_p = Path(temp_str)
    try:
        save_als(root, temp_p)
        os.replace(temp_p, out_path)
    except Exception as exc:
        report.error("WRITE_ERROR", f"could not write output file: {exc}")
        return False
    finally:
        if temp_p.exists():
            temp_p.unlink()

    src_sidecar = sidecar_path_for(set_path)
    if src_sidecar.exists():
        try:
            shutil.copyfile(src_sidecar, sidecar_path_for(out_path))
        except OSError as exc:
            report.error("WRITE_ERROR", f"could not copy the sidecar: {exc}")
            return False
    return True


def _validate_output_path(set_p: Path, output: str | Path | None, report: Report) -> Path | None:
    out_p = Path(output) if output is not None else set_p.parent / f"{set_p.stem} locators.als"
    if out_p.resolve().parent != set_p.resolve().parent:
        report.error(
            "LOCATORS_OUTPUT_FOLDER",
            "output file must be in the same directory as source set",
            {"set": str(set_p), "output": str(out_p)},
        )
        return None
    for alvo in (out_p, sidecar_path_for(out_p)):
        if alvo.exists():
            report.error(
                "OUTPUT_EXISTS",
                f"output file already exists: {alvo.name}",
                {"output": str(alvo)},
            )
            return None
    return out_p


def _blocked(report: Report, items: list, strict: bool) -> bool:
    """Erro do set ou da lista recusa sempre; erro de linha so no estrito."""
    if any(iss.severity == "error" and iss.code in SET_LEVEL_CODES for iss in report.issues):
        return True
    return strict and any(it.error_code is not None for it in items)


def _insert_sorted(
    locs_container: ET.Element, proto: ET.Element, to_add: list, anchor: Anchor
) -> None:
    """Acrescenta os locators novos e poe todos em ordem de Time, com Id posicional.

    A posicao antes da origem ja foi recusada na leitura do CSV, entao to_beats
    nao levanta aqui.
    """
    for item in to_add:
        beats = to_beats(
            item.sample_pos, anchor.live_origin_samples, anchor.sample_rate, anchor.bpm
        )
        locs_container.append(
            _build_locator_element(proto, beats, item.final_name, item.annotation)
        )
    all_locs = [c for c in locs_container if c.tag == "Locator"]
    for c in all_locs:
        locs_container.remove(c)
    all_locs.sort(key=lambda el: float(el.find("Time").get("Value", "0")))
    for c in all_locs:
        locs_container.append(c)
    renumber_siblings(locs_container, "Locator")


def import_locators(
    set_path: str | Path,
    csv_path: str | Path,
    *,
    output: str | Path | None = None,
    kind: str = "auto",
    columns: dict[str, str] | None = None,
    offset: str | None = None,
    cue_prefix: str = "ADR",
    start_tc: str | None = None,
    frame_rate: TimecodeRate | None = None,
    strict: bool = True,
) -> LocatorsResult:
    """Import locators from CSV and save a new Live set copy."""
    report = Report(strict=False)
    set_p = Path(set_path)
    csv_p = Path(csv_path)

    out_p = _validate_output_path(set_p, output, report)
    set_info = None
    if out_p is not None:
        set_info = read_set_for_locators(set_p, report, start_tc, frame_rate)
    if set_info is None:
        return LocatorsResult(report=report, output=out_p, written=False, added=0)
    root, anchor, locs_container, existing_locs = set_info

    items = read_and_process_csv(
        csv_p,
        report,
        anchor.timecode_rate,
        anchor.sample_rate,
        anchor.live_origin_samples,
        existing_locs,
        kind=kind,
        columns=columns,
        offset=offset,
        cue_prefix=cue_prefix,
    )
    proto = None
    if items is not None and not _blocked(report, items, strict):
        proto = _get_prototype(root, report)
    if proto is None:
        return LocatorsResult(report=report, output=out_p, written=False, added=0)

    skipped = [{"row": it.row_num, "code": it.error_code} for it in items if it.error_code]
    to_add = [it for it in items if it.error_code is None and not it.is_existing]
    before = _document_problems(root)
    _insert_sorted(locs_container, proto, to_add, anchor)

    if not _save_set_atomic(root, out_p, set_p, report, before):
        return LocatorsResult(report=report, output=out_p, written=False, added=0)
    return LocatorsResult(
        report=report, output=out_p, written=True, added=len(to_add), skipped=skipped
    )
