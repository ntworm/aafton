"""Tests of the JSON shapes that the app page renders."""

from aafton.ir import ClipIR, FadeIR, SessionIR, TrackIR
from aafton.report import ConversionIssue
from aafton.studio import app_view
from aafton.timecode import TimecodeRate

RATE_25 = TimecodeRate(25, 1)

# The signs the page shows, spelled here so the test does not ask the module
# for the very thing it is checking.
MINUS = "\u2212"
INFINITY = "\u221e"
MIDDLE_DOT = "\u00b7"
EN_DASH = "\u2013"
NBSP = chr(0x00A0)
TIMES = "\u00d7"


def _session(rate: TimecodeRate = RATE_25, sample_rate: int = 48000, start: int = 0) -> SessionIR:
    return SessionIR(
        name="EP02",
        sample_rate=sample_rate,
        timecode_rate=rate,
        start_timecode_samples=start,
    )


def _clip(name: str, start: int, end: int, **extra) -> ClipIR:
    return ClipIR(
        id=f"clip-{name}",
        track_id="t0",
        channel_index=1,
        event_index=0,
        name=name,
        media_id=None,
        timeline_start_samples=start,
        timeline_end_samples=end,
        **extra,
    )


def _track(name: str = "DX 1", clips: list[ClipIR] | None = None, channels: int = 1) -> TrackIR:
    track = TrackIR(
        id="t0",
        source_index=0,
        name=name,
        kind="audio",
        channel_count=channels,
    )
    track.clips = list(clips or [])
    return track


def _issue(severity: str, code: str, message: str, track: str | None = None) -> ConversionIssue:
    context = {} if track is None else {"track": track}
    return ConversionIssue(severity=severity, code=code, message=message, context=context)


def test_tc_labels_the_absolute_position_of_the_session():
    session = _session()
    assert app_view.tc(0, session) == "00:00:00:00"
    assert app_view.tc(172800000, session) == "01:00:00:00"


def test_rate_label_of_every_rate_the_page_shows():
    assert app_view.rate_label(TimecodeRate(25, 1)) == "25 fps"
    assert app_view.rate_label(TimecodeRate(24, 1)) == "24 fps"
    assert app_view.rate_label(TimecodeRate(30, 1)) == "30 fps"
    assert app_view.rate_label(TimecodeRate(24000, 1001)) == "23.976 fps"
    assert app_view.rate_label(TimecodeRate(30000, 1001)) == "29.97 fps"
    assert app_view.rate_label(TimecodeRate(30000, 1001, True)) == "29.97 fps DF"


def test_hz_label_separates_the_thousands():
    assert app_view.hz_label(48000) == f"48{NBSP}000 Hz"
    assert app_view.hz_label(44100) == f"44{NBSP}100 Hz"
    assert app_view.hz_label(96000) == f"96{NBSP}000 Hz"


def test_timeline_covers_every_clip_of_the_session():
    session = _session()
    session.tracks.append(_track("DX 1", [_clip("A", 1000, 5000), _clip("B", 6000, 9000)]))
    session.tracks.append(_track("DX 2", [_clip("C", 2000, 3000)]))
    data = app_view.timeline(session)
    assert data["start_samples"] == 1000
    assert data["end_samples"] == 9000
    assert data["start_tc"] == "00:00:00:01"
    assert data["end_tc"] == "00:00:00:05"


def test_timeline_without_clips_starts_at_the_session_start():
    session = _session(start=172800000)
    data = app_view.timeline(session)
    assert data["start_samples"] == 172800000
    assert data["end_samples"] == 172800000
    assert data["start_tc"] == "01:00:00:00"
    assert data["end_tc"] == "01:00:00:00"


def test_channels_label_of_every_kind():
    assert app_view.channels_label(_track(channels=1)) == "mono"
    assert app_view.channels_label(_track(channels=2)) == "stereo"
    assert app_view.channels_label(_track(channels=6)) == "5.1"
    assert app_view.channels_label(_track(channels=4)) == "4 ch"
    video = _track()
    video.is_video = True
    assert app_view.channels_label(video) == "video"


def test_spans_are_sorted_and_untouched_under_the_limit():
    track = _track("DX 1", [_clip("C", 40, 50), _clip("A", 0, 10), _clip("B", 20, 30)])
    assert app_view.spans(track) == [[0, 10], [20, 30], [40, 50]]


def test_spans_merge_the_smallest_gap_first():
    clips = [_clip("A", 0, 10), _clip("B", 100, 110), _clip("C", 121, 131), _clip("D", 160, 170)]
    track = _track("DX 1", clips)
    assert app_view.spans(track, limit=3) == [[0, 10], [100, 131], [160, 170]]
    assert app_view.spans(track, limit=2) == [[0, 10], [100, 170]]


