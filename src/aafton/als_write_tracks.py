"""Emissao das tracks de audio do Live a partir da sessao IR.

Este modulo concentra a criacao dos nos de AudioTrack e GroupTrack, a
montagem dos clips dentro de cada track e o registro das ligacoes que vao
para o sidecar.
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .als_crossfade import canonicalizar_crossfades
from .als_write_base import (
    AnchorError,
    WriteOptions,
    attach_to_group,
    detach_from_group,
)
from .als_write_clips import build_audio_clip, build_gain_envelope
from .als_xml import IdAllocator, clone, format_value, renumber_siblings, set_value
from .ir import SessionIR
from .media_extract import read_audio_metadata
from .report import Report


@dataclass
class WriteContext:
    """Estado compartilhado pelas etapas de emissao das tracks."""

    session: SessionIR
    options: WriteOptions
    report: Report
    allocator: IdAllocator
    posicionais: Any
    unicos: Any
    track_prototype: ET.Element
    group_prototype: ET.Element
    clip_prototype: ET.Element
    gain_envelope_prototype: ET.Element | None
    next_track_id: int
    track_to_group: dict[int, str]
    novas_tracks: list[ET.Element]
    created_groups: dict[str, int]
    emitted_tracks: set[int]
    audio_source_indices: list[Any]
    track_bindings: list[dict]
    audio_track_idx: int
    clip_ranges: list[dict]
    total_soltas: int = 0
    metadata_cache: dict = field(default_factory=dict)


def track_binding(ctx: WriteContext, idx: int, tr, node: ET.Element) -> dict:
    """Monta o registro de ligacao entre a track do Live e a origem."""
    binding = {
        "index": idx,
        "live_track_id": node.get("Id"),
        "track_name": tr.name,
        "origin_slot_id": _binding_slot_id(idx, tr),
        "mob_id": _binding_mob_id(ctx, tr),
        "channel_count": tr.channel_count,
    }
    _add_binding_extras(binding, tr)
    return binding


def _binding_slot_id(idx: int, tr) -> Any:
    """Descobre o slot de origem da track."""
    if hasattr(tr, "raw") and isinstance(tr.raw, dict) and "origin_slot_id" in tr.raw:
        return tr.raw["origin_slot_id"]
    if tr.original_source_indices:
        if len(tr.original_source_indices) == 1:
            return tr.original_source_indices[0]
        return list(tr.original_source_indices)
    if tr.source_index is not None:
        return tr.source_index
    return idx


def _binding_mob_id(ctx: WriteContext, tr) -> str | None:
    """Procura o mob id da origem da track."""
    if hasattr(tr, "raw") and isinstance(tr.raw, dict) and tr.raw.get("mob_id"):
        return str(tr.raw["mob_id"])
    for c in tr.clips:
        mob_id = _clip_mob_id(ctx, c)
        if mob_id:
            return mob_id
    return None


def _clip_mob_id(ctx: WriteContext, c) -> str | None:
    """Procura o mob id de um clip, direto ou pelo asset de midia."""
    if hasattr(c, "raw") and isinstance(c.raw, dict) and c.raw.get("mob_id"):
        return str(c.raw["mob_id"])
    if not c.media_id:
        return None
    asset = ctx.session.media_by_id(c.media_id)
    if not asset:
        return None
    return _asset_mob_id(asset)


def _asset_mob_id(asset) -> str | None:
    """Procura o mob id no asset de midia da origem."""
    if getattr(asset, "embedded_mob_id", None):
        return str(asset.embedded_mob_id)
    if not (hasattr(asset, "raw") and isinstance(asset.raw, dict)):
        return None
    if asset.raw.get("mob_id"):
        return str(asset.raw["mob_id"])
    if asset.raw.get("source_mob_id"):
        return str(asset.raw["source_mob_id"])
    return None


def _add_binding_extras(binding: dict, tr) -> None:
    """Copia os campos extras do ``raw`` da track para a ligacao."""
    if not (hasattr(tr, "raw") and isinstance(tr.raw, dict)):
        return
    if "channel_index" in tr.raw:
        binding["channel_index"] = tr.raw["channel_index"]
    if "multichannel_layout" in tr.raw:
        binding["multichannel_layout"] = tr.raw["multichannel_layout"]
    if "parent_group" in tr.raw:
        binding["parent_group"] = tr.raw["parent_group"]
    if "generated" in tr.raw:
        binding["generated"] = tr.raw["generated"]


def clips_for_live_track(session: SessionIR, t_ir) -> list:
    """Colapsa as duas projecoes de um mesmo clip estereo em um clip do Live.

    O leitor AAF conserva uma projecao por canal para que o ``verify`` possa
    provar a cobertura de todos os slots do ``Audio Channel Combiner``. O
    Live, por outro lado, representa o WAV estereo intercalado com um unico
    ``AudioClip``. Materializar as duas projecoes criaria dois clips
    sobrepostos e faria o exportador descartar uma delas como overlap.
    """
    if t_ir.channel_count != 2:
        return t_ir.clips

    result = []
    first_channel_by_projection = {}
    for clip in t_ir.clips:
        asset = session.media_by_id(clip.media_id)
        if asset is None or asset.channels != 2:
            result.append(clip)
            continue

        projection = (
            clip.media_id,
            clip.name,
            clip.timeline_start_samples,
            clip.timeline_end_samples,
            clip.source_offset_samples,
            clip.gain_linear,
            clip.muted,
            clip.fade_in.duration_samples if clip.fade_in is not None else None,
            clip.fade_out.duration_samples if clip.fade_out is not None else None,
        )
        first_channel = first_channel_by_projection.get(projection)
        if first_channel is not None and first_channel != clip.channel_index:
            continue

        first_channel_by_projection.setdefault(projection, clip.channel_index)
        result.append(clip)

    return result


def build_one_audio_track(ctx: WriteContext, t_ir, pr_id) -> ET.Element:
    """Cria o no de AudioTrack de uma track da sessao."""
    n = clone(ctx.track_prototype, ctx.allocator, ctx.posicionais, ctx.unicos)
    n.set("Id", str(pr_id))
    _set_track_names(n, t_ir)
    _apply_mixer(ctx, n, t_ir)
    _write_track_clips(ctx, n, t_ir)
    _attach_gain_envelope(ctx, n, t_ir)
    return n


def _set_track_names(n: ET.Element, t_ir) -> None:
    """Grava o nome e a anotacao da track no no do Live."""
    b_nome = n.find("Name")
    if b_nome is not None:
        for campo in ("UserName", "EffectiveName"):
            alvo = b_nome.find(campo)
            if alvo is not None:
                alvo.set("Value", t_ir.name)
        ann = b_nome.find("Annotation")
        if ann is not None:
            ann.set("Value", t_ir.annotation or "")


def _apply_mixer(ctx: WriteContext, n: ET.Element, t_ir) -> None:
    """Grava pan, mute e volume da track no mixer do Live."""
    try:
        pan_value = float(t_ir.pan)
    except (TypeError, ValueError, OverflowError):
        pan_value = math.nan
    if not math.isfinite(pan_value) or not -1.0 <= pan_value <= 1.0:
        ctx.report.error(
            "PAN_VALUE_INVALID",
            f"the track {t_ir.name!r} has pan outside the [-1, 1] range: "
            f"{t_ir.pan!r}; the center was used",
            {"track": t_ir.name, "value": repr(t_ir.pan)},
        )
        pan_value = 0.0

    pan_manual = n.find("DeviceChain/Mixer/Pan/Manual")
    if pan_manual is not None:
        pan_manual.set("Value", format_value(pan_value))

    man = n.find("DeviceChain/Mixer/Speaker/Manual")
    if man is not None:
        man.set("Value", format_value(not t_ir.muted))
    volume = n.find("DeviceChain/Mixer/Volume/Manual")
    if volume is not None:
        volume.set("Value", format_value(float(t_ir.gain_linear)))


def _write_track_clips(ctx: WriteContext, n: ET.Element, t_ir) -> None:
    """Escreve os clips da track na sequencia do Live e registra os ranges."""
    eventos = n.find("DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events")
    escritos: set[tuple[int, int]] = set()
    if eventos is not None:
        for velho in list(eventos):
            eventos.remove(velho)
        pares: list[tuple[object, ET.Element]] = []
        for clip in clips_for_live_track(ctx.session, t_ir):
            asset = ctx.session.media_by_id(clip.media_id)
            if asset is None:
                continue
            try:
                xml_clip = build_audio_clip(
                    clip,
                    asset,
                    ctx.clip_prototype,
                    ctx.allocator,
                    ctx.posicionais,
                    ctx.unicos,
                    ctx.options,
                    ctx.report,
                    is_video=t_ir.is_video,
                )
                eventos.append(xml_clip)
                escritos.add((clip.timeline_start_samples, clip.timeline_end_samples))
                pares.append((clip, xml_clip))
            except AnchorError:
                ctx.report.error(
                    "CLIP_BEFORE_ORIGIN",
                    f"the clip {clip.name!r} starts before the timeline origin",
                    {"clip_id": clip.id},
                )
            except ValueError as e:
                ctx.report.warning("ALS_UNRESOLVED_MEDIA", str(e), {"clip_id": clip.id})
        canonicalizar_crossfades(
            pares,
            ctx.options,
            is_video=t_ir.is_video,
            material=lambda clip: _material_do_clip(ctx, clip),
        )
        renumber_siblings(eventos, "AudioClip")
    ctx.clip_ranges.append({"track": t_ir.name, "ranges": [list(r) for r in sorted(escritos)]})


def _material_do_clip(ctx: WriteContext, clip) -> tuple[int, int] | None:
    """Duracao e taxa do arquivo que o clip usa, quando o arquivo responde.

    O escritor pergunta ao arquivo o mesmo que o leitor vai exigir: o material
    tem de cobrir o clip que sai, senao o .als canonico volta truncado.
    Devolve None quando nao da para saber, e material desconhecido cobre.
    """
    asset = ctx.session.media_by_id(clip.media_id)
    if asset is None or asset.status != "resolved" or not asset.resolved_path:
        return None
    caminho = str(asset.resolved_path)
    if caminho not in ctx.metadata_cache:
        ctx.metadata_cache[caminho] = _ler_metadados(caminho)
    meta = ctx.metadata_cache[caminho]
    if meta is None:
        return None
    duracao = meta.get("duration_samples")
    taxa = meta.get("sample_rate")
    if duracao is None or taxa is None:
        return None
    return duracao, taxa


def _ler_metadados(caminho: str) -> dict | None:
    """Metadados do arquivo de audio; None quando nao ha arquivo ou ele nao abre."""
    try:
        p = Path(caminho)
        if not p.is_file():
            return None
        return read_audio_metadata(p)
    except Exception:
        return None


def _attach_gain_envelope(ctx: WriteContext, n: ET.Element, t_ir) -> None:
    """Anexa a envelope de ganho da track quando ha pontos de ganho."""
    if not t_ir.gain_points or ctx.gain_envelope_prototype is None:
        return
    volume_at = n.find(".//DeviceChain/Mixer/Volume/AutomationTarget")
    if volume_at is None:
        return
    vol_id = volume_at.get("Id", "")
    if not vol_id:
        return
    env_node = build_gain_envelope(
        t_ir.gain_points,
        vol_id,
        ctx.gain_envelope_prototype,
        ctx.allocator,
        ctx.posicionais,
        ctx.unicos,
        ctx.options,
        t_ir.name,
        ctx.report,
    )
    envelopes_container = n.find(".//AutomationEnvelopes/Envelopes")
    if envelopes_container is not None:
        envelopes_container.append(env_node)
        renumber_siblings(envelopes_container, "AutomationEnvelope")


def emit_tracks(ctx: WriteContext) -> None:
    """Emite as tracks da sessao, respeitando os grupos inferidos."""
    for track in ctx.session.tracks:
        if track.channel_count > 2:
            _emit_multichannel_group(ctx, track)
            continue
        if id(track) in ctx.emitted_tracks:
            continue
        prefix = ctx.track_to_group.get(id(track))
        if prefix:
            _emit_prefix_group(ctx, prefix)
        else:
            _emit_ungrouped_track(ctx, track)


def _emit_multichannel_group(ctx: WriteContext, track) -> None:
    """Emite o grupo multicanal e as tracks filhos de cada canal."""
    from .channels import expand_multichannel_track

    layout_name = track.channel_layout or (
        "Quad"
        if track.channel_count == 4
        else (
            "5.1"
            if track.channel_count == 6
            else ("7.1" if track.channel_count == 8 else f"{track.channel_count}ch")
        )
    )
    group_name = f"{track.name} ({layout_name})"

    group_id = _new_group_node(ctx, group_name)
    child_tracks = expand_multichannel_track(track, layout_name=layout_name)
    for child in child_tracks:
        if not hasattr(child, "raw") or not isinstance(child.raw, dict):
            child.raw = {}
        child.raw["parent_group"] = group_name

        node = build_one_audio_track(ctx, child, ctx.next_track_id)
        ctx.next_track_id += 1
        attach_to_group(node, group_id)
        _register_emitted_track(ctx, child, node)
    ctx.emitted_tracks.add(id(track))


def _emit_prefix_group(ctx: WriteContext, prefix: str) -> None:
    """Emite o grupo do prefixo e todas as tracks que pertencem a ele."""
    if prefix not in ctx.created_groups:
        ctx.created_groups[prefix] = _new_group_node(ctx, prefix)
    for t_member in ctx.session.tracks:
        if id(t_member) in ctx.emitted_tracks:
            continue
        if ctx.track_to_group.get(id(t_member)) != prefix:
            continue
        node = build_one_audio_track(ctx, t_member, ctx.next_track_id)
        ctx.next_track_id += 1
        attach_to_group(node, ctx.created_groups[prefix])
        _register_emitted_track(ctx, t_member, node)


def _emit_ungrouped_track(ctx: WriteContext, track) -> None:
    """Emite uma track que nao pertence a nenhum grupo."""
    node = build_one_audio_track(ctx, track, ctx.next_track_id)
    ctx.next_track_id += 1
    try:
        set_value(node, "TrackGroupId", -1)
    except KeyError:
        pass
    detach_from_group(node)
    _register_emitted_track(ctx, track, node)


def _new_group_node(ctx: WriteContext, name: str) -> int:
    """Cria um GroupTrack novo e devolve o id reservado para ele."""
    group_node = clone(ctx.group_prototype, ctx.allocator, ctx.posicionais, ctx.unicos)
    group_id = ctx.next_track_id
    ctx.next_track_id += 1
    group_node.set("Id", str(group_id))
    b_nome = group_node.find("Name")
    if b_nome is not None:
        for c in ("UserName", "EffectiveName"):
            a = b_nome.find(c)
            if a is not None:
                a.set("Value", name)
    try:
        set_value(group_node, "TrackGroupId", -1)
    except KeyError:
        pass
    detach_from_group(group_node)
    ctx.novas_tracks.append(group_node)
    return group_id


def _register_emitted_track(ctx: WriteContext, tr, node: ET.Element) -> None:
    """Registra a track emitida nas listas do Live e do sidecar."""
    ctx.novas_tracks.append(node)
    ctx.emitted_tracks.add(id(tr))
    if not getattr(tr, "raw", {}).get("generated"):
        ctx.audio_source_indices.append(tr.original_source_indices)
    ctx.track_bindings.append(track_binding(ctx, ctx.audio_track_idx, tr, node))
    ctx.audio_track_idx += 1
