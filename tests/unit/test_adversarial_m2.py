import pytest
import wave
import aaf2

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf, _strip_channel_suffix
from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import _index
from tests.support.paths import fixture_aaf


def _create_aaf(path, slot_specs):
    """Helper to create synthetic AAF with specified slot names and PTNs.
    slot_specs is a list of (slot_name, ptn, length_samples)
    """
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

        for name, ptn, length in slot_specs:
            slot = comp.create_timeline_slot(edit_rate=48000)
            slot.name = name
            if ptn is not None:
                slot["PhysicalTrackNumber"].value = ptn
            seq = f.create.Sequence(media_kind="sound")
            slot.segment = seq
            filler = f.create.Filler("sound", length=length)
            seq.components.append(filler)


# ==============================================================================
# 1. Stress-testing Channel Counts (1ch, 2ch, 3ch, 4ch AMB, 4ch quad, 5ch, 6ch 5.1, 7ch, 8ch, 16ch)
# ==============================================================================


@pytest.mark.parametrize(
    "count,base_name,expected_suffixes,expected_layout",
    [
        (1, "MONO_TRACK", [""], None),
        (2, "STEREO_BUS", [" [L]", " [R]"], None),
        (3, "TRIPLE_BUS", [" [ch1]", " [ch2]", " [ch3]"], "generic 3-channel"),
        (4, "AMB_1ST", [" [W]", " [X]", " [Y]", " [Z]"], "ambisonic 1st order"),
        (4, "Ambisonics_B", [" [W]", " [X]", " [Y]", " [Z]"], "ambisonic 1st order"),
        (4, "my_amb_track", [" [W]", " [X]", " [Y]", " [Z]"], "ambisonic 1st order"),
        (4, "QUAD_STEM", [" [L]", " [R]", " [Ls]", " [Rs]"], "quad"),
        (5, "5.0_STEM", [" [ch1]", " [ch2]", " [ch3]", " [ch4]", " [ch5]"], "generic 5-channel"),
        (6, "5.1_SURROUND", [" [L]", " [R]", " [C]", " [LFE]", " [Ls]", " [Rs]"], "5.1"),
        (7, "7.0_STEM", [f" [ch{i + 1}]" for i in range(7)], "generic 7-channel"),
        (8, "7.1_SURROUND", [f" [ch{i + 1}]" for i in range(8)], "generic 8-channel"),
        (16, "HOA_3RD", [f" [ch{i + 1}]" for i in range(16)], "generic 16-channel"),
    ],
)
def test_all_channel_count_layouts(tmp_path, count, base_name, expected_suffixes, expected_layout):
    aaf_file = tmp_path / f"test_{count}ch_{base_name}.aaf"
    specs = [(base_name, i + 1, 48000) for i in range(count)]
    _create_aaf(aaf_file, specs)

    report = Report(strict=False)
    session = read_aaf(aaf_file, report)

    assert len(session.tracks) == count
    expected_names = [f"{base_name}{s}" for s in expected_suffixes]
    actual_names = [t.name for t in session.tracks]
    assert actual_names == expected_names

    layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
    if expected_layout is None:
        assert len(layout_issues) == 0
    else:
        assert len(layout_issues) == 1
        assert layout_issues[0].context["layout"] == expected_layout
        assert layout_issues[0].context["channels"] == count
        assert layout_issues[0].context["track"] == base_name


# ==============================================================================
# 2. Stress-testing PhysicalTrackNumber ordering and missing/None PTN
# ==============================================================================


def test_ptn_out_of_order(tmp_path):
    """Slots in AAF are defined in arbitrary order, but PTN specifies physical ordering."""
    aaf_file = tmp_path / "ptn_unordered.aaf"
    # Given slots in order: PTN 3, PTN 1, PTN 2
    specs = [("Stem", 3, 48000), ("Stem", 1, 48000), ("Stem", 2, 48000)]
    _create_aaf(aaf_file, specs)

    session = read_aaf(aaf_file, Report(strict=False))
    # Slot with PTN 1 gets ch1, PTN 2 gets ch2, PTN 3 gets ch3
    # Tracks in session IR appear in slot sequence order:
    # Slot 0 (PTN 3) -> Stem [ch3]
    # Slot 1 (PTN 1) -> Stem [ch1]
    # Slot 2 (PTN 2) -> Stem [ch2]
    assert session.tracks[0].name == "Stem [ch3]"
    assert session.tracks[1].name == "Stem [ch1]"
    assert session.tracks[2].name == "Stem [ch2]"