def test_spans_stay_inside_the_bounds_of_the_clips():
    track = _track("DX 1", [_clip("A", 0, 100), _clip("B", 50, 60), _clip("C", 200, 300)])
    assert app_view.spans(track, limit=1) == [[0, 300]]


def test_clip_rows_hold_the_timecode_the_gain_and_the_fades():
    session = _session()
    clip = _clip(
        "C12_T03",
        48000,
        96000,
        gain_linear=10 ** (-3 / 20),
        fade_in=FadeIR(kind="in", duration_samples=384),
        fade_out=FadeIR(kind="out", duration_samples=11520),
    )
    rows, more = app_view.clip_rows(_track("DX 1", [clip]), session)
    assert more == 0
    assert rows == [
        {
            "name": "C12_T03",
            "in": "00:00:01:00",
            "out": "00:00:02:00",
            "gain": f"{MINUS}3.0 dB",
            "fades": f"in 8 ms {MIDDLE_DOT} out 240 ms",
        }
    ]


def test_clip_rows_keep_the_limit_and_count_the_clips_left_out():
    session = _session()
    clips = [_clip(f"C{n}", n * 1920, n * 1920 + 1920) for n in range(5)]
    rows, more = app_view.clip_rows(_track("DX 1", clips), session, limit=2)
    assert [row["name"] for row in rows] == ["C0", "C1"]
    assert more == 3


def test_clip_rows_keep_three_hundred_clips_and_count_the_rest():
    session = _session()
    clips = [_clip(f"C{n}", n * 1920, n * 1920 + 1920) for n in range(350)]
    rows, more = app_view.clip_rows(_track("DX 1", clips), session)
    assert len(rows) == 300
    assert rows[0]["name"] == "C0"
    assert rows[-1]["name"] == "C299"
    assert more == 50


def test_notes_group_by_code_with_the_errors_first():
    issues = [
        _issue("error", "CODE_A", "first error"),
        _issue("warning", "CODE_B", "warn one"),
        _issue("error", "CODE_A", "second error"),
        _issue("info", "CODE_C", "info one"),
        _issue("info", "CODE_C", "info two"),
        _issue("warning", "CODE_D", "warn two"),
    ]
    data = app_view.notes(issues)
    assert data["main"] == [
        {"sev": "error", "code": "CODE_A", "count": 2, "text": "first error"},
        {"sev": "warning", "code": "CODE_B", "count": 1, "text": "warn one"},
        {"sev": "warning", "code": "CODE_D", "count": 1, "text": "warn two"},
    ]
    assert data["info"] == [{"sev": "info", "code": "CODE_C", "count": 2, "text": "info one"}]
    assert data["info_total"] == 2


def test_notes_keep_the_limit_of_the_main_groups():
    issues = [_issue("error", f"CODE_{n}", f"error {n}") for n in range(50)]
    assert len(app_view.notes(issues)["main"]) == 40
    assert len(app_view.notes(issues, limit=10)["main"]) == 10


def test_issue_state_prefers_the_error():
    one = [_issue("error", "E", "boom")]
    assert app_view.issue_state(one) == {"st": "err", "label": "1 error"}
    mixed = [_issue("error", "E", "boom"), _issue("warning", "W", "hmm")]
    assert app_view.issue_state(mixed) == {"st": "err", "label": "1 error"}
    two = [_issue("error", "E", "boom"), _issue("error", "E2", "boom")]
    assert app_view.issue_state(two) == {"st": "err", "label": "2 errors"}


def test_issue_state_falls_back_to_the_warning_and_to_ok():
    one = [_issue("warning", "W", "hmm")]
    assert app_view.issue_state(one) == {"st": "warn", "label": "1 note"}
    two = [_issue("warning", "W", "hmm"), _issue("warning", "W2", "hmm")]
    assert app_view.issue_state(two) == {"st": "warn", "label": "2 notes"}
    assert app_view.issue_state([_issue("info", "I", "fyi")]) == {"st": "ok", "label": "ok"}
    assert app_view.issue_state([]) == {"st": "ok", "label": "ok"}


def test_reasons_write_each_message_once_with_the_count_of_the_repeated_ones():
    issues = [
        _issue("error", "E1", "the same message"),
        _issue("warning", "E2", "the same message"),
        _issue("error", "E3", "another message"),
    ]
    assert app_view.reasons(issues) == [f"the same message ({TIMES}2)", "another message"]


