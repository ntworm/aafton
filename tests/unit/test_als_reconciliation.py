import gzip
from pathlib import Path

from aafton.als_read import read_session
from aafton.report import Report
from aafton.sidecar import Anchor, write_sidecar
from aafton.timecode import TimecodeRate

UMA_HORA = 172800000


def _create_mock_als_with_tracks(als_path: Path, track_names: list[str]) -> None:
    tracks_xml = []
    for idx, name in enumerate(track_names):
        tracks_xml.append(f"""
          <AudioTrack Id="{10 + idx}">
            <Name>
              <EffectiveName Value="{name}" />
              <UserName Value="{name}" />
            </Name>
            <DeviceChain>
              <MainSequencer>
                <Sample>
                  <ArrangerAutomation>
                    <Events />
                  </ArrangerAutomation>
                </Sample>
              </MainSequencer>
            </DeviceChain>
          </AudioTrack>
        """)

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<Ableton>
  <LiveSet>
    <Tracks>
      {"".join(tracks_xml)}
    </Tracks>
    <Locators>
      <Locators />
    </Locators>
  </LiveSet>
</Ableton>
"""
    with gzip.open(als_path, "wt", encoding="utf-8") as f:
        f.write(xml_content)


def test_reconciliation_restores_channels_and_slots_when_renamed(tmp_path):
    als_path = tmp_path / "renamed_session.als"
    # User renamed the tracks in Live 12:
    _create_mock_als_with_tracks(als_path, ["DX_NOVO_NOME", "SFX_REEDITADO"])

    bindings = [
        {
            "index": 0,
            "track_name": "DX_ORIGINAL",
            "origin_slot_id": 101,
            "mob_id": "urn:smpte:umid:mob_dx_01",
            "channel_count": 1,
        },
        {
            "index": 1,
            "track_name": "SFX_ORIGINAL",
            "origin_slot_id": [201, 202],
            "mob_id": "urn:smpte:umid:mob_sfx_stereo",
            "channel_count": 2,
        },
    ]

    anchor = Anchor(
        live_origin_samples=UMA_HORA,
        session_start_samples=UMA_HORA,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        bpm=60.0,
        format_version=2,
        track_bindings=bindings,
        # old v1 track_channels dictionary uses old names:
        track_channels={"DX_ORIGINAL": 1, "SFX_ORIGINAL": 2},
    )
    write_sidecar(als_path, anchor)

    report = Report(strict=False)
    session = read_session(als_path, report)

    assert len(session.tracks) == 2

    # Track 0: renamed in Live, but restored via track_bindings
    t0 = session.tracks[0]
    assert t0.name == "DX_NOVO_NOME"
    assert t0.channel_count == 1
    assert t0.original_source_indices == [101]
    assert t0.raw.get("origin_slot_id") == 101
    assert t0.raw.get("mob_id") == "urn:smpte:umid:mob_dx_01"

    # Track 1: stereo track renamed in Live, restored via track_bindings
    t1 = session.tracks[1]
    assert t1.name == "SFX_REEDITADO"
    assert t1.channel_count == 2
    assert t1.original_source_indices == [201, 202]
    assert t1.raw.get("origin_slot_id") == [201, 202]
    assert t1.raw.get("mob_id") == "urn:smpte:umid:mob_sfx_stereo"


def test_reconciliation_resolves_duplicate_track_names_without_ambiguity(tmp_path):
    als_path = tmp_path / "duplicate_names.als"
    # Two tracks having the exact same name in Live
    _create_mock_als_with_tracks(als_path, ["AUDIO", "AUDIO"])

    bindings = [
        {
            "index": 0,
            "track_name": "AUDIO",
            "origin_slot_id": 1,
            "mob_id": "urn:smpte:umid:mob1",
            "channel_count": 1,
        },
        {
            "index": 1,
            "track_name": "AUDIO",
            "origin_slot_id": [2, 3],
            "mob_id": "urn:smpte:umid:mob2",
            "channel_count": 2,
        },
    ]

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

    assert len(session.tracks) == 2
    assert session.tracks[0].channel_count == 1
    assert session.tracks[0].original_source_indices == [1]
    assert session.tracks[1].channel_count == 2
    assert session.tracks[1].original_source_indices == [2, 3]


def test_reconciliation_v1_fallback_when_bindings_absent(tmp_path):
    als_path = tmp_path / "legacy_v1.als"
    _create_mock_als_with_tracks(als_path, ["VOZ", "MUSICA"])

    anchor = Anchor(
        live_origin_samples=UMA_HORA,
        session_start_samples=UMA_HORA,
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        bpm=60.0,
        format_version=1,
        track_channels={"VOZ": 1, "MUSICA": 2},
        audio_source_indices=[[0], [1, 2]],
    )
    write_sidecar(als_path, anchor)

    report = Report(strict=False)
    session = read_session(als_path, report)

    assert session.tracks[0].channel_count == 1
    assert session.tracks[1].channel_count == 2
