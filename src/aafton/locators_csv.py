"""CSV parsing and validation for locators import."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .locators_annotation import (
    format_adr_annotation,
    is_adr_locator,
    parse_adr_annotation_data,
)
from .report import Report
from .timecode import (
    _LABEL_RE,
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    label_to_frames,
    quantize_samples,
)

COLUMN_SYNONYMS: dict[str, set[str]] = {
    "tc_in": {"tc", "timecode", "in", "tc in", "start", "entrada", "inicio"},
    "tc_out": {"out", "tc out", "end", "saida", "fim"},
    "name": {"name", "nome", "marker", "marcador", "title"},
    "cue": {"cue", "cue #", "cue number", "numero", "#"},
    "character": {"character", "char", "personagem", "role"},
    "line": {"line", "dialogue", "fala", "texto"},
    "comment": {"comment", "note", "notes", "nota", "obs", "reason", "motivo"},
}


@dataclass
class CsvRowItem:
    """Parsed and validated CSV row."""

    row_num: int
    tc_in_raw: str
    in_frames: int
    sample_pos: int
    tc_out_raw: str | None
    shifted_out_label: str | None
    name_raw: str
    cue_raw: str
    character: str
    line: str
    comment: str
    final_name: str = ""
    annotation: str = ""
    is_existing: bool = False
    error_code: str | None = None


def normalize_header(header: str) -> str:
    """Normalize header name: casefold, remove accents, underscores/hyphens to space."""
    h = unicodedata.normalize("NFKD", header)
    h = "".join(c for c in h if not unicodedata.combining(c))
    h = h.casefold()
    h = re.sub(r"[-_]+", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def map_headers(headers: list[str], columns: dict[str, str] | None) -> dict[int, str]:
    """Map CSV column indices to canonical field names."""
    mapping: dict[int, str] = {}
    normalized_headers = [normalize_header(h) for h in headers]
    for canonical, custom_h in (columns or {}).items():
        _map_first(mapping, normalized_headers, canonical, {normalize_header(custom_h)})
    for canonical, syns in COLUMN_SYNONYMS.items():
        if canonical not in mapping.values():
            _map_first(mapping, normalized_headers, canonical, syns)
    return mapping


def _map_first(
    mapping: dict[int, str], normalized: list[str], canonical: str, names: set[str]
) -> None:
    """Liga o primeiro cabecalho livre que casa com um dos nomes."""
    for idx, nh in enumerate(normalized):
        if idx not in mapping and nh in names:
            mapping[idx] = canonical
            return


def parse_offset_frames(offset_str: str, rate: TimecodeRate) -> int:
    """Parse duration offset [+|-]HH:MM:SS:FF into nominal frames."""
    s = offset_str.strip()
    match = re.match(r"^([+-]?)(\d{2,3}):(\d{2}):(\d{2})[:;](\d{2,3})$", s)
    if not match:
        raise ValueError(f"invalid offset format: {offset_str!r}")
    sign_str, hh, mm, ss, ff = match.groups()
    nominal = rate.nominal_fps
    f_val = int(ff)
    total = (((int(hh) * 60) + int(mm)) * 60 + int(ss)) * nominal + f_val
    return -total if sign_str == "-" else total


def read_raw_csv(csv_path: Path, report: Report) -> str | None:
    """Read CSV file text trying UTF-8 then cp1252."""
    try:
        raw_bytes = csv_path.read_bytes()
    except OSError as exc:
        report.error("CSV_READ_ERROR", f"could not read CSV file: {exc}", {"path": str(csv_path)})
        return None

    try:
        return raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw_bytes.decode("cp1252")
            report.warning(
                "CSV_ENCODING_CP1252",
                "CSV was decoded with cp1252 fallback instead of UTF-8",
                {"path": str(csv_path)},
            )
            return text
        except Exception as exc:
            report.error(
                "CSV_ENCODING_ERROR",
                f"could not decode CSV file: {exc}",
                {"path": str(csv_path)},
            )
            return None


def detect_delimiter(text: str) -> str:
    """Detect CSV delimiter between comma, semicolon, and tab."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        return dialect.delimiter
    except Exception:
        return ","


