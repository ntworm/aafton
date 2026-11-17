"""Proposta de organizacao de sessao (grupos, tracks de destino, edicoes e JSON).

Implementa as secoes 'Disposicao proposta', 'Cores' (rotulos e nomes curtos) e
'Revisao e edicoes' do plano de organizacao de sessao (P2). Funcao pura
build_proposal sobre SessionIR antes do pareamento estereo.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from collections import Counter
from dataclasses import dataclass, field, replace

from .bwf_meta import BwfInfo
from .ir import SessionIR, TrackIR
from .organize_classify import (
    AMBIENCE,
    CAMERA,
    CERTAINTY_LEVELS,
    MUSIC,
    PICTURE_REF,
    PRODUCTION,
    SFX,
    VO,
)
from .organize_compact import compactar_homes
from .organize_desmarque import desmarcar_por_chave
from .organize_family import Family
from .organize_home import _HomeBuilder, _unit_fits
from .organize_plan_json import (
    destination_track_from_dict,
    destination_track_to_dict,
    edits_from_dict,
    edits_from_json,
    edits_to_dict,
    edits_to_json,
    left_track_from_dict,
    left_track_to_dict,
    proposal_from_dict,
    proposal_from_json,
    proposal_group_from_dict,
    proposal_group_to_dict,
    proposal_to_dict,
    proposal_to_json,
)
from .organize_renomear import resolver_nomes
from .organize_units import (
    FAMILY_PREFIX,
    excluded_track_units,
    OriginKey,
    PairKey,
    Unit,
    build_session_units,
    default_media_meta,
    detect_accepted_pairs,
    is_track_excluded,
)

DEFAULT_PLAN_SIGNALS: frozenset[str] = frozenset(
    {"file", "meta", "name", "family", "color", "compact"}
)
KIND_ORDER: tuple[str, ...] = (PRODUCTION, VO, CAMERA, MUSIC, SFX, AMBIENCE, PICTURE_REF)
KIND_LABELS: dict[str, tuple[str, str]] = {
    PRODUCTION: ("PRODUCTION", "PROD"),
    VO: ("VO / NARRATION", "VO"),
    CAMERA: ("CAMERA", "CAM"),
    MUSIC: ("MUSIC", "MX"),
    SFX: ("SFX", "SFX"),
    AMBIENCE: ("AMBIENCE", "AMB"),
    PICTURE_REF: ("PICTURE REF", "REF"),
}


@dataclass(frozen=True)
class Edits:
    """Edicoes de revisao por chave de grupo, track de destino e chave de origem."""

    groups: dict[str, bool] = field(default_factory=dict)
    tracks: dict[str, bool] = field(default_factory=dict)
    origins: dict[OriginKey, bool] = field(default_factory=dict)
    group_names: dict[str, str] = field(default_factory=dict)
    track_names: dict[str, str] = field(default_factory=dict)
    track_name_origins: dict[str, tuple[OriginKey, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return edits_to_dict(self)

    def to_json(self) -> str:
        return edits_to_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> Edits:
        return edits_from_dict(cls, data)

    @classmethod
    def from_json(cls, text: str) -> Edits:
        return edits_from_json(cls, text)


@dataclass(frozen=True)
class DestinationTrack:
    """Track de destino proposta dentro de um grupo de tipo ou de familia."""

    key: str
    name: str
    kind: str
    channel_count: int
    units: tuple[Unit, ...]
    source_track: str | None
    source_indices: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return destination_track_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DestinationTrack:
        return destination_track_from_dict(cls, Unit, data)


@dataclass(frozen=True)
class ProposalGroup:
    """Grupo de tipo ou de familia com suas tracks de destino ordenadas."""

    key: str
    label: str
    short_name: str
    is_family: bool
    tracks: tuple[DestinationTrack, ...]

    def to_dict(self) -> dict[str, object]:
        return proposal_group_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ProposalGroup:
        return proposal_group_from_dict(cls, DestinationTrack, data)


@dataclass(frozen=True)
class LeftTrack:
    """Track do editor que permanece como esta (sem tipo dominante ou fora de escopo)."""

    key: str
    name: str
    channel_count: int
    source_indices: tuple[int, ...]
    units: tuple[Unit, ...]
    excluded: bool = False

    def to_dict(self) -> dict[str, object]:
        return left_track_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> LeftTrack:
        return left_track_from_dict(cls, Unit, data)


@dataclass(frozen=True)
class Proposal:
    """Proposta completa de organizacao da sessao."""

    stereo: str
    level: str
    signals: tuple[str, ...]
    groups: tuple[ProposalGroup, ...]
    left_tracks: tuple[LeftTrack, ...]
    edits: Edits = field(default_factory=Edits)
    compactacao: dict[str, tuple[int, int]] = field(default_factory=dict)

    def all_origin_keys(self) -> list[OriginKey]:
        """Lista todas as chaves de origem presentes nos grupos e nas tracks mantidas."""
        keys = [k for g in self.groups for t in g.tracks for u in t.units for k in u.origin_keys]
        keys.extend(k for left in self.left_tracks for u in left.units for k in u.origin_keys)
        return keys

    def moved_origin_keys(self) -> set[OriginKey]:
        """Conjunto das chaves de origem que mudaram da track original do editor.

        So conta unidade aceita: a nao aceita fica no grupo da track do editor
        dela, e a compactacao pode po-la numa track que herdou outra identidade.
        """
        return {
            k
            for g in self.groups
            for t in g.tracks
            for u in t.units
            if u.accepted and (not t.source_indices or u.source_tracks != t.source_indices)
            for k in u.origin_keys
        }

    def to_dict(self) -> dict[str, object]:
        return proposal_to_dict(self)

    def to_json(self) -> str:
        return proposal_to_json(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Proposal:
        return proposal_from_dict(cls, ProposalGroup, LeftTrack, Edits, data)

    @classmethod
    def from_json(cls, text: str) -> Proposal:
        return proposal_from_json(cls, ProposalGroup, LeftTrack, Edits, text)


def _dominant_kind(units: list[Unit]) -> str | None:
    """Escolhe o tipo com mais unidades aceitas; desempata pela primeira aparicao na track."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for idx, unit in enumerate(units):
        if unit.accepted and unit.kind is not None:
            counts[unit.kind] = counts.get(unit.kind, 0) + 1
            first_seen.setdefault(unit.kind, idx)
    return min(counts, key=lambda k: (-counts[k], first_seen[k])) if counts else None


