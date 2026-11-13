"""Testes do timecode de drop frame no XML do FCP 7.

Numa sessao de drop frame, o timecode de cada arquivo de midia tambem sai
como drop frame: o rotulo do arquivo sai do frame count dele, com ponto e
virgula. Numa sessao de 25 fps nada muda.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.formats.otio_read import read_timeline
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import label_to_frames
from tests.unit.test_edl_write import _cena, _wav
from tests.unit.test_melhorias_xml import DF, NDF, TAXA, _escrever, _posicoes, _samples

pytest.importorskip("opentimelineio")

INICIO_DF = "01:00:00;00"
ARQUIVO_DF = "10:00:00;00"
ARQUIVO_NDF = "10:00:00:00"


def _sessao_df(tmp_path: Path) -> SessionIR:
    """Sessao de drop frame com um arquivo de midia que comeca em 10:00:00;00."""
    arquivo = tmp_path / "dial01.wav"
    _wav(arquivo, 20, _samples(label_to_frames(ARQUIVO_DF, DF), DF, TAXA))
    asset = MediaAsset(
        id="m0",
        filename="dial01.wav",
        channels=1,
        sample_rate=TAXA,
        duration_samples=TAXA * 20,
        status="resolved",
        resolved_path=str(arquivo),
    )
    inicio = label_to_frames(INICIO_DF, DF)
    clip = ClipIR(
        id="t0c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="dial01.wav",
        media_id="m0",
        timeline_start_samples=_samples(inicio, DF, TAXA),
        timeline_end_samples=_samples(inicio + 30, DF, TAXA),
        source_offset_samples=0,
    )
    track = TrackIR(id="t0", source_index=0, name="1", kind="audio", channel_count=1, clips=[clip])
    return SessionIR(
        name="CENA",
        sample_rate=TAXA,
        timecode_rate=DF,
        start_timecode_samples=_samples(inicio, DF, TAXA),
        tracks=[track],
        media=[asset],
    )


def _timecode_do_arquivo(destino: Path) -> ET.Element:
    """O timecode do primeiro arquivo de midia do XML escrito."""
    raiz = ET.fromstring(destino.read_bytes())
    arquivo = next(raiz.iter("file"))
    timecode = arquivo.find("timecode")
    assert timecode is not None
    return timecode


def test_xml_de_sessao_drop_frame_marca_o_timecode_do_arquivo(tmp_path):
    """Numa sessao de drop frame, o timecode do arquivo sai em DF."""
    sessao = _sessao_df(tmp_path)
    destino = tmp_path / "df.xml"

    report = _escrever(sessao, destino)

    assert report.count("error") == 0
    timecode = _timecode_do_arquivo(destino)
    assert timecode.findtext("displayformat") == "DF"
    assert timecode.findtext("string") == ARQUIVO_DF
    assert timecode.findtext("frame") == str(label_to_frames(ARQUIVO_DF, DF))


def test_xml_de_sessao_drop_frame_volta_com_as_posicoes(tmp_path):
    """O XML de drop frame volta com as posicoes e os offsets de origem."""
    sessao = _sessao_df(tmp_path)
    destino = tmp_path / "df.xml"

    assert _escrever(sessao, destino).count("error") == 0

    lida = read_timeline(destino, Report(strict=False))
    assert lida.timecode_rate == DF
    assert lida.start_timecode_samples == sessao.start_timecode_samples
    assert _posicoes(lida) == _posicoes(sessao)
    assert [clip.source_offset_samples for clip in lida.all_clips()] == [
        clip.source_offset_samples for clip in sessao.all_clips()
    ]


def test_xml_de_sessao_sem_drop_frame_mantem_o_timecode_do_arquivo(tmp_path):
    """Numa sessao de 25 fps, o timecode do arquivo continua como estava."""
    sessao = _cena(tmp_path)
    destino = tmp_path / "cena12.xml"

    assert _escrever(sessao, destino).count("error") == 0

    timecode = _timecode_do_arquivo(destino)
    assert timecode.findtext("displayformat") == "NDF"
    assert timecode.findtext("string") == ARQUIVO_NDF
    assert timecode.findtext("frame") == str(label_to_frames(ARQUIVO_NDF, NDF))
