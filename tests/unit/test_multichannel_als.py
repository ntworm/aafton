import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")
UMA_HORA = 172800000


def _create_dummy_wav(path: Path) -> Path:
    data = (
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
        b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )
    path.write_bytes(data)
    return path


@pytest.mark.skipif(not TEMPLATE.exists(), reason="templates/live12.als ausente")
def test_write_session_multichannel_51_creates_grouptrack_and_bindings(tmp_path):
    wav_path = _create_dummy_wav(tmp_path / "audio_51.wav")
    asset = MediaAsset(
        id="m_51",
        filename="audio_51.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav_path),
        embedded_mob_id="urn:smpte:umid:060a2b34010101010101010013-000000-51515151",
    )

    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_surround",
            channel_index=i,
            event_index=i,
            name=f"Clip_ch{i}",
            media_id="m_51",
            timeline_start_samples=UMA_HORA,
            timeline_end_samples=UMA_HORA + 48000,
            source_offset_samples=0,
        )
        for i in range(6)
    ]

    track_51 = TrackIR(
        id="t_surround",
        source_index=0,
        original_source_indices=[1, 2, 3, 4, 5, 6],
        name="Surround 5.1",
        kind="audio",
        channel_count=6,
        channel_layout="5.1 SMPTE",
        clips=clips,
    )

    session = SessionIR(
        name="test_multichannel",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track_51],
        media=[asset],
    )

    out_als = tmp_path / "multichannel.als"
    report = Report(strict=True)
    write_session(
        session,
        TEMPLATE,
        out_als,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        report,
    )

    # 1. Nao deve haver erro MULTICHANNEL_UNSUPPORTED
    unsupported_issues = [i for i in report.issues if i.code == "MULTICHANNEL_UNSUPPORTED"]
    assert len(unsupported_issues) == 0

    # 2. ALS XML deve conter 1 GroupTrack e 6 AudioTracks agrupadas
    with gzip.open(out_als, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())

    group_tracks = list(root.iter("GroupTrack"))
    audio_tracks = list(root.iter("AudioTrack"))

    assert len(group_tracks) >= 1
    # GroupTrack nomeado f"{track.name} ({layout_name})"
    group = next(
        g
        for g in group_tracks
        if (
            g.find("Name/UserName") is not None
            and g.find("Name/UserName").get("Value") == "Surround 5.1 (5.1 SMPTE)"
        )
        or (
            g.find("Name/EffectiveName") is not None
            and g.find("Name/EffectiveName").get("Value") == "Surround 5.1 (5.1 SMPTE)"
        )
    )
    group_id = group.get("Id")

    # 6 AudioTracks com TrackGroupId correspondente
    child_nodes = [
        t
        for t in audio_tracks
        if t.find("TrackGroupId") is not None and t.find("TrackGroupId").get("Value") == group_id
    ]
    assert len(child_nodes) == 6

    child_names = [t.find("Name/EffectiveName").get("Value") for t in child_nodes]
    expected_names = [
        "Surround 5.1 [L]",
        "Surround 5.1 [R]",
        "Surround 5.1 [C]",
        "Surround 5.1 [LFE]",
        "Surround 5.1 [Ls]",
        "Surround 5.1 [Rs]",
    ]
    assert child_names == expected_names

    # Pans discretos no ALS
    child_pans = [float(t.find("DeviceChain/Mixer/Pan/Manual").get("Value")) for t in child_nodes]
    assert child_pans == [-1.0, 1.0, 0.0, 0.0, -1.0, 1.0]

    # 3. Sidecar deve conter track_bindings com metadados de canal
    sidecar_file = sidecar_path_for(out_als)
    assert sidecar_file.exists()
    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))

    bindings = sidecar_data["track_bindings"]
    assert len(bindings) == 6

    for idx, b in enumerate(bindings):
        assert b["channel_count"] == 1
        assert b["channel_index"] == idx
        assert b["multichannel_layout"] == "5.1 SMPTE"
        assert b["parent_group"] == "Surround 5.1 (5.1 SMPTE)"
        assert b["track_name"] == expected_names[idx]
        assert b["origin_slot_id"] == idx + 1

    # 4. als_read.py reconcilia as faixas multicanal
    read_rep = Report(strict=False)
    read_sess = read_session(out_als, read_rep)
    assert len(read_sess.tracks) == 6

    for idx, t in enumerate(read_sess.tracks):
        assert t.name == expected_names[idx]
        assert t.channel_count == 1
        assert t.group_path == ["Surround 5.1 (5.1 SMPTE)"]
        assert t.raw.get("channel_index") == idx
        assert t.raw.get("multichannel_layout") == "5.1 SMPTE"
        assert t.raw.get("parent_group") == "Surround 5.1 (5.1 SMPTE)"
        assert t.original_source_indices == [idx + 1]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="templates/live12.als ausente")
