"""Testes das correcoes do MXF: drop frame, acento no nome e colisao de WAV.

Os MXF de teste sao gerados aqui mesmo, com o ffmpeg, para o repositorio
nao precisar guardar midia de terceiros.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.media_extract import parse_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate, label_to_samples

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

TAXA = 48000
# Timecode de arquivo a 25 fps: 10:00:00:00.
TC_DEZ_HORAS = 1728000000
# Timecode de arquivo a 25 fps: 11:00:00:00.
TC_ONZE_HORAS = 1900800000
# Pasta com acento: o 'A' com acento (U+00C1) vem de chr para o fonte ficar em ASCII.
PASTA_COM_ACENTO = chr(0xC1) + "udio"

pytestmark = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg e ffprobe nao estao no PATH",
)


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas do relatorio."""
    return [issue.code for issue in report.issues]


def _sine(frequencia: int = 1000, duracao: int = 2) -> str:
    """Fonte de audio sintetico, em texto de lavfi."""
    return f"sine=frequency={frequencia}:sample_rate={TAXA}:duration={duracao}"


def _ffmpeg(destino: Path, argumentos: list[str]) -> Path:
    """Roda o ffmpeg com os argumentos dados e devolve o arquivo gravado."""
    subprocess.run(["ffmpeg", "-y", *argumentos, str(destino)], check=True, capture_output=True)
    return destino


def _opatom(destino: Path, timecode: str = "10:00:00:00", duracao: int = 2) -> Path:
    """Grava um MXF OP-Atom com um stream de audio mono."""
    argumentos = ["-f", "lavfi", "-i", _sine(duracao=duracao)]
    argumentos += ["-c:a", "pcm_s24le", "-timecode", timecode, "-f", "mxf_opatom"]
    return _ffmpeg(destino, argumentos)


def _op1a_2997(destino: Path, timecode: str = "01:00:00;00") -> Path:
    """Grava um MXF OP1a com video a 29.97 e audio mono."""
    argumentos = ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30000/1001:duration=2"]
    argumentos += ["-f", "lavfi", "-i", _sine()]
    argumentos += ["-map", "0", "-map", "1"]
    argumentos += ["-c:v", "mpeg2video", "-c:a", "pcm_s24le", "-timecode", timecode]
    argumentos += ["-f", "mxf"]
    return _ffmpeg(destino, argumentos)


def test_op1a_com_timecode_drop_frame_conta_em_drop_frame(tmp_path):
    """OP1a a 29.97 com timecode drop frame conta o rotulo em drop frame."""
    from aafton.mxf import extrair_mxf

    origem = _op1a_2997(tmp_path / "d.mxf")
    report = Report(strict=False)
    taxa = TimecodeRate(30000, 1001, True)

    wav = extrair_mxf(origem, tmp_path / "importados", taxa, report)

    assert wav is not None
    esperado = round(label_to_samples("01:00:00;00", taxa, TAXA))
    assert parse_bext(wav)["time_reference"] == esperado
    assert "MEDIA_MXF_RATE_MISMATCH" not in _codigos(report)


def test_mxf_em_pasta_com_acento_extrai_o_audio(tmp_path):
    """MXF em pasta com acento no nome nao quebra a extracao."""
    from aafton.ir import MediaAsset
    from aafton.media_extract import resolve_asset
    from aafton.mxf import extrair_mxf

    pasta = tmp_path / PASTA_COM_ACENTO
    pasta.mkdir()
    origem = _opatom(pasta / "a.mxf")
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TimecodeRate(25, 1), report)

    assert wav is not None
    assert parse_bext(wav)["time_reference"] == TC_DEZ_HORAS
    assert "MEDIA_MXF_EXTRACTED" in _codigos(report)

    asset = MediaAsset(id="m1", filename="a.mxf", declared_location=str(origem))
    resolve_asset(
        asset, [tmp_path], report, mxf_dir=tmp_path / "importados", taxa_tc=TimecodeRate(25, 1)
    )

    assert asset.status == "resolved"
    assert "MEDIA_RESOLVE_ERROR" not in _codigos(report)
    assert "MEDIA_MXF_UNSUPPORTED" not in _codigos(report)


def test_dois_mxf_com_o_mesmo_nome_viram_wavs_diferentes(tmp_path):
    """Dois MXF com o mesmo nome em pastas diferentes viram WAVs diferentes."""
    from aafton.mxf import extrair_mxf

    primeira = tmp_path / "primeira"
    segunda = tmp_path / "segunda"
    primeira.mkdir()
    segunda.mkdir()
    origem_a = _opatom(primeira / "cena.mxf", timecode="10:00:00:00")
    origem_b = _opatom(segunda / "cena.mxf", timecode="11:00:00:00")
    destino = tmp_path / "importados"
    report = Report(strict=False)

    wav_a = extrair_mxf(origem_a, destino, TimecodeRate(25, 1), report)
    wav_b = extrair_mxf(origem_b, destino, TimecodeRate(25, 1), report)

    assert wav_a is not None
    assert wav_b is not None
    assert wav_a != wav_b
    assert len(list(destino.glob("*.wav"))) == 2
    assert parse_bext(wav_a)["time_reference"] == TC_DEZ_HORAS
    assert parse_bext(wav_b)["time_reference"] == TC_ONZE_HORAS