def _apply_unit_override(unit: Unit, edits: Edits) -> Unit:
    """Aplica edicoes por grupo e por chave de origem (uma chave vale para a unidade toda)."""
    override = edits.groups.get(unit.kind) if unit.kind is not None else None
    for o_key in unit.origin_keys:
        if o_key in edits.origins:
            override = edits.origins[o_key]
            break
    if override is None or (override and unit.kind is None) or override == unit.accepted:
        return unit
    return replace(unit, accepted=override)


def _group_units_by_editor_track(
    units: list[Unit],
) -> list[tuple[str, tuple[int, ...], int, list[Unit]]]:
    """Agrupa unidades pela track do editor (ou par L/R) mantendo a ordem de aparicao."""
    # A chave e o conjunto de slots da track do editor: nomes podem se repetir no AAF.
    order: list[tuple[int, ...]] = []
    by_src: dict[tuple[int, ...], list[Unit]] = {}
    for u in units:
        if u.source_tracks not in by_src:
            order.append(u.source_tracks)
            by_src[u.source_tracks] = []
        by_src[u.source_tracks].append(u)
    return [(by_src[k][0].source_label, k, by_src[k][0].channel_count, by_src[k]) for k in order]


def _assign_home_keys(homes: list[_HomeBuilder], ordered_kinds: list[str]) -> None:
    """Atribui a chave deterministica de cada track de destino como no mock."""
    # Track do editor com nome repetido leva os slots na chave, para nao colidir.
    nomes = Counter(h.source_track for h in homes if h.source_track is not None)
    for kind in ordered_kinds:
        for idx, h in enumerate([x for x in homes if x.kind == kind]):
            suffix = h.source_track if h.source_track is not None else f"new{idx}"
            if h.source_track is not None and nomes[h.source_track] > 1:
                suffix += "@" + "-".join(str(i) for i in h.source_indices)
            h.key = f"{kind}:{suffix}"


