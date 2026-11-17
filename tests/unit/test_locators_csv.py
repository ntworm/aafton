"""Unit tests for aafton locators import and CSV parsing rules."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_read import _main_track, _tempo_do_set, read_session
from aafton.als_xml import (
    classify_id_tags,
    get_value,
    load_als,
    save_als,
    set_value,
    validate_document,
)
from aafton.bundled import default_template
from aafton.report import Report
from aafton.sidecar import Anchor, read_anchor, sidecar_path_for, write_sidecar
from aafton.timecode import (
    _LABEL_RE,
    TimecodeRate,
    frames_to_samples_exact,
    label_to_frames,
    quantize_samples,
)
from tests.unit.test_review_robustness import make_set


def expected_sample(tc: str, rate: TimecodeRate, sample_rate: int) -> int:
    """Exact integer sample position for comparison (Acceptance Criterion 3)."""
    frames = label_to_frames(tc, rate)
    exact = frames_to_samples_exact(frames, rate, sample_rate)
    return quantize_samples(exact)[0]


def read_set_locators(als_path: Path, anchor: Anchor) -> list[tuple[str, int, str]]:
    """Return user locators as (name, position_samples, annotation)."""
    root = load_als(als_path)
    locs = []
    for loc in root.iter("Locator"):
        name_node = loc.find("Name")
        time_node = loc.find("Time")
        if name_node is None or time_node is None:
            continue
        name = name_node.get("Value", "")
        if name.startswith("TC ") or _LABEL_RE.match(name):
            continue
        time_beats = float(time_node.get("Value", "0"))
        samples = anchor.live_origin_samples + round(
            time_beats * 60.0 / anchor.bpm * anchor.sample_rate
        )
        annotation = get_value(loc, "Annotation") or ""
        locs.append((name, samples, annotation))
    return locs


def get_clip_starts(als_path: Path) -> list[str]:
    """Return CurrentStart values for all audio clips in set."""
    root = load_als(als_path)
    return [
        c.find("CurrentStart").get("Value", "")
        for c in root.findall(".//AudioClip")
        if c.find("CurrentStart") is not None
    ]


def set_tempo_mismatch(als_path: Path, bpm: float = 140.0) -> None:
    """Modify Manual tempo to mismatch anchor bpm."""
    root = load_als(als_path)
    main_track = _main_track(root)
    assert main_track is not None
    set_value(main_track, "DeviceChain/Mixer/Tempo/Manual", bpm)
    save_als(root, als_path)


def add_tempo_automation(als_path: Path) -> None:
    """Add tempo automation curve on MainTrack."""
    root = load_als(als_path)
    main_track = _main_track(root)
    assert main_track is not None
    tempo_target = main_track.find("DeviceChain/Mixer/Tempo/AutomationTarget")
    if tempo_target is None:
        mixer = main_track.find("DeviceChain/Mixer")
        assert mixer is not None
        tempo = mixer.find("Tempo")
        assert tempo is not None
        tempo_target = ET.SubElement(tempo, "AutomationTarget", {"Id": "999"})
    tempo_id = tempo_target.get("Id", "999")
    envelopes = main_track.find("AutomationEnvelopes/Envelopes")
    if envelopes is None:
        auto_env = main_track.find("AutomationEnvelopes")
        if auto_env is None:
            auto_env = ET.SubElement(main_track, "AutomationEnvelopes")
        envelopes = ET.SubElement(auto_env, "Envelopes")
    env = ET.SubElement(envelopes, "AutomationEnvelope", {"Id": "998"})
    target = ET.SubElement(env, "EnvelopeTarget")
    ET.SubElement(target, "PointeeId", {"Value": tempo_id})
    events = ET.SubElement(ET.SubElement(env, "Automation"), "Events")
    ET.SubElement(events, "FloatEvent", {"Id": "0", "Time": "0", "Value": "100"})
    ET.SubElement(events, "FloatEvent", {"Id": "1", "Time": "10", "Value": "140"})
    save_als(root, als_path)


def remove_locators_container(als_path: Path) -> None:
    """Remove LiveSet/Locators/Locators container."""
    root = load_als(als_path)
    locs_parent = root.find(".//LiveSet/Locators")
    if locs_parent is not None:
        locs = locs_parent.find("Locators")
        if locs is not None:
            locs_parent.remove(locs)
    save_als(root, als_path)


def clear_locators(als_path: Path) -> None:
    """Clear all locator children from container."""
    root = load_als(als_path)
    locs = root.find(".//LiveSet/Locators/Locators")
    if locs is not None:
        for child in list(locs):
            locs.remove(child)
    save_als(root, als_path)


def strip_locator_annotation(als_path: Path) -> None:
    """Strip Annotation element from all locators."""
    root = load_als(als_path)
    for loc in root.iter("Locator"):
        ann = loc.find("Annotation")
        if ann is not None:
            loc.remove(ann)
    save_als(root, als_path)


def add_warp_clip(als_path: Path) -> None:
    """Set IsWarped to true on first audio clip."""
    root = load_als(als_path)
    clip = root.find(".//AudioClip")
    assert clip is not None
    set_value(clip, "IsWarped", "true")
    save_als(root, als_path)


def add_midi_track(als_path: Path) -> None:
    """Add a minimal MidiTrack to LiveSet/Tracks."""
    root = load_als(als_path)
    tracks = root.find("LiveSet/Tracks")
    assert tracks is not None
    ET.SubElement(tracks, "MidiTrack", {"Id": "888"})
    save_als(root, als_path)


# 1. Marker import formats and set integrity


@pytest.mark.parametrize(
    ("delimiter", "header_row", "sep_char"),
    [
        (",", "entrada,nome,nota", ","),
        (";", "inicio;marcador;obs", ";"),
        ("\t", "start\ttitle\tmotivo", "\t"),
    ],
)
def test_import_markers_formats_and_integrity(tmp_path, delimiter, header_row, sep_char):
    """Import markers CSV in comma, semicolon, and tab with PT headers."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    orig_hash = hashlib.sha256(set_path.read_bytes()).hexdigest()
    orig_clips = get_clip_starts(set_path)
    orig_sidecar = sidecar_path_for(set_path).read_bytes()

    csv_file = tmp_path / "markers.csv"
    row1 = f"01:00:10:00{sep_char}Cena 1{sep_char}Primeiro take"
    row2 = f"01:00:20:00{sep_char}Cena 2{sep_char}Segundo take"
    csv_file.write_text(f"{header_row}\n{row1}\n{row2}\n", encoding="utf-8")

    res = import_locators(set_path, csv_file)
    assert res.written is True
    assert res.output is not None
    assert res.added == 2

    # Verify origin .als hash unchanged
    assert hashlib.sha256(set_path.read_bytes()).hexdigest() == orig_hash
    # Verify clip positions unchanged
    assert get_clip_starts(res.output) == orig_clips
    # Verify sidecar copied byte-identical
    assert sidecar_path_for(res.output).read_bytes() == orig_sidecar

    # Verify sample-accurate positions
    anchor = read_anchor(res.output)
    expected_sample_1 = expected_sample("01:00:10:00", anchor.timecode_rate, anchor.sample_rate)
    expected_sample_2 = expected_sample("01:00:20:00", anchor.timecode_rate, anchor.sample_rate)

    session = read_session(res.output, Report(strict=False))
    names = {m.name: (m.position_samples, m.comment) for m in session.markers}
    assert names["Cena 1"] == (expected_sample_1, "Primeiro take")
    assert names["Cena 2"] == (expected_sample_2, "Segundo take")


