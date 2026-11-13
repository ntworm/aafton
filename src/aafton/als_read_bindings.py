# src/aafton/als_read_bindings.py
"""Bindings entre as faixas do ALS e as faixas de origem.

O sidecar novo guarda o Id do Live, e a identidade da faixa e direta. O sidecar
antigo so tinha nome e indice; nesse caso a identidade pode ser ambigua, e o
leitor avisa em vez de adivinhar.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field

from .ir import SessionIR, TrackIR
from .report import Report
from .sidecar import Anchor


@dataclass
class BindingIndexes:
    """Indices de busca dos bindings do sidecar."""

    by_index: dict[int, dict] = field(default_factory=dict)
    by_name: dict[str, dict | None] = field(default_factory=dict)
    by_id: dict[str, dict | None] = field(default_factory=dict)
    used: set[int] = field(default_factory=set)


@dataclass
class BindingLookup:
    """Estado da busca de bindings de origem de um mesmo ALS."""

    anchor: Anchor | None = None
    indexes: BindingIndexes = field(default_factory=BindingIndexes)
    current_names: set[str] = field(default_factory=set)


def build_binding_indexes(anchor: Anchor | None) -> BindingIndexes:
    """Indexa os bindings do sidecar por indice, por nome e por Id do Live."""
    indexes = BindingIndexes()
    if not anchor or not anchor.track_bindings:
        return indexes
    for b in anchor.track_bindings:
        if not isinstance(b, dict):
            continue
        _index_binding(indexes, b)
    return indexes


def _index_binding(indexes: BindingIndexes, b: dict) -> None:
    """Guarda o binding nos indices de indice, de nome e de Id do Live."""
    idx = b.get("index")
    if idx is not None:
        indexes.by_index[int(idx)] = b
    live_track_id = b.get("live_track_id")
    if live_track_id is not None:
        key = str(live_track_id)
        indexes.by_id[key] = None if key in indexes.by_id else b
    b_name = b.get("track_name") or b.get("name")
    if b_name and b_name not in indexes.by_name:
        indexes.by_name[b_name] = b
    elif b_name:
        # Duplicate name in bindings: ambiguous! Mark as None to reject blind name matching
        indexes.by_name[b_name] = None


def match_binding(
    lookup: BindingLookup,
    track_node: ET.Element,
    track: TrackIR,
    indice: int,
    report: Report,
) -> dict | None:
    """Escolhe o binding de origem da faixa; ambiguidade vira aviso."""
    anchor = lookup.anchor
    if not anchor or not anchor.track_bindings:
        return None
    indexes = lookup.indexes
    if indexes.by_id:
        binding = indexes.by_id.get(track_node.get("Id", ""))
    else:
        binding = _match_legacy_binding(indexes, track, indice, lookup.current_names, report)
    if binding is None:
        return None
    if id(binding) in indexes.used:
        report.warning(
            "AMBIGUOUS_TRACK_MATCH",
            "source binding already used by another track",
            {"track": track.name},
        )
        return None
    indexes.used.add(id(binding))
    return binding


def _match_legacy_binding(
    indexes: BindingIndexes,
    track: TrackIR,
    indice: int,
    current_names: set[str],
    report: Report,
) -> dict | None:
    """Sidecar antigo: o nome unico vence; o indice so recupera com aviso."""
    # Sidecars antigos nao tem Id do Live. Nome unico vence indice;
    # indice so recupera renomeacao ou nomes duplicados, com aviso.
    binding = indexes.by_name.get(track.name)
    if binding is not None:
        return binding
    cand = indexes.by_index.get(indice)
    if cand and (
        (cand.get("track_name") or cand.get("name")) == track.name
        or (cand.get("track_name") or cand.get("name")) not in current_names
    ):
        binding = cand
    report.warning(
        "AMBIGUOUS_TRACK_MATCH",
        f"legacy identity by index for track {track.name!r}; confirm the source",
        {"track": track.name, "index": indice},
    )
    return binding


def apply_binding(track: TrackIR, binding: dict, session: SessionIR) -> None:
    """Aplica o binding de origem na faixa: canais, slot de origem e grupo."""
    if not hasattr(track, "raw"):
        track.raw = {}
    _apply_channel_count(track, binding)
    _apply_origin_slot(track, binding)
    _apply_mob_id(track, binding, session)
    _apply_channel_index(track, binding)
    _apply_multichannel_layout(track, binding)
    _apply_parent_group(track, binding)
    _apply_generated(track, binding)


def _apply_channel_count(track: TrackIR, binding: dict) -> None:
    """Contagem de canais vinda do binding."""
    if "channel_count" in binding and binding["channel_count"] is not None:
        track.channel_count = int(binding["channel_count"])


def _apply_origin_slot(track: TrackIR, binding: dict) -> None:
    """Slot de origem: o indice do slot vira o source_index da faixa."""
    if "origin_slot_id" not in binding or binding["origin_slot_id"] is None:
        return
    orig_slot = binding["origin_slot_id"]
    track.raw["origin_slot_id"] = orig_slot
    if isinstance(orig_slot, list):
        track.original_source_indices = list(orig_slot)
        if orig_slot:
            track.source_index = orig_slot[0]
    else:
        track.original_source_indices = [int(orig_slot)]
        track.source_index = int(orig_slot)


def _apply_mob_id(track: TrackIR, binding: dict, session: SessionIR) -> None:
    """Mob id do binding, tambem marcado nos clips ja lidos."""
    if "mob_id" not in binding or not binding["mob_id"]:
        return
    track.raw["mob_id"] = binding["mob_id"]
    for clip in track.clips:
        if not clip.media_id:
            continue
        asset = session.media_by_id(clip.media_id)
        if asset and not asset.embedded_mob_id:
            asset.embedded_mob_id = str(binding["mob_id"])


def _apply_channel_index(track: TrackIR, binding: dict) -> None:
    """Indice do canal dentro do grupo multicanal."""
    if "channel_index" in binding and binding["channel_index"] is not None:
        track.raw["channel_index"] = int(binding["channel_index"])


def _apply_multichannel_layout(track: TrackIR, binding: dict) -> None:
    """Layout multicanal declarado pelo binding."""
    if "multichannel_layout" in binding and binding["multichannel_layout"] is not None:
        track.raw["multichannel_layout"] = str(binding["multichannel_layout"])
        track.channel_layout = str(binding["multichannel_layout"])


def _apply_parent_group(track: TrackIR, binding: dict) -> None:
    """Grupo pai do binding, acrescentado ao group_path da faixa."""
    if "parent_group" in binding and binding["parent_group"] is not None:
        parent_grp = str(binding["parent_group"])
        track.raw["parent_group"] = parent_grp
        if parent_grp not in track.group_path:
            track.group_path.append(parent_grp)


def _apply_generated(track: TrackIR, binding: dict) -> None:
    """Marca a faixa como gerada por esta ferramenta."""
    if "generated" in binding and binding["generated"]:
        track.raw["generated"] = str(binding["generated"])


def uses_legacy_channels(lookup: BindingLookup, track: TrackIR) -> bool:
    """Diz se a faixa ainda usa o mapa de canais do sidecar antigo."""
    anchor = lookup.anchor
    if lookup.indexes.by_id:
        return False
    if not anchor or not anchor.track_channels:
        return False
    return track.name in anchor.track_channels


def apply_legacy_channels(track: TrackIR, anchor: Anchor, indice: int) -> None:
    """Canais e indices de origem do sidecar antigo, por nome de faixa."""
    track.channel_count = anchor.track_channels[track.name]
    if not anchor.audio_source_indices or indice >= len(anchor.audio_source_indices):
        return
    src_idx = anchor.audio_source_indices[indice]
    if isinstance(src_idx, list):
        track.original_source_indices = list(src_idx)
        if src_idx:
            track.source_index = src_idx[0]
    elif isinstance(src_idx, int):
        track.original_source_indices = [src_idx]
        track.source_index = src_idx


def infer_channels_from_media(track: TrackIR, session: SessionIR, report: Report) -> None:
    """Contagem de canais deduzida dos clips ja lidos da faixa."""
    track_channels_set = set()
    for clip in track.clips:
        clip_asset = next((m for m in session.media if m.id == clip.media_id), None)
        if clip_asset and clip_asset.channels is not None:
            track_channels_set.add(clip_asset.channels)

    if not track_channels_set:
        return
    if len(track_channels_set) > 1:
        max_ch = max(track_channels_set)
        report.warning(
            "TRACK_CHANNEL_MISMATCH",
            f"Track {track.name!r} has clips with mismatching channel counts: {track_channels_set}. Assuming the largest ({max_ch}).",
            {"track": track.name, "channels": list(track_channels_set)},
        )
        track.channel_count = max_ch
    else:
        track.channel_count = track_channels_set.pop()


def _expected_multichannel_members(anchor: Anchor) -> dict[str, list[dict]]:
    """Membros multicanal esperados por grupo, segundo o sidecar."""
    group_expected: dict[str, list[dict]] = defaultdict(list)
    for b in anchor.track_bindings:
        if isinstance(b, dict) and b.get("parent_group"):
            group_expected[b["parent_group"]].append(b)
    return group_expected


def _found_multichannel_members(session: SessionIR) -> set[tuple[str, object]]:
    """Membros multicanal que realmente apareceram no ALS."""
    found: set[tuple[str, object]] = set()
    for t in session.tracks:
        if hasattr(t, "raw") and "channel_index" in t.raw and "parent_group" in t.raw:
            found.add((t.raw["parent_group"], t.raw["channel_index"]))
    return found


def check_multichannel_members(anchor: Anchor | None, session: SessionIR, report: Report) -> None:
    """Avisa quando um canal multicanal do sidecar nao aparece no ALS."""
    if not anchor or not anchor.track_bindings:
        return
    group_expected = _expected_multichannel_members(anchor)
    if not group_expected:
        return
    found_bindings = _found_multichannel_members(session)
    for pg, expected_list in group_expected.items():
        for exp in expected_list:
            ch_idx = exp.get("channel_index")
            if (pg, ch_idx) not in found_bindings:
                report.warning(
                    "MULTICHANNEL_MEMBER_MISSING",
                    f"Multichannel track {exp.get('track_name')!r} (channel {ch_idx}) of group {pg!r} was not found in the ALS.",
                    {
                        "parent_group": pg,
                        "channel_index": ch_idx,
                        "expected_track": exp.get("track_name"),
                    },
                )
