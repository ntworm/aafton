"""Testes da escrita de XML do FCP 7 a partir do SessionIR."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats import ExtraAusente
from aafton.ir import FadeIR, MarkerIR
from aafton.report import Report, StrictModeError
from tests.unit.test_edl_write import _cena, _clip

otio = pytest.importorskip("opentimelineio")

INICIO = 172800000
# Marcador da cena: 01:00:01:00 a 25 fps.
MARCADOR = 172896000


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


def _captura_timeline(monkeypatch) -> dict:
    """Guarda a timeline que o escritor entrega ao adaptador do OpenTimelineIO.

    O XML do FCP 7 nao carrega o nome das tracks, entao o nome so pode ser
    conferido na timeline, antes da traducao.
    """
    caixa: dict = {}
    original = otio.adapters.write_to_string

    def captura(timeline, adaptador, **kwargs):
        caixa["timeline"] = timeline
        return original(timeline, adaptador, **kwargs)

    monkeypatch.setattr(otio.adapters, "write_to_string", captura)
    return caixa


def test_xml_escreve_a_cena_e_lida_de_volta(tmp_path):
    """O XML escrito, lido pelo leitor de formatos, da a cena da fixture."""
    sessao = _cena_com_marcador(tmp_path)
    destino = tmp_path / "cena12.xml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert destino.exists()
    assert report.count("error") == 0

    lida = _modulo("otio_read").read_timeline(destino, Report(strict=False))

    assert lida.start_timecode_samples == INICIO
    clips = list(lida.all_clips())
    assert [clip.timeline_start_samples for clip in clips] == [172800000, 172992000, 172848000]
    assert [clip.timeline_end_samples for clip in clips] == [172896000, 173040000, 172944000]
    assert [clip.source_offset_samples for clip in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in lida.markers] == [("TESTE", MARCADOR)]
    midias = [lida.media_by_id(clip.media_id) for clip in clips]
    caminhos = [Path(m.declared_location).resolve() for m in midias]
    assert caminhos == [
        (tmp_path / "dial01.wav").resolve(),
        (tmp_path / "dial01.wav").resolve(),
        (tmp_path / "dial02.wav").resolve(),
    ]


def test_fade_e_erro_e_nao_deixa_arquivo(tmp_path):
    """Fade e erro; em modo estrito nada e gravado na pasta."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.tracks[0].clips[0].fade_in = FadeIR(kind="in", duration_samples=4800)
    destino = tmp_path / "cena12.xml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_XML_FEATURE_UNSUPPORTED" in _codigos(report)

    pasta = tmp_path / "estrito"
    pasta.mkdir()
    with pytest.raises(StrictModeError):
        _modulo("destino").write_destination(sessao, pasta / "cena12.xml", Report(strict=True))
    assert list(pasta.iterdir()) == []


def test_extra_ausente_e_erro_e_nao_deixa_arquivo(tmp_path, monkeypatch):
    """Sem o extra formats o XML nao e escrito e o erro fica no relatorio."""
    sessao = _cena_com_marcador(tmp_path)
    destino = tmp_path / "cena12.xml"
    report = Report(strict=False)

    def sem_extra():
        raise ExtraAusente("the formats extra is not installed")

    monkeypatch.setattr(_modulo("otio_write"), "carregar_otio", sem_extra)
    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_EXTRA_MISSING" in _codigos(report)
    assert not destino.exists()


def test_clips_sobrepostos_viram_erro(tmp_path):
    """Clip que comeca antes do fim do anterior fica de fora e vira erro."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.tracks[1].clips.append(_clip("t1c1", "t1", "dial02.wav", "m1", 172896000, 172944000, 0))
    destino = tmp_path / "cena12.xml"
    report = Report(strict=False)

    _modulo("destino").write_destination(sessao, destino, report)

    assert "FORMAT_XML_OVERLAPPING_CLIPS" in _codigos(report)

    lida = _modulo("otio_read").read_timeline(destino, Report(strict=False))
    clips = list(lida.all_clips())
    assert [clip.timeline_start_samples for clip in clips] == [172800000, 172992000, 172848000]


def test_track_de_dois_canais_da_duas_tracks_de_audio(tmp_path, monkeypatch):
    """Track de dois canais vira duas tracks de audio, com o nome indexado."""
    sessao = _cena_com_marcador(tmp_path)
    sessao.tracks[0].channel_count = 2
    sessao.tracks[0].clips = [sessao.tracks[0].clips[0]]
    sessao.tracks = [sessao.tracks[0]]
    destino = tmp_path / "cena12.xml"
    report = Report(strict=False)
    caixa = _captura_timeline(monkeypatch)

    _modulo("destino").write_destination(sessao, destino, report)

    assert report.count("error") == 0
    assert [track.name for track in caixa["timeline"].tracks] == ["1 1", "1 2"]

    lida = _modulo("otio_read").read_timeline(destino, Report(strict=False))
    assert len(lida.tracks) == 2
    assert [track.kind for track in lida.tracks] == ["audio", "audio"]
