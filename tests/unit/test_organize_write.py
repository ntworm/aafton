"""Testes de unidade da escrita no .als dos grupos e cores de organizacao (P3b)."""

from __future__ import annotations

import gzip

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aafton.als_paths import find_prototype
from aafton.als_write import WriteOptions, write_session
from aafton.als_write_clips import CIRCLED_CLIP_COLOR
from aafton.als_write_groups import infer_track_groups
from aafton.als_xml import classify_id_tags, get_value, load_als, validate_document
from aafton.bwf_meta import BwfInfo, Ixml
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_apply import (
    FAMILY_COLOR_INDEX,
    LIVE_COLOR_BY_KIND,
    annotate_origin_keys,
    apply_proposal,
)
from aafton.organize_plan import build_proposal
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate

TEMPLATE = Path("templates/live12.als")

# Os sinais de hoje, sem a compactacao: estes testes conferem a disposicao de
# uma track por track do editor.
SINAIS_SEM_COMPACT = ("file", "meta", "name", "family", "color")


def _recorder_bwf() -> BwfInfo:
    return BwfInfo(
        path=Path("dummy.wav"),
        channels=1,
        sample_rate=48000,
        bits=24,
        frames=48000,
        ixml=Ixml(scene="35A", take="4"),
    )


def _make_clip(
    clip_id: str,
    track_id: str,
    event_index: int,
    name: str,
    start: int,
    end: int,
    *,
    crossfade_in: bool = False,
) -> tuple[ClipIR, MediaAsset]:
    media_id = f"m_{clip_id}"
    asset = MediaAsset(
        id=media_id,
        filename=name,
        channels=1,
        sample_rate=48000,
        duration_samples=max(48000, end - start),
        status="resolved",
        resolved_path=f"/fake/{media_id}.wav",
    )
    fade_in = (
        FadeIR(kind="crossfade", duration_samples=100)
        if crossfade_in
        else FadeIR(kind="in", duration_samples=50)
    )
    clip = ClipIR(
        id=clip_id,
        track_id=track_id,
        channel_index=0,
        event_index=event_index,
        name=name,
        media_id=media_id,
        timeline_start_samples=start,
        timeline_end_samples=end,
        source_offset_samples=120,
        gain_linear=0.85,
        fade_in=fade_in,
        fade_out=FadeIR(kind="out", duration_samples=60),
        crossfade_in_state=crossfade_in,
    )
    return clip, asset


def _add_track(
    session: SessionIR,
    source_index: int,
    name: str,
    specs: list[tuple[str, int, int, bool]],
    *,
    is_video: bool = False,
) -> TrackIR:
    track_id = f"trk_{source_index}"
    clips: list[ClipIR] = []
    for ev_idx, (c_name, start, end, xfade) in enumerate(specs):
        clip, asset = _make_clip(
            f"c_{source_index}_{ev_idx}",
            track_id,
            ev_idx,
            c_name,
            start,
            end,
            crossfade_in=xfade,
        )
        clips.append(clip)
        session.media.append(asset)
    track = TrackIR(
        id=track_id,
        source_index=source_index,
        name=name,
        kind="video" if is_video else "audio",
        channel_count=1,
        clips=clips,
        is_video=is_video,
    )
    session.tracks.append(track)
    return track


