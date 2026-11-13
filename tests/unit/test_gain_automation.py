from pathlib import Path
from aafton.aaf_read import read_aaf
from aafton.report import Report
import pytest


def test_gain_automation_in_fades(tmp_path):
    aaf_path = Path("tests/fixtures/aaf/SPOT_AAF_TESTE.aaf")
    if not aaf_path.exists():
        pytest.skip(f"Fixture {aaf_path} not found")

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    # Com a conversao de envelope implementada, as tracks Audio 3 [L] e [R]
    # recebem envelopes e os avisos antigos sao substituidos por GAIN_ENVELOPE_WRITTEN
    envelope_tracks = [i for i in report.issues if i.code == "GAIN_ENVELOPE_WRITTEN"]
    assert len(envelope_tracks) == 2  # Audio 3 [L] e Audio 3 [R] (ou Audio 3 stereo)

    # Cada track convertida deve ter gain_points
    tracks_with_gain_points = [t for t in session.tracks if t.gain_points]
    assert len(tracks_with_gain_points) > 0

    # Nao deve haver avisos AAF_GAIN_AUTOMATION ou AAF_GAIN_AUTOMATION_IN_FADE
    # para as tracks que receberam envelope
    clip_ramps = [i for i in report.issues if i.code == "AAF_GAIN_AUTOMATION"]
    fade_ramps = [i for i in report.issues if i.code == "AAF_GAIN_AUTOMATION_IN_FADE"]
    assert len(clip_ramps) == 0
    assert len(fade_ramps) == 0

    # O sumario deve existir
    summaries = [i for i in report.issues if i.code == "GAIN_AUTOMATION_SUMMARY"]
    assert len(summaries) == 1
    summary = summaries[0]
    # total inclui pontos convertidos em envelope
    assert summary.context["total"] > 0
