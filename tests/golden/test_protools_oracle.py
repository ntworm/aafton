"""Golden Oracle Integration Tests for Pro Tools Dialect.

Verifies ableton-aaf conversion behavior against the independent Pro Tools
session text export oracle (teste_aaf_protools_txt.txt).
"""

from __future__ import annotations

from pathlib import Path
import re
import pytest

from aafton.aaf_read import read_aaf
from aafton.protools_text import ProToolsTextExport, parse_timecode_to_samples
from aafton.report import Report

_NOME_DE_FADE = re.compile(r"^Fade\s+\d+$", re.IGNORECASE)

FIXTURES_DIR = Path("tests/fixtures/aaf")
AAF_PATH = FIXTURES_DIR / "TESTE_AFF_PROTOOLS.aaf"
TXT_PATH = FIXTURES_DIR / "teste_aaf_protools_txt.txt"

# Mapping from Pro Tools session text track names to AAF track names and channel numbers
TRACK_MAPPING: dict[str, list[tuple[str, int]]] = {
    "REF (Stereo)": [("REF [L]", 1), ("REF [R]", 2)],
    "MONO": [("MONO", 1)],
    "STEREO (Stereo)": [("STEREO [L]", 1), ("STEREO [R]", 2)],
    "5.1 (5.1)": [
        ("5.1 [L]", 1),
        ("5.1 [C]", 2),
        ("5.1 [R]", 3),
        ("5.1 [Ls]", 4),
        ("5.1 [Rs]", 5),
        ("5.1 [LFE]", 6),
    ],
    "AMB 1ST (1st Order Ambisonics)": [
        ("AMB 1ST [W]", 1),
        ("AMB 1ST [X]", 2),
        ("AMB 1ST [Y]", 3),
        ("AMB 1ST [Z]", 4),
    ],
    "soundly (Stereo)": [("soundly [L]", 1), ("soundly [R]", 2)],
}