def _build_small_session(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SessionIR, dict[str, BwfInfo]]:
    """Constroi sessao pequena com par L/R, grupos de 1 e 2+ tracks e track fora de grupo."""
    monkeypatch.setattr(
        "aafton.stereo.read_audio_metadata",
        lambda path: {
            "channels": 1,
            "sample_rate": 48000,
            "duration_samples": 48000,
            "bits": 24,
        },
    )
    session = SessionIR(
        name="Small_Organize_Write",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    # 0, 1: par estereo CAMERA + PRODUCTION (gera CAM 1 estereo e PROD 1 estereo)
    _add_track(
        session,
        0,
        "Audio 1_L",
        [
            ("A017_A013_0514N0_001.r3d_L", 0, 1000, False),
            ("A024_A021_0519VG_001.r3d_L", 2000, 3000, False),
            ("043CENA35APL1TK4-043.WAV_L", 4000, 5000, False),
        ],
    )
    _add_track(
        session,
        1,
        "Audio 1_R",
        [
            ("A017_A013_0514N0_001.r3d_R", 0, 1000, False),
            ("A024_A021_0519VG_001.r3d_R", 2000, 3000, False),
            ("043CENA35APL1TK4-043.WAV_R", 4000, 5000, False),
        ],
    )
    # 2: mono com PRODUCTION dominante + VO (grupo de 1 track: VO 1) + familia
    _add_track(
        session,
        2,
        "Audio 2",
        [
            ("026CENA37APL3TK1-026.WAV", 0, 1000, False),
            ("058CENA33APL1TK4-058.WAV", 1500, 2500, False),
            ("Personagem_02_Texto_A.mp3", 3000, 4000, False),
            ("Personagem_ 02_Texto_B.mp3", 4500, 5500, False),
            ("Personagem_02_Texto C.mp3", 6000, 7000, False),
            ("009CENA29BOFF-001.WAV", 7500, 8500, False),
        ],
    )
    # 3: track mono sem tipo vencedor em Balanced -> fica como esta (TrackGroupId -1)
    _add_track(
        session,
        3,
        "Audio 3",
        [
            ("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", 1000, 2000, False),
        ],
    )
    # 4: track de video (fora de alcance, fica como esta com TrackGroupId -1)
    _add_track(
        session,
        4,
        "Video Ref",
        [("UCJS_TrechoDoc.mp4", 0, 1000, False)],
        is_video=True,
    )

    bwf = _recorder_bwf()
    meta_map: dict[str, BwfInfo] = {}
    for asset in session.media:
        if "CENA" in asset.filename or "OFF" in asset.filename:
            meta_map[asset.id] = bwf
    return session, meta_map


def _template_default_colors() -> tuple[str, str, str]:
    """Devolve o Color Value padrao do GroupTrack, AudioTrack e AudioClip no template."""
    root = load_als(TEMPLATE)
    grp_proto = find_prototype(root, "GroupTrack")
    trk_proto = find_prototype(root, "AudioTrack")
    clp_proto = find_prototype(root, "AudioClip")
    assert grp_proto is not None and trk_proto is not None and clp_proto is not None
    return (
        get_value(grp_proto, "Color") or "",
        get_value(trk_proto, "Color") or "",
        get_value(clp_proto, "Color") or "",
    )


def _track_name(node: ET.Element) -> str:
    return get_value(node, "Name/UserName") or ""


def _clip_nodes(track_node: ET.Element) -> list[ET.Element]:
    return track_node.findall(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )


def test_organize_write_emits_group_tracks_track_colors_clip_colors_and_left_tracks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, meta_map = _build_small_session(monkeypatch)
    annotate_origin_keys(session)
    proposal = build_proposal(
        session,
        stereo="pair",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_map.get,
    )
    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)
    unmatched = apply_proposal(session, proposal, colors=True)
    assert unmatched == []

    # Confere que infer_track_groups mapeia organize_group mesmo com groups='none'
    inferred_none = infer_track_groups(session, WriteOptions(sample_rate=48000, groups="none"))
    inferred_prefix = infer_track_groups(session, WriteOptions(sample_rate=48000, groups="prefix"))
    assert inferred_none == inferred_prefix

    out_als = tmp_path / "organized.als"
    write_session(
        session,
        TEMPLATE,
        out_als,
        WriteOptions(sample_rate=48000, groups="none"),
        Report(strict=False),
    )

    root = load_als(out_als)
    posicionais, _ = classify_id_tags(root)
    validate_document(root, posicionais)

    _, default_track_color, default_clip_color = _template_default_colors()

    group_nodes = root.findall(".//LiveSet/Tracks/GroupTrack")
    audio_nodes = root.findall(".//LiveSet/Tracks/AudioTrack")

    # Um GroupTrack por grupo da proposta, inclusive grupos com uma unica track (VO, CAM, familia)
    assert len(group_nodes) == len(proposal.groups)
    assert any(len(g.tracks) == 1 for g in proposal.groups)

    group_by_name = {_track_name(g): g for g in group_nodes}
    audio_by_name = {_track_name(t): t for t in audio_nodes}

    organized_names: set[str] = set()
    for grp in proposal.groups:
        expected_color = str(FAMILY_COLOR_INDEX if grp.is_family else LIVE_COLOR_BY_KIND[grp.key])
        g_node = group_by_name[grp.label]
        g_id = g_node.get("Id")
        assert get_value(g_node, "Color") == expected_color
        assert get_value(g_node, "TrackGroupId") == "-1"

        for dest in grp.tracks:
            organized_names.add(dest.name)
            t_node = audio_by_name[dest.name]
            assert get_value(t_node, "TrackGroupId") == g_id
            assert get_value(t_node, "Color") == expected_color
            clips = _clip_nodes(t_node)
            assert len(clips) >= 1
            for c_node in clips:
                assert get_value(c_node, "Color") == expected_color

    # Tracks que ficaram como estao ficam fora de qualquer grupo (TrackGroupId -1) e sem cor de organizacao
    left_names = [t.name for t in session.tracks if t.name not in organized_names]
    assert set(left_names) == {"Audio 3", "Video Ref"}
    for name in left_names:
        t_node = audio_by_name[name]
        assert get_value(t_node, "TrackGroupId") == "-1"
        assert get_value(t_node, "Color") == default_track_color
        for c_node in _clip_nodes(t_node):
            assert get_value(c_node, "Color") == default_clip_color