def test_ptn_missing_all(tmp_path):
    """When PTN is None on all slots, sort falls back to composition slot sequence order."""
    aaf_file = tmp_path / "ptn_none.aaf"
    specs = [("STEREO", None, 48000), ("STEREO", None, 48000)]
    _create_aaf(aaf_file, specs)

    session = read_aaf(aaf_file, Report(strict=False))
    assert session.tracks[0].name == "STEREO [L]"
    assert session.tracks[1].name == "STEREO [R]"


def test_ptn_mixed_presence(tmp_path):
    """When some slots have PTN and others do not, deterministic ordering is maintained."""
    aaf_file = tmp_path / "ptn_mixed.aaf"
    # Slot 0 has None (idx 0 fallback), Slot 1 has PTN 10, Slot 2 has PTN 2
    specs = [("MIXED", None, 48000), ("MIXED", 10, 48000), ("MIXED", 2, 48000)]
    _create_aaf(aaf_file, specs)

    session = read_aaf(aaf_file, Report(strict=False))
    # Sort keys: (0, slot0) -> key 0 (ch1)
    #            (1, slot1) -> key 10 (ch3)
    #            (2, slot2) -> key 2 (ch2)
    assert session.tracks[0].name == "MIXED [ch1]"
    assert session.tracks[1].name == "MIXED [ch3]"
    assert session.tracks[2].name == "MIXED [ch2]"


# ==============================================================================
# 3. Base track names with brackets, existing suffixes, special characters
# ==============================================================================


@pytest.mark.parametrize(
    "base_name",
    [
        "FX [OLD]",
        "Track [2024-08-20] (Draft)",
        "Dialogue_#1 & SFX @ 100%",
        "Ambience [Field_Rec]",
    ],
)
def test_base_names_with_brackets_and_symbols(tmp_path, base_name):
    aaf_file = tmp_path / "special_names.aaf"
    specs = [(base_name, 1, 48000), (base_name, 2, 48000)]
    _create_aaf(aaf_file, specs)

    session = read_aaf(aaf_file, Report(strict=False))
    assert session.tracks[0].name == f"{base_name} [L]"
    assert session.tracks[1].name == f"{base_name} [R]"


# ==============================================================================
# 4. Suffix Stripping & Round-Trip Symmetry
# ==============================================================================


@pytest.mark.parametrize(
    "input_name,expected_output",
    [
        # Standard channel suffixes
        ("STEREO [L]", "STEREO"),
        ("STEREO [R]", "STEREO"),
        ("5.1 [C]", "5.1"),
        ("5.1 [LFE]", "5.1"),
        ("5.1 [Ls]", "5.1"),
        ("5.1 [Rs]", "5.1"),
        ("5.1 [LS]", "5.1"),
        ("5.1 [RS]", "5.1"),
        ("AMB [W]", "AMB"),
        ("AMB [X]", "AMB"),
        ("AMB [Y]", "AMB"),
        ("AMB [Z]", "AMB"),
        ("Stem [ch1]", "Stem"),
        ("Stem [ch8]", "Stem"),
        ("Stem [ch128]", "Stem"),
        ("Stem [CH1]", "Stem"),
        # Nested brackets
        ("FX [OLD] [L]", "FX [OLD]"),
        ("Scene 1 [Draft] [C]", "Scene 1 [Draft]"),
        # Non-layout brackets should NOT be stripped
        ("Dialogue [Boom]", "Dialogue [Boom]"),
        ("SFX [Crash]", "SFX [Crash]"),
        ("Music [Intro]", "Music [Intro]"),
        ("Track [Final]", "Track [Final]"),
        ("Track [ch]", "Track [ch]"),
        ("Track [LFE_sub]", "Track [LFE_sub]"),
        ("NoBrackets", "NoBrackets"),
    ],
)
def test_strip_channel_suffix_exhaustive(input_name, expected_output):
    assert _strip_channel_suffix(input_name) == expected_output


