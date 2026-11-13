"""Adversarial stress-testing for verify indexing and multichannel naming."""

import pytest
from pathlib import Path

from aafton.aaf_read import read_aaf, _resolve_track_names
from aafton.aaf_write import _strip_channel_suffix, write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import _index, verify_conversion
from tests.support.paths import fixture_aaf


def _make_dummy_clip(
    track_id: str, event_idx: int, start: int, duration: int = 1000, name: str = "clip.wav"
) -> ClipIR:
    return ClipIR(
        id=f"{track_id}_c{event_idx}",
        track_id=track_id,
        channel_index=0,
        event_index=event_idx,
        name=name,
        media_id="m1",
        timeline_start_samples=start,
        timeline_end_samples=start + duration,
        source_offset_samples=0,
        raw={},
    )


class TestVerifyIndexingAdversarial:
    """Adversarial stress tests for _index in src/aafton/verify.py."""

    def test_zero_collision_with_identical_track_names_and_positions(self):
        """Stress test: 20 tracks all named '5.1' with identical clip positions.
        _index must return exactly 20 * 5 = 100 clips with 0 collisions.
        """
        tracks = []
        for t_idx in range(20):
            clips = [
                _make_dummy_clip(
                    f"t{t_idx}", c_idx, start=1000 * c_idx, duration=500, name=f"clip_{c_idx}.wav"
                )
                for c_idx in range(5)
            ]
            tracks.append(
                TrackIR(
                    id=f"t{t_idx}",
                    source_index=t_idx,
                    name="5.1",
                    kind="audio",
                    channel_count=1,
                    clips=clips,
                    plugins=[],
                )
            )

        session = SessionIR(
            name="identical_tracks",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=tracks,
        )

        idx = _index(session)
        total_clips = sum(len(t.clips) for t in tracks)
        assert len(idx) == total_clips == 100

        # Assert every single clip is present and uniquely keyed by (track_idx, clip_pos_idx)
        for t_idx in range(20):
            for c_idx in range(5):
                key = (t_idx, c_idx)
                assert key in idx
                assert idx[key].id == f"t{t_idx}_c{c_idx}"

    def test_overlapping_clips_on_same_track_indexed_by_start_order(self):
        """Clips that overlap in time on the same track should be sorted and indexed deterministically."""
        clips = [
            _make_dummy_clip("t0", 0, start=5000, duration=1000, name="late.wav"),
            _make_dummy_clip("t0", 1, start=1000, duration=6000, name="long_early.wav"),
            _make_dummy_clip("t0", 2, start=2000, duration=1000, name="mid.wav"),
        ]
        track = TrackIR(
            id="t0",
            source_index=0,
            name="VO",
            kind="audio",
            channel_count=1,
            clips=clips,
            plugins=[],
        )
        session = SessionIR(
            name="overlap",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[track],
        )

        idx = _index(session)
        assert len(idx) == 3
        assert idx[(0, 0)].name == "long_early.wav"
        assert idx[(0, 1)].name == "mid.wav"
        assert idx[(0, 2)].name == "late.wav"

    def test_clips_with_identical_start_time_on_same_track(self):
        """Clips with identical start times on the same track should both be preserved (0 collision)."""
        clips = [
            _make_dummy_clip("t0", 0, start=1000, duration=500, name="a.wav"),
            _make_dummy_clip("t0", 1, start=1000, duration=1500, name="b.wav"),
            _make_dummy_clip("t0", 2, start=1000, duration=2000, name="c.wav"),
        ]
        track = TrackIR(
            id="t0",
            source_index=0,
            name="SFX",
            kind="audio",
            channel_count=1,
            clips=clips,
            plugins=[],
        )
        session = SessionIR(
            name="coincident",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[track],
        )

        idx = _index(session)
        assert len(idx) == 3
        assert (0, 0) in idx
        assert (0, 1) in idx
        assert (0, 2) in idx
        indexed_names = {idx[(0, 0)].name, idx[(0, 1)].name, idx[(0, 2)].name}
        assert indexed_names == {"a.wav", "b.wav", "c.wav"}

    def test_interleaved_empty_tracks(self):
        """Empty tracks between populated tracks do not shift track indices incorrectly."""
        t0 = TrackIR(
            id="t0",
            source_index=0,
            name="T0",
            kind="audio",
            channel_count=1,
            clips=[_make_dummy_clip("t0", 0, 100)],
            plugins=[],
        )
        t1_empty = TrackIR(
            id="t1",
            source_index=1,
            name="T1_EMPTY",
            kind="audio",
            channel_count=1,
            clips=[],
            plugins=[],
        )
        t2 = TrackIR(
            id="t2",
            source_index=2,
            name="T2",
            kind="audio",
            channel_count=1,
            clips=[
                _make_dummy_clip("t2", 0, 200),
                _make_dummy_clip("t2", 1, 400),
            ],
            plugins=[],
        )
        t3_empty = TrackIR(
            id="t3",
            source_index=3,
            name="T3_EMPTY",
            kind="audio",
            channel_count=1,
            clips=[],
            plugins=[],
        )

        session = SessionIR(
            name="interleaved",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[t0, t1_empty, t2, t3_empty],
        )

        idx = _index(session)
        assert len(idx) == 3
        assert (0, 0) in idx
        assert (1, 0) not in idx
        assert (2, 0) in idx
        assert (2, 1) in idx
        assert (3, 0) not in idx
        assert idx[(2, 0)].timeline_start_samples == 200
        assert idx[(2, 1)].timeline_start_samples == 400

    def test_100_percent_clip_coverage_on_protools_fixture(self):
        """Reading TESTE_AFF_PROTOOLS.aaf must index 185 clips in keep mode and 90 in absorb mode without loss.

            90 = 84 clips de midia + 6 fades que a fonte nao cobre.
        O Pro Tools rendeu esses seis fades mais longos do que o material que
        sobra no arquivo depois do corte, entao absorve-los apontaria para alem
        do fim do arquivo e o trecho tocaria silencio. Eles sao mantidos como
        clip proprio (FADE_KEPT_SOURCE_TOO_SHORT). Em --fades keep o total e 185.
        """
        pt_aaf = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")

        report = Report(strict=False)
        session_keep = read_aaf(pt_aaf, report, fades="keep")
        idx_keep = _index(session_keep)
        total_clips_keep = sum(len(t.clips) for t in session_keep.tracks)
        assert total_clips_keep == 185
        assert len(idx_keep) == 185  # 100% indexed in keep mode

        session_absorb = read_aaf(pt_aaf, report, fades="absorb")
        idx_absorb = _index(session_absorb)
        total_clips_absorb = sum(len(t.clips) for t in session_absorb.tracks)
        assert total_clips_absorb == 90
        assert len(idx_absorb) == 90  # 100% indexed in absorb mode


