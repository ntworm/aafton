import pytest
from aafton.channels import (
    LAYOUT_QUAD,
    LAYOUT_51_SMPTE,
    LAYOUT_51_FILM,
    LAYOUT_71_SMPTE,
    LAYOUT_71_FILM,
    expand_multichannel_track,
    get_layout_channels,
)
from aafton.ir import ClipIR, TrackIR


def test_supported_layouts():
    assert LAYOUT_QUAD == ["L", "R", "Ls", "Rs"]
    assert LAYOUT_51_SMPTE == ["L", "R", "C", "LFE", "Ls", "Rs"]
    assert LAYOUT_51_FILM == ["L", "C", "R", "Ls", "Rs", "LFE"]
    assert LAYOUT_71_SMPTE == ["L", "R", "C", "LFE", "Lss", "Rss", "Lsr", "Rsr"]
    assert LAYOUT_71_FILM == ["L", "C", "R", "Lss", "Rss", "Lsr", "Rsr", "LFE"]

    assert get_layout_channels(4, "quad") == LAYOUT_QUAD
    assert get_layout_channels(6, "smpte") == LAYOUT_51_SMPTE
    assert get_layout_channels(6, "film") == LAYOUT_51_FILM
    assert get_layout_channels(8, "smpte") == LAYOUT_71_SMPTE
    assert get_layout_channels(8, "film") == LAYOUT_71_FILM


def test_expand_multichannel_track_mono_and_stereo():
    mono = TrackIR(
        id="t_mono",
        source_index=0,
        name="Dialogue Mono",
        kind="audio",
        channel_count=1,
    )
    assert expand_multichannel_track(mono) == [mono]

    stereo = TrackIR(
        id="t_stereo",
        source_index=1,
        name="Music Stereo",
        kind="audio",
        channel_count=2,
    )
    assert expand_multichannel_track(stereo) == [stereo]


def test_expand_multichannel_track_quad():
    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_quad",
            channel_index=i,
            event_index=i,
            name=f"QuadClip_{i}",
            media_id=f"m_{i}",
            timeline_start_samples=0,
            timeline_end_samples=48000,
        )
        for i in range(4)
    ]
    track = TrackIR(
        id="t_quad",
        source_index=10,
        original_source_indices=[10, 11, 12, 13],
        name="Ambience Quad",
        kind="audio",
        channel_count=4,
        clips=clips,
    )

    expanded = expand_multichannel_track(track, layout_name="quad")
    assert len(expanded) == 4
    assert [t.name for t in expanded] == [
        "Ambience Quad [L]",
        "Ambience Quad [R]",
        "Ambience Quad [Ls]",
        "Ambience Quad [Rs]",
    ]
    assert [t.channel_count for t in expanded] == [1, 1, 1, 1]
    assert [t.pan for t in expanded] == [-1.0, 1.0, -1.0, 1.0]

    for idx, child in enumerate(expanded):
        assert len(child.clips) == 1
        assert child.clips[0].name == f"QuadClip_{idx}"
        assert child.clips[0].channel_index == 0


def test_expand_multichannel_track_51_smpte():
    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_51",
            channel_index=i,
            event_index=i,
            name=f"FX_{i}",
            media_id=f"m_{i}",
            timeline_start_samples=1000,
            timeline_end_samples=5000,
        )
        for i in range(6)
    ]
    track = TrackIR(
        id="t_51",
        source_index=20,
        name="FX 5.1",
        kind="audio",
        channel_count=6,
        clips=clips,
    )

    expanded = expand_multichannel_track(track, layout_name="smpte")
    assert len(expanded) == 6
    assert [t.name for t in expanded] == [
        "FX 5.1 [L]",
        "FX 5.1 [R]",
        "FX 5.1 [C]",
        "FX 5.1 [LFE]",
        "FX 5.1 [Ls]",
        "FX 5.1 [Rs]",
    ]
    assert [t.pan for t in expanded] == [-1.0, 1.0, 0.0, 0.0, -1.0, 1.0]
    for idx, child in enumerate(expanded):
        assert len(child.clips) == 1
        assert child.clips[0].name == f"FX_{idx}"
        assert child.clips[0].channel_index == 0


def test_expand_multichannel_track_51_film():
    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_51f",
            channel_index=i,
            event_index=i,
            name=f"PrintMaster_{i}",
            media_id=f"m_{i}",
            timeline_start_samples=0,
            timeline_end_samples=10000,
        )
        for i in range(6)
    ]
    track = TrackIR(
        id="t_51f",
        source_index=30,
        name="PrintMaster",
        kind="audio",
        channel_count=6,
        clips=clips,
    )

    expanded = expand_multichannel_track(track, layout_name="film")
    assert len(expanded) == 6
    assert [t.name for t in expanded] == [
        "PrintMaster [L]",
        "PrintMaster [C]",
        "PrintMaster [R]",
        "PrintMaster [Ls]",
        "PrintMaster [Rs]",
        "PrintMaster [LFE]",
    ]
    assert [t.pan for t in expanded] == [-1.0, 0.0, 1.0, -1.0, 1.0, 0.0]


def test_expand_multichannel_track_71_smpte():
    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_71",
            channel_index=i,
            event_index=i,
            name=f"Bed_{i}",
            media_id=f"m_{i}",
            timeline_start_samples=0,
            timeline_end_samples=8000,
        )
        for i in range(8)
    ]
    track = TrackIR(
        id="t_71",
        source_index=40,
        name="Bed 7.1",
        kind="audio",
        channel_count=8,
        clips=clips,
    )

    expanded = expand_multichannel_track(track, layout_name="smpte")
    assert len(expanded) == 8
    assert [t.name for t in expanded] == [
        "Bed 7.1 [L]",
        "Bed 7.1 [R]",
        "Bed 7.1 [C]",
        "Bed 7.1 [LFE]",
        "Bed 7.1 [Lss]",
        "Bed 7.1 [Rss]",
        "Bed 7.1 [Lsr]",
        "Bed 7.1 [Rsr]",
    ]
    assert [t.pan for t in expanded] == [-1.0, 1.0, 0.0, 0.0, -1.0, 1.0, -1.0, 1.0]


def test_expand_multichannel_track_71_film():
    clips = [
        ClipIR(
            id=f"c_{i}",
            track_id="t_71f",
            channel_index=i,
            event_index=i,
            name=f"BedFilm_{i}",
            media_id=f"m_{i}",
            timeline_start_samples=0,
            timeline_end_samples=8000,
        )
        for i in range(8)
    ]
    track = TrackIR(
        id="t_71f",
        source_index=50,
        name="Bed 7.1 Film",
        kind="audio",
        channel_count=8,
        clips=clips,
    )

    expanded = expand_multichannel_track(track, layout_name="film")
    assert len(expanded) == 8
    assert [t.name for t in expanded] == [
        "Bed 7.1 Film [L]",
        "Bed 7.1 Film [C]",
        "Bed 7.1 Film [R]",
        "Bed 7.1 Film [Lss]",
        "Bed 7.1 Film [Rss]",
        "Bed 7.1 Film [Lsr]",
        "Bed 7.1 Film [Rsr]",
        "Bed 7.1 Film [LFE]",
    ]
    assert [t.pan for t in expanded] == [-1.0, 0.0, 1.0, -1.0, 1.0, -1.0, 1.0, 0.0]


def test_expand_multichannel_unsupported_count():
    track = TrackIR(
        id="t_odd",
        source_index=60,
        name="Odd",
        kind="audio",
        channel_count=3,
    )
    with pytest.raises(ValueError, match="not supported|unsupported"):
        expand_multichannel_track(track)
