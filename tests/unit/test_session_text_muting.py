import pytest
from pathlib import Path
from aafton.session_text import process_session_text
from aafton.report import Report
from aafton.ir import SessionIR, TrackIR
from aafton.timecode import TimecodeRate


def test_session_text_track_muting():
    # 1. SPOT_AAF_TESTE (0 muted)
    txt_spot = Path("tests/fixtures/aaf/SPOT_AAF_TESTE.txt")
    if not txt_spot.exists():
        pytest.skip("Fixture SPOT_AAF_TESTE.txt missing")

    session_spot = SessionIR(
        name="dummy", sample_rate=48000, start_timecode_samples=0, timecode_rate=TimecodeRate(25, 1)
    )
    # create some tracks that exist in SPOT_AAF_TESTE
    t1 = TrackIR(id="t1", source_index=1, name="Audio 1", kind="audio", channel_count=1)
    t2 = TrackIR(id="t2", source_index=2, name="Audio 3", kind="audio", channel_count=1)
    session_spot.tracks.extend([t1, t2])

    rep_spot = Report(strict=False)
    process_session_text(session_spot, "dummy.aaf", str(txt_spot), rep_spot)
    assert not t1.muted
    assert not t2.muted

    # 2. TESTE_AAF_PROTOOLS (REF is muted)
    txt_teste = Path("tests/fixtures/aaf/teste_aaf_protools_txt.txt")
    if not txt_teste.exists():
        pytest.skip("Fixture teste_aaf_protools_txt.txt missing")

    session_teste = SessionIR(
        name="dummy", sample_rate=48000, start_timecode_samples=0, timecode_rate=TimecodeRate(25, 1)
    )
    t_ref = TrackIR(id="t_ref", source_index=1, name="REF [L]", kind="audio", channel_count=1)
    session_teste.tracks.append(t_ref)

    rep_teste = Report(strict=False)
    process_session_text(session_teste, "dummy.aaf", str(txt_teste), rep_teste)
    assert t_ref.muted
