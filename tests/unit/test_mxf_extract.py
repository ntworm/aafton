"""Testes da extracao de midia MXF com o ffmpeg.

Os MXF de teste sao gerados aqui mesmo, com o ffmpeg, para o repositorio
nao precisar guardar midia de terceiros.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.ir import MediaAsset
from aafton.media_extract import (
    parse_bext,
    read_audio_metadata,
    resolve_asset,
    write_wav_with_bext,
)
from aafton.report import Report
from aafton.timecode import TimecodeRate

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "formatos"
TEMPLATE = REPO / "templates" / "live12.als"

TAXA = 48000
# Timecode de arquivo a 25 fps: 10:00:00:00.
TC_DEZ_HORAS = 1728000000
# Timecode de arquivo a 24 fps: 10:00:00:12.
TC_DEZ_HORAS_24 = 1728024000

pytestmark = pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg e ffprobe nao estao no PATH",
)


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas do relatorio."""
    return [issue.code for issue in report.issues]


def _contexto(report: Report, code: str) -> dict:
    """Contexto da primeira issue com o codigo dado."""
    for issue in report.issues:
        if issue.code == code:
            return issue.context
    return {}


def _codigos_json(relatorio: Path) -> list[str]:
    """Codigos de todos os problemas do relatorio gravado em json."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def _wavs(pasta: Path) -> list[Path]:
    """WAVs que existem na pasta; pasta inexistente nao tem nenhum."""
    if not pasta.exists():
        return []
    return list(pasta.glob("*.wav"))


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


def _op1a(destino: Path, timecode: str = "10:00:00:00", fps: int = 25) -> Path:
    """Grava um MXF OP1a com video e dois streams de audio mono."""
    argumentos = ["-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}:duration=2"]
    argumentos += ["-f", "lavfi", "-i", _sine(1000)]
    argumentos += ["-f", "lavfi", "-i", _sine(500)]
    argumentos += ["-map", "0", "-map", "1", "-map", "2"]
    argumentos += ["-c:v", "mpeg2video", "-c:a", "pcm_s24le", "-timecode", timecode]
    argumentos += ["-f", "mxf"]
    return _ffmpeg(destino, argumentos)


def _wav(destino: Path, segundos: float, time_reference: int) -> Path:
    """WAV mono de 48 kHz e 24 bits, com o timecode de origem no bext."""
    amostras = int(segundos * TAXA)
    write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * amostras,
        channels=1,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=time_reference,
    )
    return destino


def test_mxf_opatom_vira_wav_mono_com_o_timecode(tmp_path):
    """OP-Atom vira um WAV mono de 24 bits com o timecode no bext."""
    from aafton.mxf import extrair_mxf

    origem = _opatom(tmp_path / "a.mxf")
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TimecodeRate(25, 1), report)

    assert wav is not None
    assert wav.name.startswith("a_") and wav.name.endswith("_mxf.wav")
    assert [p.name for p in (tmp_path / "importados").glob("a_*_mxf.wav")] == [wav.name]
    meta = read_audio_metadata(wav)
    assert meta["channels"] == 1
    assert meta["bits"] == 24
    assert meta["sample_rate"] == TAXA
    assert meta["duration_samples"] == 96000
    assert parse_bext(wav)["time_reference"] == TC_DEZ_HORAS
    assert "MEDIA_MXF_EXTRACTED" in _codigos(report)
    contexto = _contexto(report, "MEDIA_MXF_EXTRACTED")
    assert contexto["source"] == str(origem)
    assert contexto["wav"] == str(wav)
    assert contexto["channels"] == 1
    assert contexto["timecode"] == "10:00:00:00"
    assert contexto["time_reference"] == TC_DEZ_HORAS
    assert len(contexto["sha256"]) == 64


def test_mxf_op1a_com_dois_streams_vira_wav_estereo(tmp_path):
    """OP1a com dois streams de audio vira um WAV de dois canais."""
    from aafton.mxf import extrair_mxf

    origem = _op1a(tmp_path / "b.mxf")
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TimecodeRate(25, 1), report)

    assert wav is not None
    meta = read_audio_metadata(wav)
    assert meta["channels"] == 2
    assert meta["bits"] == 24
    assert meta["sample_rate"] == TAXA
    assert meta["duration_samples"] == 96000
    assert parse_bext(wav)["time_reference"] == TC_DEZ_HORAS
    assert "MEDIA_MXF_RATE_MISMATCH" not in _codigos(report)
    assert _contexto(report, "MEDIA_MXF_EXTRACTED")["channels"] == 2


def test_video_em_24_avisa_e_usa_a_taxa_do_video(tmp_path):
    """Video em 24 fps avisa a diferenca e conta o timecode no video."""
    from aafton.mxf import extrair_mxf

    origem = _op1a(tmp_path / "c.mxf", timecode="10:00:00:12", fps=24)
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TimecodeRate(25, 1), report)

    assert wav is not None
    assert parse_bext(wav)["time_reference"] == TC_DEZ_HORAS_24
    assert "MEDIA_MXF_RATE_MISMATCH" in _codigos(report)


def test_resolve_asset_com_pasta_de_midia_vira_wav(tmp_path):
    """Com a pasta de midia, o asset MXF vira o WAV extraido."""
    origem = _opatom(tmp_path / "a.mxf")
    pasta = tmp_path / "importados"
    asset = MediaAsset(id="m1", filename="a.mxf", declared_location=str(origem))
    report = Report(strict=False)

    resolve_asset(asset, [tmp_path], report, mxf_dir=pasta, taxa_tc=TimecodeRate(25, 1))

    assert asset.status == "resolved"
    nome = Path(asset.resolved_path).name
    assert nome.startswith("a_") and nome.endswith("_mxf.wav")
    assert asset.channels == 1
    assert asset.sample_rate == TAXA
    assert asset.duration_samples == 96000
    assert "MEDIA_MXF_EXTRACTED" in _codigos(report)
    assert "MEDIA_MXF_UNSUPPORTED" not in _codigos(report)


def test_resolve_asset_sem_pasta_de_midia_mantem_o_mxf_nao_suportado(tmp_path):
    """Sem a pasta de midia, o MXF continua marcado como nao suportado."""
    origem = _opatom(tmp_path / "a.mxf")
    asset = MediaAsset(id="m1", filename="a.mxf", declared_location=str(origem))
    report = Report(strict=False)

    resolve_asset(asset, [tmp_path], report)

    assert asset.status == "unsupported"
    assert "MEDIA_MXF_UNSUPPORTED" in _codigos(report)


def test_resolve_asset_sem_ffmpeg_mantem_o_mxf_nao_suportado(tmp_path, monkeypatch):
    """Sem ffmpeg no PATH, nada e extraido e o MXF continua nao suportado."""
    origem = _opatom(tmp_path / "a.mxf")
    pasta = tmp_path / "importados"
    monkeypatch.setattr(shutil, "which", lambda nome: None)
    asset = MediaAsset(id="m1", filename="a.mxf", declared_location=str(origem))
    report = Report(strict=False)

    resolve_asset(asset, [tmp_path], report, mxf_dir=pasta, taxa_tc=TimecodeRate(25, 1))

    assert asset.status == "unsupported"
    assert "MEDIA_MXF_UNSUPPORTED" in _codigos(report)
    assert _wavs(pasta) == []


def test_mxf_corrompido_nao_deixa_wav(tmp_path):
    """Um MXF invalido nao produz WAV e nao some com o erro de nao suportado."""
    origem = tmp_path / "lixo.mxf"
    origem.write_bytes(b"\x06\x0e\x2b\x34")
    pasta = tmp_path / "importados"
    asset = MediaAsset(id="m1", filename="lixo.mxf", declared_location=str(origem))
    report = Report(strict=False)

    resolve_asset(asset, [tmp_path], report, mxf_dir=pasta, taxa_tc=TimecodeRate(25, 1))

    assert asset.status == "unsupported"
    assert "MEDIA_MXF_UNSUPPORTED" in _codigos(report)
    assert _wavs(pasta) == []


def test_import_do_xml_com_mxf_extrai_o_audio(tmp_path):
    """O import do XML com midia MXF extrai o audio e aponta o clip para ele."""
    from aafton.cli import main

    cena = tmp_path / "cena"
    cena.mkdir()
    xml = cena / "cena12_premiere.xml"
    original = (FIXTURES / "cena12_premiere.xml").read_text(encoding="utf-8")
    xml.write_text(original.replace("dial02.wav", "dial02.mxf"), encoding="utf-8")
    _wav(cena / "dial01.wav", 20, TC_DEZ_HORAS)
    _opatom(cena / "dial02.mxf", timecode="00:00:00:00", duracao=10)
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"

    argumentos = ["import", str(xml), str(saida), "--template", str(TEMPLATE)]
    argumentos += ["--permissive", "--report", str(relatorio)]
    codigo = main(argumentos)

    assert codigo == 0
    assert "MEDIA_MXF_EXTRACTED" in _codigos_json(relatorio)
    sessao = read_session(saida, Report(strict=False))
    cenas = [track for track in sessao.tracks if not track.is_video]
    assert len(cenas) == 2
    clip = cenas[1].clips[0]
    asset = sessao.media_by_id(clip.media_id)
    assert asset is not None
    nome = Path(asset.resolved_path).name
    assert nome.startswith("dial02_") and nome.endswith("_mxf.wav")
    assert clip.source_offset_samples == 0
