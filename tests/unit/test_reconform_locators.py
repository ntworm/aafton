"""Testes unitarios da ancora, relatorios e locators da reconformacao."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from aafton import locators_write
from aafton.als_write_base import to_beats
from aafton.als_xml import get_value, load_als, renumber_siblings, save_als, set_value
from aafton.cut_ir import CutEvent, CutIR
from aafton.ir import PRODUCTION_LAYOUT
from aafton.reconform_anchor import resolve_reconform_anchor
from aafton.reconform_diff import Change, ChangeList, diff_cuts
from aafton.reconform_locators import write_change_locators
from aafton.reconform_report import change_list_text, write_change_list_csv
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.timecode import TimecodeRate, label_to_samples
from tests.unit.test_review_robustness import HOUR, make_set

RATE_25 = TimecodeRate(25, 1)
RATE_24 = TimecodeRate(24, 1)


def _sha256(path: Path) -> str:
    """Calcula o digest SHA-256 de um arquivo em disco."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clip(
    idx: int,
    source: str,
    rec_in: int,
    rec_out: int,
    src_in: int,
    src_out: int,
) -> CutEvent:
    """Cria um CutEvent de clip para os testes de reconformacao."""
    return CutEvent(idx, "clip", source, None, src_in, src_out, rec_in, rec_out)


def _sample_change_list() -> ChangeList:
    """Constroi um ChangeList de exemplo a 25 fps a partir de dois CutIR."""
    old_cut = CutIR(
        path="old_cut.edl",
        rate=RATE_25,
        start_frames=90000,
        events=[
            _clip(1, "A", 90000, 90025, 100, 125),
            _clip(2, "B", 90025, 90075, 200, 250),
            _clip(3, "DEL", 90075, 90100, 300, 325),
            _clip(4, "C", 90100, 90125, 400, 425),
        ],
    )
    new_cut = CutIR(
        path="new_cut.edl",
        rate=RATE_25,
        start_frames=90000,
        events=[
            _clip(1, "A", 90000, 90025, 100, 125),
            _clip(2, "INS", 90025, 90050, 500, 525),
            _clip(3, "B", 90050, 90100, 200, 250),
            _clip(4, "C", 90100, 90125, 400, 425),
        ],
    )
    cl = diff_cuts(old_cut, new_cut, Report())
    assert cl is not None
    return cl


def _add_user_locator(set_path: Path, name: str, time_beats: float, annotation: str) -> None:
    """Insere um locator de usuario no set antes do teste."""
    root = load_als(set_path)
    container = root.find(".//LiveSet/Locators/Locators")
    assert container is not None
    proto = locators_write._get_prototype(root, Report())
    assert proto is not None
    container.append(locators_write._build_locator_element(proto, time_beats, name, annotation))
    renumber_siblings(container, "Locator")
    save_als(root, set_path)


def test_text_report_and_csv(tmp_path: Path) -> None:
    """Confere linhas, contagens e colunas de timecode no texto e no CSV."""
    cl = _sample_change_list()
    text = change_list_text(cl)
    lines = text.strip().splitlines()
    assert "old_cut.edl" in lines[0]
    assert "new_cut.edl" in lines[1]
    assert "25 fps" in lines[2]
    assert "insert=1" in lines[3] and "shift=2" in lines[3] and "delete=1" in lines[3]
    change_lines = lines[5:]
    assert len(change_lines) == 4
    assert "insert | old - -> - | new 01:00:01:00 -> 01:00:02:00" in change_lines[0]
    assert "delta - | source INS" in change_lines[0]
    assert "shift | old 01:00:01:00 -> 01:00:03:00" in change_lines[1]
    assert "delta +25 | source B" in change_lines[1]
    assert "delete | old 01:00:03:00 -> 01:00:04:00 | new - -> -" in change_lines[2]
    assert "shift | old 01:00:04:00 -> 01:00:05:00" in change_lines[3]

    csv_path = write_change_list_csv(cl, tmp_path / "changes.csv")
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == [
        "kind",
        "old_in",
        "old_out",
        "new_in",
        "new_out",
        "delta_frames",
        "source",
        "detail",
    ]
    assert len(rows) == 5
    assert rows[1][:7] == ["insert", "", "", "01:00:01:00", "01:00:02:00", "", "INS"]
    assert rows[2][:7] == [
        "shift",
        "01:00:01:00",
        "01:00:03:00",
        "01:00:02:00",
        "01:00:04:00",
        "25",
        "B",
    ]
    assert rows[3][:7] == ["delete", "01:00:03:00", "01:00:04:00", "", "", "", "DEL"]