# 2. ADR Cues numbering, order and annotation


def test_import_adr_cues_auto_numbering_and_annotation(tmp_path):
    """ADR cues without cue column get sorted numbering and decision 7 format."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "adr.csv"
    # Provide CSV rows out of TC order
    csv_file.write_text(
        "tc_in,tc_out,character,line,comment\n"
        "01:00:20:00,01:00:25:00,JOAO,Segunda fala,Gritando\n"
        "01:00:05:00,01:00:08:00,MARIA,Primeira fala,Sussurrado\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is True
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)

    # First in TC order (01:00:05:00) gets ADR001, second gets ADR002
    loc_map = {name: (pos, ann) for name, pos, ann in locs}
    assert "ADR001 MARIA" in loc_map
    assert "ADR002 JOAO" in loc_map

    ann1 = loc_map["ADR001 MARIA"][1]
    assert "Out: 01:00:08:00" in ann1
    assert "Character: MARIA" in ann1
    assert "Line: Primeira fala" in ann1
    assert "Note: Sussurrado" in ann1


def test_import_adr_numbering_continues_from_existing(tmp_path):
    """ADR numbering continues from highest existing prefix number (e.g. ADR004 -> ADR005)."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    root = load_als(set_path)
    locs = root.find(".//LiveSet/Locators/Locators")
    assert locs is not None
    loc = ET.SubElement(locs, "Locator", {"Id": "20"})
    ET.SubElement(loc, "LomId", {"Value": "0"})
    ET.SubElement(loc, "Time", {"Value": "4.0"})
    ET.SubElement(loc, "Name", {"Value": "ADR004 CARLOS"})
    ET.SubElement(loc, "Annotation", {"Value": "Character: CARLOS\nLine: Antiga"})
    ET.SubElement(loc, "IsSongStart", {"Value": "false"})
    save_als(root, set_path)

    csv_file = tmp_path / "next_adr.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:15:00,01:00:18:00,ANA,Nova\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is True
    anchor = read_anchor(res.output)
    loc_names = [loc[0] for loc in read_set_locators(res.output, anchor)]
    assert "ADR005 ANA" in loc_names


