"""Testes da importacao de EDL, XML do FCP e FCPXML pelo aafton."""

from __future__ import annotations

import json
import shutil
import wave
from pathlib import Path

import pytest

import aafton.formats
import aafton.formats.otio_read
from aafton.als_read import read_session
from aafton.cli import main
from aafton.formats import ExtraAusente
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate

pytest.importorskip("opentimelineio")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "formatos"
TEMPLATE = REPO / "templates" / "live12.als"

TAXA = 48000
FORMATO_EDL = "cena12.edl"
FORMATO_XML = "cena12_premiere.xml"
FORMATO_FCPXML = "cena12.fcpxml"
FORMATOS = [FORMATO_EDL, FORMATO_XML, FORMATO_FCPXML]
# Timecode de arquivo de dial01.wav: 10:00:00:00 a 25 fps.
TC_ARQUIVO = 1728000000
# Inicio e offset de cada clip da cena, na ordem das tracks.
CLIPS_ESPERADOS = [(172800000, 48000), (172992000, 240000), (172848000, 0)]


def _origem():
    """Modulo que escolhe o leitor pelo sufixo do arquivo."""
    from aafton.formats import origem

    return origem


def _wav(destino: Path, segundos: float, time_reference: int | None) -> Path:
    """WAV mono de 48 kHz e 24 bits; sem bext quando o timecode e None."""
    amostras = int(segundos * TAXA)
    if time_reference is None:
        with wave.open(str(destino), "wb") as arquivo:
            arquivo.setnchannels(1)
            arquivo.setsampwidth(3)
            arquivo.setframerate(TAXA)
            arquivo.writeframes(b"\x00\x00\x00" * amostras)
        return destino
    write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * amostras,
        channels=1,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=time_reference,
    )
    return destino


@pytest.fixture
def cena(tmp_path):
    """Pasta com as tres fixtures e as duas midias da cena CENA12."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    for nome in FORMATOS:
        shutil.copy(FIXTURES / nome, pasta / nome)
    _wav(pasta / "dial01.wav", 20, TC_ARQUIVO)
    _wav(pasta / "dial02.wav", 10, 0)
    return pasta


def _codigos(relatorio: Path) -> list[str]:
    """Codigos de todos os problemas do relatorio gravado em json."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [issue["code"] for issue in dados["issues"]]


def _codigos_do_report(report: Report) -> list[str]:
    """Codigos de todos os problemas do relatorio em memoria."""
    return [issue.code for issue in report.issues]


def _importar(cena: Path, formato: str, saida: Path, relatorio: Path, taxa_edl: bool = True) -> int:
    """Roda o import do aafton com o template do repositorio."""
    argumentos = [
        "import",
        str(cena / formato),
        str(saida),
        "--template",
        str(TEMPLATE),
        "--permissive",
        "--report",
        str(relatorio),
    ]
    if formato.endswith(".edl") and taxa_edl:
        argumentos += ["--edl-rate", "25"]
    return main(argumentos)


@pytest.mark.parametrize("formato", FORMATOS)
def test_import_gera_als_com_os_clips_da_cena(cena, tmp_path, formato):
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"

    codigo = _importar(cena, formato, saida, relatorio)

    assert codigo == 0
    assert saida.exists()
    sessao = read_session(saida, Report(strict=False))
    assert [track.kind for track in sessao.tracks] == ["audio", "audio"]
    clips = [
        (clip.timeline_start_samples, clip.source_offset_samples)
        for track in sessao.tracks
        for clip in track.clips
    ]
    assert clips == CLIPS_ESPERADOS


def test_edl_sem_taxa_de_frames_falha_fechado(cena, tmp_path):
    relatorio = tmp_path / "relatorio.json"

    codigo = _importar(cena, FORMATO_EDL, tmp_path / "saida.als", relatorio, taxa_edl=False)

    assert codigo == 1
    assert "FORMAT_EDL_RATE_REQUIRED" in _codigos(relatorio)


def test_edl_sem_bext_nao_resolve_o_timecode_de_origem(cena, tmp_path):
    _wav(cena / "dial01.wav", 20, None)
    relatorio = tmp_path / "relatorio.json"

    codigo = _importar(cena, FORMATO_EDL, tmp_path / "saida.als", relatorio)

    assert codigo == 1
    assert "FORMAT_SOURCE_TC_UNRESOLVED" in _codigos(relatorio)


def test_inspect_do_xml_mostra_as_duas_tracks(cena, capsys):
    codigo = main(["inspect", str(cena / FORMATO_XML)])

    assert codigo == 0
    dados = json.loads(capsys.readouterr().out)
    assert len(dados["tracks"]) == 2


def test_inspect_com_taxa_de_edl_invalida_falha_com_uso(capsys):
    """Uma taxa de EDL fora da lista vira erro de uso do argparse."""
    with pytest.raises(SystemExit) as erro:
        main(["inspect", str(FIXTURES / FORMATO_EDL), "--edl-rate", "26"])

    assert erro.value.code == 2
    assert "25" in capsys.readouterr().err


def test_extra_formats_ausente_falha_fechado(cena, monkeypatch):
    def sem_extra():
        raise ExtraAusente("the formats extra is not installed")

    monkeypatch.setattr(aafton.formats, "carregar_otio", sem_extra)
    monkeypatch.setattr(aafton.formats.otio_read, "carregar_otio", sem_extra)
    report = Report(strict=False)

    sessao = _origem().read_origin(cena / FORMATO_EDL, report, edl_rate=TimecodeRate(25, 1))

    assert "FORMAT_EXTRA_MISSING" in _codigos_do_report(report)
    assert sessao.name == "cena12"
    assert sessao.sample_rate == 48000
    assert sessao.timecode_rate == TimecodeRate(25, 1)
    assert sessao.start_timecode_samples == 0
    assert sessao.tracks == []
    assert sessao.source_path == str(cena / FORMATO_EDL)


def test_sufixo_desconhecido_falha_fechado(tmp_path):
    caminho = tmp_path / "cena.txt"
    caminho.write_text("isto nao e um formato de intercambio", encoding="utf-8")
    report = Report(strict=False)

    sessao = _origem().read_origin(caminho, report)

    assert "FORMAT_UNKNOWN_SUFFIX" in _codigos_do_report(report)
    assert sessao.name == "cena"
    assert sessao.sample_rate == 48000
    assert sessao.timecode_rate == TimecodeRate(25, 1)
    assert sessao.start_timecode_samples == 0
    assert sessao.tracks == []
