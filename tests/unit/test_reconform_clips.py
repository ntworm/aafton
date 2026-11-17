"""Testes unitarios de reconform_samples e reconform_clips."""

from __future__ import annotations

import math
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_read import read_session
from aafton.als_read_clips import _beats_to_samples
from aafton.als_write import WriteOptions, write_session
from aafton.als_write_base import LIVE_CLIP_TICKS_PER_BEAT as TICKS
from aafton.als_write_base import SENTINEL_TIME, TIMELINE, samples_to_beats
from aafton.als_xml import (
    IdAllocator,
    classify_id_tags,
    get_value,
    load_als,
    save_als,
    set_value,
    validate_document,
)
from aafton.cut_ir import CutEvent, CutIR
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.reconform_clips import ClipsResult, reconform_clips
from aafton.reconform_diff import diff_cuts
from aafton.reconform_map import Saiu, Segment, TimeMap
from aafton.reconform_samples import SampleMap
from aafton.report import Report, StrictModeError
from aafton.sidecar import read_anchor
from aafton.timecode import TimecodeRate, frames_to_samples_exact, quantize_samples

HOUR = 172_800_000
RATE_25 = TimecodeRate(25, 1)
SPF_25 = 1920
HOUR_F25 = 90_000


def _clip_ev(idx: int, src: str, rec_in: int, rec_out: int, src_in: int) -> CutEvent:
    """Constroi um CutEvent de clip para os testes."""
    return CutEvent(idx, "clip", src, None, src_in, src_in + (rec_out - rec_in), rec_in, rec_out)


def _cut(events: list[CutEvent], start_frames: int = HOUR_F25) -> CutIR:
    """Constroi um CutIR a 25 fps."""
    return CutIR("cut.edl", RATE_25, start_frames, events)


def _write_wav(path: Path, duration_samples: int) -> None:
    """Grava um WAV mono silencioso para o set de teste."""
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * duration_samples)


def _make_set(
    tmp_path: Path,
    clips_spec: list[ClipIR],
    *,
    bpm: float = 60.0,
    origin: int = HOUR,
) -> Path:
    """Gera um .als real a partir de templates/live12.als seguindo test_review_robustness."""
    wav = tmp_path / "source0.wav"
    _write_wav(wav, 192_000)
    asset = MediaAsset(
        id="m0",
        filename=wav.name,
        channels=1,
        sample_rate=48000,
        duration_samples=192_000,
        status="resolved",
        resolved_path=str(wav),
    )
    track = TrackIR(
        id="t0",
        source_index=0,
        original_source_indices=[101],
        name="Track 0",
        kind="audio",
        channel_count=1,
        clips=clips_spec,
    )
    session = SessionIR(
        name="ReconformTest",
        sample_rate=48000,
        timecode_rate=RATE_25,
        start_timecode_samples=HOUR,
        tracks=[track],
        media=[asset],
    )
    path = tmp_path / f"set_{int(bpm)}.als"
    write_session(
        session,
        Path("templates/live12.als"),
        path,
        WriteOptions(sample_rate=48000, bpm=bpm, live_origin_samples=origin),
        Report(),
    )
    return path


def _attach_clip_envelope(
    clip_node: ET.Element,
    event_tag: str,
    events: list[tuple[float, str]],
) -> None:
    """Anexa um ClipEnvelope sintetico em Envelopes/Envelopes do AudioClip."""
    outer = clip_node.find("Envelopes")
    if outer is None:
        outer = ET.SubElement(clip_node, "Envelopes")
    inner = outer.find("Envelopes")
    if inner is None:
        inner = ET.SubElement(outer, "Envelopes")
    clip_env = ET.SubElement(inner, "ClipEnvelope", {"Id": str(len(list(inner)))})
    auto = ET.SubElement(clip_env, "Automation")
    ev_box = ET.SubElement(auto, "Events")
    for idx, (t_val, val_str) in enumerate(events):
        ET.SubElement(ev_box, event_tag, {"Id": str(idx), "Time": f"{t_val:g}", "Value": val_str})


