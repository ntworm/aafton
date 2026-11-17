"""Testes unitarios de reconform_tracks e reconform_automation."""

from __future__ import annotations

import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_write_base import SENTINEL_TIME, TIMELINE, samples_to_beats
from aafton.als_xml import (
    IdAllocator,
    classify_id_tags,
    format_value,
    get_value,
    load_als,
    renumber_siblings,
    save_als,
    validate_document,
)
from aafton.cut_ir import CutEvent, CutIR
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.reconform_automation import shift_track_automation
from aafton.reconform_clips import reconform_clips
from aafton.reconform_diff import diff_cuts
from aafton.reconform_samples import SampleMap
from aafton.reconform_tracks import SaiuTracks, place_saiu_pieces
from aafton.report import Report
from aafton.sidecar import read_anchor
from aafton.timecode import TimecodeRate

HOUR = 172_800_000
RATE_25 = TimecodeRate(25, 1)
SPF_25 = 1920
HOUR_F25 = 90_000


def _clip_ev(idx: int, src: str, rec_in: int, rec_out: int, src_in: int) -> CutEvent:
    """Constroi um CutEvent de clip para os testes."""
    return CutEvent(idx, "clip", src, None, src_in, src_in + (rec_out - rec_in), rec_in, rec_out)


def _cut(events: list[CutEvent]) -> CutIR:
    """Constroi um CutIR a 25 fps com inicio na marca de 1 hora."""
    return CutIR("cut.edl", RATE_25, HOUR_F25, events)


def _track_spec(
    idx: int,
    name: str,
    clip_frames: tuple[int, int] | None,
    *,
    gain_points: list[tuple[int, float]] | None = None,
    organize_group: str | None = None,
) -> TrackIR:
    """Constroi um TrackIR de teste com um clip opcional."""
    clips = []
    if clip_frames is not None:
        clips.append(
            ClipIR(
                id=f"c{idx}",
                track_id=f"t{idx}",
                channel_index=0,
                event_index=0,
                name=f"Clip {idx}",
                media_id="m0",
                timeline_start_samples=HOUR + clip_frames[0] * SPF_25,
                timeline_end_samples=HOUR + clip_frames[1] * SPF_25,
            )
        )
    return TrackIR(
        id=f"t{idx}",
        source_index=idx,
        original_source_indices=[101 + idx],
        name=name,
        kind="audio",
        channel_count=1,
        gain_points=gain_points or [],
        organize_group=organize_group,
        clips=clips,
    )


def _make_set(
    tmp_path: Path,
    tracks_spec: list[TrackIR],
    *,
    bpm: float = 60.0,
    groups: str = "none",
) -> Path:
    """Gera um .als real a partir de templates/live12.als seguindo test_review_robustness."""
    wav = tmp_path / "source0.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * 192_000)
    asset = MediaAsset(
        id="m0",
        filename=wav.name,
        channels=1,
        sample_rate=48000,
        duration_samples=192_000,
        status="resolved",
        resolved_path=str(wav),
    )
    session = SessionIR(
        name="ReconformTracksTest",
        sample_rate=48000,
        timecode_rate=RATE_25,
        start_timecode_samples=HOUR,
        tracks=tracks_spec,
        media=[asset],
    )
    path = tmp_path / f"set_{int(bpm)}.als"
    opts = WriteOptions(sample_rate=48000, bpm=bpm, live_origin_samples=HOUR, groups=groups)
    write_session(session, Path("templates/live12.als"), path, opts, Report())
    return path


def _attach_env(
    track_node: ET.Element,
    target_id: str,
    event_tag: str,
    events: list[tuple[float, str]],
) -> ET.Element:
    """Anexa um AutomationEnvelope sintetico em AutomationEnvelopes/Envelopes da track."""
    outer = track_node.find("AutomationEnvelopes")
    if outer is None:
        outer = ET.SubElement(track_node, "AutomationEnvelopes")
    inner = outer.find("Envelopes")
    if inner is None:
        inner = ET.SubElement(outer, "Envelopes")
    env = ET.SubElement(inner, "AutomationEnvelope", {"Id": str(len(list(inner)))})
    ET.SubElement(ET.SubElement(env, "EnvelopeTarget"), "PointeeId", {"Value": target_id})
    auto = ET.SubElement(env, "Automation")
    ev_box = ET.SubElement(auto, "Events")
    for idx, (t_val, val_str) in enumerate(events):
        ET.SubElement(
            ev_box, event_tag, {"Id": str(idx), "Time": format_value(t_val), "Value": val_str}
        )
    ET.SubElement(auto, "AutomationTransformViewState")
    renumber_siblings(inner, "AutomationEnvelope")
    return env