def parse_csv_rows(
    text: str, delimiter: str, report: Report
) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """Parse CSV text into header and data rows, flattening newlines."""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header: list[str] | None = None
    data_rows: list[tuple[int, list[str]]] = []

    for row in reader:
        line_num = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        cleaned_cells = []
        had_newline = False
        for cell in row:
            if "\n" in cell or "\r" in cell:
                had_newline = True
                cleaned_cells.append(re.sub(r"[\r\n]+", " ", cell))
            else:
                cleaned_cells.append(cell)
        if had_newline:
            report.warning(
                "CSV_NEWLINE_FLATTENED",
                "newlines within field were flattened to space",
                {"row": line_num},
            )
        if header is None:
            header = cleaned_cells
        else:
            data_rows.append((line_num, cleaned_cells))

    return header or [], data_rows


def detect_kind(
    kind_arg: str,
    mapping: dict[int, str],
    data_rows: list[tuple[int, list[str]]],
) -> str:
    """Determine whether list is markers or adr."""
    if kind_arg in ("markers", "adr"):
        return kind_arg
    adr_cols = {idx for idx, col in mapping.items() if col in ("tc_out", "character", "line")}
    for _, cells in data_rows:
        for idx in adr_cols:
            if idx < len(cells) and cells[idx].strip():
                return "adr"
    return "markers"


def _extract_row_fields(cells: list[str], mapping: dict[int, str]) -> dict[str, str]:
    row_dict: dict[str, str] = {}
    for idx, cell in enumerate(cells):
        field_name = mapping.get(idx)
        if field_name and cell.strip():
            row_dict[field_name] = cell.strip()
    return row_dict


def _validate_tc_in(
    tc_in_raw: str,
    offset_frames: int,
    rate: TimecodeRate,
    sample_rate: int,
    live_origin: int,
    row_num: int,
    report: Report,
) -> tuple[int, int, str | None]:
    try:
        in_frames = label_to_frames(tc_in_raw, rate) + offset_frames
    except ValueError:
        report.error("CSV_BAD_TC", f"invalid timecode {tc_in_raw!r}", {"row": row_num})
        return 0, 0, "CSV_BAD_TC"

    if in_frames < 0:
        report.error(
            "CSV_BAD_TC", f"timecode {tc_in_raw!r} becomes negative with offset", {"row": row_num}
        )
        return 0, 0, "CSV_BAD_TC"

    sample_pos, residue = quantize_samples(frames_to_samples_exact(in_frames, rate, sample_rate))
    if residue != 0:
        report.info(
            "TC_SAMPLE_RESIDUE",
            f"timecode {tc_in_raw} has sample residue {residue}",
            {"row": row_num, "tc": tc_in_raw, "residue": str(residue)},
        )

    if sample_pos < live_origin:
        report.error(
            "MARKER_BEFORE_ORIGIN",
            f"timecode {tc_in_raw} falls before Live origin {live_origin}",
            {"row": row_num},
        )
        return in_frames, sample_pos, "MARKER_BEFORE_ORIGIN"

    return in_frames, sample_pos, None


def _validate_tc_out(
    tc_out_raw: str | None,
    in_frames: int,
    offset_frames: int,
    rate: TimecodeRate,
    row_num: int,
    report: Report,
) -> tuple[str | None, str | None]:
    if not tc_out_raw:
        return None, None
    try:
        out_frames = label_to_frames(tc_out_raw, rate) + offset_frames
    except ValueError:
        report.error("CSV_BAD_TC", f"invalid out timecode {tc_out_raw!r}", {"row": row_num})
        return None, "CSV_BAD_TC"

    if out_frames < 0:
        report.error(
            "CSV_BAD_TC",
            f"out timecode {tc_out_raw!r} becomes negative with offset",
            {"row": row_num},
        )
        return None, "CSV_BAD_TC"
    if out_frames <= in_frames:
        report.error(
            "CUE_OUT_BEFORE_IN",
            f"out timecode {tc_out_raw!r} must be after in timecode",
            {"row": row_num},
        )
        return None, "CUE_OUT_BEFORE_IN"

    return frames_to_label(out_frames, rate), None


