"""Oracle test: what an independent AAF reader sees in the AAF that aafton writes.

The oracle is the OpenTimelineIO AAF adapter (``otio-aaf-adapter``), a
third-party reader that knows nothing about aafton. It reads the fixture
``tests/fixtures/aaf/TESTE_AFF_PROTOOLS.aaf`` and the AAF that aafton writes
from it, and the test compares the structure the adapter sees in the two.

Only the structure is compared: the number of audio tracks, per track the
clips with their name, their kind (clip, gap or transition), their record
start and duration in frames, the number of transitions, the markers and the
global timecode start. Gain and fade curves are out of scope: the adapter
reads the gain envelopes as effects, not curves.

The adapter is optional: the module is skipped when it is not installed.
"""

from __future__ import annotations

import re
import sys
import wave
from pathlib import Path

import pytest

import aaf2
from aafton.aaf_read import read_aaf
from aafton.aaf_write import write_aaf
from aafton.aaf_write_timecode import frame_de_inicio
from aafton.report import Report

# The adapter is an optional dependency (the "dev" extra of pyproject.toml)
# and may be unpacked in the local folder of the repository.
ORACLE_LOCAL = Path(__file__).resolve().parents[2] / ".local" / "oracle-pkgs"
if ORACLE_LOCAL.is_dir():
    sys.path.insert(0, str(ORACLE_LOCAL))

pytest.importorskip("otio_aaf_adapter")
otio = pytest.importorskip("opentimelineio")
aaf_adapter = pytest.importorskip("otio_aaf_adapter.adapters.advanced_authoring_format")

FIXTURE = Path("tests/fixtures/aaf/TESTE_AFF_PROTOOLS.aaf")

# The fixture is a Pro Tools session of 25 fps and the adapter expresses the
# times in the edit rate of each slot (25 fps in the fixture, 48000 in the AAF
# that aafton writes), so every time is converted to frames before comparing.
FPS = 25

# In the Pro Tools dialect the fades are clips of their own, whose mob is
# named "Fade N".
FADE = re.compile(r"^Fade\s+\d+")

# The labels of the channels that aafton writes in the name of the MasterMob
# of a group of 5.1. The mob of a fade of Pro Tools does not carry the label
# of its channel.
CHANNEL_LABELS = (".L", ".R", ".C", ".LFE", ".Ls", ".Rs")


def _frames(time) -> int | None:
    """The time of the adapter, in frames of the timecode rate of the session."""
    return None if time is None else round(time.rescaled_to(FPS).value)


def _items(composition):
    """Every item under a composition, in timeline order."""
    if isinstance(composition, otio.schema.Timeline):
        composition = composition.tracks
    if not isinstance(composition, otio.core.Composition):
        return
    for child in list(composition):
        yield child
        yield from _items(child)


def _regions(track) -> list[tuple[str, str, int | None, int | None]]:
    """The regions of a track: (kind, name, record start, duration), in frames.

    The record start is the start of the clip inside the media it plays.
    """
    regions = []
    for item in _items(track):
        if isinstance(item, otio.schema.Transition):
            duration = _frames(item.in_offset + item.out_offset)
            regions.append(("transition", item.name or "", None, duration))
        elif isinstance(item, (otio.schema.Clip, otio.schema.Gap)):
            source = item.source_range
            kind = "clip" if isinstance(item, otio.schema.Clip) else "gap"
            start = _frames(source.start_time)
            regions.append((kind, item.name or "", start, _frames(source.duration)))
    return regions


def _audio_tracks(timeline) -> list:
    """The audio tracks of a timeline."""
    return [track for track in timeline.tracks if track.kind == "Audio"]


def _markers(timeline) -> list:
    """The markers of a timeline (by type name: ``find_children`` does not filter)."""
    return [item for item in _items(timeline) if type(item).__name__ == "Marker"]


