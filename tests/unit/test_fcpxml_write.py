"""Testes da escrita de FCPXML 1.9 a partir do SessionIR."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

import aafton.formats
from aafton.formats import ExtraAusente
from aafton.formats.fcpxml_fix import tempo_fcpxml
from aafton.ir import FadeIR, MarkerIR
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate
from tests.unit.test_edl_write import _cena

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "formatos" / "cena12.fcpxml"

INICIO = 172800000
MARCADOR = 172896000
TAXA = 48000


def _modulo(nome: str):
    """Modulo do pacote de formatos.

    O import acontece dentro da funcao para que a falta do modulo apareca
    como falha de cada teste, e nao como erro de coleta do arquivo inteiro.
    """
    from importlib import import_module

    return import_module(f"aafton.formats.{nome}")


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _cena_com_marcador(tmp_path: Path):
    """A cena da fixture CENA12 com o marcador TESTE em 01:00:01:00."""
    sessao = _cena(tmp_path)
    sessao.markers.append(MarkerIR(id="k0", name="TESTE", position_samples=MARCADOR))
    return sessao


def _raiz(caminho: Path) -> ET.Element:
    """Raiz do FCPXML gravado."""
    return ET.fromstring(caminho.read_text(encoding="utf-8"))


def _sequencia(raiz: ET.Element) -> ET.Element:
    """Elemento sequence do FCPXML."""
    sequencia = raiz.find("./library/event/project/sequence")
    assert sequencia is not None
    return sequencia


def _asset_clips(raiz: ET.Element) -> set[tuple[str, str, str, str]]:
    """Lane, offset, start e duration de cada asset-clip do documento."""
    return {
        (clip.get("lane"), clip.get("offset"), clip.get("start"), clip.get("duration"))
        for clip in raiz.iter("asset-clip")
    }


def test_fcpxml_escreve_a_cena_com_a_forma_da_fixture(tmp_path):
    """O FCPXML escrito tem a forma da fixture, com tcStart e marcadores."""
    sessao = _cena_com_marcador(tmp_path)
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert destino.exists()
    assert report.count("error") == 0
    texto = destino.read_text(encoding="utf-8")
    assert texto.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<!DOCTYPE fcpxml>" in texto
    raiz = _raiz(destino)
    assert raiz.get("version") == "1.9"
    sequencia = _sequencia(raiz)
    assert sequencia.get("tcStart") == "3600s"
    assert sequencia.get("tcFormat") == "NDF"
    assert sequencia.get("audioRate") == "48k"
    assets = raiz.findall("./resources/asset")
    assert len(assets) == 2
    for asset in assets:
        assert asset.get("src") is None
        rep = asset.find("media-rep")
        assert rep is not None
        assert rep.get("src").endswith(asset.get("name"))
    assert _asset_clips(raiz) == _asset_clips(_raiz(FIXTURE))
    marcadores = raiz.findall(".//marker")
    assert [(m.get("value"), m.get("start")) for m in marcadores] == [("TESTE", "3602s")]


def test_fcpxml_lido_de_volta_da_a_cena(tmp_path):
    """O FCPXML lido pelo leitor de formatos devolve a cena da fixture."""
    pytest.importorskip("opentimelineio")
    sessao = _cena_com_marcador(tmp_path)
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)
    _modulo("destino").write_destination(sessao, destino, report)
    assert report.count("error") == 0

    lida = _modulo("otio_read").read_timeline(destino, Report(strict=False))

    assert lida.start_timecode_samples == INICIO
    assert lida.timecode_rate == TimecodeRate(25, 1)
    clips = list(lida.all_clips())
    assert [clip.timeline_start_samples for clip in clips] == [172800000, 172992000, 172848000]
    assert [clip.source_offset_samples for clip in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in lida.markers] == [("TESTE", MARCADOR)]


def test_clip_fora_do_frame_vai_com_fracao_exata(tmp_path):
    """Clip 100 samples fora do frame vira fracao exata, sem arredondar.

    O tempo da sessao comeca 1 sample fora do frame e o clip comeca 100
    samples depois; a fracao nao simplifica, o FCPXML guarda o denominador da
    taxa de samples e o leitor proprio do produto
    (`src/aafton/formats/fcpxml_read.py`) le o tempo de volta no mesmo
    sample, sem passar pelo adaptador do OpenTimelineIO.
    """
    sessao = _cena_com_marcador(tmp_path)
    sessao.start_timecode_samples = INICIO + 1
    clip = sessao.tracks[0].clips[0]
    clip.timeline_start_samples = INICIO + 101
    clip.timeline_end_samples = INICIO + 101 + 96000
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert report.count("error") == 0
    raiz = _raiz(destino)
    asset_clip = next(clip for clip in raiz.iter("asset-clip") if clip.get("start") == "36001s")
    assert asset_clip.get("offset") == f"{Fraction(172800101, TAXA)}s"
    assert asset_clip.get("offset") == "172800101/48000s"
    assert tempo_fcpxml(asset_clip.get("offset")) * TAXA == 172800101


def test_fade_e_erro_e_nao_deixa_arquivo(tmp_path):
    """Fade e erro; em modo estrito nada e gravado na pasta."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.tracks[0].clips[0].fade_in = FadeIR(kind="in", duration_samples=4800)
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_FCPXML_FEATURE_UNSUPPORTED" in _codigos(report)
    assert any("which the FCPXML does not carry" in issue.message for issue in report.issues)

    pasta = tmp_path / "estrito"
    pasta.mkdir()
    with pytest.raises(StrictModeError):
        _modulo("destino").write_destination(sessao, pasta / "cena12.fcpxml", Report(strict=True))
    assert list(pasta.iterdir()) == []


def test_drop_frame_29_97_escreve_o_frame_duration_exato(tmp_path):
    """A 29.97 drop frame da frameDuration 1001/30000s e o tcFormat DF."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.timecode_rate = TimecodeRate(30000, 1001, True)
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert report.count("error") == 0
    raiz = _raiz(destino)
    formato = raiz.find("./resources/format")
    assert formato.get("frameDuration") == "1001/30000s"
    assert _sequencia(raiz).get("tcFormat") == "DF"


def test_taxa_de_amostras_sem_nome_no_fcpxml_falha(tmp_path):
    """Uma taxa de audio que o FCPXML nao nomeia vira erro."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.sample_rate = 88200
    for asset in sessao.media:
        asset.sample_rate = 88200
    destino = tmp_path / "cena12.fcpxml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_FCPXML_SAMPLE_RATE_UNSUPPORTED" in _codigos(report)
    assert not destino.exists()


def test_escreve_sem_o_extra_formats(tmp_path, monkeypatch):
    """O FCPXML e escrito sem o extra formats, que nao e preciso."""
    sessao = _cena_com_marcador(tmp_path)
    destino = tmp_path / "cena12.fcpxml"

    def sem_extra():
        raise ExtraAusente("the formats extra is not installed")

    monkeypatch.setattr(aafton.formats, "carregar_otio", sem_extra)
    _modulo("fcpxml_write").write_fcpxml(sessao, destino, Report(strict=False))

    assert destino.exists()
    assert _raiz(destino).get("version") == "1.9"