def _check_reserved_name(name: str, row_num: int, report: Report) -> str | None:
    if name.startswith("TC ") or _LABEL_RE.match(name):
        report.error(
            "LOCATOR_NAME_RESERVED",
            f"name {name!r} is reserved for timecode markers",
            {"row": row_num, "name": name},
        )
        return "LOCATOR_NAME_RESERVED"
    return None


def _check_marker_looks_adr(comment: str, row_num: int, report: Report) -> None:
    if not comment:
        return
    for line in comment.splitlines():
        if line.startswith(("Out: ", "Character: ", "Line: ")):
            report.warning(
                "MARKER_COMMENT_LOOKS_ADR",
                "marker comment looks like an ADR cue",
                {"row": row_num},
            )
            break


def check_item_exists(
    item: CsvRowItem, is_adr: bool, existing_locs: list[tuple[str, int, str]]
) -> bool:
    """Check if item matches an existing locator in the set."""
    for ex_name, ex_pos, ex_ann in existing_locs:
        if ex_pos != item.sample_pos:
            continue
        if is_adr:
            if not is_adr_locator(ex_name, ex_ann):
                continue
            ex = parse_adr_annotation_data(ex_ann)
            if (
                ex.get("out") == item.shifted_out_label
                and ex.get("character", "") == item.character
                and ex.get("line", "") == item.line
            ):
                return True
        elif ex_name == item.final_name:
            return True
    return False


def _assign_markers_metadata(
    items: list[CsvRowItem],
    existing_locs: list[tuple[str, int, str]],
    report: Report,
) -> None:
    for item in items:
        name = item.name_raw if item.name_raw else f"marker {item.row_num}"
        err = _check_reserved_name(name, item.row_num, report)
        if err and not item.error_code:
            item.error_code = err
        _check_marker_looks_adr(item.comment, item.row_num, report)
        item.final_name = name
        item.annotation = item.comment
        if not item.error_code and check_item_exists(item, False, existing_locs):
            item.is_existing = True
            report.info("LOCATOR_EXISTS", f"locator already exists: {name}", {"row": item.row_num})


def _check_and_assign_cue(
    item: CsvRowItem, c_id: str, seen: set[str], existing: set[str], report: Report
) -> None:
    if c_id in existing or c_id in seen:
        report.error(
            "CUE_DUPLICATE", f"duplicate cue number {c_id!r}", {"row": item.row_num, "cue": c_id}
        )
        if not item.error_code:
            item.error_code = "CUE_DUPLICATE"
    seen.add(c_id)
    item.final_name = f"{c_id} {item.character}".strip() if item.character else c_id
    err_res = _check_reserved_name(item.final_name, item.row_num, report)
    if err_res and not item.error_code:
        item.error_code = err_res


def _existing_cues(
    existing_locs: list[tuple[str, int, str]], cue_prefix: str
) -> tuple[set[str], int]:
    """Numeros de cue que o set ja tem e o maior numero com o prefixo dado."""
    cues: set[str] = set()
    max_num = 0
    plen = len(cue_prefix)
    for ex_name, _, ex_ann in existing_locs:
        if not ex_name or not is_adr_locator(ex_name, ex_ann):
            continue
        tok = ex_name.split()[0]
        cues.add(tok)
        if tok.startswith(cue_prefix) and tok[plen:].isdigit():
            max_num = max(max_num, int(tok[plen:]))
    return cues, max_num


