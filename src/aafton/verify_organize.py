"""Remontagem da sessao organizada para verificacao contra a origem (P3c).

Quando o sidecar traz ``clip_origins``, reconstroi um ``SessionIR`` de
destino na disposicao do editor (uma track por ``source_index`` que aparece
nas chaves, ou mais as tracks sem chave como video) e devolve os indices
``[[i] ...]`` dessa disposicao. Vale para o AAF de origem em qualquer caminho;
o AAF exportado pelo aafton (a volta) nao passa por aqui:
``aaf_exportado_pelo_aafton`` o desvia para a comparacao pela ordem do writer.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .ir import ClipIR, SessionIR, TrackIR


def _mesmo_caminho(aaf_path: str | Path, source_path: str | None) -> bool:
    """Diz se o arquivo comparado resolve para o mesmo caminho que source_path."""
    if not source_path:
        return False
    try:
        return Path(aaf_path).resolve() == Path(source_path).resolve()
    except OSError:
        return False


def _mob_ids_da_origem(anchor: Any) -> set[str]:
    """Mob ids que o sidecar guardou do AAF de origem (track_bindings)."""
    ids: set[str] = set()
    for binding in getattr(anchor, "track_bindings", None) or []:
        mob_id = binding.get("mob_id") if isinstance(binding, dict) else None
        if mob_id:
            ids.add(str(mob_id))
    return ids


def _mob_ids_do_aaf(aaf_path: str | Path) -> set[str] | None:
    """Mob ids de todos os mobs do AAF; None quando o arquivo nao abre."""
    import aaf2

    try:
        with aaf2.open(str(aaf_path), "r") as f:
            return {str(mob.mob_id) for mob in f.content.mobs}
    except Exception:  # noqa: BLE001 - arquivo ilegivel segue o caminho de sempre
        return None


def aaf_exportado_pelo_aafton(aaf_path: str | Path, anchor: Any) -> bool:
    """Diz se o AAF comparado e a volta do set, e nao o AAF de origem do sidecar.

    O AAF de origem, no caminho do sidecar ou copiado, traz os mobs cujos ids o
    import guardou em ``track_bindings``; o writer do aafton cria mobs novos.
    E a volta quando o arquivo nao e o ``source_path`` e nao tem nenhum desses
    ids. Sidecar sem mob id guardado e set com expansao de canais (as irmas do
    som direto tem conferencia propria) seguem o caminho de sempre.
    """
    if anchor is None or Path(aaf_path).suffix.lower() != ".aaf":
        return False
    if getattr(anchor, "expanded", None):
        return False
    if _mesmo_caminho(aaf_path, getattr(anchor, "source_path", None)):
        return False
    esperados = _mob_ids_da_origem(anchor)
    if not esperados:
        return False
    presentes = _mob_ids_do_aaf(aaf_path)
    return presentes is not None and not (esperados & presentes)


def _inicio_da_ancora(anchor: Any):
    """Inicio do set gravado no sidecar, como o StartSpec do verify sem sidecar."""
    from .als_ancora import StartSpec
    from .timecode import samples_to_label

    rate = anchor.timecode_rate
    rotulo = samples_to_label(anchor.session_start_samples, rate, anchor.sample_rate)
    return StartSpec(rotulo, rate.fps, rate.drop_frame, assumed=False)


def verify_da_volta(aaf_path, als_path, anchor, tolerancia, leitor_aaf):
    """Verificacao da volta, ou None quando o AAF comparado e a origem.

    A volta compara o set pela ordem de slots do writer (verify sem sidecar),
    le o AAF com fades=keep e confere o inicio contra a ancora do sidecar, nao
    contra um inicio dado para sets sem registro.
    """
    if not aaf_exportado_pelo_aafton(aaf_path, anchor):
        return None
    from .verify_sem_sidecar import verify_sem_sidecar

    inicio = _inicio_da_ancora(anchor)

    def rodar(leave_out_render, tracks):
        return verify_sem_sidecar(
            aaf_path, als_path, tolerancia, inicio, leave_out_render, leitor_aaf, tracks, "keep"
        )

    return rodar


def _entradas_para_destino(
    clip_origins: list[dict],
    tracks: list[TrackIR],
) -> list[dict]:
    """Alinha as entradas de clip_origins com as tracks de destino apos filtrar irmas."""
    if len(clip_origins) == len(tracks):
        return clip_origins
    por_nome: dict[str, list[dict]] = {}
    for entry in clip_origins:
        if isinstance(entry, dict):
            por_nome.setdefault(str(entry.get("track", "")), []).append(entry)
    alinhadas: list[dict] = []
    for trk in tracks:
        fila = por_nome.get(trk.name)
        alinhadas.append(fila.pop(0) if fila else {"track": trk.name, "clips": []})
    return alinhadas


def _chaves_do_clip(raw_entry: Any) -> list[tuple[int, int]]:
    """Extrai a lista de pares (source_index, order) de uma entrada de clip_origins."""
    if not isinstance(raw_entry, (list, tuple)) or len(raw_entry) < 3:
        return []
    chaves_brutas = raw_entry[2] or ()
    return [
        (int(par[0]), int(par[1]))
        for par in chaves_brutas
        if isinstance(par, (list, tuple)) and len(par) >= 2
    ]


def _coletar_por_origem(
    destino: SessionIR,
    entradas: list[dict],
) -> tuple[dict[int, list[tuple[int, ClipIR, TrackIR]]], list[TrackIR]]:
    """Agrupa clips pelas chaves de origem e separa tracks sem chaves (ex: video)."""
    por_origem: dict[int, list[tuple[int, ClipIR, TrackIR]]] = {}
    tracks_sem_chave: list[TrackIR] = []

    for trk, entry in zip(destino.tracks, entradas):
        clips_meta = entry.get("clips") if isinstance(entry, dict) else None
        clips_meta = clips_meta if isinstance(clips_meta, list) else []
        teve_chave = False
        for idx, clip in enumerate(trk.clips):
            chaves = _chaves_do_clip(clips_meta[idx]) if idx < len(clips_meta) else []
            if not chaves:
                continue
            teve_chave = True
            for src_idx, order in chaves:
                por_origem.setdefault(src_idx, []).append((order, clip, trk))
        # Track sem chave, com ou sem clips (video, track vazia do editor), entra
        # pelo proprio indice, como na disposicao sem organizacao.
        if not teve_chave:
            tracks_sem_chave.append(trk)

    return por_origem, tracks_sem_chave


def _montar_track_origem(
    src_idx: int,
    itens: list[tuple[int, ClipIR, TrackIR]],
) -> TrackIR:
    """Cria a TrackIR reconstruida para um source_index do editor."""
    itens_ordenados = sorted(
        itens,
        key=lambda item: (
            item[0],
            item[1].timeline_start_samples,
            item[1].timeline_end_samples,
            item[1].name,
            item[1].id,
        ),
    )
    primeira_trk = itens_ordenados[0][2]
    track_id = f"org_src_{src_idx}"
    clips: list[ClipIR] = []
    for pos, (_, clip_orig, _) in enumerate(itens_ordenados):
        copia = deepcopy(clip_orig)
        copia.id = f"{track_id}_c{pos}"
        copia.track_id = track_id
        copia.channel_index = 0
        copia.event_index = pos
        clips.append(copia)

    return TrackIR(
        id=track_id,
        source_index=src_idx,
        original_source_indices=[src_idx],
        name=f"Source {src_idx}",
        kind=primeira_trk.kind,
        channel_count=1,
        clips=clips,
        is_video=primeira_trk.is_video,
    )


def _anexar_tracks_sem_chave(
    novas_tracks: list[TrackIR],
    novos_indices: list[list[int]],
    tracks_sem_chave: list[TrackIR],
) -> None:
    """Preserva tracks sem chave de origem (como a track de video) na sessao reconstruida."""
    usados = {idx for sub in novos_indices for idx in sub}
    for trk in tracks_sem_chave:
        candidatos = [
            idx
            for idx in (trk.original_source_indices or [trk.source_index])
            if isinstance(idx, int) and idx >= 0 and idx not in usados
        ]
        if not candidatos:
            continue
        # Track sem chave de varios slots (par vazio, combinada vazia) mantem todos.
        usados.update(candidatos)
        copia_trk = deepcopy(trk)
        copia_trk.source_index = candidatos[0]
        copia_trk.original_source_indices = list(candidatos)
        novas_tracks.append(copia_trk)
        novos_indices.append(list(candidatos))


def rebuild_organized_destination(
    destino: SessionIR,
    anchor: Any,
    aaf_path: str | Path,
) -> tuple[SessionIR, list[Any] | None]:
    """Ajusta o destino e o mapa audio_source_indices quando o sidecar tem clip_origins."""
    source_indices = getattr(anchor, "audio_source_indices", None)
    clip_origins = getattr(anchor, "clip_origins", None)
    if not clip_origins:
        return destino, source_indices

    entradas = _entradas_para_destino(clip_origins, destino.tracks)
    por_origem, tracks_sem_chave = _coletar_por_origem(destino, entradas)

    novas_tracks: list[TrackIR] = []
    novos_indices: list[list[int]] = []
    for src_idx in sorted(por_origem):
        novas_tracks.append(_montar_track_origem(src_idx, por_origem[src_idx]))
        novos_indices.append([src_idx])

    _anexar_tracks_sem_chave(novas_tracks, novos_indices, tracks_sem_chave)

    reconstruido = deepcopy(destino)
    reconstruido.tracks = novas_tracks
    return reconstruido, novos_indices