def test_locators_written_exact_beats_and_source_preserved(tmp_path: Path) -> None:
    """Grava locators RC em beats exatos na copia, sem marcar shift, e mantem origem intacta."""
    set_path = make_set(tmp_path)
    _add_user_locator(set_path, "User Cue", 5.0, "keep me")
    sidecar_p = sidecar_path_for(set_path)
    set_hash = _sha256(set_path)
    sidecar_hash = _sha256(sidecar_p)

    cl = _sample_change_list()
    result = write_change_locators(set_path, cl, Report())
    assert result.written is True
    assert result.added == 2
    assert result.output == tmp_path / "review reconform locators.als"
    assert _sha256(set_path) == set_hash
    assert _sha256(sidecar_p) == sidecar_hash

    out_root = load_als(result.output)
    locs = out_root.findall(".//LiveSet/Locators/Locators/Locator")
    entries = [
        (
            get_value(loc, "Name"),
            float(get_value(loc, "Time") or "0"),
            get_value(loc, "Annotation") or "",
        )
        for loc in locs
    ]
    assert ("TC 01:00:00:00 @ 48000 @ 25", 0.0, "") in entries
    assert ("User Cue", 5.0, "keep me") in entries
    rc_entries = [item for item in entries if (item[0] or "").startswith("RC ")]
    nao_shift = [ch for ch in cl.changes if ch.kind != "shift"]
    expected_frames = [90025, 90075]
    expected_tcs = ["01:00:01:00", "01:00:03:00"]
    expected_rc = [
        (
            f"RC {ch.kind} {expected_tcs[i]}",
            to_beats(expected_frames[i] * 1920, HOUR, 48000, 60.0),
            ch.detail,
        )
        for i, ch in enumerate(nao_shift)
    ]
    assert rc_entries == expected_rc
    assert not any(name.startswith("RC shift ") for name, _, _ in rc_entries)


def test_ambiguous_flag_and_before_origin_skipped(tmp_path: Path) -> None:
    """Marca mudancas ambiguas no texto e pula locator anterior a origem do set."""
    old_cut = CutIR("old.edl", RATE_25, 90000, [_clip(1, "A", 90000, 90050, 100, 150)])
    new_cut = CutIR(
        "new.edl",
        RATE_25,
        90000,
        [_clip(1, "A", 90000, 90050, 100, 150), _clip(2, "A", 90050, 90100, 100, 150)],
    )
    amb_cl = diff_cuts(old_cut, new_cut, Report())
    assert amb_cl is not None
    text = change_list_text(amb_cl)
    assert "Status: AMBIGUOUS" in text
    assert text.count("[AMBIGUOUS] ambiguous") == 2

    set_path = make_set(tmp_path)
    early_cl = ChangeList(
        old_path="old.edl",
        new_path="new.edl",
        rate=RATE_25,
        changes=[
            Change("delete", 89975, 90000, None, None, "EARLY", None, "deleted 25 frames"),
            Change("insert", None, None, 90050, 90075, "TAIL", None, "inserted 25 frames"),
        ],
        counts={"delete": 1, "insert": 1},
        ambiguous=False,
        time_map=None,
    )
    report = Report(strict=False)
    res = write_change_locators(set_path, early_cl, report)
    assert res.written is True
    assert res.added == 1
    assert any(iss.code == "RECONFORM_LOCATOR_BEFORE_ORIGIN" for iss in report.issues)
    assert len(res.skipped) == 1
    out_root = load_als(res.output)
    names = [
        get_value(loc, "Name") for loc in out_root.findall(".//LiveSet/Locators/Locators/Locator")
    ]
    assert "RC insert 01:00:00:00" in names


