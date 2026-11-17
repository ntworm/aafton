"""Unit tests for aafton cue sheet export and round-trip verification."""

import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_xml import load_als, save_als
from tests.unit.test_review_robustness import make_set


def add_user_locator(set_path: Path, name: str, time_beats: float, annotation: str = "") -> None:
    """Add a user locator element directly to LiveSet/Locators/Locators."""
    root = load_als(set_path)
    locs = root.find(".//LiveSet/Locators/Locators")
    assert locs is not None
    next_id = str(len(list(locs)) + 10)
    elem = ET.SubElement(locs, "Locator", {"Id": next_id})
    ET.SubElement(elem, "LomId", {"Value": "0"})
    ET.SubElement(elem, "Time", {"Value": str(time_beats)})
    ET.SubElement(elem, "Name", {"Value": name})
    ET.SubElement(elem, "Annotation", {"Value": annotation})
    ET.SubElement(elem, "IsSongStart", {"Value": "false"})
    save_als(root, set_path)


def test_export_cue_sheet_basic_format(tmp_path):
    """Export creates UTF-8 BOM CSV with decision 11 columns in time order."""
    from aafton.cue_sheet import export_cue_sheet

    set_path = make_set(tmp_path)
    # Add ADR cue at 10.0 beats (01:00:10:00)
    add_user_locator(
        set_path,
        "ADR001 JOAO",
        10.0,
        "Out: 01:00:15:00\nCharacter: JOAO\nLine: Ol\xe1 mundo\nNote: Sussurrado",
    )
    # Add marker at 20.0 beats (01:00:20:00)
    add_user_locator(set_path, "Cena 1", 20.0, "Inicio da cena")

    out_csv = tmp_path / "cues.csv"
    res = export_cue_sheet(set_path, out_csv)
    assert res.written is True
    assert res.output == out_csv

    # Verify UTF-8 BOM
    raw_bytes = out_csv.read_bytes()
    assert raw_bytes.startswith(b"\xef\xbb\xbf")

    # Read CSV contents
    text = raw_bytes.decode("utf-8-sig")
    reader = list(csv.DictReader(text.splitlines()))
    assert len(reader) == 2

    # Expected columns
    expected_cols = ["cue", "tc_in", "tc_out", "character", "line", "name", "comment"]
    assert list(reader[0].keys()) == expected_cols

    # ADR row
    adr_row = reader[0]
    assert adr_row["cue"] == "ADR001"
    assert adr_row["tc_in"] == "01:00:10:00"
    assert adr_row["tc_out"] == "01:00:15:00"
    assert adr_row["character"] == "JOAO"
    assert adr_row["line"] == "Ol\xe1 mundo"
    assert adr_row["name"] == ""
    assert adr_row["comment"] == "Sussurrado"

    # Marker row
    marker_row = reader[1]
    assert marker_row["cue"] == ""
    assert marker_row["tc_in"] == "01:00:20:00"
    assert marker_row["tc_out"] == ""
    assert marker_row["character"] == ""
    assert marker_row["line"] == ""
    assert marker_row["name"] == "Cena 1"
    assert marker_row["comment"] == "Inicio da cena"


def test_export_only_cues_filter(tmp_path):
    """--only-cues exports only ADR cues, omitting general markers."""
    from aafton.cue_sheet import export_cue_sheet

    set_path = make_set(tmp_path)
    add_user_locator(
        set_path,
        "ADR001 JOAO",
        10.0,
        "Out: 01:00:15:00\nCharacter: JOAO\nLine: Fala",
    )
    add_user_locator(set_path, "Marcador Geral", 20.0, "Nota geral")

    out_csv = tmp_path / "only_cues.csv"
    res = export_cue_sheet(set_path, out_csv, only_cues=True)
    assert res.written is True
    text = out_csv.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert len(rows) == 1
    assert rows[0]["cue"] == "ADR001"


def test_export_duplicate_cue_warning(tmp_path):
    """Duplicate cue in set issues warning CUE_DUPLICATE but exports both rows."""
    from aafton.cue_sheet import export_cue_sheet

    set_path = make_set(tmp_path)
    add_user_locator(
        set_path,
        "ADR001 A",
        10.0,
        "Out: 01:00:15:00\nCharacter: A\nLine: Fala 1",
    )
    add_user_locator(
        set_path,
        "ADR001 B",
        20.0,
        "Out: 01:00:25:00\nCharacter: B\nLine: Fala 2",
    )

    out_csv = tmp_path / "dup_cues.csv"
    res = export_cue_sheet(set_path, out_csv)
    assert res.written is True
    assert any(i.code == "CUE_DUPLICATE" for i in res.report.issues)
    text = out_csv.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert len(rows) == 2


