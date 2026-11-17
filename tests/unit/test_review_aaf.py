import io
import struct
from types import SimpleNamespace

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import extract_embedded_essence, read_audio_metadata
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes


def session_for(wav, rate):
    track = TrackIR(id="t", source_index=0, name="Audio", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c",
            track_id="t",
            channel_index=0,
            event_index=0,
            name="Clip",
            media_id="m",
            timeline_start_samples=0,
            timeline_end_samples=48048,
            source_offset_samples=0,
        )
    )
    return SessionIR(
        name="Test",
        sample_rate=48000,
        timecode_rate=rate,
        start_timecode_samples=0,
        tracks=[track],
        media=[
            MediaAsset(
                id="m",
                filename=wav.name,
                channels=1,
                sample_rate=48000,
                duration_samples=96000,
                status="resolved",
                resolved_path=str(wav),
            )
        ],
    )


@pytest.mark.parametrize(
    "num,den,drop",
    [(24000, 1001, False), (30000, 1001, False), (30000, 1001, True), (60000, 1001, True)],
)
def test_export_fractional_rate_keeps_nominal_fps_and_exact_slot_rate(tmp_path, num, den, drop):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    rate = TimecodeRate(num, den, drop)
    path = tmp_path / "output.aaf"
    write_aaf(session_for(wav, rate), path, Report())
    with aaf2.open(str(path), "r") as f:
        slot = next(
            s
            for s in next(f.content.compositionmobs()).slots
            if type(s.segment).__name__ == "Timecode"
        )
        assert slot.segment.fps == rate.nominal_fps
        assert slot.edit_rate == rate.fps
    assert read_aaf(path, Report()).timecode_rate == rate