class TestVerifyDiffReporting:
    """Stress tests for verify_conversion comparison logic and diff reporting."""

    def test_mismatched_track_counts_and_missing_clips_reporting(self, tmp_path):
        """When ALS is missing entire tracks or has missing clips, verify correctly reports per-track index using legacy fallback."""
        # Create synthetic AAF
        aaf_path = tmp_path / "origin.aaf"
        wav_path = tmp_path / "dummy.wav"
        import wave

        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(48000)
            w.writeframes(b"\x00" * 48000)

        t0_clips = [_make_dummy_clip("t0", 0, 1000, 1000, "c0.wav")]
        t1_clips = [_make_dummy_clip("t1", 0, 2000, 1000, "c1.wav")]
        t2_clips = [_make_dummy_clip("t2", 0, 3000, 1000, "c2.wav")]

        session_orig = SessionIR(
            name="Orig",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[
                TrackIR(
                    id="t0",
                    source_index=0,
                    name="DX [L]",
                    kind="audio",
                    channel_count=1,
                    clips=t0_clips,
                    plugins=[],
                ),
                TrackIR(
                    id="t1",
                    source_index=1,
                    name="DX [R]",
                    kind="audio",
                    channel_count=1,
                    clips=t1_clips,
                    plugins=[],
                ),
                TrackIR(
                    id="t2",
                    source_index=2,
                    name="FX",
                    kind="audio",
                    channel_count=1,
                    clips=t2_clips,
                    plugins=[],
                ),
            ],
            media=[
                MediaAsset(
                    id="m1",
                    filename=str(wav_path),
                    channels=1,
                    sample_rate=48000,
                    duration_samples=48000,
                    status="resolved",
                    resolved_path=str(wav_path),
                    raw={},
                )
            ],
        )
        write_aaf(session_orig, aaf_path)

        from aafton.als_write import write_session, WriteOptions
        from pathlib import Path

        template_path = Path("templates/live12.als")
        if not template_path.exists():
            pytest.skip("template live12.als missing")

        als_path = tmp_path / "dest.als"
        session_dest = SessionIR(
            name="Dest",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[
                TrackIR(
                    id="t0",
                    source_index=0,
                    name="DX [L]",
                    kind="audio",
                    channel_count=1,
                    clips=t0_clips,
                    plugins=[],
                ),
                TrackIR(
                    id="t1",
                    source_index=1,
                    name="DX [R]",
                    kind="audio",
                    channel_count=1,
                    clips=[],
                    plugins=[],
                ),  # missing clip
            ],
            media=session_orig.media,
        )
        write_session(
            session_dest,
            template_path,
            als_path,
            WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0, groups="none"),
            Report(strict=False),
        )
        from aafton.sidecar import sidecar_path_for
        import json

        sidecar_path = sidecar_path_for(als_path)
        with open(sidecar_path, "r") as f:
            sidecar = json.load(f)
        if "audio_source_indices" in sidecar:
            del sidecar["audio_source_indices"]
        with open(sidecar_path, "w") as f:
            json.dump(sidecar, f)

        res = verify_conversion(aaf_path, als_path)
        assert res.ok is False
        assert len(res.rows) == 3

        # Track 0: ok
        assert res.rows[0].track == "DX [L]"
        assert res.rows[0].status == "ok"
        assert res.rows[0].delta_samples == 0

        # Track 1: missing in ALS
        assert res.rows[1].track == "DX [R]"
        assert res.rows[1].status == "missing in the ALS"

        # Track 2: missing in ALS
        assert res.rows[2].track == "FX"
        assert res.rows[2].status == "missing in the ALS"

        # Table formatting works without error
        table_output = res.to_table()
        assert "missing in the ALS" in table_output
        assert "DX [L]" in table_output
        assert "DX [R]" in table_output
        assert "FX" in table_output

    def test_verify_empty_sessions(self, tmp_path):
        """Verify em sessao vazia reprova porque zero eventos de audio foram comparados."""
        aaf_path = tmp_path / "empty.aaf"
        session_empty = SessionIR(
            name="Empty",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1, False),
            start_timecode_samples=0,
            tracks=[],
            media=[],
        )
        write_aaf(session_empty, aaf_path)

        from aafton.als_write import write_session, WriteOptions

        als_path = tmp_path / "empty.als"
        template_path = Path("templates/live12.als")
        if not template_path.exists():
            pytest.skip("template live12.als missing")

        write_session(
            session_empty,
            template_path,
            als_path,
            WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0),
            Report(strict=False),
        )

        res = verify_conversion(aaf_path, als_path)
        assert res.ok is False
        assert res.audio_expected == 0
        assert res.audio_compared == 0
        assert len(res.rows) == 0
        assert "Regime:" in res.to_table()
        assert "failed" in res.to_markdown()


