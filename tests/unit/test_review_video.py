import aaf2

from aafton.aaf_read import read_aaf
from aafton.report import Report
from aafton import video


def test_picture_slot_keeps_absolute_origin_and_source_trim(tmp_path, monkeypatch):
    movie = tmp_path / "raw.mp4"
    movie.write_bytes(b"fixture")
    path = tmp_path / "source.aaf"
    with aaf2.open(str(path), "w") as f:
        comp = f.create.CompositionMob("Film")
        f.content.mobs.append(comp)
        source = f.create.SourceMob("raw")
        f.content.mobs.append(source)
        descriptor = f.create.CDCIDescriptor()
        for key, value in {
            "SampleRate": 25,
            "Length": 2500,
            "StoredWidth": 1920,
            "StoredHeight": 1080,
            "FrameLayout": "FullFrame",
            "VideoLineMap": [0, 0],
            "ImageAspectRatio": "16/9",
            "ComponentWidth": 8,
            "HorizontalSubsampling": 2,
        }.items():
            descriptor[key].value = value
        locator = f.create.NetworkLocator()
        locator["URLString"].value = movie.as_uri()
        descriptor["Locator"].append(locator)
        source.descriptor = descriptor
        source_slot = source.create_timeline_slot(edit_rate=25)
        source_slot.segment = f.create.SourceClip(media_kind="picture", length=2500)
        slot = comp.create_timeline_slot(edit_rate=25)
        seq = f.create.Sequence(media_kind="picture")
        slot.segment = seq
        seq.components.append(f.create.Filler("picture", length=25))
        clip = f.create.SourceClip(media_kind="picture", start=250, length=50)
        clip.mob, clip.slot_id = source, source_slot.slot_id
        seq.components.append(clip)
        tcslot = comp.create_timeline_slot(edit_rate=25)
        tc = f.create.Timecode(fps=25)
        tc.start = 90000
        tcslot.segment = tc
    session = read_aaf(path, Report())
    monkeypatch.setattr(video, "analyze_video", lambda p: (100.0, None, "h264"))
    monkeypatch.setattr(video, "video_has_audio", lambda p: True)
    video.process_video(session, path, str(movie), None, Report())
    clip = session.tracks[0].clips[0]
    assert clip.timeline_start_samples == 172848000
    assert clip.duration_samples == 96000
    assert clip.source_offset_samples == 480000
