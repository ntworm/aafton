"""Testes das correcoes do XML do FCP 7: drop frame e taxa de amostras.

O adaptador do OpenTimelineIO escreve o timecode da sequencia como nao drop
frame e nao escreve a taxa de amostras da sessao. O escritor corrige os dois
depois de escrever o XML, e o leitor tem de devolver a mesma cena.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats.otio_read import read_timeline
from aafton.formats.otio_write import write_fcp_xml
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate, frames_to_samples_exact, label_to_frames
from tests.unit.test_edl_write import _cena

pytest.importorskip("opentimelineio")

DF = TimecodeRate(30000, 1001, True)
NDF = TimecodeRate(25, 1)
INICIO_DF = "01:00:00;00"
TAXA = 48000


def _samples(frame: int, rate: TimecodeRate, sample_rate: int) -> int:
    """Posicao em samples de um frame, arredondada."""
    return round(frames_to_samples_exact(frame, rate, sample_rate))


def _sessao(rate: TimecodeRate, sample_rate: int, inicio_frame: int) -> SessionIR:
    """Sessao minima: uma track de audio com um clip, no frame de inicio dado."""
    asset = MediaAsset(
        id="m0",
        filename="dial01.wav",
        channels=1,
        sample_rate=sample_rate,
        duration_samples=sample_rate * 10,
        status="resolved",
    )
    clip = ClipIR(
        id="t0c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="dial01.wav",
        media_id="m0",
        timeline_start_samples=_samples(inicio_frame, rate, sample_rate),
        timeline_end_samples=_samples(inicio_frame + 30, rate, sample_rate),
        source_offset_samples=0,
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        name="1",
        kind="audio",
        channel_count=1,
        clips=[clip],
    )
    return SessionIR(
        name="CENA",
        sample_rate=sample_rate,
        timecode_rate=rate,
        start_timecode_samples=_samples(inicio_frame, rate, sample_rate),
        tracks=[track],
        media=[asset],
    )


def _escrever(sessao: SessionIR, destino: Path) -> Report:
    """Escreve a sessao em XML do FCP e devolve o relatorio."""
    report = Report(strict=False)
    write_fcp_xml(sessao, destino, report)
    return report


def _posicoes(sessao: SessionIR) -> list[tuple[int, int]]:
    """Inicio e fim de cada clip da sessao, na ordem."""
    return [(clip.timeline_start_samples, clip.timeline_end_samples) for clip in sessao.all_clips()]


def test_xml_de_sessao_drop_frame_le_o_timecode_certao(tmp_path):
    """A sessao de drop frame volta do XML como drop frame, no mesmo inicio."""
    inicio = label_to_frames(INICIO_DF, DF)
    sessao = _sessao(DF, TAXA, inicio)
    destino = tmp_path / "df.xml"

    report = _escrever(sessao, destino)

    assert report.count("error") == 0
    texto = destino.read_text(encoding="utf-8")
    assert texto.startswith("<?xml")
    assert "<!DOCTYPE xmeml>" in texto
    lida = read_timeline(destino, Report(strict=False))
    assert lida.timecode_rate == DF
    assert lida.start_timecode_samples == sessao.start_timecode_samples
    assert _posicoes(lida) == _posicoes(sessao)


def test_xml_guarda_a_taxa_de_amostras_da_sessao(tmp_path):
    """A taxa de amostras da sessao volta do XML, mesmo fora de 48 kHz."""
    inicio = label_to_frames(INICIO_DF, DF)
    sessao = _sessao(DF, 96000, inicio)
    destino = tmp_path / "df96.xml"

    report = _escrever(sessao, destino)

    assert report.count("error") == 0
    lida = read_timeline(destino, Report(strict=False))
    assert lida.sample_rate == 96000


def test_xml_de_cena_de_25_fps_continua_igual(tmp_path):
    """A cena de 25 fps nao drop frame volta do XML como estava."""
    sessao = _cena(tmp_path)
    destino = tmp_path / "cena12.xml"

    report = _escrever(sessao, destino)

    assert report.count("error") == 0
    lida = read_timeline(destino, Report(strict=False))
    assert lida.timecode_rate == NDF
    assert lida.sample_rate == TAXA
    assert lida.start_timecode_samples == sessao.start_timecode_samples
    assert _posicoes(lida) == _posicoes(sessao)