# 3. Columns, offset, frame rate and timecode variations


def test_column_mapping_override(tmp_path):
    """--column overrides header mapping."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "custom_cols.csv"
    csv_file.write_text(
        "MeuTC,Descricao,Obs\n01:00:10:00,Ponto A,Observacao A\n",
        encoding="utf-8",
    )
    columns = {"tc_in": "MeuTC", "name": "Descricao", "comment": "Obs"}
    res = import_locators(set_path, csv_file, columns=columns)
    assert res.written is True
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)
    assert any(loc[0] == "Ponto A" and loc[2] == "Observacao A" for loc in locs)


def test_offset_negative_and_positive(tmp_path):
    """Negative and positive offset shifts locators and Out annotation."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path, origin=0)
    csv_file = tmp_path / "offset.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:00:00,01:00:10:00,HERO,Fugir\n",
        encoding="utf-8",
    )
    # -00:59:00:00 shifts 01:00:00:00 back to 00:01:00:00
    res_neg = import_locators(
        set_path, csv_file, offset="-00:59:00:00", output=tmp_path / "neg.als"
    )
    assert res_neg.written is True
    anchor = read_anchor(res_neg.output)
    locs = read_set_locators(res_neg.output, anchor)
    expected_sample_neg = expected_sample("00:01:00:00", anchor.timecode_rate, anchor.sample_rate)
    assert locs[0][1] == expected_sample_neg
    assert "Out: 00:01:10:00" in locs[0][2]

    # +00:00:10:00 shifts Out from 01:00:10:00 to 01:00:20:00
    res_pos = import_locators(
        set_path, csv_file, offset="+00:00:10:00", output=tmp_path / "pos.als"
    )
    assert res_pos.written is True
    locs_pos = read_set_locators(res_pos.output, anchor)
    assert "Out: 01:00:20:00" in locs_pos[0][2]


def test_drop_frame_2997_and_offset(tmp_path):
    """Drop frame semicolon parsing and offset at 29.97 DF."""
    from aafton.locators_write import import_locators

    base_als = tmp_path / "df2997.als"
    save_als(load_als(default_template()), base_als)
    rate = TimecodeRate(30000, 1001, drop_frame=True)
    # Origin at 0, so the shifted TC (00:01:00;02) falls inside the session
    origin = 0
    write_sidecar(
        base_als,
        Anchor(
            live_origin_samples=origin,
            session_start_samples=origin,
            sample_rate=48000,
            timecode_rate=rate,
            bpm=_tempo_do_set(load_als(base_als)),
            source_path=str(base_als),
        ),
    )
    csv_file = tmp_path / "df.csv"
    csv_file.write_text("tc_in,name\n01:00:00;02,DF Marker\n", encoding="utf-8")
    res = import_locators(base_als, csv_file, offset="-00:59:00:00")
    assert res.written is True