def _place_moving_units(homes: list[_HomeBuilder], moving: list[Unit]) -> None:
    """Aloca unidades que mudam de track na primeira compativel onde caibam ou cria nova."""
    moving_sorted = sorted(
        moving,
        key=lambda u: (u.start_samples, u.end_samples, u.source_tracks, u.origin_keys),
    )
    for u in moving_sorted:
        assert u.kind is not None
        target = next(
            (
                h
                for h in homes
                if h.kind == u.kind
                and h.channel_count == u.channel_count
                and _unit_fits(h.spans, u)
            ),
            None,
        )
        if target is None:
            target = _HomeBuilder(u.kind, None, (), u.channel_count)
            homes.append(target)
        target.units.append(u)
        target.spans.append((u.start_samples, u.end_samples))


def _layout_units(
    units: list[Unit],
    ordered_kinds: list[str],
    empty_in_scope: list[TrackIR],
    compact: bool = False,
) -> tuple[list[_HomeBuilder], list[LeftTrack], dict[str, tuple[int, int]]]:
    """Executa a disposicao em tracks dominantes, realocando unidades de outros tipos."""
    homes: list[_HomeBuilder] = []
    left_tracks: list[LeftTrack] = []
    moving: list[Unit] = []

    grupos = _group_units_by_editor_track(units)
    # A posicao de cada track do editor na ordem das agrupagens das unidades.
    ordem_editor = {grupo[1]: idx for idx, grupo in enumerate(grupos)}

    for src_label, src_indices, ch_count, track_units in grupos:
        dom = _dominant_kind(track_units)
        if dom is None:
            sorted_u = tuple(sorted(track_units, key=lambda u: u.start_samples))
            left_tracks.append(
                LeftTrack(
                    f"left:{src_label}@{'-'.join(str(i) for i in src_indices)}",
                    src_label,
                    ch_count,
                    src_indices,
                    sorted_u,
                    False,
                )
            )
            continue
        home = _HomeBuilder(dom, src_label, src_indices, ch_count)
        for u in track_units:
            if u.accepted and u.kind != dom:
                moving.append(u)
            else:
                home.units.append(u)
                home.spans.append((u.start_samples, u.end_samples))
        homes.append(home)

    for trk in empty_in_scope:
        left_tracks.append(
            LeftTrack(
                f"left:{trk.name}@{trk.source_index}",
                trk.name,
                trk.channel_count,
                tuple(trk.original_source_indices or [trk.source_index]),
                (),
            )
        )
    _place_moving_units(homes, moving)
    compactacao: dict[str, tuple[int, int]] = {}
    if compact:
        homes, compactacao = compactar_homes(homes, ordem_editor)
    _assign_home_keys(homes, ordered_kinds)
    return homes, left_tracks, compactacao


def _build_excluded_left_tracks(
    session: SessionIR,
    excluded_source_indices: Collection[int],
) -> list[LeftTrack]:
    """Constroi LeftTrack para as tracks fora de escopo (excluidas, video, LTC, >2 canais)."""
    out: list[LeftTrack] = []
    for track in session.tracks:
        if not is_track_excluded(track, excluded_source_indices):
            continue
        units = excluded_track_units(session, track)
        out.append(
            LeftTrack(
                f"excluded:{track.source_index}",
                track.name,
                track.channel_count,
                tuple(track.original_source_indices or [track.source_index]),
                units,
                True,
            )
        )
    return out


def _resolve_group_titles(
    kind: str,
    family_by_key: dict[str, Family],
    edits: Edits,
) -> tuple[bool, str, str]:
    """Devolve (is_family, rotulo_do_grupo, prefixo_curto_das_tracks)."""
    is_fam = kind.startswith(FAMILY_PREFIX)
    custom = edits.group_names.get(kind) or edits.group_names.get(f"g:{kind}")
    if is_fam:
        default_label = family_by_key[kind].label if kind in family_by_key else kind[7:]
        label = custom or default_label
        return True, label, label
    default_label, default_short = KIND_LABELS[kind]
    return False, custom or default_label, custom or default_short