def test_reasons_keep_the_first_messages_and_say_how_many_were_left_out():
    issues = [_issue("error", f"E{n}", f"message {n}") for n in range(8)]
    assert app_view.reasons(issues) == [f"message {n}" for n in range(6)] + ["+ 2 more"]
    assert app_view.reasons(issues, limit=2) == ["message 0", "message 1", "+ 6 more"]
    assert app_view.reasons(issues, limit=0) == ["+ 8 more"]


def test_plural_counts_the_noun():
    assert app_view.plural(1, "note", "notes") == "1 note"
    assert app_view.plural(3, "note", "notes") == "3 notes"
    assert app_view.plural(0, "error", "errors") == "0 errors"


def test_fact_holds_the_key_the_value_the_sub_and_the_state():
    assert app_view.fact("Program", "ProTools") == {
        "k": "Program",
        "v": "ProTools",
        "sub": None,
        "st": None,
    }
    assert app_view.fact("Media", "0 of 1", "1 missing", "err") == {
        "k": "Media",
        "v": "0 of 1",
        "sub": "1 missing",
        "st": "err",
    }


def test_issues_by_track_groups_by_the_track_of_the_context():
    issues = [
        _issue("error", "E", "boom", "DX 1"),
        _issue("warning", "W", "hmm", "DX 2"),
        _issue("info", "I", "fyi", "DX 1"),
        _issue("info", "I2", "fyi"),
    ]
    grouped = app_view.issues_by_track(issues)
    assert list(grouped) == ["DX 1", "DX 2"]
    assert [issue.code for issue in grouped["DX 1"]] == ["E", "I"]
    assert len(grouped["DX 2"]) == 1


def test_issues_by_track_keeps_the_issues_of_a_track_without_a_name():
    issues = [
        _issue("warning", "W", "hmm", ""),
        _issue("info", "I2", "fyi"),
    ]
    grouped = app_view.issues_by_track(issues)
    assert list(grouped) == [""]
    assert [issue.code for issue in grouped[""]] == ["W"]


def test_track_row_holds_every_field_of_the_sheet():
    session = _session()
    track = _track("DX 1", [_clip("A", 0, 48000)], channels=2)
    state = {"st": "ok", "label": "ok"}
    row = app_view.track_row(3, track, session, state, ["because"])
    assert row["index"] == 3
    assert row["audio_index"] is None
    assert row["name"] == "DX 1"
    assert row["kind"] == "stereo"
    assert row["clips"] == 1
    assert row["spans"] == [[0, 48000]]
    assert row["pick"] is None
    assert row["state"] == state
    assert row["reasons"] == ["because"]
    assert row["clip_rows"][0]["name"] == "A"
    assert row["clips_more"] == 0


def test_track_row_takes_the_kind_the_pick_and_the_audio_index():
    session = _session()
    track = _track("MIDI 1", [], channels=2)
    state = {"st": "off", "label": "empty"}
    row = app_view.track_row(
        0,
        track,
        session,
        state,
        [],
        kind="MIDI",
        pick=False,
        audio_index=2,
    )
    assert row["kind"] == "MIDI"
    assert row["pick"] is False
    assert row["audio_index"] == 2


def test_gain_db_writes_one_decimal_with_the_sign():
    assert app_view.gain_db(1.0) == "0.0 dB"
    assert app_view.gain_db(10 ** (3 / 20)) == "+3.0 dB"
    assert app_view.gain_db(10 ** (-3 / 20)) == f"{MINUS}3.0 dB"
    assert app_view.gain_db(0.0) == f"{MINUS}{INFINITY} dB"
    assert app_view.gain_db(-1.0) == f"{MINUS}{INFINITY} dB"


def test_fades_label_of_a_clip():
    both = _clip(
        "A",
        0,
        100,
        fade_in=FadeIR(kind="in", duration_samples=11520),
        fade_out=FadeIR(kind="out", duration_samples=38400),
    )
    assert app_view.fades_label(both, 48000) == f"in 240 ms {MIDDLE_DOT} out 800 ms"
    only_in = _clip("B", 0, 100, fade_in=FadeIR(kind="in", duration_samples=384))
    assert app_view.fades_label(only_in, 48000) == "in 8 ms"
    only_out = _clip("C", 0, 100, fade_out=FadeIR(kind="out", duration_samples=38400))
    assert app_view.fades_label(only_out, 48000) == "out 800 ms"
    assert app_view.fades_label(_clip("D", 0, 100), 48000) == EN_DASH


def test_fades_label_ignores_a_fade_of_no_samples():
    clip = _clip("A", 0, 100, fade_in=FadeIR(kind="in", duration_samples=0))
    assert app_view.fades_label(clip, 48000) == EN_DASH