def test_export_refusal_when_output_exists(tmp_path):
    """Export refuses to overwrite existing output file."""
    from aafton.cue_sheet import export_cue_sheet

    set_path = make_set(tmp_path)
    out_csv = tmp_path / "already_exists.csv"
    out_csv.write_text("existing content", encoding="utf-8")

    res = export_cue_sheet(set_path, out_csv)
    assert res.written is False
    assert out_csv.read_text(encoding="utf-8") == "existing content"


def test_round_trip_markers(tmp_path):
    """Round trip: import markers CSV, export cue sheet, compare fields."""
    from aafton.cue_sheet import export_cue_sheet
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    in_csv = tmp_path / "input_markers.csv"
    in_csv.write_text(
        "tc_in,name,comment\n01:00:10:00,Cena A,Primeira nota\n01:00:30:00,Cena B,Segunda nota\n",
        encoding="utf-8",
    )

    import_res = import_locators(set_path, in_csv)
    assert import_res.written is True

    out_csv = tmp_path / "exported_markers.csv"
    export_res = export_cue_sheet(import_res.output, out_csv)
    assert export_res.written is True

    text = out_csv.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert len(rows) == 2
    assert (rows[0]["tc_in"], rows[0]["name"], rows[0]["comment"]) == (
        "01:00:10:00",
        "Cena A",
        "Primeira nota",
    )
    assert (rows[1]["tc_in"], rows[1]["name"], rows[1]["comment"]) == (
        "01:00:30:00",
        "Cena B",
        "Segunda nota",
    )


def test_round_trip_adr(tmp_path):
    """Round trip: import ADR CSV, export cue sheet, compare all ADR fields."""
    from aafton.cue_sheet import export_cue_sheet
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    in_csv = tmp_path / "input_adr.csv"
    in_csv.write_text(
        "cue,tc_in,tc_out,character,line,comment\n"
        "ADR001,01:00:05:00,01:00:08:00,LUCAS,Primeira fala,Calmo\n"
        "ADR002,01:00:20:00,01:00:24:00,MARIA,Segunda fala,Brava\n",
        encoding="utf-8",
    )

    import_res = import_locators(set_path, in_csv)
    assert import_res.written is True

    out_csv = tmp_path / "exported_adr.csv"
    export_res = export_cue_sheet(import_res.output, out_csv)
    assert export_res.written is True

    text = out_csv.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(text.splitlines()))
    assert len(rows) == 2
    for r_in, r_out in zip(list(csv.DictReader(in_csv.read_text().splitlines())), rows):
        for field in ("cue", "tc_in", "tc_out", "character", "line", "comment"):
            assert r_out[field] == r_in[field]


def test_kind_auto_on_exported_markers_reimported(tmp_path):
    """Export of markers-only set, re-imported with kind=auto, is treated as markers."""
    from aafton.cue_sheet import export_cue_sheet
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    add_user_locator(set_path, "Marcador 1", 10.0, "Nota 1")
    add_user_locator(set_path, "Marcador 2", 20.0, "Nota 2")

    exported_csv = tmp_path / "exported.csv"
    res_export = export_cue_sheet(set_path, exported_csv)
    assert res_export.written is True

    # Fresh base set for re-import
    (tmp_path / "fresh").mkdir()
    fresh_set = make_set(tmp_path / "fresh")
    res_import = import_locators(fresh_set, exported_csv, kind="auto")
    assert res_import.written is True
    # If treated as markers, added locators retain name and do not have ADR cue prefix
    from aafton.sidecar import read_anchor
    from tests.unit.test_locators_csv import read_set_locators

    anchor = read_anchor(res_import.output)

    user_locs = read_set_locators(res_import.output, anchor)
    names = [loc[0] for loc in user_locs]
    assert "Marcador 1" in names
    assert "Marcador 2" in names
    assert not any(n.startswith("ADR") for n in names)


def test_cli_locators_export(tmp_path, capsys):
    """CLI locators export exits 0 on success, 1 on refusal, and outputs --json."""
    from aafton.cli import main

    set_path = make_set(tmp_path)
    out_csv = tmp_path / "cli_out.csv"

    # Success case
    code = main(["locators", "export", str(set_path), "--output", str(out_csv), "--json"])
    assert code == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "issues" in data

    # Refusal case: overwrite existing output file
    code_refusal = main(["locators", "export", str(set_path), "--output", str(out_csv)])
    assert code_refusal == 1