def test_organize_write_colors_false_keeps_template_default_colors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, meta_map = _build_small_session(monkeypatch)
    annotate_origin_keys(session)
    proposal = build_proposal(
        session,
        stereo="pair",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_map.get,
    )
    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)
    apply_proposal(session, proposal, colors=False)

    out_als = tmp_path / "organized_no_colors.als"
    write_session(
        session,
        TEMPLATE,
        out_als,
        WriteOptions(sample_rate=48000, groups="none"),
        Report(strict=False),
    )

    root = load_als(out_als)
    default_group_color, default_track_color, default_clip_color = _template_default_colors()

    group_nodes = root.findall(".//LiveSet/Tracks/GroupTrack")
    audio_nodes = root.findall(".//LiveSet/Tracks/AudioTrack")

    assert [_track_name(g) for g in group_nodes] == [g.label for g in proposal.groups]
    for g_node in group_nodes:
        assert get_value(g_node, "Color") == default_group_color

    for t_node in audio_nodes:
        assert get_value(t_node, "Color") == default_track_color
        for c_node in _clip_nodes(t_node):
            assert get_value(c_node, "Color") == default_clip_color


def test_unorganized_session_writes_identical_bytes_and_template_colors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_a, _ = _build_small_session(monkeypatch)
    session_b, _ = _build_small_session(monkeypatch)

    als_a = tmp_path / "unorganized_a.als"
    als_b = tmp_path / "unorganized_b.als"

    write_session(
        session_a,
        TEMPLATE,
        als_a,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )
    write_session(
        session_b,
        TEMPLATE,
        als_b,
        WriteOptions(sample_rate=48000, groups="prefix"),
        Report(strict=False),
    )

    # O gzip guarda a hora da escrita no cabecalho: compara o XML descomprimido
    assert gzip.decompress(als_a.read_bytes()) == gzip.decompress(als_b.read_bytes())

    root = load_als(als_a)
    default_group_color, default_track_color, default_clip_color = _template_default_colors()
    for g_node in root.findall(".//LiveSet/Tracks/GroupTrack"):
        assert get_value(g_node, "Color") == default_group_color
    for t_node in root.findall(".//LiveSet/Tracks/AudioTrack"):
        assert get_value(t_node, "Color") == default_track_color
        for c_node in _clip_nodes(t_node):
            assert get_value(c_node, "Color") == default_clip_color


def test_production_circled_clip_keeps_color_5_when_track_is_organized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session, meta_map = _build_small_session(monkeypatch)
    # Marca o primeiro clip de som direto da Audio 2 como circled
    circled_clip = session.tracks[2].clips[0]
    circled_clip.raw["production"] = {
        "circled": True,
        "annotation": "SC 37A TK 1 *",
    }

    annotate_origin_keys(session)
    proposal = build_proposal(
        session,
        stereo="pair",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_map.get,
    )
    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)
    apply_proposal(session, proposal, colors=True)

    out_als = tmp_path / "organized_circled.als"
    write_session(
        session,
        TEMPLATE,
        out_als,
        WriteOptions(sample_rate=48000, groups="none"),
        Report(strict=False),
    )

    root = load_als(out_als)
    audio_by_name = {_track_name(t): t for t in root.findall(".//LiveSet/Tracks/AudioTrack")}
    # A Audio 2 vira a track da familia Personagem 02 Texto; os dois takes vao para o PROD 1
    prod_mono = audio_by_name["PROD 1"]
    # O Live guarda o nome do clip sem a extensao do arquivo
    clips_by_name = {get_value(c, "Name"): c for c in _clip_nodes(prod_mono)}
    clips_by_name.update({f"{nome}.WAV": no for nome, no in list(clips_by_name.items())})

    # O clip circled mantem a cor 5 (CIRCLED_CLIP_COLOR), enquanto o outro clip da track usa a cor de PRODUCTION (3)
    assert get_value(clips_by_name["026CENA37APL3TK1-026.WAV"], "Color") == str(CIRCLED_CLIP_COLOR)
    assert get_value(clips_by_name["058CENA33APL1TK4-058.WAV"], "Color") == str(
        LIVE_COLOR_BY_KIND["PRODUCTION"]
    )