def _events_of_first_track(root: ET.Element) -> list[ET.Element]:
    """Devolve os AudioClips do arranjo da primeira AudioTrack."""
    track = root.find("./LiveSet/Tracks/AudioTrack")
    assert track is not None
    events = track.find(TRACK_FIELDS["arrangement_events"])
    assert events is not None
    return events.findall("AudioClip")


def test_sample_map_spans_e_map_sample() -> None:
    """Valida leader, tail, coalescencia de map, saiu com offset e taxa fracionaria."""
    tm = TimeMap(
        rate=RATE_25,
        segments=[Segment(10, 20, 10), Segment(20, 30, 20), Segment(40, 60, 30)],
        saiu=[Saiu(30, 40, 30)],
        leader_delta=0,
        tail_delta=-10,
    )
    smap = SampleMap.from_time_map(tm, 48000)
    assert smap.map_sample(5 * SPF_25) == 5 * SPF_25
    assert smap.map_sample(35 * SPF_25) is None
    assert smap.map_sample(45 * SPF_25) == 35 * SPF_25
    assert smap.map_sample(70 * SPF_25) == 60 * SPF_25
    assert smap.spans(5 * SPF_25, 65 * SPF_25) == [
        (5 * SPF_25, 30 * SPF_25, "map", 0),
        (30 * SPF_25, 40 * SPF_25, "saiu", 30 * SPF_25),
        (40 * SPF_25, 65 * SPF_25, "map", -10 * SPF_25),
    ]
    assert smap.spans(33 * SPF_25, 38 * SPF_25) == [
        (33 * SPF_25, 38 * SPF_25, "saiu", 33 * SPF_25),
    ]
    rate_2997 = TimecodeRate(30000, 1001)
    tm_frac = TimeMap(rate=rate_2997, segments=[Segment(10, 25, 15)], leader_delta=5, tail_delta=5)
    smap_frac = SampleMap.from_time_map(tm_frac, 48000)
    exp_in, _ = quantize_samples(frames_to_samples_exact(10, rate_2997, 48000))
    exp_new, _ = quantize_samples(frames_to_samples_exact(15, rate_2997, 48000))
    assert smap_frac.map_sample(exp_in) == exp_new


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_clip_movido_inteiro_por_insercao(tmp_path: Path, bpm: float) -> None:
    """Insercao antes do clip desloca Time/CurrentStart/CurrentEnd com precisao de sample."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Clip 0",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 50 * SPF_25,
        source_offset_samples=0,
    )
    path = _make_set(tmp_path, [clip], bpm=bpm)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "B", HOUR_F25 + 25, HOUR_F25 + 50, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "INS", HOUR_F25 + 25, HOUR_F25 + 32, 900),
            _clip_ev(3, "B", HOUR_F25 + 32, HOUR_F25 + 57, 200),
        ]
    )
    cl = diff_cuts(old, new, Report())
    assert cl is not None and cl.time_map is not None
    smap = SampleMap.from_time_map(cl.time_map, 48000)
    root = load_als(path)
    anchor = read_anchor(path)
    allocator = IdAllocator.from_root(root)
    result = reconform_clips(root, anchor, smap, Report(), allocator)
    assert (result.moved, result.sliced, result.saiu) == (1, 0, [])
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)
    save_als(root, path)
    reloaded = read_session(path, Report()).tracks[0].clips[0]
    assert reloaded.timeline_start_samples == HOUR + 32 * SPF_25
    assert reloaded.timeline_end_samples == HOUR + 57 * SPF_25


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_clip_cruzando_corte_deletado_loop_e_saiu(tmp_path: Path, bpm: float) -> None:
    """Clip atravessando trecho deletado gera 2 pecas map + 1 saiu com LoopStart exato."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Clip 0",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 75 * SPF_25,
        source_offset_samples=9600,
    )
    path = _make_set(tmp_path, [clip], bpm=bpm)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "X", HOUR_F25 + 40, HOUR_F25 + 53, 500),
            _clip_ev(3, "B", HOUR_F25 + 53, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "B", HOUR_F25 + 40, HOUR_F25 + 62, 200),
        ]
    )
    cl = diff_cuts(old, new, Report())
    assert cl is not None and cl.time_map is not None
    smap = SampleMap.from_time_map(cl.time_map, 48000)
    root = load_als(path)
    orig_clip = _events_of_first_track(root)[0]
    orig_out_marker = get_value(orig_clip, AUDIO_CLIP_FIELDS["out_marker"])
    orig_start_rel = get_value(orig_clip, AUDIO_CLIP_FIELDS["start_relative"])
    result = reconform_clips(root, read_anchor(path), smap, Report(), IdAllocator.from_root(root))
    assert (result.moved, result.sliced, len(result.saiu)) == (0, 1, 1)
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)
    clips_xml = _events_of_first_track(root)
    assert len(clips_xml) == 2
    assert round(float(get_value(clips_xml[0], AUDIO_CLIP_FIELDS["loop_start"])) * 48000) == 9600
    assert (
        round(float(get_value(clips_xml[0], AUDIO_CLIP_FIELDS["loop_end"])) * 48000)
        == 9600 + 15 * SPF_25
    )
    assert (
        round(float(get_value(clips_xml[1], AUDIO_CLIP_FIELDS["loop_start"])) * 48000)
        == 9600 + 28 * SPF_25
    )
    assert (
        round(float(get_value(clips_xml[1], AUDIO_CLIP_FIELDS["loop_end"])) * 48000)
        == 9600 + 50 * SPF_25
    )
    for node in [*clips_xml, result.saiu[0].clip_element]:
        assert get_value(node, AUDIO_CLIP_FIELDS["hidden_loop_start"]) == get_value(
            node, AUDIO_CLIP_FIELDS["loop_start"]
        )
        assert get_value(node, AUDIO_CLIP_FIELDS["hidden_loop_end"]) == get_value(
            node, AUDIO_CLIP_FIELDS["loop_end"]
        )
        assert get_value(node, AUDIO_CLIP_FIELDS["out_marker"]) == orig_out_marker
        assert get_value(node, AUDIO_CLIP_FIELDS["start_relative"]) == orig_start_rel
    saiu_piece = result.saiu[0]
    assert saiu_piece.track_node is root.find("./LiveSet/Tracks/AudioTrack")
    assert saiu_piece.new_abs_start == HOUR + 40 * SPF_25
    assert (
        round(float(get_value(saiu_piece.clip_element, AUDIO_CLIP_FIELDS["loop_start"])) * 48000)
        == 9600 + 15 * SPF_25
    )
    save_als(root, path)
    reloaded = read_session(path, Report()).tracks[0].clips
    assert [
        (c.timeline_start_samples, c.timeline_end_samples, c.source_offset_samples)
        for c in reloaded
    ] == [
        (HOUR + 25 * SPF_25, HOUR + 40 * SPF_25, 9600),
        (HOUR + 40 * SPF_25, HOUR + 62 * SPF_25, 9600 + 28 * SPF_25),
    ]


