"""Testes de unidade da aplicacao em memoria da organizacao de sessao (P3a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton import organize_apply, organize_plan, pipeline
from aafton.bwf_meta import BwfInfo, Ixml
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_apply import (
    FAMILY_COLOR_INDEX,
    LIVE_COLOR_BY_KIND,
    annotate_origin_keys,
    apply_proposal,
)
from aafton.organize_classify import (
    AMBIENCE,
    CAMERA,
    MUSIC,
    PICTURE_REF,
    PRODUCTION,
    SFX,
    VO,
)
from aafton.organize_plan import KIND_LABELS, build_proposal
from aafton.pipeline import ImportOptions, _process_session
from aafton.report import Report
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate
from aafton.verify import _index

BACH_NAME = "Bach Ich ruf zu dir, BWV 639 - Daniel Oyarzabal - Daniel Oyarzabal (youtube).mp3.mpeg"
FOGOS_NAME = (
    "SOM DE FOGOS DE ARTIF\u00cdCIO PARA DESSENSIBILIZA\u00c7\u00c3O"
    " DE C\u00c3ES - Shih Tzu Educado (youtube).mp3"
)
PASSAROS_NAME = "CANTO DOS PASS\u00c1ROS.aac"

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
    offset: int = 120,
    gain: float = 0.85,
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
    fade_out = FadeIR(kind="out", duration_samples=60)
    clip = ClipIR(
        id=clip_id,
        track_id=track_id,
        channel_index=0,
        event_index=event_index,
        name=name,
        media_id=media_id,
        timeline_start_samples=start,
        timeline_end_samples=end,
        source_offset_samples=offset,
        gain_linear=gain,
        fade_in=fade_in,
        fade_out=fade_out,
        crossfade_in_state=crossfade_in,
    )
    return clip, asset


def _add_track(
    session: SessionIR,
    source_index: int,
    name: str,
    specs: list[tuple[str, int, int, bool]],
    *,
    channel_count: int = 1,
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
            offset=100 + ev_idx * 10,
            gain=0.5 + (ev_idx % 4) * 0.1,
        )
        clips.append(clip)
        session.media.append(asset)
    track = TrackIR(
        id=track_id,
        source_index=source_index,
        name=name,
        kind="video" if is_video else "audio",
        channel_count=channel_count,
        clips=clips,
        is_video=is_video,
    )
    session.tracks.append(track)
    return track


def _build_joao_session(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SessionIR, dict[str, BwfInfo]]:
    """Constroi sessao sintetica com Audio 1_L, Audio 1_R, Audio 1 e demais faixas estilo Joao."""
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
        name="Filme_Teste",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    # 0, 1: Audio 1_L e Audio 1_R (par estereo aceito: CAMERA dominante + PRODUCTION que move)
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
    # 2: Audio 1 (mono homonima: CAMERA + PRODUCTION + familia HomemJovem + crossfade misto)
    _add_track(
        session,
        2,
        "Audio 1",
        [
            ("025CENA37PL2TK1-025.WAV", 500, 1500, False),
            ("A010_A009_0505HJ_001.R3D", 1800, 2800, False),
            ("A012_A006_0511SU_001.r3d", 3200, 4200, False),
            ("HomemJovem_006.mp3", 5000, 6000, False),
            ("HomemJovem_006.mp3", 6500, 7500, False),
            ("21CENA12APL01TK05-007.WAV", 8000, 9000, False),
            ("A025_A008_0519GS_001.r3d", 8800, 9800, True),
        ],
    )
    # 3: Audio 2 (mono: PRODUCTION dominante + familia Personagem 02 Texto com 3 clips nota 2)
    _add_track(
        session,
        3,
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
    # 4: Audio 3 (HomemJovem_004 forma familia com o _006 da Audio 1; WhatsApp sem tipo fica nela)
    _add_track(
        session,
        4,
        "Audio 3",
        [
            ("HomemJovem_004.mp3", 1000, 2000, False),
            ("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", 3000, 4000, False),
        ],
    )
    # 5: Video Ref (fora de escopo)
    _add_track(
        session,
        5,
        "Video Ref",
        [("UCJS_TrechoDocParaVers\u00e3oLONGA.mp4", 0, 1000, False)],
        is_video=True,
    )

    bwf = _recorder_bwf()
    meta_map: dict[str, BwfInfo] = {}
    for asset in session.media:
        if "CENA" in asset.filename or "OFF" in asset.filename:
            meta_map[asset.id] = bwf
    return session, meta_map


def test_annotate_propose_pair_and_apply_preserves_keys_attributes_and_colors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, meta_map = _build_joao_session(monkeypatch)
    expected_index = _index(session)
    expected_keys = set(expected_index.keys())

    # Guarda os atributos originais por chave de origem antes do pareamento
    snapshots = {
        key: (
            clip.timeline_start_samples,
            clip.timeline_end_samples,
            clip.source_offset_samples,
            clip.gain_linear,
            clip.fade_in,
            clip.fade_out,
            clip.crossfade_in_state,
        )
        for key, clip in expected_index.items()
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
    unmatched = apply_proposal(session, proposal, colors=True)
    assert unmatched == []

    # 1. Toda chave de origem da sessao lida aparece exatamente em um clip apos apply_proposal
    seen_keys: list[tuple[int, int]] = []
    for track in session.tracks:
        for clip in track.clips:
            raw_keys = [tuple(p) for p in clip.raw["origin_keys"]]
            seen_keys.extend(raw_keys)
            # Cada clip movido preserva posicoes, offset, ganho, fades e crossfade_in_state
            for rk in raw_keys:
                assert (
                    clip.timeline_start_samples,
                    clip.timeline_end_samples,
                    clip.source_offset_samples,
                    clip.gain_linear,
                    clip.fade_in,
                    clip.fade_out,
                    clip.crossfade_in_state,
                ) == snapshots[rk]

    assert len(seen_keys) == len(expected_keys)
    assert set(seen_keys) == expected_keys

    # 2. Nomes de destino, grupos, cores e ordem das tracks batem com a proposta e a tabela
    expected_dest_names = [t.name for g in proposal.groups for t in g.tracks]
    actual_dest_tracks = session.tracks[: len(expected_dest_names)]
    assert [t.name for t in actual_dest_tracks] == expected_dest_names

    expected_colors = {
        PRODUCTION: 3,
        VO: 9,
        CAMERA: 27,
        MUSIC: 24,
        SFX: 14,
        AMBIENCE: 19,
        PICTURE_REF: 13,
    }
    assert LIVE_COLOR_BY_KIND == expected_colors
    assert FAMILY_COLOR_INDEX == 16

    cursor = 0
    for grp in proposal.groups:
        expected_color = FAMILY_COLOR_INDEX if grp.is_family else expected_colors[grp.key]
        if not grp.is_family:
            assert grp.label == KIND_LABELS[grp.key][0]
        for dest in grp.tracks:
            trk = session.tracks[cursor]
            cursor += 1
            assert trk.name == dest.name
            assert trk.organize_group == grp.label
            assert trk.organize_group_color == expected_color
            assert trk.color_index == expected_color
            assert all(c.color_index == expected_color for c in trk.clips)
            if dest.source_track is None:
                assert trk.original_source_indices == [-1]

    # 3. Tracks novas criadas pela organizacao tem original_source_indices == [-1]
    new_tracks = [t for t in actual_dest_tracks if t.source_index == -1]
    assert len(new_tracks) >= 1
    for nt in new_tracks:
        assert nt.original_source_indices == [-1]

    # 4. Left tracks ficam depois das tracks organizadas e mantem nome e clips
    left_after = session.tracks[len(expected_dest_names) :]
    assert [t.name for t in left_after] == ["Video Ref"]
    assert len(left_after[0].clips) == 1
    assert left_after[0].organize_group is None
    assert left_after[0].color_index is None

    # A Audio 3 vira a track da familia HomemJovem (HomemJovem_004 aqui e _006 na Audio 1);
    # o clip do WhatsApp, sem tipo, fica nela, onde o editor o pos
    familia = next(t for t in session.tracks if t.name == "HomemJovem 1")
    assert any("WhatsApp" in c.name for c in familia.clips)


def test_apply_proposal_with_colors_false_sets_no_color_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, meta_map = _build_joao_session(monkeypatch)
    annotate_origin_keys(session)
    proposal = build_proposal(
        session,
        stereo="pair",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_map.get,
    )
    process_stereo_pairing(session, "pair", Report(strict=False), None, dry_run=True)
    unmatched = apply_proposal(session, proposal, colors=False)
    assert unmatched == []

    for track in session.tracks:
        assert track.color_index is None
        assert track.organize_group_color is None
        for clip in track.clips:
            assert clip.color_index is None


def test_unpaired_stereo_unit_is_reported_and_stays_on_editor_tracks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, meta_map = _build_joao_session(monkeypatch)
    annotate_origin_keys(session)
    # Proposta gerada assumindo stereo='pair', onde Audio 1_L e Audio 1_R formam unidades de par
    proposal = build_proposal(
        session,
        stereo="pair",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_map.get,
    )
    # Simula falha no pareamento na escrita rodando process_stereo_pairing com 'split'
    process_stereo_pairing(session, "split", Report(strict=False), None, dry_run=True)
    unmatched = apply_proposal(session, proposal, colors=True)

    # As 3 unidades do par Audio 1_L/R nao viraram estereo e devem ser relatadas
    reported_keys = {u.origin_keys for u in unmatched}
    assert ((0, 0), (1, 0)) in reported_keys
    assert ((0, 1), (1, 1)) in reported_keys
    assert ((0, 2), (1, 2)) in reported_keys

    # Os clips de Audio 1_L e Audio 1_R permanecem nas tracks do editor
    by_name = {t.name: t for t in session.tracks}
    assert "Audio 1_L" in by_name
    assert "Audio 1_R" in by_name
    assert len(by_name["Audio 1_L"].clips) == 3
    assert len(by_name["Audio 1_R"].clips) == 3


def test_import_options_defaults_and_process_session_organize_off_vs_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    opts = ImportOptions(input="in.aaf", output="out.als")
    assert opts.organize == "off"
    assert opts.certainty == "permissive"
    assert opts.organize_signals == ("file", "meta", "name", "family", "color", "compact")
    assert opts.organize_edits is None

    session, _ = _build_joao_session(monkeypatch)
    monkeypatch.setattr("aafton.session_text.process_session_text", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "check_frame_alignment", lambda *a, **kw: None)
    monkeypatch.setattr("aafton.video.process_video", lambda *a, **kw: None)

    calls: list[str] = []
    monkeypatch.setattr(
        organize_apply,
        "annotate_origin_keys",
        lambda s: calls.append("annotate"),
    )
    monkeypatch.setattr(
        organize_plan,
        "build_proposal",
        lambda *a, **kw: calls.append("propose"),
    )
    monkeypatch.setattr(
        organize_apply,
        "apply_proposal",
        lambda *a, **kw: calls.append("apply"),
    )

    # Com organize='off' e organize='prefix', nenhuma funcao de organizacao por tipo e chamada
    _process_session(session, opts, Report(strict=False), tmp_path, None)
    assert calls == []

    opts_prefix = ImportOptions(input="in.aaf", output="out.als", organize="prefix")
    _process_session(session, opts_prefix, Report(strict=False), tmp_path, None)
    assert calls == []

    # Com organize='type', _process_session executa annotate, build_proposal e apply_proposal
    session_type, _ = _build_joao_session(monkeypatch)
    monkeypatch.undo()
    session_type, _ = _build_joao_session(monkeypatch)
    monkeypatch.setattr("aafton.session_text.process_session_text", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "check_frame_alignment", lambda *a, **kw: None)
    monkeypatch.setattr("aafton.video.process_video", lambda *a, **kw: None)
    monkeypatch.setattr(
        "aafton.stereo.process_stereo_pairing",
        lambda s, mode, rep, d: process_stereo_pairing(s, mode, rep, d, dry_run=True),
    )

    opts_type = ImportOptions(input="in.aaf", output="out.als", organize="type")
    _process_session(session_type, opts_type, Report(strict=False), tmp_path, None)
    assert any(t.organize_group is not None for t in session_type.tracks)
    assert any(t.color_index is not None for t in session_type.tracks)
