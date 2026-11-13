"""Definicoes de layouts multicanal e expansao de trilhas para Ableton Live.

O Live nao suporta faixas de audio com mais de 2 canais nativos. Portanto,
trilhas multicanal (Quad, 5.1 e 7.1) sao expandidas em faixas mono filhas
vinculadas sob GroupTrack com posicionamento estereo (pan) estatico discreto.
"""

from __future__ import annotations

from copy import deepcopy

from .ir import ClipIR, TrackIR

LAYOUT_QUAD: list[str] = ["L", "R", "Ls", "Rs"]
LAYOUT_51_SMPTE: list[str] = ["L", "R", "C", "LFE", "Ls", "Rs"]
LAYOUT_51_FILM: list[str] = ["L", "C", "R", "Ls", "Rs", "LFE"]
LAYOUT_71_SMPTE: list[str] = ["L", "R", "C", "LFE", "Lss", "Rss", "Lsr", "Rsr"]
LAYOUT_71_FILM: list[str] = ["L", "C", "R", "Lss", "Rss", "Lsr", "Rsr", "LFE"]

SUPPORTED_LAYOUTS: dict[str, list[str]] = {
    "quad": LAYOUT_QUAD,
    "4.0": LAYOUT_QUAD,
    "5.1_smpte": LAYOUT_51_SMPTE,
    "5.1_film": LAYOUT_51_FILM,
    "7.1_smpte": LAYOUT_71_SMPTE,
    "7.1_film": LAYOUT_71_FILM,
}

CHANNEL_PAN: dict[str, float] = {
    "L": -1.0,
    "R": 1.0,
    "C": 0.0,
    "LFE": 0.0,
    "Ls": -1.0,
    "Rs": 1.0,
    "Lss": -1.0,
    "Rss": 1.0,
    "Lsr": -1.0,
    "Rsr": 1.0,
}


def pan_for_channel(suffix: str) -> float:
    """Devolve o valor de pan discreto (-1.0 a 1.0) para um dado canal."""
    if suffix in CHANNEL_PAN:
        return CHANNEL_PAN[suffix]
    if suffix.startswith("L") and suffix != "LFE":
        return -1.0
    if suffix.startswith("R"):
        return 1.0
    return 0.0


def get_layout_channels(channel_count: int, layout_name: str = "smpte") -> list[str]:
    """Retorna os sufixos ordenados dos canais para um determinado layout."""
    norm_layout = (layout_name or "smpte").strip().lower()

    if channel_count == 4:
        return list(LAYOUT_QUAD)
    elif channel_count == 6:
        if "film" in norm_layout:
            return list(LAYOUT_51_FILM)
        return list(LAYOUT_51_SMPTE)
    elif channel_count == 8:
        if "film" in norm_layout:
            return list(LAYOUT_71_FILM)
        return list(LAYOUT_71_SMPTE)
    else:
        raise ValueError(
            f"Multichannel layout not supported for {channel_count} channels (layout: {layout_name})"
        )


def expand_multichannel_track(track: TrackIR, layout_name: str = "smpte") -> list[TrackIR]:
    """Expande uma TrackIR multicanal em multiplas faixas mono discretas.

    Se channel_count <= 2, retorna [track] inalterado.
    Se channel_count > 2 (4, 6, 8), gera lista de faixas mono (channel_count=1)
    filhas com nome f"{track.name} [{suffix}]", isolando os clips de cada
    channel_index correspondente e atribuindo pan discreto.
    """
    if track.channel_count <= 2:
        return [track]

    effective_layout = layout_name
    if layout_name == "smpte" and track.channel_layout:
        effective_layout = track.channel_layout

    suffixes = get_layout_channels(track.channel_count, effective_layout)

    child_tracks: list[TrackIR] = []
    for ch_idx, suffix in enumerate(suffixes):
        child_id = f"{track.id}_{ch_idx}"

        # Isolar clips pertencentes a este channel_index
        child_clips: list[ClipIR] = []
        for c in track.clips:
            if c.channel_index == ch_idx:
                c_copy = deepcopy(c)
                c_copy.track_id = child_id
                c_copy.channel_index = 0
                if not hasattr(c_copy, "raw") or not isinstance(c_copy.raw, dict):
                    c_copy.raw = {}
                c_copy.raw["original_channel_index"] = ch_idx
                child_clips.append(c_copy)

        if track.original_source_indices and ch_idx < len(track.original_source_indices):
            orig_src = track.original_source_indices[ch_idx]
        else:
            orig_src = track.source_index + ch_idx

        child_track = TrackIR(
            id=child_id,
            source_index=orig_src,
            name=f"{track.name} [{suffix}]",
            kind=track.kind,
            channel_count=1,
            channel_layout=suffix,
            group_path=list(track.group_path),
            original_source_indices=[orig_src],
            hidden=track.hidden,
            muted=track.muted,
            gain_linear=track.gain_linear,
            pan=pan_for_channel(suffix),
            plugins=list(track.plugins),
            clips=child_clips,
            gain_points=list(track.gain_points),
        )
        if not hasattr(child_track, "raw"):
            child_track.raw = {}
        child_track.raw["parent_track_name"] = track.name
        child_track.raw["channel_index"] = ch_idx
        child_track.raw["multichannel_layout"] = effective_layout
        child_track.raw["suffix"] = suffix
        child_tracks.append(child_track)

    return child_tracks
