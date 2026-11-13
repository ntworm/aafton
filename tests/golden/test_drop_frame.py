from aafton.timecode import samples_to_label, TimecodeRate
from aafton.cli import _resolve_origin
from aafton.ir import SessionIR, TrackIR, ClipIR, MediaAsset, MarkerIR
from aafton.report import Report
from aafton.als_write import write_session, WriteOptions


def test_samples_to_label_drop_vs_nondrop():
    rate_df = TimecodeRate(30000, 1001, True)
    assert samples_to_label(172972800, rate_df, 48000) == "01:00:03;18"
    assert samples_to_label(203214211, rate_df, 48000) == "01:10:33;18"

    rate_ndf = TimecodeRate(30000, 1001, False)
    assert samples_to_label(172972800, rate_ndf, 48000) == "01:00:00:00"
    assert samples_to_label(203214211, rate_ndf, 48000) == "01:10:29:12"

    assert ";" in samples_to_label(172972800, rate_df, 48000)
    assert ":" in samples_to_label(172972800, rate_ndf, 48000)

    rate_25 = TimecodeRate(25, 1, False)
    assert samples_to_label(172800000, rate_25, 48000) == "01:00:00:00"
    assert samples_to_label(172800000 + 1920, rate_25, 48000) == "01:00:00:01"


def test_anchoring_marker_before_clip(tmp_path):
    session = SessionIR(
        name="Test",
        start_timecode_samples=0,
        sample_rate=48000,
        timecode_rate=TimecodeRate(30000, 1001, False),
    )
    track = TrackIR(id="t1", source_index=1, name="Audio", kind="audio", channel_count=1)
    MediaAsset(id="a1", filename="a.wav", channels=1, sample_rate=48000)
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip",
        media_id="a1",
        timeline_start_samples=184214656,
        timeline_end_samples=184214656 + 48000,
        source_offset_samples=0,
    )
    track.clips.append(clip)
    session.tracks.append(track)
    session.markers.append(MarkerIR(id="m1", name="FFOA", position_samples=172972800, comment=""))

    report = Report()
    origin = _resolve_origin(None, session, report)
    assert origin == 172492800

    report = Report(strict=False)
    output = tmp_path / "drop_test.als"
    write_session(
        session,
        "templates/live12.als",
        output,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=origin),
        report,
    )
    assert not any(err.code == "MARKER_BEFORE_ORIGIN" for err in report.issues)

    origin_explicit = 180000000
    report = Report(strict=False)
    write_session(
        session,
        "templates/live12.als",
        output,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=origin_explicit),
        report,
    )

    errors = [err.code for err in report.issues]
    assert "MARKER_BEFORE_ORIGIN" in errors
