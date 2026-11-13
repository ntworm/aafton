"""Testes para resolução de essência embarcada e identificação de canais múltiplos."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from aafton.aaf_read import _source_mob_ref
from aafton.cli import _session_to_dict
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import extract_embedded_essence
from aafton.report import Report
from aafton.timecode import TimecodeRate


class MockDescriptor:
    def __init__(self, name: str, is_physical: bool = False):
        self.__name = name
        self.sample_rate = 48000
        self.channels = 1
        self.quantization_bits = 24
        if is_physical:

            class TapeDescriptor:
                pass

            self.__class__ = TapeDescriptor
        else:

            class WAVEDescriptor:
                pass

            self.__class__ = WAVEDescriptor

    def get(self, prop_name: str):
        return None


class MockMob:
    def __init__(self, mob_id: str, name: str | None, descriptor=None, has_essence: bool = False):
        self.mob_id = mob_id
        self.name = name
        self.descriptor = descriptor
        self.essence = MagicMock() if has_essence else None
        self.slots = []


def test_source_mob_ref_preserves_wave_over_tape():
    """Garante que um TapeMob downstream não sobrescreva o SourceMob com essência."""
    # Mob 3: TapeMob (sem essência)
    tape_mob = MockMob(
        mob_id="urn:smpte:umid:0000.tape",
        name="Take_01",
        descriptor=MockDescriptor("TapeDescriptor", is_physical=True),
        has_essence=False,
    )

    # Mob 2: File SourceMob com WAVEDescriptor e essência
    wave_mob = MockMob(
        mob_id="urn:smpte:umid:0000.wave",
        name=None,
        descriptor=MockDescriptor("WAVEDescriptor", is_physical=False),
        has_essence=True,
    )
    # Slot ligando wave_mob ao tape_mob
    slot_wave = MagicMock()
    slot_wave.slot_id = 1
    slot_wave.name = "A1"
    comp_wave = MagicMock()
    comp_wave.mob = tape_mob
    slot_wave.segment.components = [comp_wave]
    wave_mob.slots = [slot_wave]

    # Mob 1: MasterMob
    master_mob = MockMob(
        mob_id="urn:smpte:umid:0000.master",
        name="Take_01",
        descriptor=None,
        has_essence=False,
    )
    slot_master = MagicMock()
    slot_master.slot_id = 1
    slot_master.name = "A1"
    comp_master = MagicMock()
    comp_master.mob = wave_mob
    slot_master.segment.components = [comp_master]
    master_mob.slots = [slot_master]

    # SourceClip na timeline
    clip_comp = MagicMock()
    clip_comp.mob = master_mob
    clip_comp.get.side_effect = lambda prop: (
        MagicMock(value=1) if prop == "SourceMobSlotID" else None
    )

    nome, mob_id = _source_mob_ref(clip_comp)
    assert mob_id == "urn:smpte:umid:0000.wave"
    assert "Take_01" in nome
    assert nome.endswith(".wav")


def test_extract_embedded_essence_reuse_existing(tmp_path: Path):
    """Reutilização exige comparar bytes da essência com o arquivo existente."""
    import io

    report = Report()
    dest_dir = tmp_path / "extracted"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Cria WAV pré-existente válido
    existing_wav = dest_dir / "audio_test.wav"
    # Cabeçalho RIFF WAVE mínimo válido
    header = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80\xbb\x00\x00\x00\x77\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    existing_wav.write_bytes(header)

    mock_mob = MockMob(
        mob_id="urn:smpte:umid:test.1",
        name="audio_test",
        descriptor=MockDescriptor("WAVEDescriptor"),
        has_essence=True,
    )
    mock_mob.essence.open.return_value = io.BytesIO(header)

    res = extract_embedded_essence(
        aaf_file=None,
        source_mob=mock_mob,
        destination_dir=dest_dir,
        time_reference=0,
        report=report,
        suggested_filename="audio_test.wav",
        skip_existing=True,
    )

    assert res == existing_wav.resolve()
    assert any(i.code == "MEDIA_REUSED" for i in report.issues)


def test_session_to_dict_computes_inspector_metrics():
    """Valida que _session_to_dict fornece campos consolidados de timeline para o inspetor."""
    session = SessionIR(
        name="TestSession",
        sample_rate=48000,
        timecode_rate=TimecodeRate(num=24, den=1, drop_frame=False),
        start_timecode_samples=48000 * 3600,  # 01:00:00:00
        source_path="C:/audio/test.aaf",
    )
    asset = MediaAsset(
        id="m0",
        filename="take1_A1.wav",
        sample_rate=48000,
        channels=1,
        status="resolved",
        embedded_mob_id="urn:smpte:umid:take1.1",
    )
    session.media.append(asset)

    track = TrackIR(id="t0", source_index=0, name="Audio 1", kind="audio", channel_count=1)
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        media_id="m0",
        name="Clip 1",
        timeline_start_samples=48000 * 3600,
        timeline_end_samples=48000 * 3610,  # +10s
    )
    track.clips.append(clip)
    session.tracks.append(track)

    d = _session_to_dict(session)
    assert d["total_tracks"] == 1
    assert d["total_clips"] == 1
    assert d["total_media"] == 1
    assert d["embedded_media_count"] == 1
    assert d["resolved_media_count"] == 1
    assert d["duration_seconds"] == 10.0
    assert d["start_timecode"] == "01:00:00:00"
    assert d["end_timecode"] == "01:00:10:00"
