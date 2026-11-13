"""Tests of the read-only scan of a Live set."""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_preflight import scan_set

TEMPLATE = Path("templates/live12.als")


def test_scan_of_the_template_lists_the_tracks_in_document_order():
    """The template has two groups, three audio tracks and one return, in that order."""
    tracks, _ = scan_set(TEMPLATE)

    assert [track.kind for track in tracks] == [
        "group",
        "audio",
        "audio",
        "group",
        "audio",
        "return",
    ]
    assert [track.index for track in tracks] == [0, 1, 2, 3, 4, 5]


def test_scan_of_the_template_numbers_the_audio_tracks_and_counts_their_clips():
    """The audio tracks carry the numbers that read_session gives to its own tracks."""
    tracks, _ = scan_set(TEMPLATE)

    audio = [track for track in tracks if track.kind == "audio"]
    assert [track.audio_index for track in audio] == [0, 1, 2]
    assert [track.clips for track in audio] == [1, 2, 3]
    assert [track.audio_index for track in tracks if track.kind != "audio"] == [None] * 3


def test_scan_of_the_template_counts_the_groups_and_reads_the_return_devices():
    """The return track of the template carries the Reverb device."""
    tracks, _ = scan_set(TEMPLATE)

    groups = [track for track in tracks if track.kind == "group"]
    returns = [track for track in tracks if track.kind == "return"]
    assert len(groups) == 2
    assert len(returns) == 1
    assert returns[0].devices == ["Reverb"]


def test_scan_of_the_template_reads_the_creator_of_the_document():
    """The document says which Live wrote the set."""
    _, creator = scan_set(TEMPLATE)

    assert creator.startswith("Ableton Live")


def _write_set(path: Path) -> Path:
    """Writes a synthetic set: a MIDI track with two clips and a frozen audio track."""
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.5b11")
    tracks = ET.SubElement(ET.SubElement(root, "LiveSet"), "Tracks")

    midi = ET.SubElement(tracks, "MidiTrack")
    ET.SubElement(ET.SubElement(midi, "Name"), "EffectiveName", Value="MIDI 1")
    chain = ET.SubElement(midi, "DeviceChain")
    devices = ET.SubElement(ET.SubElement(chain, "DeviceChain"), "Devices")
    ET.SubElement(devices, "Eq8", Id="0")
    ET.SubElement(devices, "Compressor2", Id="1")
    sequencer = ET.SubElement(chain, "MainSequencer")
    events = ET.SubElement(
        ET.SubElement(ET.SubElement(sequencer, "ClipTimeable"), "ArrangerAutomation"),
        "Events",
    )
    for start, end in (("4", "8"), ("8", "12")):
        clip = ET.SubElement(events, "MidiClip")
        ET.SubElement(clip, "CurrentStart", Value=start)
        ET.SubElement(clip, "CurrentEnd", Value=end)

    audio = ET.SubElement(tracks, "AudioTrack")
    ET.SubElement(ET.SubElement(audio, "Name"), "EffectiveName", Value="AUDIO 1")
    ET.SubElement(audio, "Freeze", Value="true")
    chain = ET.SubElement(audio, "DeviceChain")
    devices = ET.SubElement(ET.SubElement(chain, "DeviceChain"), "Devices")
    ET.SubElement(devices, "Eq8", Id="0")
    ET.SubElement(devices, "Reverb", Id="1")

    with gzip.open(path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))
    return path


def test_scan_of_a_synthetic_set_reads_the_kinds_and_the_midi_clips(tmp_path):
    """The MIDI track of the synthetic set has two clips, with their times in beats."""
    path = _write_set(tmp_path / "sintetico.als")

    tracks, creator = scan_set(path)

    assert creator == "Ableton Live 12.4.5b11"
    assert [track.kind for track in tracks] == ["midi", "audio"]

    midi = tracks[0]
    assert midi.name == "MIDI 1"
    assert midi.clips == 2
    assert midi.midi_spans == [(4.0, 8.0), (8.0, 12.0)]
    assert midi.audio_index is None
    assert midi.frozen is False


def test_scan_of_a_synthetic_set_reads_the_devices_and_the_freeze(tmp_path):
    """The audio track of the synthetic set is frozen and carries two devices."""
    path = _write_set(tmp_path / "sintetico.als")

    tracks, _ = scan_set(path)

    audio = tracks[1]
    assert audio.name == "AUDIO 1"
    assert audio.devices == ["Eq8", "Reverb"]
    assert audio.frozen is True
    assert audio.audio_index == 0
    assert audio.midi_spans == []


def test_scan_of_a_set_without_tracks_is_empty(tmp_path):
    """A set without ./LiveSet/Tracks has no track and does not fail."""
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.5b11")
    ET.SubElement(root, "LiveSet")
    path = tmp_path / "vazio.als"
    with gzip.open(path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))

    tracks, creator = scan_set(path)

    assert tracks == []
    assert creator == "Ableton Live 12.4.5b11"


def test_scan_falls_back_to_the_user_name_and_to_the_empty_name(tmp_path):
    """The name of a track is the EffectiveName, then the UserName, then empty.

    The reader of the engine leaves a track with no name under the empty name:
    the scan agrees with it, so both attribute the issues to the same track.
    """
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.5b11")
    tracks = ET.SubElement(ET.SubElement(root, "LiveSet"), "Tracks")
    group = ET.SubElement(tracks, "GroupTrack")
    ET.SubElement(ET.SubElement(group, "Name"), "UserName", Value="G1")
    ET.SubElement(tracks, "ReturnTrack")
    path = tmp_path / "sem_nome.als"
    with gzip.open(path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))

    tracks, _ = scan_set(path)

    assert [track.name for track in tracks] == ["G1", ""]