def _clips(regions, media=False) -> list:
    """The clips of a list of regions; with media, the ones that are not fades."""
    achados = [region for region in regions if region[0] == "clip"]
    if media:
        achados = [region for region in achados if not FADE.match(region[1])]
    return achados


def _transitions(regions) -> list:
    """The transitions of a list of regions."""
    return [region for region in regions if region[0] == "transition"]


def _mesmo_nome(original: str, escrito: str) -> bool:
    """Whether the clip of the written AAF is the clip of the fixture.

    The name that the adapter reads is the name of the MasterMob of the clip.
    aafton writes the label of the channel in the name of the mob when the
    name does not carry it: the mob of a fade of Pro Tools ('Fade 26') does
    not and becomes 'Fade 26.L'.
    """
    if escrito == original:
        return True
    return any(escrito == original + label for label in CHANNEL_LABELS)


def _slot_de_timecode(path) -> tuple | None:
    """The timecode slot of an AAF: (start in frames, physical track number).

    The physical track number is None when the slot does not declare one.
    """
    with aaf2.open(str(path), "r") as f:
        for mob in f.content.mobs:
            if type(mob).__name__ != "CompositionMob":
                continue
            for slot in mob.slots:
                segment = getattr(slot, "segment", None)
                if segment is None or type(segment).__name__ != "Timecode":
                    continue
                try:
                    ptn = int(slot["PhysicalTrackNumber"].value)
                except Exception:  # noqa: BLE001 - the property may not exist
                    ptn = None
                return int(segment.start), ptn
    return None