class TestSuffixStrippingAdversarial:
    """Stress-test channel suffix stripping on various edge cases."""

    @pytest.mark.parametrize(
        "input_name, expected",
        [
            ("5.1 [L]", "5.1"),
            ("5.1 [R]", "5.1"),
            ("5.1 [C]", "5.1"),
            ("5.1 [LFE]", "5.1"),
            ("5.1 [Ls]", "5.1"),
            ("5.1 [Rs]", "5.1"),
            ("AMB [W]", "AMB"),
            ("AMB [X]", "AMB"),
            ("AMB [Y]", "AMB"),
            ("AMB [Z]", "AMB"),
            ("Track [ch1]", "Track"),
            ("Track [ch2]", "Track"),
            ("Track [ch128]", "Track"),
            ("Track [CH5]", "Track"),
            ("Stereo Track [L]", "Stereo Track"),
            ("Stereo Track [R]", "Stereo Track"),
            # Case insensitivity
            ("Track [c]", "Track"),
            ("Track [lfe]", "Track"),
            ("Track [ls]", "Track"),
            ("Track [rs]", "Track"),
            ("Track [w]", "Track"),
            # Spaces before bracket
            ("Track  [R]", "Track"),
            # Non-layout brackets should NOT be stripped
            ("Final Mix [Master]", "Final Mix [Master]"),
            ("Guitar [Lead]", "Guitar [Lead]"),
            ("VO [Take 1]", "VO [Take 1]"),
            ("Effects [Explosion]", "Effects [Explosion]"),
            ("No Brackets", "No Brackets"),
            ("", ""),
            ("[L]", ""),
            ("5.1 [C] Extra", "5.1 [C] Extra"),
        ],
    )
    def test_strip_channel_suffix_cases(self, input_name, expected):
        assert _strip_channel_suffix(input_name) == expected


class TestResolveTrackNamesAdversarial:
    """Stress test _resolve_track_names under strange/hostile input combinations."""

    def test_empty_audio_slots(self):
        report = Report(strict=False)
        resolved = _resolve_track_names([], report)
        assert resolved == {}
        assert len(report.issues) == 0

    def test_slots_with_none_or_empty_names(self):
        class DummySlot:
            def __init__(self, name):
                self.name = name

        slots = [(0, DummySlot(None)), (1, DummySlot("")), (2, DummySlot("VALID"))]
        report = Report(strict=False)
        resolved = _resolve_track_names(slots, report)
        assert resolved == {2: "VALID"}

    def test_large_number_of_channels(self):
        class DummySlot:
            def __init__(self, name, ptn):
                self.name = name
                self.physical_track_number = ptn

        slots = [(i, DummySlot("DOLBY_ATMOS_BED", i + 1)) for i in range(128)]
        report = Report(strict=False)
        resolved = _resolve_track_names(slots, report)
        assert len(resolved) == 128
        assert resolved[0] == "DOLBY_ATMOS_BED [ch1]"
        assert resolved[127] == "DOLBY_ATMOS_BED [ch128]"
        layout_issues = [i for i in report.issues if i.code == "CHANNEL_LAYOUT_GUESSED"]
        assert len(layout_issues) == 1
        assert layout_issues[0].context["layout"] == "generic 128-channel"

    def test_missing_ptn_falls_back_to_slot_index(self):
        class DummySlot:
            def __init__(self, name):
                self.name = name

        slots = [(10, DummySlot("STEREO")), (5, DummySlot("STEREO"))]
        report = Report(strict=False)
        resolved = _resolve_track_names(slots, report)
        # orig_idx 5 came first in order of sorting by idx
        assert resolved[5] == "STEREO [L]"
        assert resolved[10] == "STEREO [R]"