def test_without_sidecar_uses_tc_locator_at_25fps(tmp_path: Path) -> None:
    """Sem sidecar usa o locator TC na taxa do corte sem assumir 24 fps."""
    set_path = make_set(tmp_path)
    sidecar_path_for(set_path).unlink()
    root = load_als(set_path)
    tc_loc = next(
        loc
        for loc in root.findall(".//LiveSet/Locators/Locators/Locator")
        if (get_value(loc, "Name") or "").startswith("TC ")
    )
    set_value(tc_loc, "Name", "TC 01:00:00:24 @ 48000 @ 25")
    save_als(root, set_path)

    report = Report(strict=False)
    anchor = resolve_reconform_anchor(set_path, load_als(set_path), report, RATE_25)
    assert anchor is not None
    assert not report.incomplete
    assert not any(iss.code == "RECONFORM_ANCHOR_MISSING" for iss in report.issues)
    assert anchor.timecode_rate == RATE_25
    expected_origin = int(label_to_samples("01:00:00:24", RATE_25, 48000))
    assert anchor.live_origin_samples == expected_origin

    cl = _sample_change_list()
    res = write_change_locators(set_path, cl, Report(strict=False))
    assert res.written is True
    assert not any(iss.code == "RECONFORM_ANCHOR_MISSING" for iss in res.report.issues)


def test_anchor_errors_and_output_folder_refused(tmp_path: Path) -> None:
    """Recusa taxa divergente, layout de som direto, ausencia de ancora e pasta externa."""
    set_path = make_set(tmp_path)
    cl_25 = _sample_change_list()

    mismatch_report = Report(strict=False)
    mismatch = resolve_reconform_anchor(set_path, load_als(set_path), mismatch_report, RATE_24)
    assert mismatch is None
    assert any(iss.code == "RECONFORM_RATE_MISMATCH" for iss in mismatch_report.issues)

    outside = tmp_path / "other" / "out.als"
    outside.parent.mkdir()
    folder_report = Report(strict=False)
    folder_res = write_change_locators(set_path, cl_25, folder_report, output=outside)
    assert folder_res.written is False
    assert any(iss.code == "LOCATORS_OUTPUT_FOLDER" for iss in folder_report.issues)
    assert not outside.exists()

    sidecar_p = sidecar_path_for(set_path)
    data = json.loads(sidecar_p.read_text(encoding="utf-8"))
    data["layout"] = PRODUCTION_LAYOUT
    data["blocks"] = []
    sidecar_p.write_text(json.dumps(data), encoding="utf-8")
    prod_report = Report(strict=False)
    assert resolve_reconform_anchor(set_path, load_als(set_path), prod_report, RATE_25) is None
    assert any(iss.code == "RECONFORM_PRODUCTION_SET" for iss in prod_report.issues)

    sidecar_p.unlink()
    root = load_als(set_path)
    container = root.find(".//LiveSet/Locators/Locators")
    assert container is not None
    for loc in list(container):
        if (get_value(loc, "Name") or "").startswith("TC "):
            container.remove(loc)
    save_als(root, set_path)
    missing_report = Report(strict=False)
    missing = resolve_reconform_anchor(set_path, load_als(set_path), missing_report, RATE_25)
    assert missing is None
    assert any(iss.code == "RECONFORM_ANCHOR_MISSING" for iss in missing_report.issues)


def test_tempo_mismatch_rejects_anchor(tmp_path: Path) -> None:
    """Recusa ancora quando o BPM do set diverge do sidecar."""
    set_path = make_set(tmp_path)
    root = load_als(set_path)
    set_value(root, ".//MainTrack/DeviceChain/Mixer/Tempo/Manual", 120.0)
    save_als(root, set_path)
    report = Report(strict=False)
    assert resolve_reconform_anchor(set_path, load_als(set_path), report, RATE_25) is None
    assert any(iss.code == "ALS_TEMPO_CHANGED" for iss in report.issues)
