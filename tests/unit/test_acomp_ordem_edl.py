"""Testes da ordem das tracks de audio de um EDL relido.

O adaptador do OpenTimelineIO cria uma track por canal, na ordem do primeiro
evento de cada uma: uma EDL com os canais A, A3 e A2 chega com as tracks fora
de ordem, e um `.als` reimportado dela troca as tracks. O leitor do produto
ordena as tracks de audio pelo numero do canal, sem mexer nas posicoes, nas
duracoes e nas origens dos eventos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats import otio_read
from aafton.report import Report
from aafton.timecode import TimecodeRate

TAXAS = TimecodeRate(25, 1)
TAXA = 48000

# A EDL do caso lcut_conectado: os canais A, A3 e A2, com o primeiro evento do
# canal A3 antes do primeiro evento do canal A2.
LCUT = """TITLE: LCUT
FCM: NON-DROP FRAME

001  DIALA    A     C        00:00:00:00 00:00:12:00 01:00:00:00 01:00:12:00
* FROM CLIP NAME: dialA.wav
* SOURCE FILE: dialA.wav

002  MUSICA   A3    C        00:00:05:00 00:00:08:00 01:00:01:00 01:00:04:00
* FROM CLIP NAME: musica.wav
* SOURCE FILE: musica.wav

003  DIALB    A2    C        00:00:20:00 00:00:32:00 01:00:10:00 01:00:22:00
* FROM CLIP NAME: dialB.wav
* SOURCE FILE: dialB.wav
"""
# A EDL com o canal A10, que vem antes do canal A no arquivo.
DEZ = """TITLE: DEZ
FCM: NON-DROP FRAME

001  DIALJ    A10   C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: dialJ.wav
* SOURCE FILE: dialJ.wav

002  DIALA    A     C        00:00:00:00 00:00:02:00 01:00:00:00 01:00:02:00
* FROM CLIP NAME: dialA.wav
* SOURCE FILE: dialA.wav
"""


def _gravar(tmp_path: Path, texto: str) -> Path:
    """Grava a EDL de teste e devolve o caminho."""
    caminho = tmp_path / "cena.edl"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def _ler(caminho: Path):
    """Le a EDL e devolve a cena lida."""
    return otio_read.read_timeline(caminho, Report(strict=False), edl_rate=TAXAS)


def test_edl_relido_lista_as_tracks_na_ordem_do_canal(tmp_path):
    """A EDL com os canais A, A3 e A2 volta com as tracks na ordem do canal.

    A ordem das tracks nao mexe nos eventos: as posicoes, as duracoes e as
    origens dos clips sao as do arquivo.
    """
    caminho = _gravar(tmp_path, LCUT)

    sessao = _ler(caminho)

    assert [t.name for t in sessao.tracks] == ["A1", "A2", "A3"]
    assert [[c.name for c in t.clips] for t in sessao.tracks] == [
        ["dialA.wav"],
        ["dialB.wav"],
        ["musica.wav"],
    ]
    clips = list(sessao.all_clips())
    assert [(c.name, c.timeline_start_samples, c.timeline_end_samples) for c in clips] == [
        ("dialA.wav", 172800000, 173376000),
        ("dialB.wav", 173280000, 173856000),
        ("musica.wav", 172848000, 172992000),
    ]
    assert [c.raw["source_tc_samples"] for c in clips] == [0, 20 * TAXA, 5 * TAXA]


def test_edl_com_canal_dez_ordena_pelo_numero(tmp_path):
    """O canal A10 vem depois do canal A, e nao antes dele."""
    caminho = _gravar(tmp_path, DEZ)

    sessao = _ler(caminho)

    assert [t.name for t in sessao.tracks] == ["A1", "A10"]
    assert [[c.name for c in t.clips] for t in sessao.tracks] == [
        ["dialA.wav"],
        ["dialJ.wav"],
    ]


def test_xml_keeps_a_ordem_das_tracks(monkeypatch, tmp_path):
    """O XML do FCP nao muda a ordem das tracks: so a EDL e reordenada."""
    otio = pytest.importorskip("opentimelineio")
    timeline = otio.schema.Timeline(name="CENA")
    for nome in ("A2", "A1"):
        timeline.tracks.append(
            otio.schema.Track(name=nome, kind=otio.schema.TrackKind.Audio),
        )
    monkeypatch.setattr(otio_read, "_ler_timeline", lambda *args: timeline)
    caminho = tmp_path / "cena.xml"
    caminho.write_text(
        "<sequence><rate><timebase>25</timebase></rate></sequence>",
        encoding="utf-8",
    )

    sessao = otio_read.read_timeline(caminho, Report(strict=False))

    assert [t.name for t in sessao.tracks] == ["A2", "A1"]
