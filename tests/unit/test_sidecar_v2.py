import json
from pathlib import Path

import pytest
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import (
    SIDECAR_FORMAT_VERSION,
    Anchor,
    read_anchor,
    sidecar_path_for,
    write_sidecar,
)
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")
UMA_HORA = 172800000


def test_write_sidecar_v2_format_and_track_bindings(tmp_path):
    destino = tmp_path / "teste_v2.als"
    bindings = [
        {
            "index": 0,
            "track_name": "DX_01",
            "origin_slot_id": 1,
            "mob_id": "urn:smpte:umid:060a2b34010101010101010013-000000-00000000",
            "channel_count": 1,
        },
        {
            "index": 1,
            "track_name": "FX_STEREO",
            "origin_slot_id": [2, 3],
            "mob_id": "urn:smpte:umid:060a2b34010101010101010013-000000-11111111",
            "channel_count": 2,
        },
    ]
    hashes = {"audio1.wav": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}

    anchor = Anchor(
        live_origin_samples=UMA_HORA,
        session_start_samples=UMA_HORA,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        bpm=60.0,
        format_version=2,
        track_bindings=bindings,
        media_hashes=hashes,
    )
    write_sidecar(destino, anchor)

    sidecar_file = sidecar_path_for(destino)
    assert sidecar_file.exists()

    # O arquivo sai na versao que o codigo grava agora, mesmo com a ancora lida
    # de um set de versao 2.
    dados = json.loads(sidecar_file.read_text(encoding="utf-8"))
    assert dados["format_version"] == SIDECAR_FORMAT_VERSION == 3
    assert dados["track_bindings"] == bindings
    assert dados["media_hashes"] == hashes

    lido = read_anchor(destino)
    assert lido.format_version == 3
    assert lido.track_bindings == bindings
    assert lido.media_hashes == hashes


def test_sidecar_v1_backward_compatibility(tmp_path):
    destino = tmp_path / "legado_v1.als"
    sidecar_file = sidecar_path_for(destino)

    payload_v1 = {
        "format_version": 1,
        "sample_rate": 48000,
        "bpm": 120.0,
        "timecode_rate": {"num": 25, "den": 1, "drop_frame": False},
        "live_origin_samples": 0,
        "live_origin_timecode": "00:00:00:00",
        "session_start_samples": 0,
        "session_start_timecode": "00:00:00:00",
        "source_path": "antigo.aaf",
        "fades": "absorb",
        "stereo": "pair",
        "video_path": None,
        "video_anchor_source": None,
        "track_channels": {"Trilha 1": 1},
    }
    sidecar_file.write_text(json.dumps(payload_v1, indent=2), encoding="utf-8")

    lido = read_anchor(destino)
    assert lido.format_version == 1
    assert lido.sample_rate == 48000
    assert lido.track_channels == {"Trilha 1": 1}
    assert lido.track_bindings is None
    assert lido.media_hashes is None


@pytest.mark.skipif(not TEMPLATE.exists(), reason="templates/live12.als ausente")
def test_write_session_produces_v2_sidecar_with_bindings_and_hashes(tmp_path):
    wav = tmp_path / "fala.wav"
    wav.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    asset = MediaAsset(
        id="m1",
        filename="fala.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav),
        embedded_mob_id="urn:smpte:umid:060a2b34010101010101010013-000000-AABBCCDD",
    )

    track1 = TrackIR(
        id="t0",
        source_index=0,
        original_source_indices=[10],
        name="DIALOGO",
        kind="audio",
        channel_count=1,
    )
    track1.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="fala.wav",
            media_id="m1",
            timeline_start_samples=UMA_HORA,
            timeline_end_samples=UMA_HORA + 48000,
            source_offset_samples=0,
        )
    )

    track2 = TrackIR(
        id="t1",
        source_index=1,
        original_source_indices=[20, 21],
        name="SFX_ST",
        kind="audio",
        channel_count=2,
    )

    session = SessionIR(
        name="sessao_v2",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track1, track2],
        media=[asset],
    )

    destino = tmp_path / "sessao_v2.als"
    write_session(
        session,
        TEMPLATE,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        Report(strict=True),
    )

    sidecar_data = json.loads(sidecar_path_for(destino).read_text(encoding="utf-8"))
    assert sidecar_data["format_version"] == 3
    assert "track_bindings" in sidecar_data
    bindings = sidecar_data["track_bindings"]
    assert len(bindings) == 2

    assert bindings[0]["index"] == 0
    assert bindings[0]["origin_slot_id"] == 10
    assert bindings[0]["mob_id"] == "urn:smpte:umid:060a2b34010101010101010013-000000-AABBCCDD"
    assert bindings[0]["channel_count"] == 1

    assert bindings[1]["index"] == 1
    assert bindings[1]["origin_slot_id"] == [20, 21]
    assert bindings[1]["channel_count"] == 2

    assert "media_hashes" in sidecar_data
    assert "fala.wav" in sidecar_data["media_hashes"] or str(wav) in sidecar_data["media_hashes"]