def _run_apply(
    root: ET.Element,
    path: Path,
    old: CutIR,
    new: CutIR,
) -> tuple[SaiuTracks, Report]:
    """Executa reconform_clips, place_saiu_pieces e shift_track_automation."""
    cl = diff_cuts(old, new, Report())
    assert cl is not None and cl.time_map is not None
    smap = SampleMap.from_time_map(cl.time_map, 48000)
    anchor = read_anchor(path)
    allocator = IdAllocator.from_root(root)
    report = Report()
    clips_res = reconform_clips(root, anchor, smap, report, allocator)
    saiu_tracks = place_saiu_pieces(root, anchor, clips_res.saiu, report, allocator)
    shift_track_automation(root, anchor, smap, saiu_tracks, report)
    return saiu_tracks, report


def _find_env(track_node: ET.Element, target_id: str) -> ET.Element | None:
    """Encontra o AutomationEnvelope cujo PointeeId aponta para target_id."""
    for env in track_node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        pointee = env.find("EnvelopeTarget/PointeeId")
        if pointee is not None and pointee.get("Value") == target_id:
            return env
    return None


def _ev_pairs(env: ET.Element) -> list[tuple[float, str]]:
    """Lista (Time, Value) dos eventos de um AutomationEnvelope."""
    ev_box = env.find("Automation/Events")
    assert ev_box is not None
    return [(float(ev.get("Time")), ev.get("Value")) for ev in ev_box]


def _delete_mid_cuts(end_f: int = 75) -> tuple[CutIR, CutIR]:
    """Par de cortes old/new com remocao do trecho [25, 50) em 25 fps."""
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "X", HOUR_F25 + 25, HOUR_F25 + 50, 500),
            _clip_ev(3, "B", HOUR_F25 + 50, HOUR_F25 + end_f, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "B", HOUR_F25 + 25, HOUR_F25 + end_f - 25, 200),
        ]
    )
    return old, new


def _validate(root: ET.Element) -> None:
    """Executa validate_document no documento reconformado."""
    pos_tags, _ = classify_id_tags(root)
    validate_document(root, pos_tags)


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_delete_no_meio_cria_track_saiu_mutada_apos_origem(tmp_path: Path, bpm: float) -> None:
    """Remocao no meio cria '<nome> [saiu]' mutada logo apos a origem no ponto de corte."""
    path = _make_set(
        tmp_path,
        [_track_spec(0, "Dialog", (0, 75)), _track_spec(1, "Amb", (0, 25))],
        bpm=bpm,
    )
    old, new = _delete_mid_cuts(75)
    root = load_als(path)
    saiu_tracks, _ = _run_apply(root, path, old, new)

    assert len(saiu_tracks) == 1
    audio_tracks = root.findall("./LiveSet/Tracks/AudioTrack")
    assert [get_value(t, TRACK_FIELDS["name"]) for t in audio_tracks] == [
        "Dialog",
        "Dialog [saiu]",
        "Amb",
    ]
    assert [get_value(t, TRACK_FIELDS["effective_name"]) for t in audio_tracks] == [
        "Dialog",
        "Dialog [saiu]",
        "Amb",
    ]
    saiu_node = audio_tracks[1]
    assert saiu_tracks.by_source[audio_tracks[0].get("Id")] == [saiu_node]
    assert get_value(saiu_node, TRACK_FIELDS["mute"]) == "false"
    assert saiu_node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope") == []

    saiu_clips = saiu_node.findall(f"{TRACK_FIELDS['arrangement_events']}/AudioClip")
    assert len(saiu_clips) == 1
    exp_start = samples_to_beats(25 * SPF_25, 48000, bpm, TIMELINE)
    exp_end = samples_to_beats(50 * SPF_25, 48000, bpm, TIMELINE)
    assert float(saiu_clips[0].get("Time")) == pytest.approx(exp_start)
    assert float(get_value(saiu_clips[0], AUDIO_CLIP_FIELDS["current_start"])) == pytest.approx(
        exp_start
    )
    assert float(get_value(saiu_clips[0], AUDIO_CLIP_FIELDS["current_end"])) == pytest.approx(
        exp_end
    )
    _validate(root)
    save_als(root, path)
    reloaded = read_session(path, Report())
    assert reloaded.tracks[1].muted is True
    assert reloaded.tracks[1].clips[0].timeline_start_samples == HOUR + 25 * SPF_25
    assert reloaded.tracks[1].clips[0].timeline_end_samples == HOUR + 50 * SPF_25


