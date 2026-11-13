"""Read-only scan of the tracks of an Ableton Live set.

The scan answers what the set holds before anything is converted: how many
tracks there are, of which kind, how many arrangement clips each one carries,
which devices sit in the chain and whether the track is frozen. It reads the
.als and nothing else: it writes no file, it reports no issue, and a missing
optional node never makes it fail.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .als_xml import get_value, load_als

AUDIO_CLIP_PATH = "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
MIDI_CLIP_PATH = "DeviceChain/MainSequencer/ClipTimeable/ArrangerAutomation/Events/MidiClip"
DEVICES_PATH = "DeviceChain/DeviceChain/Devices"

TRACK_KINDS = {
    "AudioTrack": "audio",
    "MidiTrack": "midi",
    "GroupTrack": "group",
    "ReturnTrack": "return",
}


@dataclass(kw_only=True)
class ScannedTrack:
    """One track of a Live set, as the scan reads it."""

    index: int
    """Position of the track among the tracks of the set, in document order."""

    kind: str
    """Lower case name of the kind: audio, midi, group or return."""

    name: str
    """Name of the track in the Live."""

    audio_index: int | None
    """Position among the AudioTrack nodes, the enumeration read_session uses."""

    clips: int
    """Number of arrangement clips of the track, audio or MIDI."""

    devices: list[str]
    """Tag names of the devices of the chain, in the order of the chain."""

    frozen: bool
    """True when the track is frozen."""

    midi_spans: list[tuple[float, float]]
    """Start and end of each MIDI clip of the arrangement, in beats, sorted."""


def scan_set(als_path: str | Path) -> tuple[list[ScannedTrack], str]:
    """Scans the tracks of a Live set and returns them with the Creator of the document."""
    root = load_als(als_path)
    tracks: list[ScannedTrack] = []
    audio_index = 0
    for node in _track_nodes(root):
        kind = TRACK_KINDS[node.tag]
        current = None
        if kind == "audio":
            current = audio_index
            audio_index += 1
        tracks.append(_scan_track(len(tracks), kind, current, node))
    return tracks, root.get("Creator", "")


def _track_nodes(root: ET.Element) -> list[ET.Element]:
    """The children of ./LiveSet/Tracks that are tracks, in document order."""
    tracks = root.find("./LiveSet/Tracks")
    if tracks is None:
        return []
    return [child for child in tracks if child.tag in TRACK_KINDS]


def _scan_track(
    index: int,
    kind: str,
    audio_index: int | None,
    node: ET.Element,
) -> ScannedTrack:
    """Reads one track of the set."""
    return ScannedTrack(
        index=index,
        kind=kind,
        name=_track_name(node),
        audio_index=audio_index,
        clips=_clip_count(node),
        devices=_device_names(node),
        frozen=_frozen(node),
        midi_spans=_midi_spans(node),
    )


def _track_name(node: ET.Element) -> str:
    """The name of the track: EffectiveName first, UserName next, empty last.

    A track with no name at all keeps the empty name, the way the reader of
    the engine leaves it: this is the name both use to attribute the issues.
    """
    for path in ("Name/EffectiveName", "Name/UserName"):
        value = get_value(node, path)
        if value:
            return value
    return ""


def _clip_count(node: ET.Element) -> int:
    """The number of arrangement clips of the track, audio or MIDI."""
    return len(node.findall(AUDIO_CLIP_PATH)) + len(node.findall(MIDI_CLIP_PATH))


def _device_names(node: ET.Element) -> list[str]:
    """The tag names of the devices of the chain, in the order of the chain."""
    return [device.tag for device in node.findall(f"{DEVICES_PATH}/*")]


def _frozen(node: ET.Element) -> bool:
    """True when the track is frozen."""
    value = get_value(node, "Freeze")
    return value is not None and value.strip().lower() == "true"


def _midi_spans(node: ET.Element) -> list[tuple[float, float]]:
    """The start and the end of each MIDI clip of the arrangement, in beats, sorted."""
    spans: list[tuple[float, float]] = []
    for clip in node.findall(MIDI_CLIP_PATH):
        start = _beats(clip, "CurrentStart")
        end = _beats(clip, "CurrentEnd")
        if start is None or end is None:
            continue
        spans.append((start, end))
    return sorted(spans)


def _beats(clip: ET.Element, field: str) -> float | None:
    """The value of a time attribute of the clip, in beats; None when it is not there."""
    value = get_value(clip, field)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
