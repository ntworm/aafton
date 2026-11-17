import hashlib
from pathlib import Path

from .als_write_base import WriteOptions
from .als_write_tracks import WriteContext
from .ir import EXPANDED_MARK, PRODUCTION_LAYOUT, SessionIR
from .sidecar import SIDECAR_FORMAT_VERSION, Anchor, write_sidecar


def _video_path_e_anchor(session: SessionIR) -> tuple[str | None, str | None]:
    video_path: str | None = None
    video_anchor: str | None = None
    if session.tracks and session.tracks[0].is_video and session.tracks[0].clips:
        for a in session.media:
            if a.id == session.tracks[0].clips[0].media_id:
                video_path = a.resolved_path
                break
        video_anchor = session.tracks[0].clips[0].anchor_source
    return video_path, video_anchor


def _media_hashes(session: SessionIR) -> dict[str, str]:
    media_hashes: dict[str, str] = {}
    for asset in session.media:
        p_str = asset.resolved_path or asset.declared_location
        if p_str:
            p = Path(p_str)
            if p.is_file():
                try:
                    h = hashlib.sha256()
                    with open(p, "rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
                    digest = h.hexdigest()
                    if asset.filename:
                        media_hashes[asset.filename] = digest
                    media_hashes[str(_caminho_absoluto(p))] = digest
                except Exception:
                    pass
    return media_hashes


def _caminho_absoluto(caminho: Path) -> Path:
    """Caminho absoluto do arquivo; um caminho absoluto fica como esta.

    O sidecar guarda o caminho absoluto para que o verify ache o arquivo
    mesmo quando a importacao usou uma pasta relativa. Um caminho
    absoluto nao e reescrito, para nao mudar o que ja foi gravado.
    """
    if caminho.is_absolute():
        return caminho
    return caminho.resolve()


def _expandidas(session: SessionIR) -> dict | None:
    """Irmas da expansao de canais: a marca e, por clip, o original e a entrada.

    As irmas sao guardadas pelo nome da track, que a expansao faz unico no set.
    """
    tracks: dict[str, dict] = {}
    meta: dict = {}
    for track in session.tracks:
        raw = getattr(track, "raw", None)
        if not isinstance(raw, dict) or raw.get("expanded") != EXPANDED_MARK:
            continue
        meta = meta or raw
        tracks[track.name] = {
            "mark": EXPANDED_MARK,
            "owner": raw.get("owner"),
            "clips": [dict(c.raw["expanded"]) for c in track.clips if "expanded" in c.raw],
        }
    if not tracks:
        return None
    return {
        "folder": meta.get("folder"),
        "editor": meta.get("editor"),
        "editor_sha256": meta.get("editor_sha256"),
        "tracks": tracks,
    }


def write_anchor_sidecar(
    output_path: str | Path,
    session: SessionIR,
    options: WriteOptions,
    ctx: WriteContext,
    origin: int,
) -> None:
    video_path, video_anchor = _video_path_e_anchor(session)
    media_hashes = _media_hashes(session)
    write_sidecar(
        output_path,
        Anchor(
            format_version=SIDECAR_FORMAT_VERSION,
            live_origin_samples=origin,
            session_start_samples=session.start_timecode_samples,
            sample_rate=options.sample_rate,
            timecode_rate=session.timecode_rate,
            bpm=options.bpm,
            source_path=session.source_path,
            fades=options.fades,
            stereo=options.stereo,
            video_path=video_path,
            video_anchor_source=video_anchor,
            track_channels={t.name: t.channel_count for t in session.tracks},
            audio_source_indices=ctx.audio_source_indices,
            clip_ranges=ctx.clip_ranges,
            clip_origins=ctx.clip_origins
            if any(
                key
                for entry in ctx.clip_origins
                for _, _, keys in entry.get("clips", ())
                for key in keys
            )
            else None,
            track_bindings=ctx.track_bindings,
            media_hashes=media_hashes if media_hashes else None,
            layout=session.layout if session.layout == PRODUCTION_LAYOUT else None,
            blocks=session.layout_blocks if session.layout == PRODUCTION_LAYOUT else None,
            expanded=_expandidas(session),
        ),
    )