def _dummy_media(session, directory: Path) -> None:
    """Points every asset of the session at an empty WAV file.

    The writer reads the file it links to: the file only has to be a valid WAV.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for index, asset in enumerate(session.media):
        path = directory / f"m{index:03d}.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(48000)
            handle.writeframes(b"\x00" * 9600)
        asset.resolved_path = str(path)


@pytest.fixture(scope="module")
def oracle_do_fixture():
    """What the adapter sees in the fixture."""
    return aaf_adapter.read_from_file(str(FIXTURE))


@pytest.fixture(scope="module")
def sessao_escrita(tmp_path_factory):
    """The session read from the fixture and the AAF that aafton wrote from it.

    The session is read with ``fades="keep"``: aafton keeps the fades of the
    Pro Tools dialect as clips, so the timeline it writes is the fixture's.
    """
    report = Report(strict=False)
    session = read_aaf(FIXTURE, report, fades="keep")
    _dummy_media(session, tmp_path_factory.mktemp("midia"))
    destination = tmp_path_factory.mktemp("aaf") / "fixture.aaf"
    write_aaf(session, destination, report)
    return session, destination


@pytest.fixture(scope="module")
def oracle_do_aaf(sessao_escrita):
    """What the adapter sees in the AAF that aafton wrote."""
    _, destination = sessao_escrita
    return aaf_adapter.read_from_file(str(destination))


def test_the_adapter_sees_the_same_tracks_and_clips(oracle_do_fixture, oracle_do_aaf):
    """One audio track per sound slot, with the same clips in each track.

    The fixture has 17 sound slots, but two of them ('soundly') hold only
    silence and the simplifier of the adapter drops them. aafton writes all
    the 17 slots, and the two empty ones carry the pan operation in the AAF.

    The clips are compared by name and by order, not only by count: the name
    that the adapter reads is the name of the MasterMob of the clip.
    """
    originais = _audio_tracks(oracle_do_fixture)
    escritos = _audio_tracks(oracle_do_aaf)
    assert len(originais) == 15
    assert len(escritos) == 17
    assert [_regions(track) for track in escritos[len(originais) :]] == [[], []]
    esperado = [1, 1, 21, 14, 14, 11, 11, 11, 11, 11, 11, 17, 17, 17, 17]
    assert [len(_clips(_regions(track))) for track in originais] == esperado
    assert [len(_clips(_regions(track))) for track in escritos[: len(esperado)]] == esperado
    assert sum(esperado) == 185
    for indice, (original, escrito) in enumerate(zip(originais, escritos[: len(originais)])):
        clips_original = _clips(_regions(original))
        clips_escrito = _clips(_regions(escrito))
        assert len(clips_original) == len(clips_escrito), (
            f"the track {indice} does not have the same clips"
        )
        for clip_original, clip_escrito in zip(clips_original, clips_escrito):
            assert _mesmo_nome(clip_original[1], clip_escrito[1]), (
                f"the clip {clip_original[1]!r} of the track {indice} is not the clip "
                f"{clip_escrito[1]!r} of the AAF that aafton wrote"
            )


def test_the_adapter_sees_the_same_regions(oracle_do_fixture, oracle_do_aaf):
    """Every region of the timeline is the same in the two files.

    A region is compared by its kind (clip, gap or transition) and by its
    duration, so that a gap and a clip of the same length are not confused.

    The silence that follows the last clip of a track is not written: the
    writer writes the silence between the clips only.
    """
    originais = [_regions(track) for track in _audio_tracks(oracle_do_fixture)]
    escritos = [_regions(track) for track in _audio_tracks(oracle_do_aaf)[: len(originais)]]
    for indice, (original, escrito) in enumerate(zip(originais, escritos)):
        esperado = original[:-1] if original and original[-1][0] == "gap" else original
        assert [(region[0], region[3]) for region in esperado] == [
            (region[0], region[3]) for region in escrito
        ], f"the regions of the track {indice} are not the same"


def test_the_adapter_sees_the_same_record_start(oracle_do_fixture, oracle_do_aaf):
    """The record start of the clips, relative to the first clip of the track.

    The record start that the adapter reads in the fixture is the position of
    the clip in the Pro Tools master mob, which chains the fade mobs before
    the media; aafton writes the offset of the clip inside the media file.
    The comparison takes the chain out, by measuring from the first clip of
    the track. One frame of difference is accepted: the chain of the fixture
    is one frame shorter than the offsets of the file, because the fade
    overlaps the head of the media in the chain.

    The fades are clips of their own and they are part of the comparison too.
    The record start of a fade is not the same thing in the two files: the
    adapter reads the position of the fade in the master mob chain, which is
    never the head of the chain, while aafton writes the offset inside the
    media of the fade, which is the head of the file of the fade.
    """
    originais = [_regions(track) for track in _audio_tracks(oracle_do_fixture)]
    escritos = [_regions(track) for track in _audio_tracks(oracle_do_aaf)[: len(originais)]]
    for indice, (original, escrito) in enumerate(zip(originais, escritos)):
        media_original = _clips(original, media=True)
        media_escrito = _clips(escrito, media=True)
        assert [region[1] for region in media_original] == [region[1] for region in media_escrito], (
            f"the clips of the track {indice} are not the same"
        )
        if not media_original:
            continue
        base_original = media_original[0][2]
        base_escrito = media_escrito[0][2]
        for clip_original, clip_escrito in zip(media_original, media_escrito):
            diferenca = (clip_original[2] - base_original) - (clip_escrito[2] - base_escrito)
            assert abs(diferenca) <= 1, (
                f"the record start of the clip {clip_original[1]!r} of the track {indice} "
                f"differs by {diferenca} frames"
            )
        fades_original = [region for region in _clips(original) if FADE.match(region[1])]
        fades_escrito = [region for region in _clips(escrito) if FADE.match(region[1])]
        assert len(fades_original) == len(fades_escrito), (
            f"the track {indice} does not have the same fades"
        )
        for fade_original, fade_escrito in zip(fades_original, fades_escrito):
            assert _mesmo_nome(fade_original[1], fade_escrito[1]), (
                f"the fade {fade_original[1]!r} of the track {indice} is not the fade "
                f"{fade_escrito[1]!r} of the AAF that aafton wrote"
            )
        assert all(region[2] != 0 for region in fades_original), (
            f"a fade of the track {indice} of the fixture is at the head of the chain"
        )
        assert all(region[2] == 0 for region in fades_escrito), (
            f"a fade of the track {indice} does not start at the head of its media"
        )


def test_the_adapter_sees_the_transitions_markers_and_timecode(oracle_do_fixture, oracle_do_aaf):
    """The transitions, the markers and the global timecode start.

    The fixture has no transition and no marker: the Pro Tools dialect keeps
    the fades as clips of their own ('Fade N') and writes no marker. aafton
    reads no marker either, and the adapter does not transcribe the markers
    that aafton writes.

    The global timecode start is None in the two files: the adapter reads it
    only from a timecode slot with PhysicalTrackNumber 1, and neither has one.
    """
    for oracle in (oracle_do_fixture, oracle_do_aaf):
        regioes = [_regions(track) for track in _audio_tracks(oracle)]
        assert sum(len(_transitions(regiao)) for regiao in regioes) == 0
        assert _markers(oracle) == []
        assert oracle.global_start_time is None


def test_the_timecode_of_the_written_aaf_is_the_start_of_the_session(
    oracle_do_fixture, oracle_do_aaf, sessao_escrita
):
    """The timecode of the AAF that aafton wrote is the start of the session.

    The adapter reads the global start time only from a timecode slot whose
    PhysicalTrackNumber is 1 (_get_mob_start_tc of the adapter), and neither
    the slot of the fixture nor the 'TC' slot that aafton writes has that
    number: that is why the global start time is None in the two files. The
    value is in the file: read with aaf2, the timecode slot of the AAF that
    aafton wrote starts at the start of the session, the same frame that the
    fixture has.
    """
    session, destination = sessao_escrita
    assert oracle_do_fixture.global_start_time is None
    assert oracle_do_aaf.global_start_time is None
    timecode_fixture = _slot_de_timecode(FIXTURE)
    timecode_escrito = _slot_de_timecode(destination)
    assert timecode_fixture is not None, "the fixture has no timecode slot"
    assert timecode_escrito is not None, "the AAF that aafton wrote has no timecode slot"
    assert timecode_fixture[1] != 1, "the adapter would read the timecode of the fixture"
    assert timecode_escrito[1] != 1, "the adapter would read the timecode of the written AAF"
    assert timecode_escrito[0] == timecode_fixture[0]
    frames, _truncado = frame_de_inicio(
        session.start_timecode_samples, session.timecode_rate, session.sample_rate
    )
    assert timecode_escrito[0] == frames


def test_the_absorbed_fades_become_transitions(tmp_path_factory):
    """With the fades absorbed, the crossfades become transitions.

    The Pro Tools dialect keeps the fades as clips of their own, so the
    fixture has no transition. When aafton absorbs the fades, the crossfades
    become transitions of the AAF that it writes and the clip of the fade is
    merged into the neighbour that absorbs it, so the track has fewer clips
    than the fixture (185) and the adapter counts the transitions.
    """
    report = Report(strict=False)
    session = read_aaf(FIXTURE, report, fades="absorb")
    _dummy_media(session, tmp_path_factory.mktemp("midia-absorvida"))
    destination = tmp_path_factory.mktemp("aaf-absorvida") / "fixture.aaf"
    write_aaf(session, destination, report)
    oracle = aaf_adapter.read_from_file(str(destination))
    regioes = [_regions(track) for track in _audio_tracks(oracle)]
    assert [len(_clips(regiao)) for regiao in regioes] == [
        1, 1, 10, 8, 8, 5, 5, 5, 5, 5, 5, 8, 8, 8, 8, 0, 0
    ]
    transicoes = [len(_transitions(regiao)) for regiao in regioes]
    assert transicoes == [0, 0, 11, 4, 4, 6, 6, 6, 6, 6, 6, 8, 8, 8, 8, 0, 0]
