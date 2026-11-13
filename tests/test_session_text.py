from aafton.ir import SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.session_text import process_session_text, find_session_text


def create_mock_session(sample_rate=48000, fps=25) -> SessionIR:
    return SessionIR(
        name="test_session",
        sample_rate=sample_rate,
        timecode_rate=TimecodeRate(num=fps, den=1, drop_frame=False),
        start_timecode_samples=0,
        tracks=[],
        media=[],
        markers=[],
    )


def test_find_session_text_multiple_candidates(tmp_path):
    (tmp_path / "1.txt").write_text("SESSION NAME: A\n", encoding="utf-8")
    (tmp_path / "2.txt").write_text("SESSION NAME: B\n", encoding="utf-8")

    aaf_path = tmp_path / "test.aaf"
    aaf_path.touch()

    candidates = find_session_text(aaf_path, None)
    assert isinstance(candidates, list)
    assert len(candidates) == 2

    report = Report(strict=False)
    session = create_mock_session()
    process_session_text(session, aaf_path, None, report)

    issues = [i for i in report.issues if i.code == "SESSION_TEXT_MULTIPLE_CANDIDATES"]
    assert len(issues) == 1


def test_find_session_text_no_candidates(tmp_path):
    aaf_path = tmp_path / "test.aaf"
    aaf_path.touch()

    report = Report(strict=False)
    session = create_mock_session()
    process_session_text(session, aaf_path, None, report)

    # Ausencia de texto não é problema
    assert not report.incomplete
    assert len(report.issues) == 0


def test_process_session_text_sample_rate_mismatch(tmp_path):
    txt_content = """SESSION NAME: Test
SAMPLE RATE: 44100.000000
TIMECODE FORMAT: 25 Frame
BIT DEPTH: 24-bit
SESSION START TIMECODE: 00:00:00:00.00
"""
    txt_path = tmp_path / "test.txt"
    txt_path.write_text(txt_content, encoding="utf-8")

    aaf_path = tmp_path / "test.aaf"

    report = Report(strict=False)
    session = create_mock_session(sample_rate=48000)
    process_session_text(session, aaf_path, str(txt_path), report)

    issues = [i for i in report.issues if i.code == "SESSION_TEXT_MISMATCH"]
    assert len(issues) == 1
    assert "44100" in issues[0].message
    assert "48000" in issues[0].message
    assert len(session.markers) == 0


def test_process_session_text_extracts_markers(tmp_path):
    txt_content = """SESSION NAME: Test
SAMPLE RATE: 48000.000000
TIMECODE FORMAT: 25 Frame
BIT DEPTH: 24-bit
SESSION START TIMECODE: 00:00:00:00.00

M A R K E R S  L I S T I N G
#   	LOCATION     	TIME REFERENCE    	UNITS    	NAME                             	TRACK NAME                       	TRACK TYPE   	COMMENTS
1   	01:00:00:00.00	172800000         	Samples  	FFOA                             	TC                               	Ruler                            	
2   	01:00:00:17.60	172833792         	Samples  	FOLEY                            	NOTES                            	Ruler                            	TECIDO A BATER
    	             	                  	           	                                 	                                 	             	FLAG?
    	             	                  	           	                                 	                                 	             	SACUDIR DE PANOS
3   	01:00:23:15.93	173934592         	Samples  	TC 01:00:23:15 @ 48000           	NOTES                            	Ruler                            	PÉ DESCALÇO EM MADEIRA
"""
    txt_path = tmp_path / "test.txt"
    txt_path.write_text(txt_content, encoding="utf-8")

    aaf_path = tmp_path / "test.aaf"

    report = Report(strict=False)
    session = create_mock_session(sample_rate=48000)
    process_session_text(session, aaf_path, str(txt_path), report)

    assert len(session.markers) == 3

    # 1. First marker has no comments
    assert session.markers[0].name == "FFOA"
    assert session.markers[0].position_samples == 172800000

    # 2. Multiline comment truncation
    assert session.markers[1].name == "FOLEY — TECIDO A BATER"
    assert session.markers[1].position_samples == 172833792
    assert session.markers[1].comment == "TECIDO A BATER\nFLAG?\nSACUDIR DE PANOS"

    trunc_issues = [i for i in report.issues if i.code == "MARKER_COMMENT_IN_ANNOTATION"]
    assert len(trunc_issues) == 1
    assert trunc_issues[0].context["count"] == 2

    # 3. Anchor collision rename
    assert session.markers[2].name == "TC 01:00:23:15 @ 48000 — PÉ DESCALÇO EM MADEIRA (imported)"
    renamed_issues = [i for i in report.issues if i.code == "MARKER_RENAMED_TO_AVOID_ANCHOR"]
    assert len(renamed_issues) == 1


def test_process_session_text_track_state(tmp_path):
    txt_content = """SESSION NAME: Test
SAMPLE RATE: 48000.000000
TIMECODE FORMAT: 25 Frame
BIT DEPTH: 24-bit
SESSION START TIMECODE: 00:00:00:00.00

T R A C K  L I S T I N G
TRACK NAME:	REF (Stereo)
COMMENTS:	
USER DELAY:	0 Samples
STATE: Muted 
PLUG-INS: 

TRACK NAME:	MAINVOX
COMMENTS:	
USER DELAY:	0 Samples
STATE: Hidden Inactive Muted 
PLUG-INS: 

TRACK NAME:	soundly (Stereo)
COMMENTS:	
USER DELAY:	0 Samples
STATE: SoloSafe 
PLUG-INS: 
"""
    txt_path = tmp_path / "test.txt"
    txt_path.write_text(txt_content, encoding="utf-8")
    aaf_path = tmp_path / "test.aaf"

    report = Report(strict=False)
    session = create_mock_session(sample_rate=48000)

    t_ref = TrackIR(id="t1", source_index=1, name="REF [L]", kind="audio", channel_count=1)
    t_main = TrackIR(id="t2", source_index=2, name="MAINVOX", kind="audio", channel_count=1)
    t_sound = TrackIR(id="t3", source_index=3, name="soundly", kind="audio", channel_count=1)
    t_extra = TrackIR(id="t4", source_index=4, name="EXTRA", kind="audio", channel_count=1)
    session.tracks.extend([t_ref, t_main, t_sound, t_extra])

    process_session_text(session, aaf_path, str(txt_path), report)

    assert t_ref.muted is True
    assert t_main.muted is True
    assert t_sound.muted is False
    assert t_extra.muted is False

    inact_issues = [i for i in report.issues if i.code == "TRACK_INACTIVE_AS_MUTED"]
    assert len(inact_issues) == 1
    assert inact_issues[0].context["track"] == "MAINVOX"
