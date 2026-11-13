"""Testes da segunda rodada da leitura de MXF e da descoberta de video.

Cobrem o timecode sem drop frame em sessao de drop frame e o MXF que so
tem audio, que nao pode virar o video da sessao.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.media_extract import parse_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

TAXA = 48000
# Uma hora de timecode NDF a 29.97: 108000 frames de 1601,6 samples.
HORA_NDF_2997 = 172972800

pytestmark = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg e ffprobe nao estao no PATH",
)


def _sine(duracao: int = 2) -> str:
    """Fonte de audio sintetico, em texto de lavfi."""
    return f"sine=frequency=1000:sample_rate={TAXA}:duration={duracao}"


def _ffmpeg(destino: Path, argumentos: list[str]) -> Path:
    """Roda o ffmpeg com os argumentos dados e devolve o arquivo gravado."""
    subprocess.run(["ffmpeg", "-y", *argumentos, str(destino)], check=True, capture_output=True)
    return destino


def _opatom(destino: Path, timecode: str) -> Path:
    """Grava um MXF OP-Atom com um stream de audio mono."""
    argumentos = ["-f", "lavfi", "-i", _sine()]
    argumentos += ["-c:a", "pcm_s24le", "-timecode", timecode, "-f", "mxf_opatom"]
    return _ffmpeg(destino, argumentos)


def _op1a(destino: Path, timecode: str) -> Path:
    """Grava um MXF OP1a com video e um stream de audio mono."""
    argumentos = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
    argumentos += ["-f", "lavfi", "-i", _sine()]
    argumentos += ["-map", "0", "-map", "1", "-c:v", "mpeg2video", "-c:a", "pcm_s24le"]
    argumentos += ["-timecode", timecode, "-f", "mxf"]
    return _ffmpeg(destino, argumentos)


def test_mxf_com_timecode_sem_drop_frame_em_sessao_de_drop_frame(tmp_path):
    """O timecode NDF do MXF e contado sem drop frame, mesmo em sessao DF."""
    from aafton.mxf import extrair_mxf

    origem = _opatom(tmp_path / "ndf.mxf", "01:00:00:00")
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TimecodeRate(30000, 1001, True), report)

    assert wav is not None
    assert parse_bext(wav)["time_reference"] == HORA_NDF_2997


def test_descoberta_de_video_ignora_mxf_sem_video(tmp_path):
    """Um MXF que so tem audio nao vira o video da sessao."""
    from aafton.video import find_video_file

    raiz = tmp_path / "PROJETO"
    pasta_aaf = raiz / "AAF"
    pasta_video = raiz / "Video Files"
    pasta_aaf.mkdir(parents=True)
    pasta_video.mkdir(parents=True)
    aaf = pasta_aaf / "edit_v1.aaf"
    aaf.write_bytes(b"dummy_aaf")
    _opatom(pasta_video / "audio.mxf", "01:00:00:00")

    achado = find_video_file(aaf, None)

    assert achado is None


def test_descoberta_de_video_aceita_mxf_com_video(tmp_path):
    """Um MXF com stream de video continua sendo o video da sessao."""
    from aafton.video import find_video_file

    raiz = tmp_path / "PROJETO"
    pasta_aaf = raiz / "AAF"
    pasta_video = raiz / "Video Files"
    pasta_aaf.mkdir(parents=True)
    pasta_video.mkdir(parents=True)
    aaf = pasta_aaf / "edit_v1.aaf"
    aaf.write_bytes(b"dummy_aaf")
    mxf = _op1a(pasta_video / "edit_v1.mxf", "01:00:00:00")

    achado = find_video_file(aaf, None)

    assert achado == mxf.resolve()
