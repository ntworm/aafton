"""Regressões de edição real do XML, sem depender do estado do computador."""

import copy
import json
import wave
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_xml import load_als, save_als, set_value
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import read_anchor, sidecar_path_for
from aafton.timecode import TimecodeRate

HOUR = 172800000


def make_set(tmp_path, *, origin=HOUR):
    tracks, media = [], []
    for i, channels in enumerate((1, 2)):
        wav = tmp_path / f"source{i}.wav"
        with wave.open(str(wav), "wb") as f:
            f.setparams((channels, 2, 48000, 0, "NONE", "not compressed"))
            f.writeframes(b"\0\0" * channels * 48000)
        asset = MediaAsset(
            id=f"m{i}",
            filename=wav.name,
            channels=channels,
            sample_rate=48000,
            duration_samples=48000,
            status="resolved",
            resolved_path=str(wav),
        )
        track = TrackIR(
            id=f"t{i}",
            source_index=i,
            original_source_indices=[101 + i],
            name=f"Track {i}",
            kind="audio",
            channel_count=channels,
        )
        track.clips.append(
            ClipIR(
                id=f"c{i}",
                track_id=track.id,
                channel_index=0,
                event_index=0,
                name=f"Clip {i}",
                media_id=asset.id,
                timeline_start_samples=HOUR + 48000,
                timeline_end_samples=HOUR + 96000,
                source_offset_samples=0,
            )
        )
        tracks.append(track)
        media.append(asset)
    session = SessionIR(
        name="Review",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=HOUR,
        tracks=tracks,
        media=media,
    )
    path = tmp_path / "review.als"
    write_session(
        session,
        Path("templates/live12.als"),
        path,
        WriteOptions(sample_rate=48000, bpm=60, live_origin_samples=origin),
        Report(),
    )
    return path


def test_explicit_zero_origin_is_not_replaced_by_session_start(tmp_path):
    path = make_set(tmp_path, origin=0)
    assert read_anchor(path).live_origin_samples == 0
    assert read_session(path, Report()).tracks[0].clips[0].timeline_start_samples == HOUR + 48000


@pytest.mark.parametrize("legacy", [False, True])
def test_track_identity_survives_reorder(tmp_path, legacy):
    path = make_set(tmp_path)
    root = load_als(path)
    container = root.find("LiveSet/Tracks")
    audio = container.findall("AudioTrack")
    container.remove(audio[1])
    container.insert(0, audio[1])
    if not legacy:
        for t in audio:
            set_value(t, "Name/EffectiveName", "Renamed")
            set_value(t, "Name/UserName", "Renamed")
    else:
        sidecar = sidecar_path_for(path)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        for b in data["track_bindings"]:
            b.pop("live_track_id", None)
        sidecar.write_text(json.dumps(data), encoding="utf-8")
    save_als(root, path)
    tracks = read_session(path, Report()).tracks
    assert [t.original_source_indices for t in tracks] == [[102], [101]]
    assert [t.channel_count for t in tracks] == [2, 1]


def test_inserted_track_does_not_inherit_another_tracks_binding(tmp_path):
    path = make_set(tmp_path)
    root = load_als(path)
    container = root.find("LiveSet/Tracks")
    new = copy.deepcopy(container.find("AudioTrack"))
    new.set("Id", "999999")
    set_value(new, "Name/EffectiveName", "New track")
    container.insert(0, new)
    save_als(root, path)
    tracks = read_session(path, Report()).tracks
    assert not getattr(tracks[0], "raw", {}).get("origin_slot_id")
    assert [t.original_source_indices for t in tracks[1:]] == [[101], [102]]


def test_collect_all_relative_reference_resolves_from_set(tmp_path, monkeypatch):
    path = make_set(tmp_path)
    root = load_als(path)
    clip = next(root.iter("AudioClip"))
    media = tmp_path / "Samples" / "Imported"
    media.mkdir(parents=True)
    wav = tmp_path / "source0.wav"
    wav.rename(media / wav.name)
    set_value(clip, "SampleRef/FileRef/Path", "")
    set_value(clip, "SampleRef/FileRef/RelativePath", "Samples/Imported/source0.wav")
    set_value(clip, "SampleRef/FileRef/RelativePathType", 3)
    save_als(root, path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    session = read_session(path, Report())
    asset = session.media_by_id(session.tracks[0].clips[0].media_id)
    assert asset.status == "resolved"
    assert Path(asset.resolved_path) == media / "source0.wav"


def test_session_view_clips_are_not_exported_into_arrangement(tmp_path):
    import xml.etree.ElementTree as ET

    path = make_set(tmp_path)
    root = load_als(path)
    track = next(root.iter("AudioTrack"))
    slot = ET.SubElement(track, "ClipSlot")
    slot.append(copy.deepcopy(next(track.iter("AudioClip"))))
    save_als(root, path)
    session = read_session(path, Report())
    assert len(session.tracks[0].clips) == 1


def test_live_pan_mute_and_clip_disabled_are_read(tmp_path):
    path = make_set(tmp_path)
    root = load_als(path)
    track = next(root.iter("AudioTrack"))
    set_value(track, "DeviceChain/Mixer/Pan/Manual", -0.75)
    set_value(track, "DeviceChain/Mixer/Speaker/Manual", False)
    set_value(next(track.iter("AudioClip")), "Disabled", True)
    save_als(root, path)
    track = read_session(path, Report()).tracks[0]
    assert track.pan == -0.75
    assert track.muted
    assert track.clips[0].muted


def test_positional_ids_must_be_unique_among_siblings():
    import xml.etree.ElementTree as ET
    from aafton.als_xml import DocumentInvalid, validate_document

    root = ET.fromstring(
        '<Ableton><Events><AudioClip Id="0"/><AudioClip Id="0"/></Events></Ableton>'
    )
    with pytest.raises(DocumentInvalid, match="duplicated positional Id"):
        validate_document(root, frozenset({"AudioClip"}))