def test_colisao_de_pecas_saiu_cria_tracks_overflow(tmp_path: Path) -> None:
    """Pecas removidas com intervalos sobrepostos criam '[saiu] 2' sem descartar nenhuma."""
    path = _make_set(tmp_path, [_track_spec(0, "Voz", (0, 95))], bpm=60.0)
    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip_ev(2, "X1", HOUR_F25 + 20, HOUR_F25 + 40, 500),
            _clip_ev(3, "B", HOUR_F25 + 40, HOUR_F25 + 50, 200),
            _clip_ev(4, "X2", HOUR_F25 + 50, HOUR_F25 + 75, 600),
            _clip_ev(5, "C", HOUR_F25 + 75, HOUR_F25 + 85, 300),
            _clip_ev(6, "X3", HOUR_F25 + 85, HOUR_F25 + 95, 700),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 20, 100),
            _clip_ev(2, "B", HOUR_F25 + 20, HOUR_F25 + 30, 200),
            _clip_ev(3, "C", HOUR_F25 + 30, HOUR_F25 + 40, 300),
        ]
    )
    root = load_als(path)
    saiu_tracks, _ = _run_apply(root, path, old, new)

    audio_tracks = root.findall("./LiveSet/Tracks/AudioTrack")
    assert [get_value(t, TRACK_FIELDS["name"]) for t in audio_tracks] == [
        "Voz",
        "Voz [saiu]",
        "Voz [saiu] 2",
    ]
    assert len(saiu_tracks) == 2
    clips_1 = audio_tracks[1].findall(f"{TRACK_FIELDS['arrangement_events']}/AudioClip")
    clips_2 = audio_tracks[2].findall(f"{TRACK_FIELDS['arrangement_events']}/AudioClip")
    assert [c.get("Id") for c in clips_1] == ["0", "1"]
    assert len(clips_2) == 1
    assert float(get_value(clips_1[0], AUDIO_CLIP_FIELDS["current_start"])) == pytest.approx(0.8)
    assert float(get_value(clips_1[1], AUDIO_CLIP_FIELDS["current_start"])) == pytest.approx(1.6)
    assert float(get_value(clips_2[0], AUDIO_CLIP_FIELDS["current_start"])) == pytest.approx(1.2)
    _validate(root)


@pytest.mark.parametrize("bpm", [60.0, 120.0])
def test_automacao_volume_deslocada_e_saiu_sem_speaker(tmp_path: Path, bpm: float) -> None:
    """Volume da AudioTrack desloca com breakpoints, vai para [saiu] e ignora Speaker."""
    gains = [(HOUR, 1.0), (HOUR + 100 * SPF_25, 0.2)]
    path = _make_set(tmp_path, [_track_spec(0, "Musica", (0, 100), gain_points=gains)], bpm=bpm)
    root = load_als(path)
    src_track = root.find("./LiveSet/Tracks/AudioTrack")
    assert src_track is not None
    src_vol_id = src_track.find("DeviceChain/Mixer/Volume/AutomationTarget").get("Id")
    src_spk_id = src_track.find("DeviceChain/Mixer/Speaker/AutomationTarget").get("Id")
    scale = bpm / 60.0
    _attach_env(
        src_track,
        src_spk_id,
        "BoolEvent",
        [(SENTINEL_TIME, "true"), (0.0, "true"), (1.5 * scale, "false"), (2.5 * scale, "true")],
    )
    old, new = _delete_mid_cuts(100)
    saiu_tracks, report = _run_apply(root, path, old, new)
    assert not [i for i in report.issues if i.code == "RECONFORM_AUTOMATION_DROPPED"]

    kept_pts = _ev_pairs(_find_env(src_track, src_vol_id))
    assert kept_pts[0] == (SENTINEL_TIME, "1")
    exp_kept_t = [0.0, 1.0 * scale, 1.0 * scale, 3.0 * scale]
    assert [t for t, _ in kept_pts[1:]] == pytest.approx(exp_kept_t)
    assert [float(v) for _, v in kept_pts[1:]] == pytest.approx([1.0, 0.8, 0.6, 0.2])

    saiu_node = saiu_tracks.by_source[src_track.get("Id")][0]
    saiu_vol_id = saiu_node.find("DeviceChain/Mixer/Volume/AutomationTarget").get("Id")
    saiu_spk_id = saiu_node.find("DeviceChain/Mixer/Speaker/AutomationTarget").get("Id")
    assert saiu_vol_id != src_vol_id
    assert _find_env(saiu_node, saiu_spk_id) is None
    assert get_value(saiu_node, TRACK_FIELDS["mute"]) == "false"

    saiu_env = _find_env(saiu_node, saiu_vol_id)
    assert saiu_env is not None
    saiu_pts = _ev_pairs(saiu_env)
    assert saiu_pts[0] == (SENTINEL_TIME, "1")
    assert [t for t, _ in saiu_pts[1:]] == pytest.approx([1.0 * scale, 2.0 * scale])
    assert [float(v) for _, v in saiu_pts[1:]] == pytest.approx([0.8, 0.6])
    assert [ev.get("Id") for ev in saiu_env.find("Automation/Events")] == ["0", "1", "2"]

    spk_pts = _ev_pairs(_find_env(src_track, src_spk_id))
    assert spk_pts[0] == (SENTINEL_TIME, "true")
    exp_spk_t = [0.0, 1.0 * scale, 1.0 * scale, 1.5 * scale]
    assert [t for t, _ in spk_pts[1:]] == pytest.approx(exp_spk_t)
    assert [v for _, v in spk_pts[1:]] == ["true", "true", "false", "true"]
    _validate(root)


