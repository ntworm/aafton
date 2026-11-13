"""D12b: forma estéreo medida no AAF multicanal do Pro Tools 26.4.1.

Perfil externo guarda apenas propriedades estruturais, sem áudio. A prova de
importação no Pro Tools permanece no portão humano da calibração.
"""

import json
import struct
import wave
from pathlib import Path
from urllib.parse import unquote, urlparse

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.audio_split import split_stereo_source
from aafton.media_extract import read_audio_metadata
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_calibration_aaf

PROFILE = Path(__file__).parents[1] / "fixtures/aaf/protools_stereo_profile.json"


@pytest.mark.parametrize("writer", ["produto", "calibracao"])
def test_estereo_preserva_forma_protools_e_amostras(tmp_path, writer):
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    wav = tmp_path / "stereo.wav"
    frames, start, offset, duration = 4096, 7, 617, 2048
    payload = b"".join(struct.pack("<hh", i % 997, -(i % 991)) for i in range(frames))
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(payload)
    aaf = tmp_path / "stereo.aaf"
    if writer == "calibracao":
        build_calibration_aaf(
            aaf,
            [
                {
                    "name": "ESTEREO",
                    "clips": [
                        {"path": str(wav), "start": start, "offset": offset, "duration": duration}
                    ],
                }
            ],
            frames=frames,
            start_timecode_frames=0,
        )
    else:
        media = MediaAsset(
            id="m",
            filename=wav.name,
            channels=2,
            sample_rate=48000,
            duration_samples=frames,
            resolved_path=str(wav),
            status="resolved",
        )
        clip = ClipIR(
            id="c",
            track_id="t",
            channel_index=0,
            event_index=0,
            name="estereo",
            media_id="m",
            timeline_start_samples=start,
            timeline_end_samples=start + duration,
            source_offset_samples=offset,
        )
        session = SessionIR(
            name="estereo",
            sample_rate=48000,
            timecode_rate=TimecodeRate(25, 1),
            start_timecode_samples=0,
            media=[media],
            tracks=[
                TrackIR(
                    id="t",
                    source_index=0,
                    name="ESTEREO",
                    kind="audio",
                    channel_count=2,
                    clips=[clip],
                )
            ],
        )
        write_aaf(session, aaf, Report(strict=False))

    with aaf2.open(str(aaf), "r") as f:
        slot = next(
            s for s in next(f.content.compositionmobs()).slots if s.segment.media_kind == "Sound"
        )
        assert {v.name: v.value for v in slot["TimelineMobAttributeList"].value} == profile[
            "slot_attributes"
        ]
        assert slot.segment.length == (frames if writer == "calibracao" else start + duration)
        op = next(c for c in slot.segment.components if type(c).__name__ == "OperationGroup")
        assert str(op.operation.auid) == profile["operation_auid"]
        assert {v.name: v.value for v in op["ComponentAttributeList"].value} == profile[
            "component_attributes"
        ]
        for name, value in profile["operation_properties"].items():
            assert op.operation[name].value == value
        assert {p.parameterdef.name: p.value for p in op.parameters} == profile["parameters"]
        assert len({str(sc.mob.mob_id) for sc in op.segments}) == 2
        source_ids = []
        for channel, sc in enumerate(op.segments):
            assert (sc.start, sc.length) == (offset, duration)
            master_slot = sc.mob.slot_at(sc.slot_id)
            assert type(master_slot.segment).__name__ == profile["master_segment_type"]
            assert master_slot.segment.length == frames
            source_slot = master_slot.segment.mob.slot_at(master_slot.segment.slot_id)
            assert type(source_slot.segment).__name__ == profile["source_segment_type"]
            assert source_slot.segment.length == frames
            source_mob = master_slot.segment.mob
            source_ids.append(str(source_mob.mob_id))
            summary = bytes(source_mob.descriptor["Summary"].value)
            fmt = summary.index(b"fmt ") + 8
            assert struct.unpack_from("<H", summary, fmt + 2)[0] == profile["source_channels"]
            locator = source_mob.descriptor["Locator"].value[0]["URLString"].value
            parsed = urlparse(locator)
            mono = (
                Path(unquote(parsed.path).lstrip("/")) if parsed.scheme == "file" else Path(locator)
            )
            with wave.open(str(mono), "rb") as w:
                assert (w.getnchannels(), w.getframerate(), w.getnframes(), w.getsampwidth()) == (
                    1,
                    48000,
                    frames,
                    2,
                )
                expected = b"".join(
                    payload[i + channel * 2 : i + channel * 2 + 2]
                    for i in range(0, len(payload), 4)
                )
                assert w.readframes(frames) == expected
        assert len(set(source_ids)) == 2
    # Desentrelaçar só separa bytes; fonte original permanece intacta.
    with wave.open(str(wav), "rb") as w:
        assert w.readframes(frames) == payload
    read = read_aaf(aaf, Report(strict=False), fades="keep")
    assert read.tracks[0].channel_count == 2
    assert [
        (c.channel_index, c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in read.tracks[0].clips
    ] == [(0, start, start + duration, offset), (1, start, start + duration, offset)]


@pytest.mark.parametrize(
    "bits,tag,container",
    [
        (8, 1, "wav"),
        (16, 1, "wav"),
        (24, 1, "wav"),
        (32, 1, "wav"),
        (32, 3, "wav"),
        (24, 0xFFFE, "wav"),
        (24, 1, "aiff"),
    ],
)
def test_separacao_preserva_bytes_formato_e_canais(tmp_path, bits, tag, container):
    width, frames = bits // 8, 17
    left = [bytes((i * 19 + b) % 256 for b in range(width)) for i in range(frames)]
    right = [bytes((255 - i * 13 - b) % 256 for b in range(width)) for i in range(frames)]
    source = tmp_path / "audio.dat"  # magic bytes, não extensão
    if container == "wav":
        fmt = struct.pack("<HHIIHH", tag, 2, 48000, 48000 * 2 * width, 2 * width, bits)
        if tag == 0xFFFE:
            fmt += struct.pack("<HHI", 22, bits, 3) + bytes.fromhex(
                "0100000000001000800000aa00389b71"
            )
        pcm = b"".join(item + r for item, r in zip(left, right))
        body = (
            b"WAVEfmt "
            + struct.pack("<I", len(fmt))
            + fmt
            + b"data"
            + struct.pack("<I", len(pcm))
            + pcm
        )
        source.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    else:
        pcm = b"".join(item[::-1] + r[::-1] for item, r in zip(left, right))
        comm = struct.pack(">HIH", 2, frames, bits) + bytes.fromhex("400ebb80000000000000")
        body = (
            b"AIFFCOMM"
            + struct.pack(">I", len(comm))
            + comm
            + b"SSND"
            + struct.pack(">III", len(pcm) + 8, 0, 0)
            + pcm
        )
        source.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    original = source.read_bytes()
    outputs = split_stereo_source(source, tmp_path / "media")
    for output, expected in zip(outputs, (left, right)):
        meta = read_audio_metadata(output)
        assert (meta["channels"], meta["bits"], meta["sample_rate"], meta["duration_samples"]) == (
            1,
            bits,
            48000,
            frames,
        )
        assert struct.unpack_from("<H", meta["fmt_payload"])[0] == (
            tag if container == "wav" else 1
        )
        with output.open("rb") as f:
            f.seek(meta["data_offset"])
            assert f.read(meta["data_size"]) == b"".join(expected)
    assert source.read_bytes() == original
    assert split_stereo_source(source, tmp_path / "media") == outputs
    # O retorno dos dois mono ao Live também mantém tag e amostras.
    from aafton.stereo import _interleave_wav

    joined = tmp_path / "joined.wav"
    _interleave_wav(*outputs, joined, *(read_audio_metadata(p) for p in outputs))
    meta = read_audio_metadata(joined)
    assert struct.unpack_from("<H", meta["fmt_payload"])[0] == (tag if container == "wav" else 1)
    with joined.open("rb") as f:
        f.seek(meta["data_offset"])
        assert f.read(meta["data_size"]) == b"".join(item + r for item, r in zip(left, right))
    # Alterar a fonte mantém os arquivos anteriores e gera novos hashes.
    source.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    assert split_stereo_source(source, tmp_path / "media") != outputs
    assert all(p.exists() for p in outputs)
    outputs[0].write_bytes(b"arquivo existente divergente")
    source.write_bytes(original)
    with pytest.raises(ValueError, match="existing mono source differs"):
        split_stereo_source(source, tmp_path / "media")
    assert outputs[0].read_bytes() == b"arquivo existente divergente"


def test_cli_export_estereo_preserva_fontes_no_roundtrip(tmp_path):
    from aafton.cli import main
    from aafton.als_write import write_session, WriteOptions

    wav = tmp_path / "stereo.wav"
    payload = b"".join(struct.pack("<hh", i * 3, -i * 7) for i in range(1024))
    with wave.open(str(wav), "wb") as w:
        w.setparams((2, 2, 48000, 1024, "NONE", "not compressed"))
        w.writeframes(payload)
    media = MediaAsset(
        id="m",
        filename=wav.name,
        channels=2,
        sample_rate=48000,
        duration_samples=1024,
        resolved_path=str(wav),
        status="resolved",
    )
    clip = ClipIR(
        id="c",
        track_id="t",
        channel_index=0,
        event_index=0,
        name="c",
        media_id="m",
        timeline_start_samples=7,
        timeline_end_samples=519,
        source_offset_samples=99,
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        media=[media],
        tracks=[
            TrackIR(id="t", source_index=0, name="S", kind="audio", channel_count=2, clips=[clip])
        ],
    )
    als, aaf = tmp_path / "s.als", tmp_path / "s.aaf"
    write_session(
        session,
        Path("templates/live12.als"),
        als,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=0, stereo="pair"),
        Report(strict=False),
    )
    assert main(["export", str(als), str(aaf)]) == 0
    reread = read_aaf(aaf, Report(strict=False), fades="keep")
    assert [
        (c.channel_index, c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in reread.tracks[0].clips
    ] == [(0, 7, 519, 99), (1, 7, 519, 99)]
    assert len({c.media_id for c in reread.tracks[0].clips}) == 2
    imported = tmp_path / "imported.als"
    assert (
        main(
            [
                "import",
                str(aaf),
                str(imported),
                "--audio-dir",
                str(tmp_path / "s Media"),
                "--fades",
                "keep",
                "--stereo",
                "pair",
                "--live-origin",
                "00:00:00:00",
            ]
        )
        == 0
    )
    import gzip
    import xml.etree.ElementTree as ET

    tree = ET.fromstring(gzip.decompress(imported.read_bytes()))
    assert len(tree.findall(".//Tracks/AudioTrack//AudioClip")) == 1
    # Nova exportação do IR com fontes mono distintas mantém o canal direito.
    for asset in reread.media:
        from aafton.media_extract import resolve_asset

        resolve_asset(asset, [tmp_path / "s Media"], Report(strict=True))
    second = tmp_path / "second.aaf"
    write_aaf(reread, second, Report(strict=True))
    second_read = read_aaf(second, Report(strict=False), fades="keep")
    assert [Path(m.filename).name for m in second_read.media] == [
        Path(m.filename).name for m in reread.media
    ]
    assert wav.read_bytes()[44:] == payload


def test_fonte_estereo_ausente_preserva_aaf_preexistente(tmp_path):
    from aafton.report import StrictModeError

    media = MediaAsset(
        id="m",
        filename="ausente.wav",
        channels=2,
        sample_rate=48000,
        duration_samples=1024,
        resolved_path=str(tmp_path / "ausente.wav"),
        status="resolved",
    )
    clip = ClipIR(
        id="c",
        track_id="t",
        channel_index=0,
        event_index=0,
        name="c",
        media_id="m",
        timeline_start_samples=0,
        timeline_end_samples=512,
        source_offset_samples=0,
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        media=[media],
        tracks=[
            TrackIR(id="t", source_index=0, name="S", kind="audio", channel_count=2, clips=[clip])
        ],
    )
    aaf = tmp_path / "existing.aaf"
    aaf.write_bytes(b"original")
    with pytest.raises(StrictModeError, match="WRITE_STEREO_SOURCE"):
        write_aaf(session, aaf, Report(strict=True))
    assert aaf.read_bytes() == b"original"
    report = Report(strict=False)
    write_aaf(session, aaf, report)
    assert report.incomplete and aaf.read_bytes() == b"original"