def test_full_roundtrip_suffix_non_accumulation(tmp_path):
    """End-to-end: Session with multichannel tracks exported via write_aaf and re-read via read_aaf."""
    wav_path = tmp_path / "audio.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\0" * 4800)

    # 6 tracks of 5.1
    suffixes = ["L", "R", "C", "LFE", "Ls", "Rs"]
    tracks = []
    for i, s in enumerate(suffixes):
        clip = ClipIR(
            id=f"c_{i}",
            track_id=f"t_{i}",
            channel_index=0,
            event_index=0,
            name=f"clip_{s}",
            media_id=str(wav_path),
            timeline_start_samples=0,
            timeline_end_samples=4800,
            source_offset_samples=0,
            raw={},
        )
        t = TrackIR(
            id=f"t_{i}",
            source_index=i,
            name=f"Surround_5.1 [{s}]",
            kind="audio",
            channel_count=1,
            clips=[clip],
            plugins=[],
        )
        tracks.append(t)

    session_in = SessionIR(
        name="Surround_Test",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=tracks,
        media=[],
        markers=[],
        source_path=None,
        source_application=None,
    )

    exported_aaf = tmp_path / "exported_5_1.aaf"
    write_aaf(session_in, exported_aaf, Report(strict=False))

    session_out = read_aaf(exported_aaf, Report(strict=False))

    # All tracks must cleanly resolve to 'Surround_5.1 [L]', etc., without double suffix '[L] [L]'
    actual_names = [t.name for t in session_out.tracks]
    expected_names = [f"Surround_5.1 [{s}]" for s in suffixes]
    assert actual_names == expected_names


# ==============================================================================
# 5. Verify Indexing Robustness & Real Fixture
# ==============================================================================


def test_verify_indexing_collision_with_many_identical_names():
    """Verify _index handles 10 identically named tracks with identical clip timestamps."""
    tracks = []
    for t_idx in range(10):
        clips = [
            ClipIR(
                id=f"t{t_idx}_c{c_idx}",
                track_id=f"t{t_idx}",
                channel_index=0,
                event_index=c_idx,
                name=f"clip_{c_idx}.wav",
                media_id="m1",
                source_offset_samples=0,
                timeline_start_samples=c_idx * 1000,
                timeline_end_samples=(c_idx + 1) * 1000,
            )
            for c_idx in range(5)
        ]
        tracks.append(
            TrackIR(
                id=f"t{t_idx}",
                source_index=t_idx,
                name="DUPLICATE_NAME",
                kind="audio",
                channel_count=1,
                clips=clips,
            )
        )

    session = SessionIR(
        name="multi_collision",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=0,
        tracks=tracks,
    )

    indexed = _index(session)
    # 10 tracks * 5 clips = 50 distinct indexed items
    assert len(indexed) == 50
    for t_idx in range(10):
        for c_idx in range(5):
            assert (t_idx, c_idx) in indexed
            assert indexed[(t_idx, c_idx)].id == f"t{t_idx}_c{c_idx}"


def test_unnamed_slots_get_distinct_track_names(tmp_path):
    """Slots with empty string or None name get default Track 1, Track 2 etc."""
    aaf_file = tmp_path / "unnamed_slots.aaf"
    specs = [("", None, 48000), ("", None, 48000)]
    _create_aaf(aaf_file, specs)

    session = read_aaf(aaf_file, Report(strict=False))
    assert len(session.tracks) == 2
    assert session.tracks[0].name == "Track 1"
    assert session.tracks[1].name == "Track 2"


def test_real_protools_fixture_track_names_and_verify_coverage():
    """Verify on real fixture TESTE_AFF_PROTOOLS.aaf:
    1. Exactly 17 distinct tracks
    2. All 185 segments indexed by verify _index
    """
    pt_aaf = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report)

    # 17 distinct track names
    track_names = [t.name for t in session.tracks]
    assert len(track_names) == 17
    assert len(set(track_names)) == 17

    expected = [
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
    assert track_names == expected

    # Verify indexing coverage in keep mode (all 185 segments) and absorb mode (84 clips)
    session_keep = read_aaf(pt_aaf, report, fades="keep")
    indexed_keep = _index(session_keep)
    total_clips_keep = sum(len(t.clips) for t in session_keep.tracks)
    assert total_clips_keep == 185
    assert len(indexed_keep) == 185

    indexed_absorb = _index(session)
    total_clips_absorb = sum(len(t.clips) for t in session.tracks)
    # 90 = 84 clips de midia + 6 fades que a fonte nao cobre.
    # O Pro Tools rendeu esses seis fades mais longos do que o material que
    # sobra no arquivo depois do corte, entao absorve-los apontaria para alem
    # do fim do arquivo e o trecho tocaria silencio. Eles sao mantidos como
    # clip proprio (FADE_KEPT_SOURCE_TOO_SHORT). Em --fades keep o total e 185.
    assert total_clips_absorb == 90
    assert len(indexed_absorb) == 90