def _assign_adr_metadata(
    items: list[CsvRowItem],
    cue_prefix: str,
    existing_locs: list[tuple[str, int, str]],
    report: Report,
) -> None:
    existing_cues, max_num = _existing_cues(existing_locs, cue_prefix)
    for item in items:
        item.annotation = format_adr_annotation(
            item.shifted_out_label, item.character, item.line, item.comment
        )
        if not item.error_code and check_item_exists(item, True, existing_locs):
            item.is_existing = True
            report.info(
                "LOCATOR_EXISTS",
                f"ADR cue already exists at {item.tc_in_raw}",
                {"row": item.row_num},
            )

    seen_cues: set[str] = set()
    for item in items:
        if not item.is_existing and item.cue_raw:
            _check_and_assign_cue(item, item.cue_raw, seen_cues, existing_cues, report)

    auto_items = [it for it in items if not it.is_existing and not it.cue_raw]
    for item in sorted(auto_items, key=lambda it: it.sample_pos):
        max_num += 1
        _check_and_assign_cue(item, f"{cue_prefix}{max_num:03d}", seen_cues, existing_cues, report)


def _row_fields(cells: list[str], mapping: dict[int, str], is_adr: bool) -> dict[str, str]:
    """Campos da linha; numa lista de marcadores o TC de saida nao conta."""
    fields = _extract_row_fields(cells, mapping)
    if not is_adr:
        fields.pop("tc_out", None)
    return fields


def _parse_row(
    line_num: int,
    fields: dict[str, str],
    offset_frames: int,
    rate: TimecodeRate,
    sample_rate: int,
    live_origin: int,
    report: Report,
) -> CsvRowItem:
    """Uma linha do CSV com o TC de entrada e de saida ja conferidos."""
    tc_in_raw = fields.get("tc_in", "")
    tc_out_raw = fields.get("tc_out")
    in_f, sample_pos, err_in = _validate_tc_in(
        tc_in_raw, offset_frames, rate, sample_rate, live_origin, line_num, report
    )
    shifted_out, err_out = _validate_tc_out(tc_out_raw, in_f, offset_frames, rate, line_num, report)
    return CsvRowItem(
        row_num=line_num,
        tc_in_raw=tc_in_raw,
        in_frames=in_f,
        sample_pos=sample_pos,
        tc_out_raw=tc_out_raw,
        shifted_out_label=shifted_out,
        name_raw=fields.get("name", ""),
        cue_raw=fields.get("cue", ""),
        character=fields.get("character", ""),
        line=fields.get("line", ""),
        comment=fields.get("comment", ""),
        error_code=err_in or err_out,
    )


def read_and_process_csv(
    csv_path: Path,
    report: Report,
    rate: TimecodeRate,
    sample_rate: int,
    live_origin: int,
    existing_locs: list[tuple[str, int, str]],
    kind: str = "auto",
    columns: dict[str, str] | None = None,
    offset: str | None = None,
    cue_prefix: str = "ADR",
) -> list[CsvRowItem] | None:
    """Read CSV, normalize headers, parse and validate rows."""
    text = read_raw_csv(csv_path, report)
    if text is None:
        return None

    delim = detect_delimiter(text)
    header, data_rows = parse_csv_rows(text, delim, report)
    mapping = map_headers(header, columns)

    if "tc_in" not in mapping.values():
        report.error("CSV_NO_TC", "no timecode column found in CSV", {"headers": header})
        return None

    offset_frames = 0
    if offset:
        try:
            offset_frames = parse_offset_frames(offset, rate)
        except ValueError as exc:
            report.error("CSV_BAD_TC", str(exc))
            return None

    actual_kind = detect_kind(kind, mapping, data_rows)
    is_adr = actual_kind == "adr"

    items = [
        _parse_row(
            line_num,
            _row_fields(cells, mapping, is_adr),
            offset_frames,
            rate,
            sample_rate,
            live_origin,
            report,
        )
        for line_num, cells in data_rows
    ]

    if is_adr:
        _assign_adr_metadata(items, cue_prefix, existing_locs, report)
    else:
        _assign_markers_metadata(items, existing_locs, report)

    return items