def _build_groups(
    homes: list[_HomeBuilder],
    ordered_kinds: list[str],
    family_by_key: dict[str, Family],
    edits: Edits,
) -> list[ProposalGroup]:
    """Monta os ProposalGroup com nomes curtos numerados e aplica renomeacoes."""
    groups: list[ProposalGroup] = []
    for kind in ordered_kinds:
        kind_homes = [h for h in homes if h.kind == kind and h.units]
        if not kind_homes:
            continue
        is_fam, g_label, short_prefix = _resolve_group_titles(kind, family_by_key, edits)
        dest_tracks = tuple(
            DestinationTrack(
                key=h.key,
                name=edits.track_names.get(h.key) or f"{short_prefix} {idx + 1}",
                kind=kind,
                channel_count=h.channel_count,
                units=tuple(
                    sorted(h.units, key=lambda u: (u.start_samples, u.end_samples, u.origin_keys))
                ),
                source_track=h.source_track,
                source_indices=h.source_indices,
            )
            for idx, h in enumerate(kind_homes)
        )
        groups.append(ProposalGroup(kind, g_label, short_prefix, is_fam, dest_tracks))
    return groups


def _validate_inputs(stereo: str, level: str) -> None:
    """Valida os parametros stereo e level de build_proposal."""
    if stereo not in ("pair", "split"):
        raise ValueError(f"unknown stereo mode: {stereo!r}")
    if level not in CERTAINTY_LEVELS:
        raise ValueError(f"unknown certainty level: {level!r}")


def _resolve_empty_in_scope(
    session: SessionIR,
    excluded_source_indices: Collection[int],
    units: list[Unit],
) -> list[TrackIR]:
    """Encontra tracks em escopo que nao possuem nenhum clip."""
    covered = {src for u in units for src in u.source_tracks}
    return [
        t
        for t in session.tracks
        if not is_track_excluded(t, excluded_source_indices) and t.source_index not in covered
    ]


def build_proposal(
    session: SessionIR,
    *,
    stereo: str = "pair",
    level: str = "permissive",
    signals: Iterable[str] = DEFAULT_PLAN_SIGNALS,
    edits: Edits | dict[str, object] | None = None,
    excluded_source_indices: Collection[int] = (),
    accepted_pairs: Collection[PairKey] | None = None,
    media_meta: Callable[[str | None], BwfInfo | None] | None = None,
) -> Proposal:
    """Constroi a proposta pura de organizacao da sessao antes do pareamento estereo."""
    _validate_inputs(stereo, level)
    sig_set = frozenset(signals)
    norm_edits = edits if isinstance(edits, Edits) else Edits.from_dict(edits)
    pairs = (
        detect_accepted_pairs(session, stereo) if accepted_pairs is None else set(accepted_pairs)
    )
    meta_fn = default_media_meta(session) if media_meta is None else media_meta
    raw_units, family_by_key = build_session_units(
        session,
        stereo=stereo,
        level=level,
        signals=sig_set,
        excluded_source_indices=excluded_source_indices,
        accepted_pairs=pairs,
        media_meta=meta_fn,
    )
    ordered_kinds = [*KIND_ORDER, *family_by_key.keys()]
    empty_tracks = _resolve_empty_in_scope(session, excluded_source_indices, raw_units)
    compact = "compact" in sig_set
    desmarque = desmarcar_por_chave(
        raw_units,
        ordered_kinds,
        empty_tracks,
        norm_edits,
        compact,
    )
    group_edits = resolver_nomes(desmarque.primeira, desmarque.final, norm_edits)

    excluded_left = _build_excluded_left_tracks(session, excluded_source_indices)
    groups = _build_groups(desmarque.final, ordered_kinds, family_by_key, group_edits)
    return Proposal(
        stereo=stereo,
        level=level,
        signals=tuple(sorted(sig_set)),
        groups=tuple(groups),
        left_tracks=tuple([*desmarque.left_tracks, *excluded_left]),
        edits=replace(
            norm_edits,
            track_names=group_edits.track_names,
            track_name_origins=group_edits.track_name_origins,
        ),
        compactacao=desmarque.compactacao,
    )
