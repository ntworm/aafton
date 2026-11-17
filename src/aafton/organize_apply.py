"""Aplicacao em memoria da proposta de organizacao de sessao (P3a).

Anota as chaves de origem nos clips antes do pareamento estereo e aplica a
proposta depois do pareamento e da expansao do som direto: move as unidades
encontradas pelas chaves de origem para as tracks de destino, renomeia,
atribui rotulo de grupo e cores da paleta do Live 12, ordena as tracks e
relata as unidades que nao puderam ser encontradas. A track do editor que a
organizacao esvaziou sai da sessao, para o set do Live nao ficar com track
vazia.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ir import ClipIR, SessionIR, TrackIR
from .organize_apply_vazias import remover_tracks_esvaziadas, tracks_com_clips
from .organize_classify import (
    AMBIENCE,
    CAMERA,
    MUSIC,
    PICTURE_REF,
    PRODUCTION,
    SFX,
    VO,
)
from .organize_plan import DestinationTrack, Proposal, ProposalGroup
from .organize_units import OriginKey, Unit, origin_keys_for_track, sort_track_clips
from .report import Report

FAMILY_COLOR_INDEX = 16
LIVE_COLOR_BY_KIND: dict[str, int] = {
    PRODUCTION: 3,
    VO: 9,
    CAMERA: 27,
    MUSIC: 24,
    SFX: 14,
    AMBIENCE: 19,
    PICTURE_REF: 13,
}


@dataclass(frozen=True)
class UnmatchedUnit:
    """Unidade da proposta que nao pode ser encontrada na sessao pos-pareamento."""

    group_key: str
    destination_track_key: str
    destination_track_name: str
    origin_keys: tuple[OriginKey, ...]
    source_label: str
    name: str
    reason: str


def color_for_group(kind: str, is_family: bool = False) -> int:
    """Devolve o indice de cor da paleta de clips do Live 12 para um tipo ou familia."""
    if is_family or kind.startswith("family:"):
        return FAMILY_COLOR_INDEX
    return LIVE_COLOR_BY_KIND.get(kind, FAMILY_COLOR_INDEX)


def annotate_origin_keys(session: SessionIR) -> None:
    """Registra em cada clip clip.raw['origin_keys'] = [[source_index, event_order]]."""
    for track in session.tracks:
        chaves = origin_keys_for_track(track)
        for clip in track.clips:
            clip.raw["origin_keys"] = [list(chaves[id(clip)])]


def _clip_origin_tuple(clip: ClipIR) -> tuple[OriginKey, ...]:
    """Converte clip.raw['origin_keys'] em tupla de (source_index, event_order)."""
    raw_keys = clip.raw.get("origin_keys")
    if not raw_keys:
        return ()
    return tuple((int(pair[0]), int(pair[1])) for pair in raw_keys)


ClipPair = tuple[TrackIR, ClipIR]


def _index_session_clips(
    session: SessionIR,
) -> tuple[dict[tuple[OriginKey, ...], ClipPair], dict[OriginKey, ClipPair]]:
    """Indexa clips da sessao pelo conjunto exato de chaves e por chave individual."""
    by_exact: dict[tuple[OriginKey, ...], ClipPair] = {}
    by_single: dict[OriginKey, ClipPair] = {}
    for track in session.tracks:
        for clip in track.clips:
            keys = _clip_origin_tuple(clip)
            if not keys:
                continue
            by_exact[keys] = (track, clip)
            for chave in keys:
                by_single[chave] = (track, clip)
    return by_exact, by_single


def _find_unit_clips(
    unit: Unit,
    by_exact: dict[tuple[OriginKey, ...], ClipPair],
    by_key: dict[OriginKey, ClipPair],
) -> list[ClipPair] | None:
    """Localiza os clips que realizam a unidade apos o pareamento estereo.

    Cada chave de origem esta em exatamente um clip: no clip mono, no clip de um
    par L/R ou de uma track combinada que juntou os dois canais, ou ainda numa das
    projecoes separadas de uma combinada de WAV estereo. A unidade se realiza
    quando os clips das suas chaves cobrem essas chaves e nenhuma outra.
    """
    achados = [by_key.get(chave) for chave in unit.origin_keys]
    if any(a is None for a in achados):
        return None
    clips: list[ClipPair] = []
    vistos: set[int] = set()
    for par in achados:
        if id(par[1]) not in vistos:
            vistos.add(id(par[1]))
            clips.append(par)
    cobertas = {chave for _, clip in clips for chave in _clip_origin_tuple(clip)}
    if cobertas != set(unit.origin_keys):
        return None
    # Par de tracks _L/_R: so se realiza depois de virar uma track estereo.
    if len(unit.source_tracks) == 2 and any(t.channel_count != 2 for t, _ in clips):
        return None
    return clips


def _find_home_track(
    session: SessionIR,
    dest: DestinationTrack,
    claimed_tracks: set[int],
) -> TrackIR | None:
    """Localiza a track do editor que vira a track dominante de destino."""
    if dest.source_track is None or not dest.source_indices:
        return None
    target_set = set(dest.source_indices)
    livres = [
        t
        for t in session.tracks
        if id(t) not in claimed_tracks and t.channel_count == dest.channel_count
    ]
    exata = next((t for t in livres if set(t.original_source_indices) == target_set), None)
    if exata is not None:
        return exata
    return next((t for t in livres if t.source_index in target_set), None)


def _make_new_destination_track(dest: DestinationTrack, new_index: int) -> TrackIR:
    """Cria uma TrackIR nova para uma track de destino sem track propria no editor."""
    track = TrackIR(
        id=f"org_new_{new_index}",
        source_index=-1,
        name=dest.name,
        kind="audio",
        channel_count=dest.channel_count,
        original_source_indices=[-1],
    )
    return track


def _detach_clip(source_track: TrackIR, clip: ClipIR) -> None:
    """Remove o clip da track de origem."""
    source_track.clips = [c for c in source_track.clips if c is not clip]


def _style_destination_track(
    track: TrackIR,
    dest: DestinationTrack,
    group: ProposalGroup,
    color_idx: int | None,
) -> None:
    """Aplica nome, grupo, cor e ordenacao dos clips na track de destino."""
    track.name = dest.name
    track.organize_group = group.label
    track.organize_group_color = color_idx
    track.color_index = color_idx
    track.clips = sort_track_clips(track.clips)
    for idx, clip in enumerate(track.clips):
        clip.track_id = track.id
        clip.event_index = idx
        clip.color_index = color_idx


def _clear_colors(session: SessionIR) -> None:
    """Garante color_index e organize_group_color None quando colors=False."""
    for track in session.tracks:
        track.color_index = None
        track.organize_group_color = None
        for clip in track.clips:
            clip.color_index = None


def _apply_destination_track(
    session: SessionIR,
    group: ProposalGroup,
    dest: DestinationTrack,
    g_color: int | None,
    by_exact: dict[tuple[OriginKey, ...], ClipPair],
    by_single: dict[OriginKey, ClipPair],
    claimed_tracks: set[int],
    unmatched: list[UnmatchedUnit],
    new_counter: int,
) -> tuple[TrackIR | None, int]:
    """Aplica uma track de destino, movendo os clips encontrados e estilizando."""
    matched_clips: list[ClipPair] = []
    for unit in dest.units:
        found = _find_unit_clips(unit, by_exact, by_single)
        if found is None:
            unmatched.append(
                UnmatchedUnit(
                    group_key=group.key,
                    destination_track_key=dest.key,
                    destination_track_name=dest.name,
                    origin_keys=unit.origin_keys,
                    source_label=unit.source_label,
                    name=unit.name,
                    reason="unit clips not found in paired session",
                )
            )
        else:
            matched_clips.extend(found)

    home = _find_home_track(session, dest, claimed_tracks)
    if home is None:
        if not matched_clips:
            return None, new_counter
        new_counter += 1
        target_track = _make_new_destination_track(dest, new_counter)
    else:
        target_track = home
        claimed_tracks.add(id(target_track))

    for src_trk, clip in matched_clips:
        if src_trk is not target_track:
            _detach_clip(src_trk, clip)
            target_track.clips.append(clip)

    _style_destination_track(target_track, dest, group, g_color)
    return target_track, new_counter


def _usa_cores(proposal: Proposal, colors: bool) -> bool:
    """Cor so com o pedido e com o sinal color na proposta (ou sem lista de sinais)."""
    if not colors:
        return False
    return "color" in proposal.signals if proposal.signals else True


def _renumerar(tracks: list[TrackIR]) -> None:
    """Refaz track_id e event_index dos clips das tracks que ficam como estao."""
    for trk in tracks:
        for idx, clip in enumerate(trk.clips):
            clip.track_id = trk.id
            clip.event_index = idx


def apply_proposal(
    session: SessionIR,
    proposal: Proposal,
    *,
    colors: bool = True,
    report: Report | None = None,
) -> list[UnmatchedUnit]:
    """Aplica a proposta em memoria apos pareamento estereo e expansao de canais.

    Devolve a lista de unidades que nao puderam ser encontradas (por exemplo, um
    par que falhou ao entrelacar na escrita e permaneceu dividido nas tracks do
    editor). A track do editor que a organizacao esvaziou sai da sessao; a que
    tem envelope proprio fica, e o relatorio diz o nome dela.
    """
    use_colors = _usa_cores(proposal, colors)
    com_clips = tracks_com_clips(session)
    by_exact, by_single = _index_session_clips(session)
    claimed_tracks: set[int] = set()
    ordered_dest_tracks: list[TrackIR] = []
    unmatched: list[UnmatchedUnit] = []
    new_counter = 0

    for group in proposal.groups:
        g_color = color_for_group(group.key, group.is_family) if use_colors else None
        for dest in group.tracks:
            target, new_counter = _apply_destination_track(
                session,
                group,
                dest,
                g_color,
                by_exact,
                by_single,
                claimed_tracks,
                unmatched,
                new_counter,
            )
            if target is not None:
                ordered_dest_tracks.append(target)

    remaining_tracks = [t for t in session.tracks if id(t) not in claimed_tracks]
    _renumerar(remaining_tracks)

    session.tracks = [*ordered_dest_tracks, *remaining_tracks]
    remover_tracks_esvaziadas(session, com_clips, report)
    if not use_colors:
        _clear_colors(session)
    return unmatched