@pytest.mark.parametrize("bpm", [60.0, 97.0, 120.0])
def test_reconformado_toca_na_amostra_da_grade(tmp_path: Path, bpm: float) -> None:
    """Cada clip reconformado toca na amostra esperada depois da grade do Live."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Clip 0",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 75 * SPF_25,
        source_offset_samples=9600,
    )
    path = _make_set(tmp_path, [clip], bpm=bpm)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "X", HOUR_F25 + 40, HOUR_F25 + 53, 500),
            _clip_ev(3, "B", HOUR_F25 + 53, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "B", HOUR_F25 + 40, HOUR_F25 + 62, 200),
        ]
    )
    smap = SampleMap.from_time_map(diff_cuts(old, new, Report()).time_map, 48000)
    root = load_als(path)
    result = reconform_clips(root, read_anchor(path), smap, Report(), IdAllocator.from_root(root))
    assert (result.moved, result.sliced, len(result.saiu)) == (0, 1, 1)
    esperado = [
        (HOUR + 25 * SPF_25, HOUR + 40 * SPF_25),
        (HOUR + 40 * SPF_25, HOUR + 62 * SPF_25),
        (HOUR + 40 * SPF_25, HOUR + 53 * SPF_25),
    ]
    clips = [*_events_of_first_track(root), result.saiu[0].clip_element]
    for node, (inicio, fim) in zip(clips, esperado):
        inicio_b = float(get_value(node, AUDIO_CLIP_FIELDS["current_start"]))
        fim_b = float(get_value(node, AUDIO_CLIP_FIELDS["current_end"]))
        # A posicao que o Live guarda: o inicio e o comprimento na grade de ticks.
        salvo_inicio = round(inicio_b * TICKS) / TICKS
        salvo_fim = salvo_inicio + round((fim_b - inicio_b) * TICKS) / TICKS
        assert math.ceil(salvo_inicio * 60.0 / bpm * 48000) + HOUR == inicio
        assert math.ceil(salvo_fim * 60.0 / bpm * 48000) + HOUR == fim


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_fades_e_crossfade_nas_pontas_corretas(tmp_path: Path, bpm: float) -> None:
    """FadeIn fica na primeira ponta (com clamp), FadeOut na ultima, e emendas zeram."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Clip 0",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 75 * SPF_25,
        source_offset_samples=0,
        fade_in=FadeIR(kind="in", duration_samples=48_000),
        fade_out=FadeIR(kind="out", duration_samples=19_200),
        crossfade_in_state=True,
    )
    path = _make_set(tmp_path, [clip], bpm=bpm)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "X", HOUR_F25 + 40, HOUR_F25 + 53, 500),
            _clip_ev(3, "B", HOUR_F25 + 53, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "B", HOUR_F25 + 40, HOUR_F25 + 62, 200),
        ]
    )
    smap = SampleMap.from_time_map(diff_cuts(old, new, Report()).time_map, 48000)
    root = load_als(path)
    anchor = read_anchor(path)
    result = reconform_clips(root, anchor, smap, Report(), IdAllocator.from_root(root))
    clips_xml = _events_of_first_track(root)
    saiu_xml = result.saiu[0].clip_element
    exp_in = samples_to_beats(15 * SPF_25, 48000, bpm, TIMELINE)
    exp_out = samples_to_beats(19_200, 48000, bpm, TIMELINE)
    assert float(get_value(clips_xml[0], AUDIO_CLIP_FIELDS["fade_in_length"])) == pytest.approx(
        exp_in
    )
    assert float(get_value(clips_xml[0], AUDIO_CLIP_FIELDS["fade_out_length"])) == 0.0
    assert get_value(clips_xml[0], AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "1"
    assert float(get_value(saiu_xml, AUDIO_CLIP_FIELDS["fade_in_length"])) == 0.0
    assert float(get_value(saiu_xml, AUDIO_CLIP_FIELDS["fade_out_length"])) == 0.0
    assert get_value(saiu_xml, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "0"
    assert float(get_value(clips_xml[1], AUDIO_CLIP_FIELDS["fade_in_length"])) == 0.0
    assert float(get_value(clips_xml[1], AUDIO_CLIP_FIELDS["fade_out_length"])) == pytest.approx(
        exp_out
    )
    assert get_value(clips_xml[1], AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "0"
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_clip_envelope_fatiado_float_e_bool(tmp_path: Path, bpm: float) -> None:
    """ClipEnvelope preserva sentinela, interpola FloatEvent e segura valor em BoolEvent."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Clip 0",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 75 * SPF_25,
        source_offset_samples=0,
    )
    path = _make_set(tmp_path, [clip], bpm=bpm)
    root = load_als(path)
    total_beats = 2.0 * (bpm / 60.0)
    mid_step = 0.8 * (bpm / 60.0)
    clip_xml = _events_of_first_track(root)[0]
    _attach_clip_envelope(
        clip_xml,
        "FloatEvent",
        [(SENTINEL_TIME, "0"), (0.0, "0"), (total_beats, "1")],
    )
    _attach_clip_envelope(
        clip_xml,
        "BoolEvent",
        [(SENTINEL_TIME, "false"), (0.0, "false"), (mid_step, "true")],
    )
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "X", HOUR_F25 + 40, HOUR_F25 + 53, 500),
            _clip_ev(3, "B", HOUR_F25 + 53, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 40, 100),
            _clip_ev(2, "B", HOUR_F25 + 40, HOUR_F25 + 62, 200),
        ]
    )
    smap = SampleMap.from_time_map(diff_cuts(old, new, Report()).time_map, 48000)
    result = reconform_clips(root, read_anchor(path), smap, Report(), IdAllocator.from_root(root))
    p0, p2 = _events_of_first_track(root)
    p1 = result.saiu[0].clip_element
    for piece, frac_in, frac_out in ((p0, 0.0, 0.3), (p1, 0.3, 0.56), (p2, 0.56, 1.0)):
        envs = piece.findall("Envelopes/Envelopes/ClipEnvelope")
        f_events = list(envs[0].find("Automation/Events"))
        assert float(f_events[0].get("Time")) == SENTINEL_TIME
        obtido = [v for e in f_events[1:] for v in (float(e.get("Time")), float(e.get("Value")))]
        esperado = [0.0, frac_in, (frac_out - frac_in) * total_beats, frac_out]
        assert obtido == pytest.approx(esperado)
        b_events = list(envs[1].find("Automation/Events"))
        assert float(b_events[0].get("Time")) == SENTINEL_TIME
        tempos = [float(e.get("Time")) for e in b_events[1:]]
        valores = [e.get("Value") for e in b_events[1:]]
        esperado_b = (
            [(0.0, "false"), (0.6 * (bpm / 60.0), "false")]
            if piece is p0
            else [
                (0.0, "false"),
                (mid_step - 0.6 * (bpm / 60.0), "true"),
                (0.52 * (bpm / 60.0), "true"),
            ]
            if piece is p1
            else [(0.0, "true"), (0.88 * (bpm / 60.0), "true")]
        )
        assert tempos == pytest.approx([t for t, _ in esperado_b])
        assert valores == [v for _, v in esperado_b]
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)


def test_loop_on_bloqueado_sem_mutar_root(tmp_path: Path) -> None:
    """Clip com LoopOn ativo gera RECONFORM_LOOPED_CLIP e deixa o root intacto."""
    clips = [
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="Ok",
            media_id="m0",
            timeline_start_samples=HOUR + 25 * SPF_25,
            timeline_end_samples=HOUR + 50 * SPF_25,
        ),
        ClipIR(
            id="c1",
            track_id="t0",
            channel_index=0,
            event_index=1,
            name="Looped",
            media_id="m0",
            timeline_start_samples=HOUR + 50 * SPF_25,
            timeline_end_samples=HOUR + 75 * SPF_25,
        ),
    ]
    path = _make_set(tmp_path, clips, bpm=60.0)
    root = load_als(path)
    set_value(_events_of_first_track(root)[1], AUDIO_CLIP_FIELDS["loop_on"], True)
    before = ET.tostring(root)
    old = _cut([_clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 75, 100)])
    new = _cut([_clip_ev(1, "A", HOUR_F25 + 35, HOUR_F25 + 85, 100)])
    smap = SampleMap.from_time_map(diff_cuts(old, new, Report()).time_map, 48000)
    report = Report(strict=False)
    result = reconform_clips(root, read_anchor(path), smap, report, IdAllocator.from_root(root))
    assert result == ClipsResult()
    assert report.incomplete
    assert any(issue.code == "RECONFORM_LOOPED_CLIP" for issue in report.issues)
    assert ET.tostring(root) == before
    with pytest.raises(StrictModeError, match="RECONFORM_LOOPED_CLIP"):
        reconform_clips(
            root, read_anchor(path), smap, Report(strict=True), IdAllocator.from_root(root)
        )


def test_warped_clip_inteiro_ok_e_fatiado_bloqueado(tmp_path: Path) -> None:
    """Clip com warp movido inteiro passa, mas fatiado bloqueia sem alterar o root."""
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="Warped",
        media_id="m0",
        timeline_start_samples=HOUR + 25 * SPF_25,
        timeline_end_samples=HOUR + 75 * SPF_25,
    )
    path = _make_set(tmp_path, [clip], bpm=60.0)
    root = load_als(path)
    clip_xml = _events_of_first_track(root)[0]
    set_value(clip_xml, AUDIO_CLIP_FIELDS["is_warped"], True)
    set_value(clip_xml, AUDIO_CLIP_FIELDS["loop_start"], 12.5)
    old_move = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "B", HOUR_F25 + 25, HOUR_F25 + 75, 200),
        ]
    )
    new_move = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "INS", HOUR_F25 + 25, HOUR_F25 + 35, 900),
            _clip_ev(3, "B", HOUR_F25 + 35, HOUR_F25 + 85, 200),
        ]
    )
    smap_move = SampleMap.from_time_map(diff_cuts(old_move, new_move, Report()).time_map, 48000)
    anchor = read_anchor(path)
    res_ok = reconform_clips(root, anchor, smap_move, Report(), IdAllocator.from_root(root))
    assert res_ok.moved == 1
    assert HOUR + _beats_to_samples(float(clip_xml.get("Time")), 48000, 60.0) == HOUR + 35 * SPF_25
    assert get_value(clip_xml, AUDIO_CLIP_FIELDS["loop_start"]) == "12.5"
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)

    before = ET.tostring(root)
    old_split = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 35, HOUR_F25 + 60, 100),
            _clip_ev(2, "B", HOUR_F25 + 60, HOUR_F25 + 85, 200),
        ]
    )
    new_split = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 35, HOUR_F25 + 60, 100),
            _clip_ev(2, "INS", HOUR_F25 + 60, HOUR_F25 + 70, 900),
            _clip_ev(3, "B", HOUR_F25 + 70, HOUR_F25 + 95, 200),
        ]
    )
    smap_split = SampleMap.from_time_map(diff_cuts(old_split, new_split, Report()).time_map, 48000)
    report = Report(strict=False)
    res_err = reconform_clips(root, anchor, smap_split, report, IdAllocator.from_root(root))
    assert res_err == ClipsResult()
    assert report.incomplete
    assert any(issue.code == "RECONFORM_WARPED_CLIP" for issue in report.issues)
    assert ET.tostring(root) == before


def test_reordenacao_de_clips_e_ids_unicos(tmp_path: Path) -> None:
    """Troca de ordem reordena Events por Time e mantem Ids validos em validate_document."""
    clips = [
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="Primeiro",
            media_id="m0",
            timeline_start_samples=HOUR + 25 * SPF_25,
            timeline_end_samples=HOUR + 50 * SPF_25,
        ),
        ClipIR(
            id="c1",
            track_id="t0",
            channel_index=0,
            event_index=1,
            name="Segundo",
            media_id="m0",
            timeline_start_samples=HOUR + 50 * SPF_25,
            timeline_end_samples=HOUR + 75 * SPF_25,
        ),
    ]
    path = _make_set(tmp_path, clips, bpm=60.0)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25 + 25, HOUR_F25 + 50, 100),
            _clip_ev(2, "B", HOUR_F25 + 50, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "B", HOUR_F25 + 25, HOUR_F25 + 50, 200),
            _clip_ev(2, "A", HOUR_F25 + 50, HOUR_F25 + 75, 100),
        ]
    )
    smap = SampleMap.from_time_map(diff_cuts(old, new, Report()).time_map, 48000)
    root = load_als(path)
    result = reconform_clips(root, read_anchor(path), smap, Report(), IdAllocator.from_root(root))
    assert result.moved == 2
    clips_xml = _events_of_first_track(root)
    assert [get_value(c, AUDIO_CLIP_FIELDS["name"]) for c in clips_xml] == ["Segundo", "Primeiro"]
    assert [c.get("Id") for c in clips_xml] == ["0", "1"]
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)
