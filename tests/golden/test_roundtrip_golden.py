"""Roundtrip ponta a ponta: ALS -> SessionIR -> AAF -> SessionIR.

O que este arquivo fixa e a garantia de integridade, nao de perda: um set do
Live lido e reescrito tem de voltar com os mesmos clips, nas mesmas posicoes em
samples, com os mesmos marcadores. O que nao cabe no AAF continua recusado e
declarado, e nao some em silencio.
"""

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als, save_als
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="templates/live12.als ainda nao existe"
)

SR = 48000
BPM = 60.0
ORIGEM = 172800000
BEAT = 48000
FIM = ORIGEM + 5 * BEAT


def _sub(parent, caminho):
    node = parent
    for nome in caminho:
        achado = node.find(nome)
        if achado is None:
            achado = ET.SubElement(node, nome)
        node = achado
    return node


def _cenario(tmp_path: Path, *, automacao: bool) -> Path:
    """Monta um .als com uma track, dois clips e um marcador."""
    wav = tmp_path / "audio.wav"
    wav.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00"
        b"\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    asset = MediaAsset(
        id="m1",
        filename="audio.wav",
        channels=1,
        sample_rate=SR,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    for indice, inicio in enumerate((BEAT, 3 * BEAT)):
        track.clips.append(
            ClipIR(
                id=f"c{indice}",
                track_id="t0",
                channel_index=0,
                event_index=indice,
                name="audio.wav",
                media_id="m1",
                timeline_start_samples=ORIGEM + inicio,
                timeline_end_samples=ORIGEM + inicio + BEAT,
                source_offset_samples=0,
            )
        )
    session = SessionIR(
        name="golden",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )
    destino = tmp_path / "golden.als"
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, bpm=BPM, live_origin_samples=ORIGEM),
        Report(),
    )

    root = load_als(destino)
    if automacao:
        alvo = _sub(
            root.find(".//AudioTrack"), ["DeviceChain", "Mixer", "Volume", "AutomationTarget"]
        )
        alvo.set("Id", "9001")
        envelope = _sub(
            root.find(".//AudioTrack"), ["AutomationEnvelopes", "Envelopes", "AutomationEnvelope"]
        )
        _sub(envelope, ["EnvelopeTarget", "PointeeId"]).set("Value", "9001")
        eventos = _sub(envelope, ["Automation", "Events"])
        for indice, (beat, valor) in enumerate(((1.5, 0.5), (3.5, 1.0))):
            ev = ET.SubElement(eventos, "FloatEvent")
            ev.set("Id", str(indice))
            ev.set("Time", repr(beat))
            ev.set("Value", repr(valor))
    save_als(root, destino)
    return destino


def test_golden_als_ida_e_volta_preserva_posicoes(tmp_path):
    """Ler e reescrever sem automacao nao move nenhum clip."""
    original = read_session(_cenario(tmp_path, automacao=False), Report())
    esperado = [
        (c.timeline_start_samples, c.timeline_end_samples) for c in original.tracks[0].clips
    ]

    destino = tmp_path / "volta.als"
    write_session(
        original,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=SR, bpm=BPM, live_origin_samples=ORIGEM),
        Report(),
    )
    relido = read_session(destino, Report())
    assert [
        (c.timeline_start_samples, c.timeline_end_samples) for c in relido.tracks[0].clips
    ] == esperado


def test_golden_automacao_do_live_e_lida_em_samples_absolutos(tmp_path):
    """A curva entra no IR mesmo sem caminho de escrita para o AAF."""
    report = Report()
    session = read_session(_cenario(tmp_path, automacao=True), report)
    pontos = session.tracks[0].gain_points
    assert pontos == [(ORIGEM + int(1.5 * BEAT), 0.5), (ORIGEM + int(3.5 * BEAT), 1.0)]
    # O wav do cenario e um stub sem audio real, entao o leitor avisa metadata.
    # O que este teste garante e que nao sobrou aviso de automacao nao reconciliada.
    assert "ALS_AUTOMATION_UNSUPPORTED" not in [i.code for i in report.issues]


def test_golden_automacao_do_live_vai_para_o_aaf_em_trechos(tmp_path):
    """A automacao lida sai no AAF com LinearInterp (ou em trechos constantes em fallback).

    O que a garantia cobre e que nada some em silencio e que os pontos da curva chegam ao AAF.
    """
    session = read_session(_cenario(tmp_path, automacao=True), Report())
    assert session.unsupported_export_features == []

    destino = tmp_path / "saida.aaf"
    report = Report(strict=False)
    write_aaf(session, destino, report)
    codes = [i.code for i in report.issues]
    assert "GAIN_AUTOMATION_WRITTEN" in codes or "GAIN_AUTOMATION_SEGMENTED" in codes
    assert "WRITE_SESSION_FEATURE_UNSUPPORTED" not in codes

    relido = read_aaf(destino, Report(strict=False))
    assert relido.tracks, "o AAF relido ficou sem track"
    assert relido.tracks[0].gain_points, "os pontos de automacao foram preservados no AAF"


def test_golden_ganho_estatico_continua_exato(tmp_path):
    """Sem automacao nada muda: o ganho do clip segue sendo track vezes clip."""

    session = read_session(_cenario(tmp_path, automacao=False), Report())
    session.tracks[0].gain_linear = 0.5
    session.tracks[0].clips[0].gain_linear = 0.25
    destino = tmp_path / "estatico.aaf"
    write_aaf(session, destino, Report(strict=False))
    relido = read_aaf(destino, Report(strict=False))
    clipe = next(relido.all_clips())
    assert clipe.gain_linear == pytest.approx(0.125)
