"""Testes do reaproveitamento do proxy de video com timecode queimado.

O modulo video_burn.py grava um proxy com o timecode desenhado no quadro. Um
proxy de duas horas de filme leva minutos para gravar; estes testes cobrem o
caminho que reaproveita o proxy ja queimado e o nome da track quando o proxy
queimado entra no import.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.ir import SessionIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.video_track import add_video_track

START = "01:00:00:00"
OUTRO_START = "02:00:00:00"
RATE = TimecodeRate(25, 1)


def _cria_video(caminho: Path) -> Path:
    """Monta um video de teste de 2 s, 320x240 e 25 fps."""
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    args.append(str(caminho))
    res = subprocess.run(args, check=False, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"ffmpeg falhou ao criar o video: {res.stderr[-2000:]}")
    return caminho


def _json_do_proxy(proxy: Path) -> dict:
    """Le o JSON que descreve o proxy queimado."""
    caminho = proxy.with_name(proxy.name + ".json")
    return json.loads(caminho.read_text(encoding="utf-8"))


def _session() -> SessionIR:
    """Sessao vazia para os testes do nome da track."""
    return SessionIR(name="test", sample_rate=48000, timecode_rate=RATE, start_timecode_samples=0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
def test_burn_grava_o_json_e_depois_reaproveita_o_proxy(tmp_path, monkeypatch):
    from aafton.video_burn import burn_timecode_proxy

    video = _cria_video(tmp_path / "video.mp4")
    report = Report(strict=False)
    saida = burn_timecode_proxy(video, START, RATE, report)
    assert saida is not None
    assert any(issue.code == "VIDEO_TC_BURNED" for issue in report.issues)

    meta = _json_do_proxy(saida)
    assert meta["source"] == str(video.resolve())
    assert meta["source_size"] == video.stat().st_size
    assert meta["source_mtime_ns"] == video.stat().st_mtime_ns
    assert meta["start"] == START
    assert meta["rate"] == "25/1"

    def _nao_roda(*args, **kwargs):
        raise AssertionError("o ffmpeg nao pode rodar quando o proxy ja serve")

    monkeypatch.setattr("aafton.video_burn.subprocess.run", _nao_roda)
    report2 = Report(strict=False)
    saida2 = burn_timecode_proxy(video, START, RATE, report2)
    assert saida2 == saida
    assert any(issue.code == "VIDEO_TC_REUSED" for issue in report2.issues)
    assert not any(issue.code == "VIDEO_TC_BURNED" for issue in report2.issues)

    issue = next(i for i in report2.issues if i.code == "VIDEO_TC_REUSED")
    assert issue.context["start"] == START
    assert issue.context["rate"] == "25/1"
    assert Path(issue.context["path"]).name == "video_LiveProxy_TC.mp4"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
def test_burn_com_outro_start_queima_o_proxy_de_novo(tmp_path):
    from aafton.video_burn import burn_timecode_proxy

    video = _cria_video(tmp_path / "video.mp4")
    report = Report(strict=False)
    burn_timecode_proxy(video, START, RATE, report)

    report2 = Report(strict=False)
    saida = burn_timecode_proxy(video, OUTRO_START, RATE, report2)
    assert saida is not None
    assert any(issue.code == "VIDEO_TC_BURNED" for issue in report2.issues)
    assert not any(issue.code == "VIDEO_TC_REUSED" for issue in report2.issues)
    assert _json_do_proxy(saida)["start"] == OUTRO_START


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
def test_burn_com_outra_rate_queima_o_proxy_de_novo(tmp_path):
    from aafton.video_burn import burn_timecode_proxy

    video = _cria_video(tmp_path / "video.mp4")
    report = Report(strict=False)
    burn_timecode_proxy(video, START, RATE, report)

    report2 = Report(strict=False)
    saida = burn_timecode_proxy(video, START, TimecodeRate(30, 1), report2)
    assert saida is not None
    assert any(issue.code == "VIDEO_TC_BURNED" for issue in report2.issues)
    assert not any(issue.code == "VIDEO_TC_REUSED" for issue in report2.issues)
    assert _json_do_proxy(saida)["rate"] == "30/1"


def test_add_video_track_tira_o_sufixo_do_proxy_queimado():
    session = _session()
    report = Report(strict=False)
    add_video_track(session, Path("x/video_LiveProxy_TC.mp4"), 0, None, 10.0, 0, None, report)
    assert session.tracks[0].name == "video"
    assert session.tracks[0].clips[0].name == "video_LiveProxy_TC"


def test_add_video_track_tira_o_sufixo_do_proxy_normal():
    session = _session()
    report = Report(strict=False)
    add_video_track(session, Path("x/video_LiveProxy.mp4"), 0, None, 10.0, 0, None, report)
    assert session.tracks[0].name == "video"
    assert session.tracks[0].clips[0].name == "video_LiveProxy"