def test_underscore_headers(tmp_path):
    """Headers with underscores (tc_in, tc_out) are properly matched."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "underscore.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:10:00,01:00:15:00,VOX,Fala\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is True


def test_single_column_csv_comma_fallback(tmp_path):
    """Single-column CSV without delimiters defaults to comma and imports."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "single_col.csv"
    csv_file.write_text("tc_in\n01:00:10:00\n01:00:20:00\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is True
    assert res.added == 2


def test_residue_info_at_44100(tmp_path):
    """At 44100 Hz and 23.976 fps, sample residue is logged as info and written."""
    from aafton.locators_write import import_locators

    base_als = tmp_path / "res44100.als"
    save_als(load_als(default_template()), base_als)
    rate = TimecodeRate(24000, 1001)
    write_sidecar(
        base_als,
        Anchor(
            live_origin_samples=0,
            session_start_samples=0,
            sample_rate=44100,
            timecode_rate=rate,
            bpm=_tempo_do_set(load_als(base_als)),
            source_path=str(base_als),
        ),
    )
    csv_file = tmp_path / "tc_residue.csv"
    # 01:00:00:01 at 44100 and 23.976 produces a non-integer sample fraction
    csv_file.write_text("tc_in,name\n01:00:00:01,ResidueMarker\n", encoding="utf-8")
    res = import_locators(base_als, csv_file, strict=True)
    assert res.written is True
    residue_issues = [i for i in res.report.issues if i.code == "TC_SAMPLE_RESIDUE"]
    assert len(residue_issues) >= 1
    assert residue_issues[0].severity == "info"


def test_adr_with_only_line(tmp_path):
    """CSV with tc_in and line becomes ADR cue under kind auto."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "only_line.csv"
    csv_file.write_text("tc_in,line\n01:00:10:00,Apenas fala\n", encoding="utf-8")
    res = import_locators(set_path, csv_file, kind="auto")
    assert res.written is True
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)
    assert locs[0][0].startswith("ADR001")
    assert "Line: Apenas fala" in locs[0][2]


def test_newline_flattening(tmp_path):
    """Field containing newline is flattened to space with warning."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "newline.csv"
    csv_file.write_text(
        'tc_in,name,comment\n01:00:10:00,Nome,"Primeira linha\nSegunda linha"\n',
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is True
    assert any(i.code == "CSV_NEWLINE_FLATTENED" for i in res.report.issues)
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)
    assert locs[0][2] == "Primeira linha Segunda linha"


# 4. Strict refusals


def test_strict_refusal_bad_tc(tmp_path):
    """Invalid timecode refuses in strict mode with CSV_BAD_TC."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "bad_tc.csv"
    csv_file.write_text("tc_in,name\n99:99:99:99,Bad\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "CSV_BAD_TC" for i in res.report.issues)


def test_strict_refusal_marker_before_origin(tmp_path):
    """TC before Live origin refuses with MARKER_BEFORE_ORIGIN."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)  # origin is 1 hour: 01:00:00:00
    csv_file = tmp_path / "before_origin.csv"
    csv_file.write_text("tc_in,name\n00:30:00:00,Before\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "MARKER_BEFORE_ORIGIN" for i in res.report.issues)


def test_strict_refusal_out_before_in(tmp_path):
    """tc_out <= tc_in refuses with CUE_OUT_BEFORE_IN."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "out_before_in.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:15:00,01:00:10:00,A,Fala\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "CUE_OUT_BEFORE_IN" for i in res.report.issues)


def test_strict_refusal_duplicate_cue_in_csv(tmp_path):
    """Duplicate cue within CSV list refuses with CUE_DUPLICATE."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "dup_cue.csv"
    csv_file.write_text(
        "cue,tc_in,tc_out,character,line\n"
        "ADR001,01:00:10:00,01:00:15:00,A,Fala 1\n"
        "ADR001,01:00:20:00,01:00:25:00,B,Fala 2\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "CUE_DUPLICATE" for i in res.report.issues)


def test_strict_refusal_duplicate_cue_with_set(tmp_path):
    """Cue conflicting with different content in set refuses with CUE_DUPLICATE."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    root = load_als(set_path)
    locs = root.find(".//LiveSet/Locators/Locators")
    assert locs is not None
    loc = ET.SubElement(locs, "Locator", {"Id": "30"})
    ET.SubElement(loc, "LomId", {"Value": "0"})
    ET.SubElement(loc, "Time", {"Value": "2.0"})
    ET.SubElement(loc, "Name", {"Value": "ADR001 JOAO"})
    ET.SubElement(loc, "Annotation", {"Value": "Line: Conteudo original"})
    ET.SubElement(loc, "IsSongStart", {"Value": "false"})
    save_als(root, set_path)

    csv_file = tmp_path / "conflict.csv"
    csv_file.write_text(
        "cue,tc_in,tc_out,character,line\nADR001,01:00:10:00,01:00:15:00,MARIA,Outro conteudo\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "CUE_DUPLICATE" for i in res.report.issues)


def test_strict_refusal_reserved_locator_name(tmp_path):
    """Reserved names ('TC ...' or timecode labels) refuse with LOCATOR_NAME_RESERVED."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "reserved.csv"
    csv_file.write_text(
        "tc_in,name\n01:00:10:00,TC Ancoragem\n01:00:20:00,01:00:20:00\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "LOCATOR_NAME_RESERVED" for i in res.report.issues)


def test_strict_refusal_missing_tc_column(tmp_path):
    """CSV missing timecode column refuses with CSV_NO_TC."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "no_tc.csv"
    csv_file.write_text("name,comment\nMarcador,Comentario\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "CSV_NO_TC" for i in res.report.issues)


def test_refusal_tempo_automation(tmp_path):
    """Tempo automation refuses in strict and permissive with ALS_TEMPO_AUTOMATION_UNSUPPORTED."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    add_tempo_automation(set_path)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")

    res_strict = import_locators(set_path, csv_file, strict=True)
    assert res_strict.written is False
    assert any(i.code == "ALS_TEMPO_AUTOMATION_UNSUPPORTED" for i in res_strict.report.issues)

    res_perm = import_locators(set_path, csv_file, strict=False)
    assert res_perm.written is False
    assert any(i.code == "ALS_TEMPO_AUTOMATION_UNSUPPORTED" for i in res_perm.report.issues)


def test_refusal_tempo_changed(tmp_path):
    """Tempo changed refuses in strict and permissive with ALS_TEMPO_CHANGED."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    set_tempo_mismatch(set_path, bpm=140.0)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")

    res_strict = import_locators(set_path, csv_file, strict=True)
    assert res_strict.written is False
    assert any(i.code == "ALS_TEMPO_CHANGED" for i in res_strict.report.issues)

    res_perm = import_locators(set_path, csv_file, strict=False)
    assert res_perm.written is False
    assert any(i.code == "ALS_TEMPO_CHANGED" for i in res_perm.report.issues)


def test_refusal_no_locators_container(tmp_path):
    """Set missing LiveSet/Locators/Locators container refuses with NO_LOCATORS."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    remove_locators_container(set_path)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "NO_LOCATORS" for i in res.report.issues)


def test_refusal_start_incomplete(tmp_path):
    """--frame-rate without --start-tc on set without sidecar refuses LOCATORS_START_INCOMPLETE."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    sidecar_path_for(set_path).unlink()
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")
    res = import_locators(set_path, csv_file, frame_rate=TimecodeRate(25, 1), start_tc=None)
    assert res.written is False
    assert any(i.code == "LOCATORS_START_INCOMPLETE" for i in res.report.issues)


def test_refusal_production_set(tmp_path):
    """Production audio set refuses with LOCATORS_PRODUCTION_SET."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    sidecar_p = sidecar_path_for(set_path)
    data = json.loads(sidecar_p.read_text(encoding="utf-8"))
    data["layout"] = "production_blocks"
    sidecar_p.write_text(json.dumps(data), encoding="utf-8")

    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False
    assert any(i.code == "LOCATORS_PRODUCTION_SET" for i in res.report.issues)


def test_refusal_output_folder_and_overwrite(tmp_path):
    """Output in different folder or existing file refuses."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")

    diff_folder = tmp_path / "diff_dir"
    diff_folder.mkdir()
    res_folder = import_locators(set_path, csv_file, output=diff_folder / "out.als")
    assert res_folder.written is False
    assert any(i.code == "LOCATORS_OUTPUT_FOLDER" for i in res_folder.report.issues)

    existing_file = tmp_path / "exists.als"
    existing_file.write_bytes(b"existing")
    res_exists = import_locators(set_path, csv_file, output=existing_file)
    assert res_exists.written is False


def test_refusal_sidecar_missing_without_anchor(tmp_path):
    """Set without sidecar, start spec, or TC locator refuses anchor lookup."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    sidecar_path_for(set_path).unlink()
    clear_locators(set_path)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,M\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    assert res.written is False


# 5. Permissive mode


def test_permissive_mode_skips_bad_rows_and_writes_good(tmp_path):
    """Permissive mode writes good rows, skips bad ones and reports them."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "mixed.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n"
        "bad_tc,01:00:10:00,A,Fala 1\n"
        "00:00:10:00,01:00:10:00,B,Fala 2\n"
        "01:00:20:00,01:00:10:00,C,Fala 3\n"
        "01:00:10:00,01:00:15:00,D,Fala 4\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file, strict=False)
    assert res.written is True
    assert res.added == 1
    assert len(res.skipped) == 3
    skipped_rows = [s["row"] for s in res.skipped]
    assert 2 in skipped_rows
    assert 3 in skipped_rows
    assert 4 in skipped_rows


# 6. Idempotency (3 cases)


def test_idempotency_markers(tmp_path):
    """Re-importing identical markers list is idempotent and reports LOCATOR_EXISTS."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "idemp_m.csv"
    csv_file.write_text("tc_in,name,comment\n01:00:10:00,M1,Note1\n", encoding="utf-8")

    res1 = import_locators(set_path, csv_file)
    assert res1.written is True
    assert res1.added == 1

    res2 = import_locators(res1.output, csv_file)
    assert res2.added == 0
    assert any(i.code == "LOCATOR_EXISTS" for i in res2.report.issues)


def test_idempotency_adr_with_cue(tmp_path):
    """Re-importing ADR list with cue column is idempotent."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "idemp_adr_cue.csv"
    csv_file.write_text(
        "cue,tc_in,tc_out,character,line\nADR001,01:00:10:00,01:00:15:00,JOAO,Fala\n",
        encoding="utf-8",
    )

    res1 = import_locators(set_path, csv_file)
    assert res1.written is True
    assert res1.added == 1

    res2 = import_locators(res1.output, csv_file)
    assert res2.added == 0
    assert any(i.code == "LOCATOR_EXISTS" for i in res2.report.issues)


def test_idempotency_adr_without_cue(tmp_path):
    """Re-importing ADR list without cue column does not re-allocate cue numbers."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "idemp_adr_nocue.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:10:00,01:00:15:00,JOAO,Fala\n",
        encoding="utf-8",
    )

    res1 = import_locators(set_path, csv_file)
    assert res1.written is True
    assert res1.added == 1

    res2 = import_locators(res1.output, csv_file)
    assert res2.added == 0
    assert any(i.code == "LOCATOR_EXISTS" for i in res2.report.issues)


# 7. XML properties, validation and non-interfering clip types


def test_xml_is_song_start_and_annotation_and_validation(tmp_path):
    """New locators have IsSongStart false, get Annotation, and pass validate_document."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    strip_locator_annotation(set_path)
    csv_file = tmp_path / "xml_val.csv"
    csv_file.write_text(
        "tc_in,tc_out,character,line\n01:00:10:00,01:00:15:00,MARIA,Ola\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file)
    assert res.written is True

    root = load_als(res.output)
    posicionais, _ = classify_id_tags(root)
    validate_document(root, posicionais)

    for loc in root.iter("Locator"):
        name = loc.find("Name").get("Value", "")
        if name.startswith("TC "):
            assert loc.find("IsSongStart").get("Value") == "true"
        elif not _LABEL_RE.match(name):
            assert loc.find("IsSongStart").get("Value") == "false"
            assert loc.find("Annotation") is not None


def test_set_with_warp_or_midi_clip_in_strict(tmp_path):
    """Set with warp or MIDI clip receives locators without clip-level rejection."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    add_warp_clip(set_path)
    add_midi_track(set_path)
    csv_file = tmp_path / "warp_midi.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,WarpMarker\n", encoding="utf-8")
    res = import_locators(set_path, csv_file, strict=True)
    assert res.written is True


# 8. File encoding and template fallback


def test_cp1252_encoding_fallback(tmp_path):
    """CSV encoded in cp1252 with accents triggers warning CSV_ENCODING_CP1252."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "cp1252.csv"
    content = "entrada,nome,nota\n01:00:10:00,Aten\xe7\xe3o,A\xe7\xe3o\n"
    csv_file.write_bytes(content.encode("cp1252"))

    res = import_locators(set_path, csv_file)
    assert res.written is True
    assert any(i.code == "CSV_ENCODING_CP1252" for i in res.report.issues)
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)
    assert any(loc[0] == "Aten\xe7\xe3o" for loc in locs)


def test_template_prototype_when_set_has_no_locators(tmp_path):
    """Set without any locator clones prototype from bundled template."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    clear_locators(set_path)
    csv_file = tmp_path / "template_proto.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,FromProto\n", encoding="utf-8")

    res = import_locators(set_path, csv_file)
    assert res.written is True
    anchor = read_anchor(res.output)
    locs = read_set_locators(res.output, anchor)
    assert any(loc[0] == "FromProto" for loc in locs)