def test_master_em_saiu_descarta_com_aviso(tmp_path: Path) -> None:
    """Eventos em trecho saiu na MainTrack/MasterTrack geram aviso e sao descartados."""
    path = _make_set(tmp_path, [_track_spec(0, "Som", (0, 75))], bpm=60.0)
    root = load_als(path)
    allocator = IdAllocator.from_root(root)
    main_track = root.find("./LiveSet/MainTrack")
    assert main_track is not None
    main_vol_id = main_track.find("DeviceChain/Mixer/Volume/AutomationTarget").get("Id")
    main_pts_in = [(SENTINEL_TIME, "1"), (0.0, "1"), (1.5, "0.5"), (3.0, "0.8")]
    _attach_env(main_track, main_vol_id, "FloatEvent", main_pts_in)
    master = ET.SubElement(root.find("./LiveSet"), "MasterTrack", {"Id": str(allocator.next())})
    m_name = ET.SubElement(master, "Name")
    ET.SubElement(m_name, "EffectiveName", {"Value": "Master"})
    ET.SubElement(m_name, "UserName", {"Value": "Master"})
    m_pan_id = str(allocator.next())
    m_mix = ET.SubElement(ET.SubElement(master, "DeviceChain"), "Mixer")
    ET.SubElement(ET.SubElement(m_mix, "Pan"), "AutomationTarget", {"Id": m_pan_id})
    m_pts_in = [(SENTINEL_TIME, "0"), (0.0, "0"), (1.5, "-0.5"), (3.0, "0.5")]
    _attach_env(master, m_pan_id, "FloatEvent", m_pts_in)
    allocator.write_next_pointee_id(root)

    old, new = _delete_mid_cuts(75)
    _, report = _run_apply(root, path, old, new)
    warnings = [i for i in report.issues if i.code == "RECONFORM_AUTOMATION_DROPPED"]
    assert len(warnings) == 2
    assert any(w.context["parameter"] == "Volume" and "Volume" in w.message for w in warnings)
    assert any(
        w.context["track"] == "Master" and w.context["parameter"] == "Pan" and "Master" in w.message
        for w in warnings
    )

    main_pts = _ev_pairs(_find_env(main_track, main_vol_id))
    assert main_pts[0] == (SENTINEL_TIME, "1")
    assert [t for t, _ in main_pts[1:]] == pytest.approx([0.0, 1.0, 1.0, 2.0])
    assert [float(v) for _, v in main_pts[1:]] == pytest.approx([1.0, 2.0 / 3.0, 0.6, 0.8])
    _validate(root)