EXPECTED_17_TRACKS = [
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


@pytest.fixture(scope="module")
def protools_text_oracle() -> ProToolsTextExport:
    if not TXT_PATH.exists():
        pytest.skip(f"Fixture {TXT_PATH} not found")
    return ProToolsTextExport.parse_file(TXT_PATH)


@pytest.fixture(scope="module")
def protools_aaf_keep():
    if not AAF_PATH.exists():
        pytest.skip(f"Fixture {AAF_PATH} not found")
    report = Report()
    session = read_aaf(AAF_PATH, report, fades="keep")
    return session, report


@pytest.fixture(scope="module")
def protools_aaf_absorb():
    if not AAF_PATH.exists():
        pytest.skip(f"Fixture {AAF_PATH} not found")
    report = Report()
    session = read_aaf(AAF_PATH, report, fades="absorb")
    return session, report


class TestProToolsTextParser:
    """Tests for the Pro Tools Text Export parser itself."""

    def test_parser_extracts_session_metadata(self, protools_text_oracle: ProToolsTextExport):
        assert protools_text_oracle.session_name == "SPOT_ULHT_2025_TEATRO60s"
        assert protools_text_oracle.sample_rate == 48000
        assert protools_text_oracle.bit_depth == "24-bit"
        assert protools_text_oracle.start_timecode == "00:00:00:00.00"
        assert protools_text_oracle.timecode_format == "25 Frame"
        assert protools_text_oracle.timecode_rate.nominal_fps == 25
        assert protools_text_oracle.num_tracks == 51
        assert len(protools_text_oracle.tracks) == 51
        assert protools_text_oracle.num_clips == 1075
        assert protools_text_oracle.num_files == 158

    def test_parser_extracts_markers_with_utf8_accents(
        self, protools_text_oracle: ProToolsTextExport
    ):
        markers = protools_text_oracle.markers
        assert len(markers) == 8

        # Marker 1 (FFOA)
        assert markers[0].num == 1
        assert markers[0].name == "FFOA"
        assert markers[0].location == "01:00:00:00.00"
        assert markers[0].time_reference_samples == 172800000

        # Marker 2 (First Foley)
        m2 = markers[1]
        assert m2.time_reference_samples == 172833792

        # Marker 4 (Accented Portuguese characters)
        m4 = markers[3]
        assert m4.num == 4
        assert m4.location == "01:00:23:15.93"
        assert m4.time_reference_samples == 173934592
        assert "PÉ DESCALÇO EM MADEIRA" in m4.comments
        assert "WOOD CREAK" in m4.comments
        assert "PELE" in m4.comments

        # Marker 8 (LFOA)
        assert markers[7].num == 8
        assert markers[7].name == "LFOA"
        assert markers[7].location == "01:01:00:00.00"
        assert markers[7].time_reference_samples == 175680000

    def test_subframe_hundredths_timecode_parsing(self):
        # 1 frame at 25 fps / 48 kHz = 1920 samples
        # 1 hour = 3600 * 48000 = 172,800,000 samples
        from aafton.timecode import TimecodeRate

        assert (
            parse_timecode_to_samples("01:00:00:00.00", rate=TimecodeRate(25, 1), sample_rate=48000)
            == 172800000
        )
        assert (
            parse_timecode_to_samples("01:00:00:01.00", rate=TimecodeRate(25, 1), sample_rate=48000)
            == 172801920
        )

        # .100 subframe is 1.00 full frame
        samples_22_100 = parse_timecode_to_samples(
            "01:00:39:22.100", rate=TimecodeRate(25, 1), sample_rate=48000
        )
        samples_23_00 = parse_timecode_to_samples(
            "01:00:39:23.00", rate=TimecodeRate(25, 1), sample_rate=48000
        )
        assert samples_22_100 == samples_23_00


class TestProToolsGoldenOracle:
    """Golden integration tests comparing read_aaf against Pro Tools text export oracle."""

    def test_track_disambiguation_produces_17_distinct_names(self, protools_aaf_keep):
        session, _ = protools_aaf_keep
        track_names = [t.name for t in session.tracks]
        assert len(track_names) == 17
        assert len(set(track_names)) == 17  # Zero duplicate names
        assert track_names == EXPECTED_17_TRACKS

    def test_gain_recovery_produces_multiple_distinct_gain_values(self, protools_aaf_keep):
        session, _ = protools_aaf_keep
        gains = set()
        for track in session.tracks:
            for clip in track.clips:
                gains.add(round(clip.gain_linear, 4))

        # Pro Tools wraps clips in MonoAudioGain with VaryingValue
        # R1 ensures constant gains are recovered instead of defaulting to 1.0
        assert len(gains) > 1, f"Expected multiple distinct gains, found: {gains}"
        assert 1.0 in gains
        # Check presence of recovered fractional gain values (e.g. ~0.182, ~0.543)
        fractional_gains = [g for g in gains if g != 1.0]
        assert len(fractional_gains) >= 2

    def test_keep_mode_1to1_segment_correspondence(
        self, protools_aaf_keep, protools_text_oracle: ProToolsTextExport
    ):
        """In keep mode, every single one of the 185 timeline segments has a 1:1 match."""
        session, _ = protools_aaf_keep
        tracks_by_name = {t.name: t for t in session.tracks}

        total_segments_checked = 0
        for pt_track_name, chan_list in TRACK_MAPPING.items():
            pt_track = protools_text_oracle.tracks[pt_track_name]
            for aaf_track_name, chan_num in chan_list:
                aaf_tr = tracks_by_name[aaf_track_name]
                pt_events = pt_track.events(chan_num)
                assert len(aaf_tr.clips) == len(pt_events), (
                    f"Clip count mismatch for {aaf_track_name}: AAF has {len(aaf_tr.clips)}, "
                    f"text export has {len(pt_events)}"
                )

                for i, (clip, ev) in enumerate(zip(aaf_tr.clips, pt_events)):
                    # Check start and end positions within subframe rounding tolerance (<= 10 samples)
                    diff_start = abs(clip.timeline_start_samples - ev.start_samples)
                    diff_end = abs(clip.timeline_end_samples - ev.end_samples)
                    assert diff_start <= 10, (
                        f"{aaf_track_name}[{i}] ({clip.name}): start deviation {diff_start} samples "
                        f"(AAF={clip.timeline_start_samples}, Oracle={ev.start_samples})"
                    )
                    assert diff_end <= 10, (
                        f"{aaf_track_name}[{i}] ({clip.name}): end deviation {diff_end} samples "
                        f"(AAF={clip.timeline_end_samples}, Oracle={ev.end_samples})"
                    )
                    total_segments_checked += 1

        assert total_segments_checked == 185

    def test_absorb_mode_84_clips_correspondence(
        self, protools_aaf_absorb, protools_text_oracle: ProToolsTextExport
    ):
        """In absorb mode, 90 clips are produced with native fades.

            90 = 84 clips de midia + 6 fades que a fonte nao cobre.
        O Pro Tools rendeu esses seis fades mais longos do que o material que
        sobra no arquivo depois do corte, entao absorve-los apontaria para alem
        do fim do arquivo e o trecho tocaria silencio. Eles sao mantidos como
        clip proprio (FADE_KEPT_SOURCE_TOO_SHORT). Em --fades keep o total e 185.
        """
        session, _ = protools_aaf_absorb
        tracks_by_name = {t.name: t for t in session.tracks}

        total_clips_checked = 0
        total_fades_mantidos = 0
        clips_with_fade_in = 0
        clips_with_fade_out = 0

        for pt_track_name, chan_list in TRACK_MAPPING.items():
            pt_track = protools_text_oracle.tracks[pt_track_name]
            for aaf_track_name, chan_num in chan_list:
                aaf_tr = tracks_by_name[aaf_track_name]
                pt_non_fade_clips = pt_track.clips(chan_num)

                # O oraculo lista as regioes de midia da sessao. Um fade que a
                # fonte nao cobre e mantido como clip proprio, entao ele aparece
                # do nosso lado e nao do lado do texto. A correspondencia e
                # entre regioes de midia; os fades mantidos sao contados a
                # parte, para nao serem descartados em silencio.
                clips_de_fade = [
                    c for c in aaf_tr.clips if _NOME_DE_FADE.match((c.name or "").strip())
                ]
                clips_de_midia = [c for c in aaf_tr.clips if c not in clips_de_fade]
                total_fades_mantidos += len(clips_de_fade)

                assert len(clips_de_midia) == len(pt_non_fade_clips), (
                    f"Clip count mismatch for {aaf_track_name}: AAF has {len(clips_de_midia)} "
                    f"media clips ({len(clips_de_fade)} kept fades apart), "
                    f"text export has {len(pt_non_fade_clips)}"
                )

                for i, (clip, ev) in enumerate(zip(clips_de_midia, pt_non_fade_clips)):
                    # In absorb mode, clip start recedes if it absorbed a fade-in, and end grows if it absorbed a fade-out
                    orig_start = clip.timeline_start_samples + (
                        clip.fade_in.duration_samples if clip.fade_in else 0
                    )
                    orig_end = clip.timeline_end_samples - (
                        clip.fade_out.duration_samples if clip.fade_out else 0
                    )

                    diff_start = abs(orig_start - ev.start_samples)
                    diff_end = abs(orig_end - ev.end_samples)
                    assert diff_start <= 10, (
                        f"{aaf_track_name}[{i}] ({clip.name}): un-faded start deviation {diff_start} samples"
                    )
                    assert diff_end <= 10, (
                        f"{aaf_track_name}[{i}] ({clip.name}): un-faded end deviation {diff_end} samples"
                    )

                    if clip.fade_in and clip.fade_in.duration_samples > 0:
                        clips_with_fade_in += 1
                    if clip.fade_out and clip.fade_out.duration_samples > 0:
                        clips_with_fade_out += 1

                    # Verify no clip exceeds its own duration
                    total_fade = (clip.fade_in.duration_samples if clip.fade_in else 0) + (
                        clip.fade_out.duration_samples if clip.fade_out else 0
                    )
                    assert total_fade <= clip.duration_samples, (
                        f"{aaf_track_name}[{i}]: total fade {total_fade} exceeds clip duration {clip.duration_samples}"
                    )

                    total_clips_checked += 1

        assert total_clips_checked == 84, (
            "as regioes de midia precisam continuar sendo 84, que e o que o "
            "export em texto do Pro Tools lista"
        )
        assert total_fades_mantidos == 6, (
            "seis fades nao cabem no material que sobra no arquivo e sao mantidos "
            "como clip proprio; 84 + 6 = 90 clips no total"
        )
        assert clips_with_fade_in > 0
        assert clips_with_fade_out > 0

    def test_session_timecode_and_sample_rate_properties(self, protools_aaf_absorb):
        session, _ = protools_aaf_absorb
        assert session.sample_rate == 48000
        assert session.start_timecode_samples == 0  # Timecode slot starts at 00:00:00:00
        # Active audio content begins at 1 hour (01:00:00:00.00 = 172,800,000 samples)
        first_clip_start = min(c.timeline_start_samples for t in session.tracks for c in t.clips)
        assert first_clip_start == 172800000
        assert session.timecode_rate.fps == 25
        assert not session.timecode_rate.drop_frame
