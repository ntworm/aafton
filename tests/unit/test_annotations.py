"""Tests for Annotation writing of Marker comments and Track plugins."""

import xml.etree.ElementTree as ET

from aafton.ir import MarkerIR, TrackIR, SessionIR
from aafton.als_write import write_session, WriteOptions
from aafton.report import Report
from aafton.timecode import TimecodeRate


def test_marker_comment_written_to_annotation(tmp_path):
    session = SessionIR(
        name="test",
        start_timecode_samples=0,
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
    )
    marker = MarkerIR(
        id="m1",
        name="FOLEY \u2014 TECIDO A BATER",
        position_samples=48000,
        comment="TECIDO A BATER\nFLAG?\nSACUDIR DE PANOS",
    )
    session.markers.append(marker)

    # Teste com acento
    marker2 = MarkerIR(
        id="m2",
        name="FOLEY \u2014 P\u00c9 DESCAL\u00c7O EM MADEIRA",
        position_samples=96000,
        comment="P\u00c9 DESCAL\u00c7O EM MADEIRA\nWOOD CREAK\nPELE",
    )
    session.markers.append(marker2)

    # Teste sem comentario
    marker3 = MarkerIR(id="m3", name="SEM COMENTARIO", position_samples=144000, comment=None)
    session.markers.append(marker3)

    report = Report(strict=False)
    als_path = tmp_path / "test.als"
    opts = WriteOptions(stereo=False, permissive=True, sample_rate=48000)
    write_session(session, "templates/live12.als", als_path, opts, report)

    import gzip

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        tree = ET.parse(f)

    # Check locators
    locators = tree.findall(".//Locators/Locator")
    # locator[0] is the anchor TC
    # locator[1] is the hour locator
    assert len(locators) == 5

    loc1 = locators[2]
    assert loc1.find("Name").get("Value") == "FOLEY \u2014 TECIDO A BATER"
    assert loc1.find("Annotation").get("Value") == "TECIDO A BATER\nFLAG?\nSACUDIR DE PANOS"

    loc2 = locators[3]
    assert loc2.find("Name").get("Value") == "FOLEY \u2014 P\u00c9 DESCAL\u00c7O EM MADEIRA"
    assert (
        loc2.find("Annotation").get("Value") == "P\u00c9 DESCAL\u00c7O EM MADEIRA\nWOOD CREAK\nPELE"
    )

    loc3 = locators[4]
    assert loc3.find("Name").get("Value") == "SEM COMENTARIO"
    assert loc3.find("Annotation").get("Value") == ""


def test_track_plugins_written_to_annotation(tmp_path):
    session = SessionIR(
        name="test",
        start_timecode_samples=0,
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1, False),
    )
    track1 = TrackIR(id="t1", source_index=1, name="REF", kind="audio", channel_count=1)
    track1.annotation = (
        "Plugins no Pro Tools:\nDyn3 Compressor/Limiter (stereo)\nInsight 2 (stereo)"
    )
    session.tracks.append(track1)

    track2 = TrackIR(id="t2", source_index=2, name="NO_PLUGINS", kind="audio", channel_count=1)
    # annotation is None by default
    session.tracks.append(track2)

    report = Report(strict=False)
    als_path = tmp_path / "test_tracks.als"
    opts = WriteOptions(stereo=False, permissive=True, sample_rate=48000)
    write_session(session, "templates/live12.als", als_path, opts, report)

    import gzip

    with gzip.open(als_path, "rt", encoding="utf-8") as f:
        tree = ET.parse(f)

    audio_tracks = tree.findall(".//Tracks/AudioTrack")
    assert len(audio_tracks) == 2

    t1_annot = audio_tracks[0].find("Name/Annotation")
    assert (
        t1_annot.get("Value")
        == "Plugins no Pro Tools:\nDyn3 Compressor/Limiter (stereo)\nInsight 2 (stereo)"
    )

    t2_annot = audio_tracks[1].find("Name/Annotation")
    assert t2_annot.get("Value") == ""