def test_bool_enum_seguram_valor_e_device_mapeado(tmp_path: Path) -> None:
    """BoolEvent e EnumEvent seguram o valor anterior e mapeiam alvos de device."""
    path = _make_set(tmp_path, [_track_spec(0, "FX", (0, 75))], bpm=60.0)
    root = load_als(path)
    allocator = IdAllocator.from_root(root)
    src_track = root.find("./LiveSet/Tracks/AudioTrack")
    assert src_track is not None
    devices = src_track.find("DeviceChain/DeviceChain/Devices")
    if devices is None:
        devices = ET.SubElement(src_track.find("DeviceChain/DeviceChain"), "Devices")
    mock_dev = ET.SubElement(devices, "MockDevice", {"Id": "0"})
    toggle_id = str(allocator.next())
    mode_id = str(allocator.next())
    ET.SubElement(ET.SubElement(mock_dev, "Toggle"), "AutomationTarget", {"Id": toggle_id})
    ET.SubElement(ET.SubElement(mock_dev, "Mode"), "AutomationTarget", {"Id": mode_id})
    allocator.write_next_pointee_id(root)
    _attach_env(
        src_track,
        toggle_id,
        "BoolEvent",
        [(SENTINEL_TIME, "false"), (0.0, "false"), (1.5, "true"), (3.0, "false")],
    )
    _attach_env(src_track, mode_id, "EnumEvent", [(SENTINEL_TIME, "0"), (0.0, "1"), (2.5, "2")])

    old, new = _delete_mid_cuts(75)
    saiu_tracks, _ = _run_apply(root, path, old, new)
    saiu_node = saiu_tracks.by_source[src_track.get("Id")][0]
    saiu_toggle_id = saiu_node.find(
        "DeviceChain/DeviceChain/Devices/MockDevice/Toggle/AutomationTarget"
    ).get("Id")
    assert saiu_toggle_id != toggle_id
    assert _ev_pairs(_find_env(saiu_node, saiu_toggle_id)) == [
        (SENTINEL_TIME, "false"),
        (1.0, "false"),
        (1.5, "true"),
        (2.0, "true"),
    ]
    assert _ev_pairs(_find_env(src_track, mode_id)) == [
        (SENTINEL_TIME, "0"),
        (0.0, "1"),
        (1.0, "1"),
        (1.0, "1"),
        (1.5, "2"),
    ]
    _validate(root)


def test_grupo_sentinela_isolada_e_reordenacao(tmp_path: Path) -> None:
    """Track em GroupTrack mantem TrackGroupId, sentinela fica intacta e troca reordena."""
    dx1_gains = [(HOUR, 1.0), (HOUR + 75 * SPF_25, 0.25)]
    tracks = [
        _track_spec(0, "DX 1", (0, 75), gain_points=dx1_gains, organize_group="DX"),
        _track_spec(1, "DX 2", None, organize_group="DX"),
    ]
    path = _make_set(tmp_path, tracks, bpm=60.0, groups="prefix")
    root = load_als(path)
    dx1 = root.findall("./LiveSet/Tracks/AudioTrack")[0]
    pan_id = dx1.find("DeviceChain/Mixer/Pan/AutomationTarget").get("Id")
    _attach_env(dx1, pan_id, "FloatEvent", [(SENTINEL_TIME, "-0.25")])

    old = _cut(
        [
            _clip_ev(1, "A", HOUR_F25, HOUR_F25 + 25, 100),
            _clip_ev(2, "X", HOUR_F25 + 25, HOUR_F25 + 50, 500),
            _clip_ev(3, "B", HOUR_F25 + 50, HOUR_F25 + 75, 200),
        ]
    )
    new = _cut(
        [
            _clip_ev(1, "B", HOUR_F25, HOUR_F25 + 25, 200),
            _clip_ev(2, "A", HOUR_F25 + 25, HOUR_F25 + 50, 100),
        ]
    )
    saiu_tracks, _ = _run_apply(root, path, old, new)

    tracks_box = root.find("./LiveSet/Tracks")
    assert tracks_box is not None
    tags_and_names = [(n.tag, get_value(n, TRACK_FIELDS["name"])) for n in tracks_box]
    assert tags_and_names[:4] == [
        ("GroupTrack", "DX"),
        ("AudioTrack", "DX 1"),
        ("AudioTrack", "DX 1 [saiu]"),
        ("AudioTrack", "DX 2"),
    ]
    saiu_node = saiu_tracks.by_source[dx1.get("Id")][0]
    assert get_value(saiu_node, "TrackGroupId") == get_value(dx1, "TrackGroupId")
    assert _ev_pairs(_find_env(dx1, pan_id)) == [(SENTINEL_TIME, "-0.25")]
    assert _find_env(saiu_node, pan_id) is None

    vol_id = dx1.find("DeviceChain/Mixer/Volume/AutomationTarget").get("Id")
    vol_env = _find_env(dx1, vol_id)
    vol_pts = _ev_pairs(vol_env)
    assert vol_pts[0] == (SENTINEL_TIME, "1")
    assert [t for t, _ in vol_pts[1:]] == pytest.approx([0.0, 1.0, 1.0, 2.0])
    assert [float(v) for _, v in vol_pts[1:]] == pytest.approx([0.5, 0.25, 1.0, 0.75])
    assert [ev.get("Id") for ev in vol_env.find("Automation/Events")] == ["0", "1", "2", "3", "4"]
    _validate(root)
