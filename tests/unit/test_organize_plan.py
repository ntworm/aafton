"""Testes de unidade e propriedades da proposta de organizacao de sessao (P2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.bwf_meta import BwfInfo, Ixml
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.organize_classify import (
    AMBIENCE,
    CAMERA,
    MUSIC,
    PICTURE_REF,
    PRODUCTION,
    SFX,
    VO,
)
from aafton.organize_plan import (
    DEFAULT_PLAN_SIGNALS,
    Edits,
    Proposal,
    build_proposal,
)
from aafton.organize_units import Unit
from aafton.timecode import TimecodeRate
from aafton.verify import _index

# Os sinais de hoje, sem a compactacao: as invariantes de layout conferem a
# disposicao de uma track por track do editor.
SINAIS_SEM_COMPACT = frozenset({"file", "meta", "name", "family", "color"})

BACH_NAME = "Bach Ich ruf zu dir, BWV 639 - Daniel Oyarzabal - Daniel Oyarzabal (youtube).mp3.mpeg"
FOGOS_NAME = (
    "SOM DE FOGOS DE ARTIF\u00cdCIO PARA DESSENSIBILIZA\u00c7\u00c3O"
    " DE C\u00c3ES - Shih Tzu Educado (youtube).mp3"
)
PASSAROS_NAME = "CANTO DOS PASS\u00c1ROS.aac"


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
        crossfade_in_state=crossfade_in,
        fade_in=FadeIR(kind="crossfade", duration_samples=100) if crossfade_in else None,
    )
    return clip, asset


def _add_track_with_Specs(
    session: SessionIR,
    source_index: int,
    name: str,
    specs: list[tuple[str, int, int, bool]],
    *,
    channel_count: int = 1,
    is_video: bool = False,
    expansion_group: str | None = None,
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
        channel_count=channel_count,
        clips=clips,
        is_video=is_video,
        expansion_group=expansion_group,
    )
    session.tracks.append(track)
    return track


def _build_joao_session() -> tuple[SessionIR, dict[str, BwfInfo]]:
    """Constroi sessao sintetica espelhando o layout de um filme real de teste."""
    session = SessionIR(
        name="Filme_Teste",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    # 0, 1: Audio 1_L e Audio 1_R (par estereo aceito: PICTURE_REF + CAMERA)
    _add_track_with_Specs(
        session,
        0,
        "Audio 1_L",
        [
            ("UCJS_Abertura e PedroTierra_semGC.mp4_L", 0, 1000, False),
            ("UCJS_TrechoDocParaVers\u00e3oLONGA.mp4_L", 2000, 3000, False),
            ("A017_A013_0514N0_001.r3d_L", 4000, 5000, False),
        ],
    )
    _add_track_with_Specs(
        session,
        1,
        "Audio 1_R",
        [
            ("UCJS_Abertura e PedroTierra_semGC.mp4_R", 0, 1000, False),
            ("UCJS_TrechoDocParaVers\u00e3oLONGA.mp4_R", 2000, 3000, False),
            ("A017_A013_0514N0_001.r3d_R", 4000, 5000, False),
        ],
    )
    # 2: Audio 1 (mono homonima: CAMERA + PRODUCTION + familia HomemJovem + crossfade misto)
    _add_track_with_Specs(
        session,
        2,
        "Audio 1",
        [
            ("025CENA37PL2TK1-025.WAV", 500, 1500, False),
            ("A024_A021_0519VG_001.r3d", 2000, 3000, False),
            ("A010_A009_0505HJ_001.R3D", 3500, 4500, False),
            ("A012_A006_0511SU_001.r3d", 5000, 6000, False),
            ("HomemJovem_006.mp3", 7000, 8000, False),
            ("HomemJovem_006.mp3", 8500, 9500, False),
            ("21CENA12APL01TK05-007.WAV", 10000, 11000, False),
            ("A025_A008_0519GS_001.r3d", 10800, 11800, True),
        ],
    )
    # 3, 4: Audio 2_L e Audio 2_R (par estereo aceito: PRODUCTION dominante)
    _add_track_with_Specs(
        session,
        3,
        "Audio 2_L",
        [
            ("043CENA35APL1TK4-043.WAV_L", 1000, 2000, False),
            ("053CENA30BPL2TK1-053.WAV_L", 3000, 4000, False),
        ],
    )
    _add_track_with_Specs(
        session,
        4,
        "Audio 2_R",
        [
            ("043CENA35APL1TK4-043.WAV_R", 1000, 2000, False),
            ("053CENA30BPL2TK1-053.WAV_R", 3000, 4000, False),
        ],
    )
    # 5: Audio 2 (mono: PRODUCTION + familia Personagem + WhatsApp sem tipo)
    _add_track_with_Specs(
        session,
        5,
        "Audio 2",
        [
            ("026CENA37APL3TK1-026.WAV", 0, 1000, False),
            ("058CENA33APL1TK4-058.WAV", 1500, 2500, False),
            ("Personagem_02_Texto_A.mp3", 3000, 4000, False),
            ("Personagem_ 02_Texto_B.mp3", 4500, 5500, False),
            ("Personagem_02_Texto C.mp3", 6000, 7000, False),
            ("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", 7500, 8500, False),
        ],
    )
    # 6, 7: Audio 6_L e Audio 6_R (VO .m4a + VO OFF c/ iXML + SFX + AMBIENCE + MUSIC)
    _add_track_with_Specs(
        session,
        6,
        "Audio 6_L",
        [
            ("009CENA29BOFF-001.WAV_L", 0, 1000, False),
            ("Texto 01.m4a_L", 1500, 2500, False),
            (f"{FOGOS_NAME}_L", 3000, 4000, False),
            (f"{PASSAROS_NAME}_L", 4500, 5500, False),
            (f"{BACH_NAME}_L", 6000, 7000, False),
        ],
    )
    _add_track_with_Specs(
        session,
        7,
        "Audio 6_R",
        [
            ("009CENA29BOFF-001.WAV_R", 0, 1000, False),
            ("Texto 01.m4a_R", 1500, 2500, False),
            (f"{FOGOS_NAME}_R", 3000, 4000, False),
            (f"{PASSAROS_NAME}_R", 4500, 5500, False),
            (f"{BACH_NAME}_R", 6000, 7000, False),
        ],
    )
    # 8..12: Audio 10 e tracks fora de escopo (excluded_source_indices, >2 canais, video, LTC)
    _add_track_with_Specs(
        session,
        8,
        "Audio 10",
        [
            ("HomemJovem_004.mp3", 1000, 2000, False),
            ("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", 3000, 4000, False),
        ],
    )
    _add_track_with_Specs(
        session, 9, "Audio Expanded", [("043CENA35APL1TK4-043.WAV", 0, 1000, False)]
    )
    _add_track_with_Specs(
        session, 10, "Poly 6ch", [("043CENA35APL1TK4-043.WAV", 0, 1000, False)], channel_count=6
    )
    _add_track_with_Specs(
        session,
        11,
        "Video Ref",
        [("UCJS_TrechoDocParaVers\u00e3oLONGA.mp4", 0, 1000, False)],
        is_video=True,
    )
    _add_track_with_Specs(session, 12, "LTC", [("ltc_tc.wav", 0, 1000, False)])

    bwf = _recorder_bwf()
    meta_map: dict[str, BwfInfo] = {}
    for asset in session.media:
        if "CENA" in asset.filename or "OFF" in asset.filename:
            meta_map[asset.id] = bwf
    return session, meta_map


def _build_proposal_for_joao(
    session: SessionIR,
    meta_map: dict[str, BwfInfo],
    *,
    stereo: str = "pair",
    level: str = "balanced",
    signals: frozenset[str] = SINAIS_SEM_COMPACT,
    edits: Edits | None = None,
) -> Proposal:
    accepted_pairs = {(0, 1), (3, 4), (6, 7)} if stereo == "pair" else set()
    return build_proposal(
        session,
        stereo=stereo,
        level=level,
        signals=signals,
        edits=edits,
        excluded_source_indices={9},
        accepted_pairs=accepted_pairs,
        media_meta=meta_map.get,
    )


@pytest.mark.parametrize("stereo", ["pair", "split"])
@pytest.mark.parametrize("level", ["permissive", "balanced", "strict"])
def test_joao_session_invariants_across_levels_and_stereo(stereo: str, level: str) -> None:
    session, meta_map = _build_joao_session()
    proposal = _build_proposal_for_joao(session, meta_map, stereo=stereo, level=level)

    # 1. Toda chave de origem de verify._index aparece exatamente uma vez na proposta
    expected_keys = set(_index(session).keys())
    actual_keys = proposal.all_origin_keys()
    assert len(actual_keys) == len(expected_keys)
    assert set(actual_keys) == expected_keys

    # 2. Nenhuma sobreposicao entre duas unidades em qualquer track de destino
    for grp in proposal.groups:
        for trk in grp.tracks:
            for prev, curr in zip(trk.units, trk.units[1:]):
                assert prev.end_samples <= curr.start_samples
            for unit in trk.units:
                assert unit.channel_count == trk.channel_count
                # 3. Unidade nao aceita fica na track do editor
                if not unit.accepted:
                    assert trk.source_track == unit.source_label
                # 4. Unidade ligada por crossfade com tipos mistos nao muda de track
                if unit.mixed_kinds:
                    assert not unit.accepted
                    assert trk.source_track == unit.source_label

    # 5. No modo pair, os dois clips de um par L/R aceito ficam juntos na mesma unidade
    if stereo == "pair":
        pair_tracks = {(0, 1), (3, 4), (6, 7)}
        for grp in proposal.groups:
            for trk in grp.tracks:
                for unit in trk.units:
                    if unit.source_tracks in pair_tracks:
                        left_keys = [k for k in unit.origin_keys if k[0] == unit.source_tracks[0]]
                        right_keys = [k for k in unit.origin_keys if k[0] == unit.source_tracks[1]]
                        assert len(left_keys) == len(right_keys) >= 1

    # 6. Tracks excluidas, multicanal, video e LTC permanecem intocadas
    excluded_left = {t.source_indices: t for t in proposal.left_tracks if t.excluded}
    assert set(excluded_left.keys()) == {(9,), (10,), (11,), (12,)}
    assert excluded_left[(9,)].name == "Audio Expanded"
    assert excluded_left[(10,)].channel_count == 6
    assert excluded_left[(11,)].name == "Video Ref"
    assert excluded_left[(12,)].name == "LTC"


def _grupo_da_unidade(proposal: Proposal, unit: Unit) -> str:
    """O grupo da proposta onde a unidade ficou."""
    for grupo in proposal.groups:
        for track in grupo.tracks:
            if any(u.origin_keys == unit.origin_keys for u in track.units):
                return grupo.key
    raise AssertionError(f"a unidade {unit.origin_keys} sumiu da proposta")


def test_unidade_nao_aceita_fica_no_grupo_da_track_do_editor_com_compact() -> None:
    """Com a compactacao a unidade nao aceita nao sai do grupo da track do editor.

    A compactacao junta as tracks do mesmo tipo num bloco so: a unidade que o
    editor nao aceitou continua no grupo da track dela, ainda que a track que
    a levava agora seja outra do bloco.
    """
    session, meta_map = _build_joao_session()
    sem = _build_proposal_for_joao(session, meta_map, stereo="pair", level="permissive")
    com = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", signals=DEFAULT_PLAN_SIGNALS
    )
    assert com.compactacao, "a proposta com compactacao conta as tracks por grupo"
    for grupo in sem.groups:
        for track in grupo.tracks:
            for unidade in track.units:
                if not unidade.accepted:
                    assert _grupo_da_unidade(com, unidade) == grupo.key


@pytest.mark.parametrize("stereo", ["pair", "split"])
def test_monotonicity_of_moved_origin_keys(stereo: str) -> None:
    session, meta_map = _build_joao_session()
    p_strict = _build_proposal_for_joao(session, meta_map, stereo=stereo, level="strict")
    p_balanced = _build_proposal_for_joao(session, meta_map, stereo=stereo, level="balanced")
    p_permissive = _build_proposal_for_joao(session, meta_map, stereo=stereo, level="permissive")

    moved_strict = p_strict.moved_origin_keys()
    moved_balanced = p_balanced.moved_origin_keys()
    moved_permissive = p_permissive.moved_origin_keys()

    assert moved_strict < moved_balanced < moved_permissive


def test_naming_grouping_tie_break_and_overlap_spawns_new_track() -> None:
    session = SessionIR(
        name="TieAndOverlap",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
    )
    # Track 0: empate entre CAMERA (aparece primeiro em t=100) e PRODUCTION (em t=2000) -> CAM 1
    _add_track_with_Specs(
        session,
        0,
        "Audio 1",
        [
            ("A024_A021_0519VG_001.r3d", 100, 1000, False),
            ("043CENA35APL1TK4-043.WAV", 2000, 3000, False),
        ],
    )
    # Track 1: PRODUCTION dominante; clip CAMERA em [200, 900] sobrepoe CAM 1 mas nao os WAVs desta
    # track, entao move para CAM 2; e par final [4000, 4800]+[4500, 5200] sobrepoe com tipos mistos
    # na propria track e permanece em PROD 1.
    _add_track_with_Specs(
        session,
        1,
        "Audio 2",
        [
            ("A010_A009_0505HJ_001.R3D", 200, 900, False),
            ("025CENA37PL2TK1-025.WAV", 1000, 1800, False),
            ("058CENA33APL1TK4-058.WAV", 2000, 2800, False),
            ("053CENA30BPL2TK1-053.WAV", 4000, 4800, False),
            ("A012_A006_0511SU_001.r3d", 4500, 5200, False),
        ],
    )
    # Track 2: nenhum clip aceito -> fica em left_tracks com nome original
    _add_track_with_Specs(
        session,
        2,
        "Audio 3",
        [("WhatsApp Audio 2026-05-11 at 22.02.55.mpeg", 0, 1000, False)],
    )

    bwf = _recorder_bwf()
    meta_by_id = {a.id: bwf for a in session.media if a.filename.upper().endswith(".WAV")}
    proposal = build_proposal(
        session,
        stereo="split",
        level="balanced",
        signals=SINAIS_SEM_COMPACT,
        media_meta=meta_by_id.get,
    )
    by_key = {g.key: g for g in proposal.groups}
    assert set(by_key.keys()) == {PRODUCTION, CAMERA}

    # O WAV da Audio 1 (perdeu o empate para CAMERA) sobrepoe o PROD 1 em [2000, 2800] e nasce
    # PROD 2; a unidade sobreposta mista da Audio 2 fica no PROD 1
    prod_grp = by_key[PRODUCTION]
    assert prod_grp.label == "PRODUCTION"
    assert [t.name for t in prod_grp.tracks] == ["PROD 1", "PROD 2"]
    assert prod_grp.tracks[0].source_track == "Audio 2"
    assert prod_grp.tracks[1].source_track is None
    mixed_units = [u for u in prod_grp.tracks[0].units if u.mixed_kinds]
    assert len(mixed_units) == 1
    assert mixed_units[0].origin_keys == ((1, 3), (1, 4))
    assert not mixed_units[0].accepted

    # Empate na Audio 1 escolheu CAMERA (apareceu primeiro), e o clip sobreposto da Audio 2 criou CAM 2
    cam_grp = by_key[CAMERA]
    assert [t.name for t in cam_grp.tracks] == ["CAM 1", "CAM 2"]
    assert cam_grp.tracks[0].source_track == "Audio 1"
    assert cam_grp.tracks[1].source_track is None

    # Track sem tipo dominante mantem o nome do editor em left_tracks
    non_excluded_left = [t for t in proposal.left_tracks if not t.excluded]
    assert [t.name for t in non_excluded_left] == ["Audio 3"]


def test_edits_untick_group_track_clip_rename_and_json_roundtrip() -> None:
    session, meta_map = _build_joao_session()
    base = _build_proposal_for_joao(session, meta_map, stereo="pair", level="permissive")

    # Determinismo: duas chamadas com as mesmas entradas produzem exatamente o mesmo JSON
    base_again = _build_proposal_for_joao(session, meta_map, stereo="pair", level="permissive")
    assert base.to_json() == base_again.to_json()

    # Round-trip JSON da proposta e das edicoes
    assert Proposal.from_json(base.to_json()) == base

    # 1. Desmarcar uma chave de origem de um par L/R aplica a unidade inteira e a devolve para a track do editor
    # Em Audio 1_L/R, o clip (0, 2) e A017...r3d_L (CAMERA), que mudava para CAM
    edits_clip = Edits(origins={(0, 2): False})
    assert Edits.from_json(edits_clip.to_json()) == edits_clip
    prop_clip = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", edits=edits_clip
    )
    assert (0, 2) in base.moved_origin_keys()
    assert (1, 2) in base.moved_origin_keys()
    assert (0, 2) not in prop_clip.moved_origin_keys()
    assert (1, 2) not in prop_clip.moved_origin_keys()

    # 2. Desmarcar um grupo inteiro (CAMERA) devolve suas unidades para as tracks do editor
    edits_grp = Edits(groups={CAMERA: False})
    prop_grp = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", edits=edits_grp
    )
    assert CAMERA not in {g.key for g in prop_grp.groups}

    # 3. Desmarcar uma track de destino especifica devolve as unidades dela
    sfx_grp = next(g for g in base.groups if g.key == SFX)
    sfx_trk_key = sfx_grp.tracks[0].key
    edits_trk = Edits(tracks={sfx_trk_key: False})
    prop_trk = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", edits=edits_trk
    )
    assert SFX not in {g.key for g in prop_trk.groups}

    # 4. Renomear grupo de familia renomeia suas tracks; renomear track individual funciona
    fam_grp = next(g for g in base.groups if g.is_family and "homemjovem" in g.key)
    prod_trk_key = next(g for g in base.groups if g.key == PRODUCTION).tracks[0].key
    edits_names = Edits(
        group_names={fam_grp.key: "Locucao Jovem"},
        track_names={prod_trk_key: "Boom Direto 1"},
    )
    prop_names = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", edits=edits_names
    )
    renamed_fam = next(g for g in prop_names.groups if g.key == fam_grp.key)
    assert renamed_fam.label == "Locucao Jovem"
    assert renamed_fam.tracks[0].name == "Locucao Jovem 1"
    renamed_prod = next(g for g in prop_names.groups if g.key == PRODUCTION)
    assert renamed_prod.tracks[0].name == "Boom Direto 1"
    assert Proposal.from_json(prop_names.to_json()) == prop_names


def test_all_seven_kind_short_names_and_family_toggle() -> None:
    session, meta_map = _build_joao_session()
    with_fam = _build_proposal_for_joao(
        session, meta_map, stereo="pair", level="permissive", signals=DEFAULT_PLAN_SIGNALS
    )
    without_fam = _build_proposal_for_joao(
        session,
        meta_map,
        stereo="pair",
        level="permissive",
        signals=frozenset({"file", "meta", "name"}),
    )
    assert any(g.is_family for g in with_fam.groups)
    assert not any(g.is_family for g in without_fam.groups)

    short_by_kind = {g.key: g.short_name for g in without_fam.groups}
    assert short_by_kind == {
        PRODUCTION: "PROD",
        VO: "VO",
        CAMERA: "CAM",
        MUSIC: "MX",
        SFX: "SFX",
        AMBIENCE: "AMB",
        PICTURE_REF: "REF",
    }


def test_familia_usa_o_nome_do_clip_quando_a_midia_e_um_uuid() -> None:
    from aafton.organize_units import Unit, _unit_family_filename

    unidade = Unit(
        origin_keys=((0, 0),),
        source_tracks=(0,),
        source_label="Audio 5",
        channel_count=1,
        start_samples=0,
        end_samples=10,
        name="Personagem_02_Texto_A.mp3",
        media_filename="77fba76b-50f9-60aa-a203-e29b0000007c.wav",
        kind=None,
        score=0,
        conflict=False,
        reasons=(),
        accepted=False,
    )
    assert _unit_family_filename(unidade) == "Personagem_02_Texto_A.mp3"
