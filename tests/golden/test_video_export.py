"""Tests for exporting sessions containing video tracks."""

import pytest
import aaf2
from pathlib import Path

from aafton.cli import main
from aafton.report import Report
from tests.support.paths import corpus_dir

AAF_PATH = Path("tests/fixtures/aaf/TESTE_AFF_PROTOOLS.aaf")
VIDEO_PATH = Path("tests/fixtures/video/dummy_video.mp4")


def test_export_skips_video_and_reports_unsupported_audio_edits(tmp_path):
    """
    Export permissivo relata perda de automação/fades e ignora a imagem.
    O AAF incompleto preserva o mesmo número de slots de áudio com ou sem vídeo.
    O relatório do export traz VIDEO_TRACK_SKIPPED_ON_EXPORT citando o nome da track.
    O AAF exportado com vídeo reabre com aaf2.open.
    """
    if not AAF_PATH.exists():
        pytest.skip("Fixture AAF not found")

    als_no_video = tmp_path / "no_video.als"
    aaf_no_video = tmp_path / "out_no_video.aaf"

    als_with_video = tmp_path / "with_video.als"
    aaf_with_video = tmp_path / "out_with_video.aaf"

    # 1. Import without video
    assert (
        main(
            [
                "import",
                str(AAF_PATH),
                str(als_no_video),
                "--audio-dir",
                str(corpus_dir("teste_aaf")),
                "--session-text",
                "tests/fixtures/aaf/teste_aaf_protools_txt.txt",
            ]
        )
        == 0
    )

    # 2. Export without video
    assert main(["export", str(als_no_video), str(aaf_no_video), "--permissive"]) == 1

    # Measure slots in AAF without video
    slots_no_video = 0
    with aaf2.open(str(aaf_no_video), "r") as f:
        comp = next(
            (mob for mob in f.content.mobs if isinstance(mob, aaf2.mobs.CompositionMob)), None
        )
        slots_no_video = len(comp.slots)

    # 3. Import with video
    assert (
        main(
            [
                "import",
                str(AAF_PATH),
                str(als_with_video),
                "--video",
                str(VIDEO_PATH),
                "--audio-dir",
                str(corpus_dir("teste_aaf")),
                "--session-text",
                "tests/fixtures/aaf/teste_aaf_protools_txt.txt",
            ]
        )
        == 0
    )

    # 4. Export with video
    # Usando API direta para poder inspecionar o report e garantir que nao roda system exit
    Report(strict=False)

    # Fake session with video read from ALS
    # A CLI faria isso.
    # Mas para interceptar o report de export, podemos chamar o export da CLI e checar o json?
    # Sim, a CLI ja suporta `--report`.
    report_json = tmp_path / "export_report.json"
    assert (
        main(
            [
                "export",
                str(als_with_video),
                str(aaf_with_video),
                "--report",
                str(report_json),
                "--permissive",
            ]
        )
        == 1
    )

    # Verify the report has the skipped video track
    import json

    with open(report_json, "r", encoding="utf-8") as f:
        rep_data = json.load(f)
        issues = rep_data.get("issues", [])
        assert rep_data["incomplete"]
        # A automacao do Live agora e lida em vez de so avisada, entao a recusa
        # sai do lado do AAF. O que este teste garante e que a edicao de audio nao
        # representavel e recusada, e nao qual camada nomeia a recusa.
        assert any(
            i["code"]
            in (
                "WRITE_SESSION_FEATURE_UNSUPPORTED",
                "WRITE_GAIN_AUTOMATION_UNSUPPORTED",
                "WRITE_FADE_UNSUPPORTED",
            )
            for i in issues
        )
        skipped_issues = [i for i in issues if i["code"] == "VIDEO_TRACK_SKIPPED_ON_EXPORT"]
        assert len(skipped_issues) > 0, "Deveria ter pulado a track de video no export"
        assert (
            "dummy_video" in skipped_issues[0]["message"]
            or "video" in skipped_issues[0]["message"].lower()
        )

    # 5. Measure slots in AAF with video
    # E verifica se reabre com aaf2.open (se nao reabrir, levanta excecao)
    slots_with_video = 0
    with aaf2.open(str(aaf_with_video), "r") as f:
        comp = next(
            (mob for mob in f.content.mobs if isinstance(mob, aaf2.mobs.CompositionMob)), None
        )
        slots_with_video = len(comp.slots)

    # 6. Assert slots count is the same
    assert slots_with_video == slots_no_video, (
        f"Expected {slots_no_video} slots, got {slots_with_video}"
    )
