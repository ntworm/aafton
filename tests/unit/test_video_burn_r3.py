"""Testes da fase 2 do proxy de video queimado.

O drawtext do ffmpeg conta os frames do video na taxa da sessao. Quando o
video e de 25 fps e a sessao e de 24 fps, o rotulo queimado erra a cada
segundo e o proxy queimado fica com o moov no fim do arquivo, o que obriga o
navegador a buscar o fim antes de comecar a tocar. Estes testes cobrem as duas
correcoes: a checagem de taxa antes de queimar e o faststart do proxy.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.ir import SessionIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

RATE_24 = TimecodeRate(24, 1)
RATE_25 = TimecodeRate(25, 1)
START = "01:00:00:00"

TEM_FERRAMENTAS = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

SEM_FERRAMENTAS = pytest.mark.skipif(
    not TEM_FERRAMENTAS, reason="ffmpeg e ffprobe nao estao instalados"
)


def _cria_video(caminho: Path, fps: int) -> Path:
    """Monta um video de teste de 2 s, 320x240, na taxa de frames pedida."""
    args = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate={fps}:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(caminho),
    ]
    res = subprocess.run(args, check=False, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"ffmpeg falhou ao criar o video: {res.stderr[-2000:]}")
    return caminho


def _sessao(rate: TimecodeRate) -> SessionIR:
    """Sessao minima com a taxa de timecode pedida."""
    return SessionIR(name="test", sample_rate=48000, timecode_rate=rate, start_timecode_samples=0)


def _issue(report: Report, codigo: str):
    """Primeiro issue do codigo pedido, ou None quando ele nao aparece."""
    return next((item for item in report.issues if item.code == codigo), None)


@SEM_FERRAMENTAS
def test_taxa_do_video_diferente_nao_queima_o_proxy(tmp_path):
    from aafton.video_burn import burn_for_live

    video = _cria_video(tmp_path / "video.mp4", 25)
    proxy = tmp_path / "video_LiveProxy.mp4"
    proxy.write_bytes(b"proxy sem timecode queimado")
    report = Report(strict=False)

    atual = burn_for_live(video, proxy, 0, _sessao(RATE_24), report)

    assert atual == proxy
    issue = _issue(report, "VIDEO_BURN_RATE_MISMATCH")
    assert issue is not None, "a divergencia de taxa tem que ser avisada"
    assert issue.context["video_fps"] == 25.0
    assert issue.context["session_fps"] == 24.0
    assert not (tmp_path / "video_LiveProxy_TC.mp4").exists()


@SEM_FERRAMENTAS
def test_taxa_do_video_igual_queima_o_proxy(tmp_path):
    from aafton.video_burn import burn_for_live

    video = _cria_video(tmp_path / "video.mp4", 25)
    report = Report(strict=False)

    saida = burn_for_live(video, tmp_path / "video_LiveProxy.mp4", 0, _sessao(RATE_25), report)

    assert saida.name == "video_LiveProxy_TC.mp4"
    assert saida.exists(), "com a taxa igual a da sessao o proxy tem que ser queimado"
    assert _issue(report, "VIDEO_TC_BURNED") is not None
    assert _issue(report, "VIDEO_BURN_RATE_MISMATCH") is None


def test_comando_do_burn_tem_faststart_antes_da_saida(tmp_path):
    from aafton.video_burn import burn_command

    entrada = tmp_path / "video.mp4"
    saida = tmp_path / "video_LiveProxy_TC.mp4"

    argv = burn_command(entrada, saida, START, RATE_25, True)

    assert "-movflags" in argv
    posicao = argv.index("-movflags")
    assert argv[posicao + 1] == "+faststart"
    assert argv[-1] == str(saida)


@SEM_FERRAMENTAS
def test_proxy_queimado_tem_o_moov_na_frente(tmp_path):
    from aafton.video_burn import burn_for_live

    video = _cria_video(tmp_path / "video.mp4", 25)
    report = Report(strict=False)

    saida = burn_for_live(video, tmp_path / "video_LiveProxy.mp4", 0, _sessao(RATE_25), report)

    dados = saida.read_bytes()
    pos_moov = dados.find(b"moov")
    pos_mdat = dados.find(b"mdat")
    assert pos_moov != -1 and pos_mdat != -1, "o proxy tem que ter os atomos moov e mdat"
    assert pos_moov < pos_mdat, "o moov na frente do mdat evita buscar o fim do arquivo"