# 9. CLI integration


def test_cli_locators_import_exit_codes_and_json(tmp_path, capsys):
    """CLI locators import exits 0 on success, 1 on refusal/skipped, and prints --json."""
    from aafton.cli import main

    set_path = make_set(tmp_path)
    bad_csv = tmp_path / "three_bad.csv"
    bad_csv.write_text(
        "tc_in,tc_out,character,line\n"
        "bad_tc,01:00:10:00,A,Fala 1\n"
        "00:00:10:00,01:00:10:00,B,Fala 2\n"
        "01:00:20:00,01:00:10:00,C,Fala 3\n",
        encoding="utf-8",
    )

    code_refusal = main(["locators", "import", str(set_path), str(bad_csv), "--json"])
    assert code_refusal == 1
    out_refusal = capsys.readouterr().out
    data_refusal = json.loads(out_refusal)
    assert len(data_refusal.get("issues", [])) >= 3

    good_csv = tmp_path / "good.csv"
    good_csv.write_text("tc_in,name\n01:00:10:00,GoodMarker\n", encoding="utf-8")
    code_good = main(["locators", "import", str(set_path), str(good_csv)])
    assert code_good == 0


def test_live_saved_set_with_inflated_warp_marker_ids_is_accepted(tmp_path):
    """A set saved by Live with WarpMarker Ids out of list order still gets locators.

    Live itself writes such Ids (measured in a real set); only a problem the
    import creates refuses the write.
    """
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    root = load_als(set_path)
    clip = root.find(".//AudioClip")
    assert clip is not None
    markers = clip.find("WarpMarkers")
    if markers is None:
        markers = ET.SubElement(clip, "WarpMarkers")
    ET.SubElement(markers, "WarpMarker", {"Id": "2182", "SecTime": "0", "BeatTime": "0"})
    ET.SubElement(markers, "WarpMarker", {"Id": "2183", "SecTime": "1", "BeatTime": "2"})
    save_als(root, set_path)
    before = hashlib.sha256(set_path.read_bytes()).hexdigest()

    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,Cena\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)

    assert res.written is True
    assert not [i for i in res.report.issues if i.code == "DOCUMENT_INVALID"]
    assert hashlib.sha256(set_path.read_bytes()).hexdigest() == before


def test_markers_kind_ignores_end_timecode(tmp_path):
    """A point-marker export with End equal to Start imports under --kind markers."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "nle.csv"
    csv_file.write_text(
        "start,end,name\n01:00:10:00,01:00:10:00,Cena 1\n01:00:20:00,fim,Cena 2\n",
        encoding="utf-8",
    )
    res = import_locators(set_path, csv_file, kind="markers")
    assert res.written is True
    assert res.added == 2
    assert not [i for i in res.report.issues if i.severity == "error"]


def test_report_metadata_shows_the_anchor_used(tmp_path):
    """The report names the bar-1 timecode, frame rate, sample rate and bpm used."""
    from aafton.locators_write import import_locators

    set_path = make_set(tmp_path)
    csv_file = tmp_path / "m.csv"
    csv_file.write_text("tc_in,name\n01:00:10:00,Cena\n", encoding="utf-8")
    res = import_locators(set_path, csv_file)
    anchor = read_anchor(set_path)
    meta = res.report.metadata["anchor"]
    assert meta["sample_rate"] == anchor.sample_rate
    assert meta["bpm"] == anchor.bpm
    assert meta["live_origin_timecode"].startswith("01:00:00")
