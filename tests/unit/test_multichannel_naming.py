import aaf2
from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.support.paths import fixture_aaf


def _make_aaf_with_slots(path, slot_specs):
    with aaf2.open(str(path), "w") as f:
        comp = f.create.CompositionMob("TestComp")
        f.content.mobs.append(comp)

        # TC slot
        tc_slot = comp.create_timeline_slot(edit_rate=25)
        tc_slot.name = "TC"
        tc = f.create.Timecode(fps=25, drop=False)
        tc.start = 0
        tc.length = 1000
        tc_slot.segment = tc

        for i, (name, ptn, length) in enumerate(slot_specs):
            slot = comp.create_timeline_slot(edit_rate=48000)
            slot.name = name
            if ptn is not None:
                slot["PhysicalTrackNumber"].value = ptn
            seq = f.create.Sequence(media_kind="sound")
            slot.segment = seq
            filler = f.create.Filler("sound", length=length)
            seq.components.append(filler)


def test_single_channel_track_no_suffix(tmp_path):
    aaf_path = tmp_path / "single.aaf"
    _make_aaf_with_slots(aaf_path, [("MONO", 1, 48000)])
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 1
    assert session.tracks[0].name == "MONO"
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 0


def test_stereo_track_naming(tmp_path):
    aaf_path = tmp_path / "stereo.aaf"
    _make_aaf_with_slots(aaf_path, [("STEREO", 1, 48000), ("STEREO", 2, 48000)])
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 2
    assert [t.name for t in session.tracks] == ["STEREO [L]", "STEREO [R]"]
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 0


def test_ambisonic_1st_order_track_naming(tmp_path):
    aaf_path = tmp_path / "amb.aaf"
    _make_aaf_with_slots(
        aaf_path,
        [
            ("AMB 1ST", 1, 48000),
            ("AMB 1ST", 2, 48000),
            ("AMB 1ST", 3, 48000),
            ("AMB 1ST", 4, 48000),
        ],
    )
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 4
    assert [t.name for t in session.tracks] == [
        "AMB 1ST [W]",
        "AMB 1ST [X]",
        "AMB 1ST [Y]",
        "AMB 1ST [Z]",
    ]
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 1
    assert layout_issues[0].context["layout"] == "ambisonic 1st order"


def test_quad_track_naming(tmp_path):
    aaf_path = tmp_path / "quad.aaf"
    _make_aaf_with_slots(
        aaf_path,
        [
            ("Quad_FX", 1, 48000),
            ("Quad_FX", 2, 48000),
            ("Quad_FX", 3, 48000),
            ("Quad_FX", 4, 48000),
        ],
    )
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 4
    assert [t.name for t in session.tracks] == [
        "Quad_FX [L]",
        "Quad_FX [R]",
        "Quad_FX [Ls]",
        "Quad_FX [Rs]",
    ]
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 1
    assert layout_issues[0].context["layout"] == "quad"


def test_5_1_surround_track_naming(tmp_path):
    aaf_path = tmp_path / "5_1.aaf"
    _make_aaf_with_slots(
        aaf_path,
        [
            ("5.1", 6, 48000),
            ("5.1", 7, 48000),
            ("5.1", 8, 48000),
            ("5.1", 9, 48000),
            ("5.1", 10, 48000),
            ("5.1", 11, 48000),
        ],
    )
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 6
    assert [t.name for t in session.tracks] == [
        "5.1 [L]",
        "5.1 [R]",
        "5.1 [C]",
        "5.1 [LFE]",
        "5.1 [Ls]",
        "5.1 [Rs]",
    ]
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 1
    assert layout_issues[0].context["layout"] == "5.1"


def test_generic_n_channel_track_naming(tmp_path):
    aaf_path = tmp_path / "generic.aaf"
    _make_aaf_with_slots(
        aaf_path, [("CustomBus", 1, 48000), ("CustomBus", 2, 48000), ("CustomBus", 3, 48000)]
    )
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 3
    assert [t.name for t in session.tracks] == [
        "CustomBus [ch1]",
        "CustomBus [ch2]",
        "CustomBus [ch3]",
    ]
    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    assert len(layout_issues) == 1
    assert layout_issues[0].context["layout"] == "generic 3-channel"


def test_physical_track_number_sorting(tmp_path):
    aaf_path = tmp_path / "ptn_sort.aaf"
    _make_aaf_with_slots(aaf_path, [("STEREO", 2, 48000), ("STEREO", 1, 48000)])
    report = Report(strict=False)
    session = read_aaf(aaf_path, report)
    assert len(session.tracks) == 2
    assert session.tracks[0].name == "STEREO [R]"
    assert session.tracks[1].name == "STEREO [L]"


def test_teste_aff_protools_produces_17_distinct_track_names():
    pt_aaf = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report)
    track_names = [t.name for t in session.tracks]

    assert len(track_names) == 17
    assert len(set(track_names)) == 17
    assert "5.1 [LFE]" in track_names
    assert "AMB 1ST [W]" in track_names
    assert "MONO" in track_names
    assert "REF [L]" in track_names
    assert "soundly [R]" in track_names

    expected_names = [
        "REF [L]",
        "REF [R]",
        "MONO",
        "STEREO [L]",
        "STEREO [R]",
        "5.1 [L]",
        "5.1 [C]",
        "5.1 [R]",
        "5.1 [Ls]",
        "5.1 [Rs]",
        "5.1 [LFE]",
        "AMB 1ST [W]",
        "AMB 1ST [X]",
        "AMB 1ST [Y]",
        "AMB 1ST [Z]",
        "soundly [L]",
        "soundly [R]",
    ]
    assert track_names == expected_names