def test_write_session_multichannel_quad(tmp_path):
    wav_path = _create_dummy_wav(tmp_path / "audio_quad.wav")
    asset = MediaAsset(
        id="m_quad",
        filename="audio_quad.wav",
        channels=1,
        sample_rate=48000,
        status="resolved",
        resolved_path=str(wav_path),
    )

    track_quad = TrackIR(
        id="t_quad",
        source_index=10,
        original_source_indices=[11, 12, 13, 14],
        name="Ambience",
        kind="audio",
        channel_count=4,
        clips=[],
    )

    session = SessionIR(
        name="test_quad",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[track_quad],
        media=[asset],
    )

    out_als = tmp_path / "quad.als"
    report = Report(strict=True)
    write_session(
        session,
        TEMPLATE,
        out_als,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        report,
    )

    with gzip.open(out_als, "rt", encoding="utf-8") as f:
        root = ET.fromstring(f.read())

    group_tracks = [
        g
        for g in root.iter("GroupTrack")
        if g.find("Name/EffectiveName") is not None
        and g.find("Name/EffectiveName").get("Value") == "Ambience (Quad)"
    ]
    assert len(group_tracks) == 1

    sidecar_data = json.loads(sidecar_path_for(out_als).read_text(encoding="utf-8"))
    bindings = sidecar_data["track_bindings"]
    assert len(bindings) == 4
    assert [b["track_name"] for b in bindings] == [
        "Ambience [L]",
        "Ambience [R]",
        "Ambience [Ls]",
        "Ambience [Rs]",
    ]
    assert [b["channel_index"] for b in bindings] == [0, 1, 2, 3]
    assert [b["parent_group"] for b in bindings] == ["Ambience (Quad)"] * 4


def test_read_session_detects_missing_multichannel_member(tmp_path):
    als_path = tmp_path / "missing_member.als"

    # Criar ALS com GroupTrack e apenas 3 canais de um quad (faltando o canal 3 [Rs])
    group_id = 99
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton>
  <LiveSet>
    <Tracks>
      <GroupTrack Id="{group_id}">
        <Name><EffectiveName Value="Ambience (Quad)" /><UserName Value="Ambience (Quad)" /></Name>
      </GroupTrack>
      <AudioTrack Id="10">
        <Name><EffectiveName Value="Ambience [L]" /><UserName Value="Ambience [L]" /></Name>
        <TrackGroupId Value="{group_id}" />
      </AudioTrack>
      <AudioTrack Id="11">
        <Name><EffectiveName Value="Ambience [R]" /><UserName Value="Ambience [R]" /></Name>
        <TrackGroupId Value="{group_id}" />
      </AudioTrack>
      <AudioTrack Id="12">
        <Name><EffectiveName Value="Ambience [Ls]" /><UserName Value="Ambience [Ls]" /></Name>
        <TrackGroupId Value="{group_id}" />
      </AudioTrack>
    </Tracks>
    <Locators><Locators /></Locators>
  </LiveSet>
</Ableton>
"""
    with gzip.open(als_path, "wt", encoding="utf-8") as f:
        f.write(xml_content)

    bindings = [
        {
            "index": 0,
            "track_name": "Ambience [L]",
            "channel_index": 0,
            "multichannel_layout": "Quad",
            "parent_group": "Ambience (Quad)",
            "channel_count": 1,
        },
        {
            "index": 1,
            "track_name": "Ambience [R]",
            "channel_index": 1,
            "multichannel_layout": "Quad",
            "parent_group": "Ambience (Quad)",
            "channel_count": 1,
        },
        {
            "index": 2,
            "track_name": "Ambience [Ls]",
            "channel_index": 2,
            "multichannel_layout": "Quad",
            "parent_group": "Ambience (Quad)",
            "channel_count": 1,
        },
        {
            "index": 3,
            "track_name": "Ambience [Rs]",
            "channel_index": 3,
            "multichannel_layout": "Quad",
            "parent_group": "Ambience (Quad)",
            "channel_count": 1,
        },
    ]

    from aafton.sidecar import Anchor, write_sidecar

    anchor = Anchor(
        live_origin_samples=UMA_HORA,
        session_start_samples=UMA_HORA,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        bpm=60.0,
        format_version=2,
        track_bindings=bindings,
    )
    write_sidecar(als_path, anchor)

    report = Report(strict=False)
    session = read_session(als_path, report)

    assert len(session.tracks) == 3
    warnings = [w for w in report.issues if w.code == "MULTICHANNEL_MEMBER_MISSING"]
    assert len(warnings) == 1
    assert warnings[0].context["channel_index"] == 3
    assert warnings[0].context["parent_group"] == "Ambience (Quad)"