def test_export_timecode_length_is_session_duration(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    session = session_for(wav, TimecodeRate(25, 1))
    session.start_timecode_samples = 172800000
    session.tracks[0].clips[0].timeline_start_samples = 172800000
    session.tracks[0].clips[0].timeline_end_samples = 172848000
    path = tmp_path / "output.aaf"
    write_aaf(session, path, Report())
    with aaf2.open(str(path), "r") as f:
        tc = next(
            s.segment
            for s in next(f.content.compositionmobs()).slots
            if type(s.segment).__name__ == "Timecode"
        )
        assert tc.start == 90000
        assert tc.length == 25


@pytest.mark.parametrize("muted", [False, True])
def test_export_preserves_static_gain_or_mute(tmp_path, muted):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    session = session_for(wav, TimecodeRate(25, 1))
    session.tracks[0].gain_linear = 0.5
    session.tracks[0].clips[0].gain_linear = 0.25
    session.tracks[0].muted = muted
    path = tmp_path / "output.aaf"
    write_aaf(session, path, Report())
    # Track muda vai como clip mudo com o ganho normal (decisao de Gabriel, 15/11/2026).
    relido = next(read_aaf(path, Report()).all_clips())
    assert relido.gain_linear == 0.125
    assert relido.muted is muted


@pytest.mark.parametrize("gain", [0.0, 0.5, 0.0000001])
def test_export_stereo_gain_preserves_both_channels_and_small_gain(tmp_path, gain):
    from aafton.media_extract import resolve_asset
    from aafton.stereo import process_stereo_pairing

    wav = tmp_path / "stereo.wav"
    wav.write_bytes(wav_bytes(96000, channels=2))
    session = session_for(wav, TimecodeRate(25, 1))
    session.media[0].channels = session.tracks[0].channel_count = 2
    session.tracks[0].gain_linear = gain
    path = tmp_path / "output.aaf"
    write_aaf(session, path, Report())
    reread = read_aaf(path, Report())
    assert reread.tracks[0].channel_count == 2
    assert {c.channel_index for c in reread.tracks[0].clips} == {0, 1}
    assert all(c.gain_linear == gain for c in reread.tracks[0].clips)
    for asset in reread.media:
        resolve_asset(asset, [tmp_path], Report())
    process_stereo_pairing(reread, "pair", Report(), tmp_path / "paired")
    assert len(reread.tracks[0].clips) == 1


def test_export_rejects_unrepresented_fade_before_overwriting(tmp_path):
    from aafton.ir import FadeIR
    from aafton.report import StrictModeError

    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    session = session_for(wav, TimecodeRate(25, 1))
    session.tracks[0].clips[0].fade_in = FadeIR(kind="in", duration_samples=30000)
    session.tracks[0].clips[0].fade_out = FadeIR(kind="out", duration_samples=30000)
    path = tmp_path / "output.aaf"
    path.write_bytes(b"previous delivery")
    with pytest.raises(StrictModeError, match="WRITE_FADE_UNSUPPORTED"):
        write_aaf(session, path, Report())
    assert path.read_bytes() == b"previous delivery"


def test_failed_export_preserves_previous_delivery(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    session = session_for(wav, TimecodeRate(25, 1))
    session.tracks[0].gain_linear = float("nan")
    path = tmp_path / "output.aaf"
    path.write_bytes(b"previous delivery")
    with pytest.raises(ValueError, match="invalid gain"):
        write_aaf(session, path, Report())
    assert path.read_bytes() == b"previous delivery"
    assert not list(tmp_path.glob(".output-*.aaf"))


def test_failed_selfcheck_preserves_previous_delivery_in_permissive_mode(tmp_path):
    path = tmp_path / "output.aaf"
    path.write_bytes(b"previous delivery")
    empty = SessionIR(
        name="empty", sample_rate=48000, timecode_rate=TimecodeRate(25, 1), start_timecode_samples=0
    )
    report = Report(strict=False)
    write_aaf(empty, path, report)
    assert report.incomplete
    assert path.read_bytes() == b"previous delivery"


def test_export_mixed_sample_rates_uses_source_clock(tmp_path):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(88200, sample_rate=44100))
    session = session_for(wav, TimecodeRate(25, 1))
    session.media[0].sample_rate = 44100
    session.media[0].duration_samples = 88200
    session.tracks[0].clips[0].source_offset_samples = 4800
    path = tmp_path / "mixed.aaf"
    write_aaf(session, path, Report())
    with aaf2.open(str(path), "r") as f:
        comp = next(f.content.compositionmobs())
        segment = next(s.segment for s in comp.slots if s.segment.media_kind == "Sound")
        from aafton.aaf_write import _iter_source_clips

        source = next(_iter_source_clips(segment))
        assert source.start == 4410
    from fractions import Fraction

    reread = read_aaf(path, Report())
    assert Fraction(next(reread.all_clips()).source_offset_samples, reread.sample_rate) == Fraction(
        4800, 48000
    )


@pytest.mark.parametrize("direct", [False, True])
def test_source_offset_uses_referenced_slot_clock(tmp_path, direct):
    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    path = build_linked_aaf(
        tmp_path / "source.aaf", [(0, 24, 4800)], wav, start_timecode_frames=0, fps=24
    )
    with aaf2.open(str(path), "rw") as f:
        slot = next(
            s for s in next(f.content.compositionmobs()).slots if s.segment.media_kind == "Sound"
        )
        slot.edit_rate = 24
        if direct:
            slot.segment = list(slot.segment.components)[0]
    session = read_aaf(path, Report())
    clips = list(session.all_clips())
    assert len(clips) == 1
    assert clips[0].duration_samples == 48000
    assert clips[0].source_offset_samples == 4800


def test_embedded_same_name_does_not_reuse_other_essence(tmp_path):
    paths = []
    for sample in (1111, 2222):
        mob = SimpleNamespace(
            essence=SimpleNamespace(
                open=lambda mode, n=sample: io.BytesIO(struct.pack("<h", n) * 10)
            ),
            descriptor=SimpleNamespace(channels=1, sample_rate=48000, quantization_bits=16),
            name="audio",
            mob_id=str(sample),
        )
        paths.append(
            extract_embedded_essence(
                None,
                mob,
                tmp_path,
                0,
                Report(),
                suggested_filename="common.wav",
                existing_destinations=set(),
            )
        )
    meta = read_audio_metadata(paths[1])
    with paths[1].open("rb") as f:
        f.seek(meta["data_offset"])
        assert struct.unpack("<h", f.read(2))[0] == 2222
    assert paths[0] != paths[1]


def test_embedded_stream_never_uses_unbounded_read(tmp_path):
    class Bounded(io.BytesIO):
        def read(self, n=-1):
            assert 0 <= n <= 1024 * 1024
            return super().read(n)

    mob = SimpleNamespace(
        essence=SimpleNamespace(open=lambda mode: Bounded(b"\0\0" * 10)),
        descriptor=SimpleNamespace(channels=1, sample_rate=48000, quantization_bits=16),
        name="audio",
        mob_id="1",
    )
    path = extract_embedded_essence(None, mob, tmp_path, 0, Report())
    assert read_audio_metadata(path)["duration_samples"] == 10


def test_aiff_ssnd_offset_excludes_alignment_bytes(tmp_path):
    comm = struct.pack(">HlH", 1, 3, 16) + bytes.fromhex("400ebb80000000000000")
    ssnd = struct.pack(">II", 4, 0) + b"\0" * 4 + struct.pack(">hhh", 1000, 2000, 3000)
    body = (
        b"AIFF"
        + b"COMM"
        + struct.pack(">I", len(comm))
        + comm
        + b"SSND"
        + struct.pack(">I", len(ssnd))
        + ssnd
    )
    path = tmp_path / "aligned.aif"
    path.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    meta = read_audio_metadata(path)
    assert meta["data_size"] == 6
    with path.open("rb") as f:
        f.seek(meta["data_offset"])
        assert struct.unpack(">hhh", f.read(6)) == (1000, 2000, 3000)


def test_stereo_same_clip_names_keep_distinct_audio(tmp_path):
    from aafton.stereo import process_stereo_pairing
    import wave

    session = SessionIR(
        name="Stereo",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )
    for pair, values in enumerate(((1000, 2000), (3000, 4000))):
        for channel, value in enumerate(values):
            ident = f"{pair}_{channel}"
            wav = tmp_path / (ident + ".wav")
            with wave.open(str(wav), "wb") as f:
                f.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
                f.writeframes(struct.pack("<h", value) * 10)
            session.media.append(
                MediaAsset(
                    id=ident,
                    filename=wav.name,
                    channels=1,
                    sample_rate=48000,
                    duration_samples=10,
                    status="resolved",
                    resolved_path=str(wav),
                )
            )
            track = TrackIR(
                id=ident,
                source_index=len(session.tracks),
                name=f"Pair{pair}.{'LR'[channel]}",
                kind="audio",
                channel_count=1,
            )
            track.clips.append(
                ClipIR(
                    id=ident,
                    track_id=ident,
                    channel_index=0,
                    event_index=0,
                    name="../../common." + "LR"[channel],
                    media_id=ident,
                    timeline_start_samples=0,
                    timeline_end_samples=10,
                    source_offset_samples=0,
                )
            )
            session.tracks.append(track)
    process_stereo_pairing(session, "pair", Report(), tmp_path / "media")
    assert len(session.tracks) == 2
    paths = [session.media_by_id(t.clips[0].media_id).resolved_path for t in session.tracks]
    assert paths[0] != paths[1]
    for path, expected in zip(paths, ((1000, 2000), (3000, 4000))):
        with wave.open(path, "rb") as f:
            assert struct.unpack("<hh", f.readframes(1)) == expected
        assert tmp_path / "media" == __import__("pathlib").Path(path).parent


def test_verify_does_not_approve_discarded_aaf_component(tmp_path):
    from aafton.als_write import WriteOptions, write_session
    from aafton.verify import verify_conversion
    from aafton.media_extract import resolve_asset

    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    path = build_linked_aaf(
        tmp_path / "source.aaf", [(0, 24000, 0), (24000, 24000, 0)], wav, start_timecode_frames=0
    )
    with aaf2.open(str(path), "rw") as f:
        seq = next(
            s.segment
            for s in next(f.content.compositionmobs()).slots
            if s.segment.media_kind == "Sound"
        )
        second = list(seq.components)[1]
        scope = f.create.NestedScope(media_kind="sound", length=24000)
        inner = f.create.Sequence(media_kind="sound")
        clip = f.create.SourceClip(media_kind="sound", start=0, length=24000)
        clip.mob, clip.slot_id = second.mob, second.slot_id
        inner.components.append(clip)
        scope.slots.append(inner)
        seq.components[1] = scope
    session = read_aaf(path, Report(strict=False))
    for asset in session.media:
        resolve_asset(asset, [tmp_path], Report())
    als = tmp_path / "converted.als"
    write_session(session, "templates/live12.als", als, WriteOptions(sample_rate=48000), Report())
    result = verify_conversion(path, als)
    assert not result.ok
    assert any("AAF_UNKNOWN_COMPONENT" in error for error in result.errors)


def test_verify_accepts_valid_metadata_marker_slot(tmp_path):
    from aafton.als_write import WriteOptions, write_session
    from aafton.media_extract import resolve_asset
    from aafton.verify import verify_conversion

    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    path = build_linked_aaf(
        tmp_path / "source.aaf", [(0, 48000, 0)], wav, start_timecode_frames=0, with_marker_at=24000
    )
    session = read_aaf(path, Report())
    assert len(session.tracks) == 1
    assert len(session.markers) == 1
    for asset in session.media:
        resolve_asset(asset, [tmp_path], Report())
    als = tmp_path / "output.als"
    write_session(session, "templates/live12.als", als, WriteOptions(sample_rate=48000), Report())
    assert verify_conversion(path, als).ok


def test_verify_mixed_edit_rates_does_not_relax_sample_precision(tmp_path):
    from aafton.als_write import WriteOptions, write_session
    from aafton.als_xml import load_als, save_als, set_value
    from aafton.verify import verify_conversion
    from aafton.media_extract import resolve_asset

    wav = tmp_path / "audio.wav"
    wav.write_bytes(wav_bytes(96000))
    path = build_linked_aaf(
        tmp_path / "source.aaf", [(0, 24, 0)], wav, start_timecode_frames=0, fps=24
    )
    with aaf2.open(str(path), "rw") as f:
        comp = next(f.content.compositionmobs())
        first = next(s for s in comp.slots if s.segment.media_kind == "Sound")
        first.edit_rate = 24
        source = list(first.segment.components)[0]
        slot = comp.create_timeline_slot(edit_rate=48000)
        slot.name = "Sample exact"
        seq = f.create.Sequence(media_kind="sound")
        second = f.create.SourceClip(media_kind="sound", start=0, length=48000)
        second.mob, second.slot_id = source.mob, source.slot_id
        seq.components.append(second)
        slot.segment = seq
    session = read_aaf(path, Report())
    for asset in session.media:
        resolve_asset(asset, [tmp_path], Report())
    als = tmp_path / "converted.als"
    write_session(session, "templates/live12.als", als, WriteOptions(sample_rate=48000), Report())
    root = load_als(als)
    clip = root.findall("./LiveSet/Tracks/AudioTrack")[1].find(".//AudioClip")
    set_value(clip, "CurrentStart", 1000 / 48000)
    set_value(clip, "CurrentEnd", 1 + 1000 / 48000)
    save_als(root, als)
    result = verify_conversion(path, als)
    assert not result.ok
    assert result.tolerance_samples == 0
