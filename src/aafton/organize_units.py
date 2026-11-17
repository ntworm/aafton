"""Unidades de movimento e chaves de origem da organizacao de sessao.

Implementa as secoes 'Identidade dos clips' e 'Disposicao proposta' (etapa de
unidades) do plano de organizacao de sessao (P2). Cada clip recebe a chave de
origem (source_index, ordem do evento na track) identica a verify._index.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from copy import deepcopy
from dataclasses import dataclass

from .bwf_meta import BwfInfo
from .ir import ClipIR, SessionIR, TrackIR
from .organize_classify import (
    ALL_SIGNALS,
    Classification,
    classify,
    moves,
    read_media_meta,
    strip_channel_and_extensions,
)
from .organize_family import Family, families, stem
from .organize_plan_json import unit_from_dict, unit_to_dict
from .report import Report
from .stereo import process_stereo_pairing

OriginKey = tuple[int, int]
PairKey = tuple[int, int]
FAMILY_PREFIX = "family:"


@dataclass(frozen=True)
class ClipEvent:
    """Evento indexado na track do editor antes do pareamento e da organizacao."""

    origin_key: OriginKey
    source_index: int
    track_name: str
    clip: ClipIR
    media_filename: str | None
    classification: Classification


@dataclass(frozen=True)
class Unit:
    """Unidade indivisivel de movimento (clip simples, par L/R ou cadeia de crossfade)."""

    origin_keys: tuple[OriginKey, ...]
    source_tracks: tuple[int, ...]
    source_label: str
    channel_count: int
    start_samples: int
    end_samples: int
    name: str
    media_filename: str | None
    kind: str | None
    score: int
    conflict: bool
    reasons: tuple[str, ...]
    accepted: bool
    mixed_kinds: bool = False
    is_family: bool = False

    def to_dict(self) -> dict[str, object]:
        """Converte a unidade para dicionario serializavel em JSON."""
        return unit_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Unit:
        """Reconstroi Unit a partir de dicionario desserializado de JSON."""
        return unit_from_dict(cls, data)


def detect_accepted_pairs(session: SessionIR, stereo: str = "pair") -> set[PairKey]:
    """Descobre quais pares (left_source_index, right_source_index) o pareamento aceita."""
    if stereo != "pair":
        return set()
    copied = deepcopy(session)
    process_stereo_pairing(copied, "pair", Report(strict=False), None, dry_run=True)
    return {
        (t.original_source_indices[0], t.original_source_indices[1])
        for t in copied.tracks
        if len(t.original_source_indices) == 2
        and t.original_source_indices[0] != t.original_source_indices[1]
    }


def is_track_excluded(track: TrackIR, excluded_source_indices: Collection[int]) -> bool:
    """Indica se a track esta fora do alcance da organizacao."""
    return (
        track.source_index in excluded_source_indices
        or track.expansion_group is not None
        or track.channel_count > 2
        or track.is_video
        or track.kind == "video"
        or track.name.strip().upper() == "LTC"
    )


def sort_track_clips(clips: list[ClipIR]) -> list[ClipIR]:
    """Ordena os clips da track exatamente como verify._index."""
    return sorted(
        clips,
        key=lambda c: (c.timeline_start_samples, c.timeline_end_samples, c.name, c.id),
    )


def default_media_meta(session: SessionIR) -> Callable[[str | None], BwfInfo | None]:
    """Cria resolvedor padrao media_id -> BwfInfo com cache por media_id."""
    cache: dict[str, BwfInfo | None] = {}

    def resolver(media_id: str | None) -> BwfInfo | None:
        if not media_id:
            return None
        if media_id not in cache:
            asset = session.media_by_id(media_id)
            cache[media_id] = read_media_meta(asset.resolved_path if asset else None)
        return cache[media_id]

    return resolver


def origin_keys_for_track(track: TrackIR) -> dict[int, tuple[int, int]]:
    """Chave de origem (slot, ordem) de cada clip, no criterio de verify._index.

    Numa track com mais de um slot (Audio Channel Combiner do Pro Tools), cada
    canal e um slot proprio, com a ordem dos clips contada so naquele canal.
    """
    slots = track.original_source_indices
    if len(slots) <= 1:
        return {
            id(c): (track.source_index, pos) for pos, c in enumerate(sort_track_clips(track.clips))
        }
    chaves: dict[int, tuple[int, int]] = {}
    for canal, slot in enumerate(slots):
        do_canal = [c for c in track.clips if c.channel_index == canal]
        for pos, clip in enumerate(sort_track_clips(do_canal)):
            chaves[id(clip)] = (slot, pos)
    for clip in track.clips:
        chaves.setdefault(id(clip), (track.source_index, -1))
    return chaves


def excluded_track_units(session: SessionIR, track: TrackIR) -> tuple[Unit, ...]:
    """Unidades de uma track fora do alcance: ficam como estao, com a chave por canal."""
    chaves = origin_keys_for_track(track)

    def nome_da_midia(clip: ClipIR) -> str | None:
        asset = session.media_by_id(clip.media_id) if clip.media_id else None
        return asset.filename if asset is not None else None

    return tuple(
        Unit(
            origin_keys=(chaves[id(clip)],),
            source_tracks=(track.source_index,),
            source_label=track.name,
            channel_count=track.channel_count,
            start_samples=clip.timeline_start_samples,
            end_samples=clip.timeline_end_samples,
            name=clip.name,
            media_filename=nome_da_midia(clip),
            kind=None,
            score=0,
            conflict=False,
            reasons=(),
            accepted=False,
        )
        for clip in sort_track_clips(track.clips)
    )


def index_track_events(
    session: SessionIR,
    track: TrackIR,
    signals: frozenset[str],
    media_meta: Callable[[str | None], BwfInfo | None],
) -> list[ClipEvent]:
    """Indexa e classifica os clips de uma track na ordem de verify._index."""
    classify_signals = signals & ALL_SIGNALS
    events: list[ClipEvent] = []
    chaves = origin_keys_for_track(track)
    for clip in sort_track_clips(track.clips):
        chave = chaves[id(clip)]
        asset = session.media_by_id(clip.media_id) if clip.media_id else None
        media_fn = asset.filename if asset is not None else None
        bwf = media_meta(clip.media_id) if "meta" in classify_signals else None
        cls = classify(clip.name, media_fn, bwf, classify_signals)
        events.append(
            ClipEvent(
                chave,
                chave[0],
                track.name,
                clip,
                media_fn,
                cls,
            )
        )
    return events


def _group_track_chains(events: list[ClipEvent]) -> list[list[ClipEvent]]:
    """Agrupa eventos sobrepostos ou ligados por crossfade_in_state na mesma track."""
    if not events:
        return []
    chains: list[list[ClipEvent]] = [[events[0]]]
    chain_end = events[0].clip.timeline_end_samples
    for ev in events[1:]:
        if ev.clip.crossfade_in_state or ev.clip.timeline_start_samples < chain_end:
            chains[-1].append(ev)
            chain_end = max(chain_end, ev.clip.timeline_end_samples)
        else:
            chains.append([ev])
            chain_end = ev.clip.timeline_end_samples
    return chains


def _clean_pair_clip_name(name: str) -> str:
    """Remove sufixo _L/_R do nome para comparar os dois lados de um par estereo."""
    base, ext = strip_channel_and_extensions(name)
    return f"{base}{ext}" if ext else base


def _chains_match_for_pair(left_chain: list[ClipEvent], right_chain: list[ClipEvent]) -> bool:
    """Verifica se duas cadeias em X_L e X_R tem o mesmo nome limpo e mesmo inicio."""
    return len(left_chain) == len(right_chain) and all(
        ev_l.clip.timeline_start_samples == ev_r.clip.timeline_start_samples
        and ev_l.clip.timeline_end_samples == ev_r.clip.timeline_end_samples
        and _clean_pair_clip_name(ev_l.clip.name) == _clean_pair_clip_name(ev_r.clip.name)
        for ev_l, ev_r in zip(left_chain, right_chain)
    )


def _merge_chain_classifications(
    events: list[ClipEvent],
    level: str,
) -> tuple[str | None, int, bool, tuple[str, ...], bool, bool]:
    """Combina a classificacao dos eventos de uma unidade indivisivel."""
    kinds = {ev.classification.kind for ev in events}
    reasons = tuple(dict.fromkeys(r for ev in events for r in ev.classification.reasons))
    if len(kinds) > 1:
        return None, 0, True, reasons, False, True
    first_cls = events[0].classification
    if first_cls.kind is None:
        return None, 0, first_cls.conflict, first_cls.reasons, False, False
    all_move = all(moves(ev.classification, level) for ev in events)
    min_score = min(ev.classification.score for ev in events)
    any_conflict = any(ev.classification.conflict for ev in events)
    return first_cls.kind, min_score, any_conflict, reasons, all_move, False


def _build_unit_from_events(
    events: list[ClipEvent],
    source_tracks: tuple[int, ...],
    source_label: str,
    channel_count: int,
    level: str,
) -> Unit:
    """Constroi uma Unit a partir da lista de eventos que a compoem."""
    kind, score, conflict, reasons, is_accepted, mixed = _merge_chain_classifications(events, level)
    first = events[0]
    return Unit(
        origin_keys=tuple(ev.origin_key for ev in events),
        source_tracks=source_tracks,
        source_label=source_label,
        channel_count=channel_count,
        start_samples=min(ev.clip.timeline_start_samples for ev in events),
        end_samples=max(ev.clip.timeline_end_samples for ev in events),
        name=first.clip.name,
        media_filename=first.media_filename,
        kind=kind,
        score=score,
        conflict=conflict,
        reasons=reasons,
        accepted=is_accepted,
        mixed_kinds=mixed,
        is_family=False,
    )


def _pair_source_label(left_name: str, right_name: str) -> str:
    """Gera o rotulo de origem para um par estereo (ex: Audio 1_L/R)."""
    if left_name.endswith(("_L", ".L")):
        return f"{left_name[:-2]}_L/R"
    return f"{left_name}/{right_name}"


def _build_paired_track_units(
    left_track: TrackIR,
    right_track: TrackIR,
    left_events: list[ClipEvent],
    right_events: list[ClipEvent],
    level: str,
) -> list[Unit]:
    """Constroi unidades para um par de tracks X_L e X_R aceito pelo pareamento."""
    left_chains = _group_track_chains(left_events)
    right_chains = _group_track_chains(right_events)
    right_by_start = {ch[0].clip.timeline_start_samples: ch for ch in right_chains}
    used_right: set[int] = set()
    units_out: list[Unit] = []
    pair_label = _pair_source_label(left_track.name, right_track.name)
    pair_sources = (left_track.source_index, right_track.source_index)
    l_src, r_src = (left_track.source_index,), (right_track.source_index,)

    for l_chain in left_chains:
        start = l_chain[0].clip.timeline_start_samples
        r_chain = right_by_start.get(start)
        if r_chain is not None and _chains_match_for_pair(l_chain, r_chain):
            used_right.add(start)
            units_out.append(
                _build_unit_from_events([*l_chain, *r_chain], pair_sources, pair_label, 2, level)
            )
        else:
            units_out.append(
                _build_unit_from_events(
                    l_chain, l_src, left_track.name, left_track.channel_count, level
                )
            )

    for r_chain in right_chains:
        if r_chain[0].clip.timeline_start_samples not in used_right:
            units_out.append(
                _build_unit_from_events(
                    r_chain, r_src, right_track.name, right_track.channel_count, level
                )
            )
    return units_out


def _build_single_track_units(
    track: TrackIR,
    events: list[ClipEvent],
    level: str,
) -> list[Unit]:
    """Constroi as unidades de uma track mono ou estereo sem par L/R."""
    src = (track.source_index,)
    return [
        _build_unit_from_events(chain, src, track.name, track.channel_count, level)
        for chain in _group_track_chains(events)
    ]


def _resolve_pair_map(
    stereo: str,
    accepted_pairs: Collection[PairKey],
    events_by_src: dict[int, list[ClipEvent]],
) -> dict[int, int]:
    """Filtra os pares (left_source_index, right_source_index) validos e em escopo."""
    if stereo != "pair":
        return {}
    return {
        l_idx: r_idx
        for l_idx, r_idx in accepted_pairs
        if l_idx in events_by_src and r_idx in events_by_src
    }


def build_session_units(
    session: SessionIR,
    *,
    stereo: str,
    level: str,
    signals: frozenset[str],
    excluded_source_indices: Collection[int],
    accepted_pairs: Collection[PairKey],
    media_meta: Callable[[str | None], BwfInfo | None],
) -> tuple[list[Unit], dict[str, Family]]:
    """Constroi todas as unidades em escopo e aplica o passe de familia quando habilitado."""
    tracks_by_src = {t.source_index: t for t in session.tracks}
    events_by_src = {
        t.source_index: index_track_events(session, t, signals, media_meta)
        for t in session.tracks
        if not is_track_excluded(t, excluded_source_indices)
    }
    pair_map = _resolve_pair_map(stereo, accepted_pairs, events_by_src)
    right_set = set(pair_map.values())

    raw_units: list[Unit] = []
    for track in session.tracks:
        src = track.source_index
        if src not in events_by_src or src in right_set:
            continue
        if src in pair_map:
            r_idx = pair_map[src]
            raw_units.extend(
                _build_paired_track_units(
                    track, tracks_by_src[r_idx], events_by_src[src], events_by_src[r_idx], level
                )
            )
        else:
            raw_units.extend(_build_single_track_units(track, events_by_src[src], level))

    return _apply_family_pass(raw_units, level) if "family" in signals else (raw_units, {})


def _unit_family_filename(unit: Unit) -> str:
    """Escolhe o nome de arquivo representativo da unidade para formar familia."""
    # O Premiere troca o arquivo por um UUID conformado (77fba76b-....wav): sem
    # radical no nome da midia, vale o nome do clip, que guarda o arquivo original.
    raw = unit.media_filename if unit.media_filename and stem(unit.media_filename) else unit.name
    base, ext = strip_channel_and_extensions(raw)
    return f"{base}{ext}" if ext else base


def _family_moves(score: int, level: str) -> bool:
    """Decide se uma unidade de familia move no nivel de certeza escolhido."""
    return score >= 1 if level == "permissive" else (score >= 2 if level == "balanced" else False)


def _promote_to_family(unit: Unit, fam: Family, level: str) -> Unit:
    """Promove uma unidade sem tipo vencedor para a familia correspondente."""
    return Unit(
        origin_keys=unit.origin_keys,
        source_tracks=unit.source_tracks,
        source_label=unit.source_label,
        channel_count=unit.channel_count,
        start_samples=unit.start_samples,
        end_samples=unit.end_samples,
        name=unit.name,
        media_filename=unit.media_filename,
        kind=f"{FAMILY_PREFIX}{fam.key}",
        score=fam.score,
        conflict=False,
        reasons=(f"file family ({fam.label})",),
        accepted=_family_moves(fam.score, level),
        mixed_kinds=False,
        is_family=True,
    )


def _apply_family_pass(
    units: list[Unit],
    level: str,
) -> tuple[list[Unit], dict[str, Family]]:
    """Atribui grupos de familia as unidades sem tipo vencedor nem conflito de cadeia."""
    candidates = [
        _unit_family_filename(u)
        for u in units
        if u.kind is None and not u.mixed_kinds and not u.conflict
    ]
    found_families = families(candidates)
    if not found_families:
        return units, {}
    file_to_family = {fn: fam for fam in found_families for fn in fam.files}
    family_by_key = {f"{FAMILY_PREFIX}{fam.key}": fam for fam in found_families}
    updated = [
        _promote_to_family(u, fam, level)
        if u.kind is None
        and not u.mixed_kinds
        and not u.conflict
        and (fam := file_to_family.get(_unit_family_filename(u))) is not None
        else u
        for u in units
    ]
    return updated, family_by_key
