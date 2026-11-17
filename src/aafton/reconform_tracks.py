"""Posicionamento em memoria das tracks [saiu] e pecas removidas na reconformacao.

Clona cada AudioTrack que teve pecas retiradas do corte, esvazia os eventos e
envelopes herdados, silencia a track e insere as pecas sem sobreposicao logo
apos a track de origem.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field

from .als_paths import AUDIO_CLIP_FIELDS, TRACK_FIELDS
from .als_read_clips import _beats_to_samples
from .als_write_base import AnchorError, WriteOptions, _clip_times
from .als_xml import (
    IdAllocator,
    classify_id_tags,
    clone,
    format_value,
    get_value,
    renumber_siblings,
    set_value,
    unique_ids,
)
from .reconform_clips import SaiuPiece
from .report import Report
from .sidecar import Anchor


@dataclass
class SaiuTracks:
    """Mapeamento das tracks [saiu] criadas indexado pelo Id da track de origem."""

    by_source: dict[str, list[ET.Element]] = field(default_factory=dict)
    # Por id() da track [saiu]: os intervalos, no corte antigo, das pecas que
    # ela recebeu. A automacao de um trecho removido vai para a track que tem
    # a peca do mesmo trecho.
    old_ranges: dict[int, list[tuple[int, int]]] = field(default_factory=dict)

    @property
    def tracks(self) -> list[ET.Element]:
        """Lista achatada das tracks [saiu] criadas em ordem de insercao."""
        return [track for group in self.by_source.values() for track in group]

    def __len__(self) -> int:
        return sum(len(group) for group in self.by_source.values())

    def __iter__(self) -> Iterator[ET.Element]:
        return iter(self.tracks)

    def __getitem__(self, index: int | slice) -> ET.Element | list[ET.Element]:
        return self.tracks[index]


@dataclass
class _SaiuSlot:
    """Estado de ocupacao de uma track [saiu] durante o empacotamento."""

    track_node: ET.Element
    events_node: ET.Element | None
    placed_ranges: list[tuple[int, int]] = field(default_factory=list)
    old_ranges: list[tuple[int, int]] = field(default_factory=list)
    regions: set[int] = field(default_factory=set)


def _track_label(track_node: ET.Element) -> str:
    """Nome legivel da AudioTrack antes de aplicar o sufixo [saiu]."""
    return (
        get_value(track_node, TRACK_FIELDS["effective_name"])
        or get_value(track_node, TRACK_FIELDS["name"])
        or ""
    )


def _saiu_name(base_name: str, index: int) -> str:
    """Nome da track [saiu] ou de overflow ([saiu] 2, [saiu] 3, ...)."""
    suffix = "[saiu]" if index == 1 else f"[saiu] {index}"
    return f"{base_name} {suffix}" if base_name else suffix


def _clear_cloned_track(track_node: ET.Element, name: str) -> ET.Element | None:
    """Renomeia, silencia, limpa envelopes e esvazia Events da track clonada."""
    for field_key in ("name", "effective_name"):
        target = track_node.find(TRACK_FIELDS[field_key])
        if target is not None:
            target.set("Value", name)
    mute_node = track_node.find(TRACK_FIELDS["mute"])
    if mute_node is not None:
        mute_node.set("Value", "false")
    envelopes = track_node.find("AutomationEnvelopes/Envelopes")
    if envelopes is not None:
        for env in list(envelopes):
            envelopes.remove(env)
    events = track_node.find(TRACK_FIELDS["arrangement_events"])
    if events is not None:
        for child in list(events):
            events.remove(child)
    return events


def _piece_duration_samples(clip_node: ET.Element, anchor: Anchor) -> int:
    """Duracao em samples de uma peca a partir de CurrentStart e CurrentEnd."""
    raw_start = get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"])
    raw_end = get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"])
    if raw_start is None or raw_end is None:
        return 0
    try:
        start_s = _beats_to_samples(float(raw_start), anchor.sample_rate, anchor.bpm)
        end_s = _beats_to_samples(float(raw_end), anchor.sample_rate, anchor.bpm)
    except ValueError:
        return 0
    return max(0, end_s - start_s)


def _set_piece_timeline(
    clip_node: ET.Element,
    abs_start: int,
    abs_end: int,
    anchor: Anchor,
) -> None:
    """Atualiza Time, CurrentStart e CurrentEnd da peca pela grade do Live.

    A peca entra no arranjo como um clip qualquer: a posicao vai na grade de
    ticks do relogio, e nao num double qualquer.
    """
    start_b, end_b = _clip_times(
        abs_start,
        abs_end - abs_start,
        WriteOptions(
            sample_rate=anchor.sample_rate,
            bpm=anchor.bpm,
            live_origin_samples=anchor.live_origin_samples,
        ),
    )
    clip_node.set("Time", format_value(start_b))
    set_value(clip_node, AUDIO_CLIP_FIELDS["current_start"], start_b)
    set_value(clip_node, AUDIO_CLIP_FIELDS["current_end"], end_b)


def _collides(start: int, end: int, placed: list[tuple[int, int]]) -> bool:
    """Verdadeiro quando [start, end) sobrepoe algum trecho ja colocado."""
    return any(start < other_end and other_start < end for other_start, other_end in placed)


def _pick_slot(
    slots: list[_SaiuSlot],
    regiao: int | None,
    abs_start: int,
    abs_end: int,
    source_track: ET.Element,
    base_name: str,
    allocator: IdAllocator,
    positional_tags: frozenset[str],
    unique: frozenset[int],
) -> _SaiuSlot:
    """Devolve a primeira track [saiu] sem colisao ou cria uma de overflow.

    A ocupacao [abs_start, abs_end) e o trecho removido inteiro quando ele e
    conhecido: a track que ja tem o trecho recebe as outras pecas dele.
    """
    for slot in slots:
        if regiao is not None and regiao in slot.regions:
            return slot
    for slot in slots:
        if not _collides(abs_start, abs_end, slot.placed_ranges):
            return slot
    new_track = clone(source_track, allocator, positional_tags, unique)
    if new_track.get("Id") is None or new_track.get("Id") == source_track.get("Id"):
        new_track.set("Id", str(allocator.next()))
    events_node = _clear_cloned_track(new_track, _saiu_name(base_name, len(slots) + 1))
    slot = _SaiuSlot(track_node=new_track, events_node=events_node)
    slots.append(slot)
    return slot


def _clip_sort_key(clip: ET.Element) -> float:
    """Chave numerica de ordenacao de AudioClips por Time."""
    raw = clip.get("Time") or get_value(clip, AUDIO_CLIP_FIELDS["current_start"]) or "0"
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _finalize_slot_events(events_node: ET.Element | None) -> None:
    """Ordena os AudioClips colocados por Time e renumera Ids posicionais."""
    if events_node is None:
        return
    clips = [child for child in events_node if child.tag == "AudioClip"]
    other = [child for child in events_node if child.tag != "AudioClip"]
    clips.sort(key=_clip_sort_key)
    for child in list(events_node):
        events_node.remove(child)
    for child in other:
        events_node.append(child)
    for clip in clips:
        events_node.append(clip)
    renumber_siblings(events_node, "AudioClip")


def _group_by_source(pieces: list[SaiuPiece]) -> list[tuple[ET.Element, list[SaiuPiece]]]:
    """Agrupa as pecas pela identidade da AudioTrack de origem em ordem de aparicao."""
    grouped: dict[int, list[SaiuPiece]] = {}
    tracks: dict[int, ET.Element] = {}
    for piece in pieces:
        key = id(piece.track_node)
        tracks[key] = piece.track_node
        grouped.setdefault(key, []).append(piece)
    return [(tracks[key], grouped[key]) for key in grouped]


def _ocupar(slot: _SaiuSlot, regiao: int | None, ocupa: tuple[int, int]) -> None:
    """Registra na track [saiu] o trecho que ela passou a ocupar.

    O intervalo de um trecho entra uma vez so: as outras pecas do mesmo trecho
    nao colidem com ele.
    """
    if regiao is None or regiao not in slot.regions:
        slot.placed_ranges.append(ocupa)
    if regiao is not None:
        slot.regions.add(regiao)


def _place_for_source(
    source_track: ET.Element,
    group: list[SaiuPiece],
    anchor: Anchor,
    report: Report,
    allocator: IdAllocator,
    positional_tags: frozenset[str],
    unique: frozenset[int],
) -> list[ET.Element]:
    """Cria as tracks [saiu] de uma AudioTrack e distribui suas pecas sem colisao."""
    base_name = _track_label(source_track)
    slots: list[_SaiuSlot] = []
    for piece in group:
        abs_start = piece.new_abs_start
        abs_end = abs_start + _piece_duration_samples(piece.clip_element, anchor)
        try:
            _set_piece_timeline(piece.clip_element, abs_start, abs_end, anchor)
        except AnchorError:
            ctx = {
                "track": base_name,
                "clip_id": piece.clip_element.get("Id"),
                "clip_name": get_value(piece.clip_element, AUDIO_CLIP_FIELDS["name"]) or "",
            }
            report.error(
                "RECONFORM_BEFORE_ORIGIN", "the removed piece falls before the Live origin", ctx
            )
            continue
        regiao, ocupa = None, (abs_start, abs_end)
        if piece.region_new is not None:
            regiao, ocupa = piece.region_new[0], piece.region_new[1:]
        slot = _pick_slot(
            slots, regiao, *ocupa, source_track, base_name, allocator, positional_tags, unique
        )
        if slot.events_node is not None:
            slot.events_node.append(piece.clip_element)
        _ocupar(slot, regiao, ocupa)
        if piece.old_abs_start >= 0:
            slot.old_ranges.append((piece.old_abs_start, piece.old_abs_start + abs_end - abs_start))
    for slot in slots:
        _finalize_slot_events(slot.events_node)
    return slots


def _insert_after(container: ET.Element, anchor_node: ET.Element, nodes: list[ET.Element]) -> None:
    """Insere as novas tracks logo apos a track de origem em LiveSet/Tracks."""
    idx = list(container).index(anchor_node)
    for offset, node in enumerate(nodes, start=1):
        container.insert(idx + offset, node)


def _update_next_pointee_id(root: ET.Element, allocator: IdAllocator) -> None:
    """Atualiza NextPointeeId se o alocador tiver avancado alem do valor atual."""
    next_node = root.find(".//NextPointeeId")
    if next_node is None:
        return
    raw = next_node.get("Value", "")
    current = int(raw) if raw.lstrip("-").isdigit() else 0
    if current <= allocator._cursor:
        allocator.write_next_pointee_id(root)


def place_saiu_pieces(
    root: ET.Element,
    anchor: Anchor,
    pieces: list[SaiuPiece],
    report: Report,
    allocator: IdAllocator,
) -> SaiuTracks:
    """Coloca as pecas removidas em tracks '<nome> [saiu]' mutadas apos a origem."""
    result = SaiuTracks()
    container = root.find("./LiveSet/Tracks")
    if container is None:
        container = root.find(".//LiveSet/Tracks")
    if not pieces or container is None:
        return result
    members = set(container)
    positional_tags, _ = classify_id_tags(root)
    unique = unique_ids(root)
    for source_track, group in _group_by_source(pieces):
        if source_track not in members:
            continue
        slots = _place_for_source(
            source_track, group, anchor, report, allocator, positional_tags, unique
        )
        created = [slot.track_node for slot in slots]
        for slot in slots:
            result.old_ranges[id(slot.track_node)] = list(slot.old_ranges)
        if created:
            _insert_after(container, source_track, created)
            result.by_source[source_track.get("Id", "")] = created
    if result.by_source:
        _update_next_pointee_id(root, allocator)
    return result
