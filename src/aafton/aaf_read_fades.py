"""Absorcao dos fades renderizados do Pro Tools.

Com `--fades absorb`, os clips de fade do Pro Tools viram fades nativos do Live.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ir import FadeIR, TrackIR

if TYPE_CHECKING:
    from .aaf_read_tracks import TrackContext


# Tipos de item que podem receber um fade sem serem clip.
TIPOS_SEM_CLIP = ("FILLER", "FADE", None, "TRANSITION", "OTHER")


def absorver_fades(ctx: TrackContext, track: TrackIR, raw_items: list[dict]) -> None:
    """Absorve os itens FADE da faixa em fades nativos do Live."""
    non_zero_indices = [i for i, it in enumerate(raw_items) if it["length_samples"] > 0]
    for pos_in_nz, idx in enumerate(non_zero_indices):
        if raw_items[idx]["kind"] != "FADE":
            continue
        fade_item = raw_items[idx]
        anterior = pos_in_nz - 1
        seguinte = pos_in_nz + 1
        prev_idx = non_zero_indices[anterior] if anterior >= 0 else None
        next_idx = non_zero_indices[seguinte] if seguinte < len(non_zero_indices) else None
        prev_item = raw_items[prev_idx] if prev_idx is not None else None
        next_item = raw_items[next_idx] if next_idx is not None else None
        _ganho_do_fade(ctx, track, fade_item, prev_item, next_item)
        _aplicar_fade(ctx, track, fade_item, prev_item, next_item)


def _ganho_do_fade(ctx, track, fade_item, prev_item, next_item) -> None:
    """Le o ganho do fade, que pode ser uma rampa de automacao."""
    p_kind = prev_item["kind"] if prev_item else None
    n_kind = next_item["kind"] if next_item else None
    target_clip_name = ""
    if p_kind == "CLIP":
        target_clip_name = prev_item["clip_assets"][0][0].name
    elif n_kind == "CLIP":
        target_clip_name = next_item["clip_assets"][0][0].name
    if "parameters" in fade_item:
        ctx.constant_gain(
            fade_item["parameters"], ctx.report, track.name, target_clip_name, is_fade=True
        )


def _aplicar_fade(ctx, track, fade_item, prev_item, next_item) -> None:
    """Distribui o fade entre os vizinhos, conforme o que existe em volta."""
    p_kind = prev_item["kind"] if prev_item else None
    n_kind = next_item["kind"] if next_item else None
    L = fade_item["length_samples"]
    if p_kind == "CLIP" and n_kind == "CLIP":
        ctx.total_absorbed_fades += 1
        _crossfade(ctx, track, prev_item, next_item, L)
        return
    if p_kind == "CLIP" and n_kind in TIPOS_SEM_CLIP:
        ctx.total_absorbed_fades += 1
        _fade_out(ctx, track, prev_item, L)
        return
    if p_kind in TIPOS_SEM_CLIP and n_kind == "CLIP":
        ctx.total_absorbed_fades += 1
        _fade_in(ctx, track, next_item, L)
        return
    _fade_sem_clips(ctx)


def _crossfade(ctx, track, prev_item, next_item, L) -> None:
    """Crossfade entre dois clips vizinhos."""
    clip_a, asset_a = prev_item["clip_assets"][0]
    clip_b = next_item["clip_assets"][0][0]
    clamped_L = L
    offset_b = clip_b.source_offset_samples or 0
    if offset_b < clamped_L:
        clamped_L = offset_b
        ctx.report.warning(
            "FADE_CLAMPED_START",
            f"insufficient offset ({offset_b}) in the clip {clip_b.name!r}; "
            f"crossfade of {L} samples shortened to {clamped_L}",
            {
                "clip": clip_b.name,
                "track": track.name,
                "requested": L,
                "applied": clamped_L,
            },
        )
    avail = _material_restante(clip_a, asset_a)
    if avail is not None and avail < clamped_L:
        clamped_L_new = avail
        ctx.report.warning(
            "FADE_CLAMPED_END",
            f"not enough media duration in the clip {clip_a.name!r}; "
            f"crossfade of {clamped_L} samples shortened to {clamped_L_new}",
            {
                "clip": clip_a.name,
                "track": track.name,
                "requested": clamped_L,
                "applied": clamped_L_new,
            },
        )
        clamped_L = clamped_L_new
    clip_a.timeline_end_samples += clamped_L
    clip_a.fade_out = FadeIR(kind="out", duration_samples=clamped_L)
    clip_b.crossfade_in_state = True


def _fade_out(ctx, track, prev_item, L) -> None:
    """Fade out no clip que vem antes do fade."""
    for clip, asset in prev_item["clip_assets"]:
        clamped_L = L
        avail = _material_restante(clip, asset)
        if avail is not None and avail < L:
            clamped_L = avail
            ctx.report.warning(
                "FADE_CLAMPED_END",
                f"not enough media duration in the clip {clip.name!r}; "
                f"fade-out of {L} samples shortened to {clamped_L}",
                {
                    "clip": clip.name,
                    "track": track.name,
                    "requested": L,
                    "applied": clamped_L,
                },
            )
        clip.timeline_end_samples += clamped_L
        clip.fade_out = FadeIR(kind="out", duration_samples=clamped_L)


def _fade_in(ctx, track, next_item, L) -> None:
    """Fade in no clip que vem depois do fade."""
    for clip, _asset in next_item["clip_assets"]:
        offset = clip.source_offset_samples or 0
        if offset < L:
            clamped_L = offset
            clip.timeline_start_samples -= clamped_L
            clip.source_offset_samples = 0
            clip.fade_in = FadeIR(kind="in", duration_samples=clamped_L)
            ctx.report.warning(
                "FADE_CLAMPED_START",
                f"insufficient offset ({offset}) in the clip {clip.name!r}; "
                f"fade-in of {L} samples shortened to {clamped_L}",
                {
                    "clip": clip.name,
                    "track": track.name,
                    "requested": L,
                    "applied": clamped_L,
                },
            )
        else:
            clip.timeline_start_samples -= L
            clip.source_offset_samples -= L
            clip.fade_in = FadeIR(kind="in", duration_samples=L)


def _fade_sem_clips(ctx) -> None:
    """Fade sem clip vizinho: ele so entra na contagem do resumo."""
    ctx.total_absorbed_fades += 1


def _material_restante(clip, asset) -> int | None:
    """Samples de material que ainda existem no arquivo, depois do clip."""
    if asset is None or asset.duration_samples is None:
        return None
    curr_src_end = (clip.source_offset_samples or 0) + clip.duration_samples
    return max(0, asset.duration_samples - curr_src_end)
