# tests/unit/test_export_integrity.py
from __future__ import annotations

from pathlib import Path
import pytest

from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate
from tests.aaf_factory import wav_bytes

UMA_HORA = 172800000


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "audio.wav"
    caminho.write_bytes(wav_bytes(48000 * 10))
    return caminho


def _sessao_com_overlap(wav):
    asset = MediaAsset(
        id="m1",
        filename=str(wav),
        channels=1,
        sample_rate=48000,
        duration_samples=48000 * 10,
        status="resolved",
        resolved_path=str(wav),
        raw={},
    )
    # Clip A: [UMA_HORA, UMA_HORA + 1000)
    # Clip B: [UMA_HORA + 500, UMA_HORA + 1500) -> overlap de 500 samples
    c1 = ClipIR(
        id="c1",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="clipA",
        media_id="m1",
        timeline_start_samples=UMA_HORA,
        timeline_end_samples=UMA_HORA + 1000,
        source_offset_samples=0,
        raw={},
    )
    c2 = ClipIR(
        id="c2",
        track_id="t0",
        channel_index=0,
        event_index=1,
        name="clipB",
        media_id="m1",
        timeline_start_samples=UMA_HORA + 500,
        timeline_end_samples=UMA_HORA + 1500,
        source_offset_samples=0,
        raw={},
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="AUDIO",
        kind="audio",
        channel_count=1,
        clips=[c1, c2],
        plugins=[],
    )
    return SessionIR(
        name="overlap_session",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
        markers=[],
        source_path=None,
        source_application=None,
    )


def test_write_overlap_strict_raises_and_destination_not_created(tmp_path, wav):
    session = _sessao_com_overlap(wav)
    output = tmp_path / "out_strict.aaf"

    with pytest.raises(StrictModeError, match="WRITE_OVERLAP"):
        write_aaf(session, output, Report(strict=True))

    assert not output.exists()


def test_write_overlap_strict_preserves_preexisting_file(tmp_path, wav):
    session = _sessao_com_overlap(wav)
    output = tmp_path / "existing.aaf"
    preexisting_bytes = b"ORIGINAL_FILE_CONTENT_DO_NOT_TOUCH"
    output.write_bytes(preexisting_bytes)

    with pytest.raises(StrictModeError, match="WRITE_OVERLAP"):
        write_aaf(session, output, Report(strict=True))

    assert output.exists()
    assert output.read_bytes() == preexisting_bytes


def test_write_overlap_permissive_reports_error_and_incomplete(tmp_path, wav):
    session = _sessao_com_overlap(wav)
    output = tmp_path / "out_permissive.aaf"
    report = Report(strict=False)

    write_aaf(session, output, report)

    assert report.incomplete is True
    assert any(i.code == "WRITE_OVERLAP" and i.severity == "error" for i in report.issues)


def test_cli_export_overlap_fails_with_exit_code_1(tmp_path, wav):
    from aafton.cli import main
    from aafton.als_write import write_session, WriteOptions

    template = Path("templates/live12.als")
    if not template.exists():
        pytest.skip("template live12.als missing")

    session = _sessao_com_overlap(wav)
    als_path = tmp_path / "overlap.als"
    write_session(
        session,
        template,
        als_path,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=UMA_HORA),
        Report(strict=False),
    )

    output = tmp_path / "cli_out.aaf"
    exit_code = main(["export", str(als_path), str(output), "--permissive"])
    assert exit_code == 1


def test_supported_transition_succeeds(tmp_path, wav):
    from aafton.ir import FadeIR

    session = _sessao_com_overlap(wav)
    # Add matching fades to c1 and c2 corresponding to the 500 samples overlap
    overlap = 500
    session.tracks[0].clips[0].fade_out = FadeIR(kind="out", duration_samples=overlap)
    session.tracks[0].clips[1].fade_in = FadeIR(kind="in", duration_samples=overlap)

    output = tmp_path / "transition.aaf"
    report = Report(strict=True)
    write_aaf(session, output, report)

    assert output.exists()
    assert not any(i.code == "WRITE_OVERLAP" for i in report.issues)
