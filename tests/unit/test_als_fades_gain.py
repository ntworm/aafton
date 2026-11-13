from pathlib import Path
import pytest

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

UMA_HORA = 172800000


def _build_test_als(tmp_path, fade_enabled=True, fade_in_len=1.0, fade_out_len=0.5, volume=0.8):
    wav = tmp_path / "audio.wav"
    if not wav.exists():
        wav.write_bytes(
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

    asset = MediaAsset(
        id="m1",
        filename="audio.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="audio.wav",
            media_id="m1",
            timeline_start_samples=UMA_HORA,
            timeline_end_samples=UMA_HORA + 96000,
            source_offset_samples=0,
        )
    )
    session = SessionIR(
        name="test_fades",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track],
        media=[asset],
    )
    destino = tmp_path / "session.als"
    report = Report()
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        report,
    )

    # Modifica o XML do .als para injetar os valores de Fades e SampleVolume desejados
    root = load_als(destino)
    clip_node = root.find(".//AudioClip")
    assert clip_node is not None

    # Ajusta ou cria <Fade Value="..." />
    fade_node = clip_node.find("Fade")
    if fade_node is None:
        import xml.etree.ElementTree as ET

        fade_node = ET.SubElement(clip_node, "Fade")
    fade_node.set("Value", "true" if fade_enabled else "false")

    # Ajusta Fades/FadeInLength e Fades/FadeOutLength
    fades_node = clip_node.find("Fades")
    if fades_node is None:
        import xml.etree.ElementTree as ET

        fades_node = ET.SubElement(clip_node, "Fades")

    fin = fades_node.find("FadeInLength")
    if fin is None:
        import xml.etree.ElementTree as ET

        fin = ET.SubElement(fades_node, "FadeInLength")
    fin.set("Value", str(fade_in_len))

    fout = fades_node.find("FadeOutLength")
    if fout is None:
        import xml.etree.ElementTree as ET

        fout = ET.SubElement(fades_node, "FadeOutLength")
    fout.set("Value", str(fade_out_len))

    # Ajusta SampleVolume
    sv = clip_node.find("SampleVolume")
    if sv is None:
        import xml.etree.ElementTree as ET

        sv = ET.SubElement(clip_node, "SampleVolume")
    sv.set("Value", str(volume))

    save_als(root, destino)
    return destino


def test_read_session_extracts_fades_and_gain(tmp_path):
    # A 60 BPM e 48000 Hz, 1 beat = 48000 samples, 0.5 beat = 24000 samples
    als_path = _build_test_als(
        tmp_path,
        fade_enabled=True,
        fade_in_len=1.0,
        fade_out_len=0.5,
        volume=0.75,
    )

    report = Report()
    session = read_session(als_path, report)
    assert len(session.tracks) == 1
    assert len(session.tracks[0].clips) == 1

    clip = session.tracks[0].clips[0]
    assert clip.fade_in is not None
    assert clip.fade_in.kind == "in"
    assert clip.fade_in.duration_samples == 48000

    assert clip.fade_out is not None
    assert clip.fade_out.kind == "out"
    assert clip.fade_out.duration_samples == 24000

    assert clip.gain_linear == pytest.approx(0.75)


def test_read_session_fades_disabled_yields_none(tmp_path):
    als_path = _build_test_als(
        tmp_path,
        fade_enabled=False,
        fade_in_len=1.0,
        fade_out_len=0.5,
        volume=1.0,
    )

    report = Report()
    session = read_session(als_path, report)
    clip = session.tracks[0].clips[0]

    assert clip.fade_in is None
    assert clip.fade_out is None
    assert clip.gain_linear == pytest.approx(1.0)


def test_read_session_zero_length_fades_yield_none(tmp_path):
    als_path = _build_test_als(
        tmp_path,
        fade_enabled=True,
        fade_in_len=0.0,
        fade_out_len=0.0,
        volume=1.2,
    )

    report = Report()
    session = read_session(als_path, report)
    clip = session.tracks[0].clips[0]

    assert clip.fade_in is None
    assert clip.fade_out is None
    assert clip.gain_linear == pytest.approx(1.2)
