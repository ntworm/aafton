"""Resolucao, planejamento e reescrita XML de sets do Ableton Live (.als)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .als_render import e_video
from .als_xml import get_value, load_als, save_als
from .collect_copy import norm_path_key, resolved_str
from .sidecar import read_anchor, sidecar_path_for

LIBRARY_TYPES = {"5", "7"}


@dataclass(kw_only=True)
class AlsRefInfo:
    """Referencia individual resolvida de um SampleRef/FileRef."""

    source: str
    resolved_key: str
    kind: str  # "video" ou "audio"
    state: str  # "ready", "library", "missing"
    size_bytes: int


def find_project_dir(als_dir: Path) -> Path:
    """Ancestral mais proximo de als_dir (ele incluso) com 'Ableton Project Info'."""
    curr = als_dir.resolve()
    while True:
        if (curr / "Ableton Project Info").is_dir():
            return curr
        parent = curr.parent
        if parent == curr:
            return als_dir.resolve()
        curr = parent


def _safe_candidate(base: Path | None, raw: str) -> Path | None:
    """Monta candidato de caminho a partir de string bruta do XML."""
    if not raw:
        return None
    cleaned = raw.replace("\\", "/")
    try:
        p = Path(cleaned)
        if p.is_absolute():
            return p
        if base is not None:
            return base / p
        return p
    except (OSError, ValueError):
        return None


def _is_file_safe(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _candidates_for(rel_type: str, rel_raw: str, abs_raw: str, als_dir: Path) -> list[Path]:
    """Candidatos em ordem de preferencia para o RelativePathType dado."""
    if rel_type == "0":
        pares = [(als_dir, abs_raw)]
    elif rel_type == "3":
        pares = [(find_project_dir(als_dir), rel_raw), (als_dir, rel_raw), (als_dir, abs_raw)]
    else:
        pares = [(als_dir, rel_raw), (als_dir, abs_raw)]
    candidatos = (_safe_candidate(base, raw) for base, raw in pares)
    return [cand for cand in candidatos if cand is not None]


def resolve_file_ref(file_ref: ET.Element, als_path: Path) -> tuple[str, Path | None, str]:
    """Resolve um elemento FileRef pela regra do RelativePathType.

    Retorna ``(status, resolved_path_or_none, fallback_display_str)`` onde
    status e ``"library"``, ``"resolved"`` ou ``"missing"``.
    """
    als_dir = als_path.resolve().parent
    rel_type = _campo(file_ref, "RelativePathType").strip()
    rel_raw = _campo(file_ref, "RelativePath")
    abs_raw = _campo(file_ref, "Path")
    bruto = abs_raw or rel_raw

    if _e_da_biblioteca(rel_type, rel_raw):
        return "library", None, bruto or "<library>"

    candidates = _candidates_for(rel_type, rel_raw, abs_raw, als_dir)
    achado = next((cand for cand in candidates if _is_file_safe(cand)), None)
    if achado is not None:
        resolved = achado.resolve()
        return "resolved", resolved, str(resolved)
    if candidates:
        return "missing", None, str(candidates[0])
    return "missing", None, bruto or "<missing>"


def _campo(file_ref: ET.Element, nome: str) -> str:
    """Valor de um campo do FileRef, vazio quando ausente."""
    return get_value(file_ref, nome) or ""


def is_video_file_ref(file_ref: ET.Element, path_str: str) -> bool:
    """Diz se o FileRef e video, pela extensao do arquivo.

    O campo Type do FileRef nao marca video: medido em 15/11/2026, o Live
    12.4.6 grava 2 em todo arquivo, .mp4 incluso, o 12.4.15b1 grava 1 em todo
    arquivo e o template do aafton grava 1 tambem no audio.
    """
    del file_ref
    return e_video(path_str)


def _e_da_biblioteca(rel_type: str, rel_raw: str) -> bool:
    """FileRef da Core Library ou do Builtin do Live.

    O Live grava esses tipos com o RelativePath relativo a biblioteca. O
    aafton escreve o tipo 5 do template com caminho absoluto nos dois campos:
    esse e midia do usuario e precisa ser coletado.
    """
    if rel_type not in LIBRARY_TYPES:
        return False
    rel = rel_raw.replace("\\", "/")
    return bool(rel) and not (rel.startswith("/") or Path(rel).is_absolute())


def _iter_original_file_refs(sample_ref: ET.Element) -> list[ET.Element]:
    """Todos os OriginalFileRef/FileRef sob SampleRef/SourceContext."""
    ctx = sample_ref.find("SourceContext")
    if ctx is None:
        return []
    found: list[ET.Element] = []
    for orig in ctx.iter("OriginalFileRef"):
        fref = orig.find("FileRef")
        if fref is not None:
            found.append(fref)
    return found


def scan_als(als_path: Path) -> tuple[list[AlsRefInfo], int]:
    """Inspeciona o .als e o sidecar, retornando itens unicos e history_kept."""
    als_path = als_path.resolve()
    root = load_als(als_path)
    items_by_key: dict[str, AlsRefInfo] = {}
    history_kept = 0

    for sample_ref in root.iter("SampleRef"):
        file_ref = sample_ref.find("FileRef")
        if file_ref is None:
            continue
        info, active_resolved = _inspect_active_ref(file_ref, als_path)
        if info.resolved_key not in items_by_key:
            items_by_key[info.resolved_key] = info
        elif info.kind == "video":
            items_by_key[info.resolved_key].kind = "video"
        history_kept += _count_history_kept(sample_ref, active_resolved, als_path)

    _add_sidecar_video(als_path, items_by_key)
    return list(items_by_key.values()), history_kept


def _inspect_active_ref(file_ref: ET.Element, als_path: Path) -> tuple[AlsRefInfo, Path | None]:
    status, resolved, display = resolve_file_ref(file_ref, als_path)
    kind = "video" if is_video_file_ref(file_ref, display) else "audio"
    if status == "library":
        key = f"library::{display.casefold()}"
        return (
            AlsRefInfo(source=display, resolved_key=key, kind=kind, state="library", size_bytes=0),
            None,
        )
    if status == "resolved" and resolved is not None:
        key = norm_path_key(resolved)
        size = resolved.stat().st_size
        return (
            AlsRefInfo(
                source=str(resolved),
                resolved_key=key,
                kind=kind,
                state="ready",
                size_bytes=size,
            ),
            resolved,
        )
    key = f"missing::{norm_path_key(display)}"
    return (
        AlsRefInfo(source=display, resolved_key=key, kind=kind, state="missing", size_bytes=0),
        None,
    )


def _count_history_kept(
    sample_ref: ET.Element, active_resolved: Path | None, als_path: Path
) -> int:
    active_key = norm_path_key(active_resolved) if active_resolved is not None else None
    kept = 0
    for orig_fref in _iter_original_file_refs(sample_ref):
        st, orig_res, orig_disp = resolve_file_ref(orig_fref, als_path)
        if st == "library":
            continue
        orig_key = norm_path_key(orig_res if orig_res is not None else orig_disp)
        if active_key is None or orig_key != active_key:
            kept += 1
    return kept


def _add_sidecar_video(als_path: Path, items_by_key: dict[str, AlsRefInfo]) -> None:
    if not sidecar_path_for(als_path).is_file():
        return
    try:
        anchor = read_anchor(als_path)
    except Exception:
        return
    if not anchor.video_path:
        return
    raw_vp = anchor.video_path
    cand = Path(raw_vp)
    if not cand.is_absolute():
        cand = als_path.parent / cand
    if _is_file_safe(cand):
        res = cand.resolve()
        key = norm_path_key(res)
        if key in items_by_key:
            items_by_key[key].kind = "video"
        else:
            items_by_key[key] = AlsRefInfo(
                source=str(res),
                resolved_key=key,
                kind="video",
                state="ready",
                size_bytes=res.stat().st_size,
            )
    else:
        disp = resolved_str(cand)
        key = f"missing::{norm_path_key(disp)}"
        if key not in items_by_key:
            items_by_key[key] = AlsRefInfo(
                source=disp,
                resolved_key=key,
                kind="video",
                state="missing",
                size_bytes=0,
            )


def _set_file_ref_fields(file_ref: ET.Element, media_folder_name: str, dest_file: Path) -> None:
    """Atualiza RelativePathType=1, RelativePath e Path no FileRef."""
    rel_posix = f"{media_folder_name}/{dest_file.name}"
    abs_posix = dest_file.resolve().as_posix()
    for tag, val in (
        ("RelativePathType", "1"),
        ("RelativePath", rel_posix),
        ("Path", abs_posix),
    ):
        node = file_ref.find(tag)
        if node is None:
            node = ET.SubElement(file_ref, tag)
        node.set("Value", val)


def rewrite_als_xml(
    source_als: Path,
    dest_als: Path,
    media_folder_name: str,
    copied_by_key: dict[str, Path],
) -> None:
    """Carrega o XML de source_als, reescreve os FileRef copiados e salva em dest_als."""
    source_als = source_als.resolve()
    root = load_als(source_als)
    for sample_ref in root.iter("SampleRef"):
        file_ref = sample_ref.find("FileRef")
        if file_ref is None:
            continue
        status, active_res, _ = resolve_file_ref(file_ref, source_als)
        if status != "resolved" or active_res is None:
            continue
        active_key = norm_path_key(active_res)
        dest_file = copied_by_key.get(active_key)
        if dest_file is None:
            continue
        _set_file_ref_fields(file_ref, media_folder_name, dest_file)
        for orig_fref in _iter_original_file_refs(sample_ref):
            st_o, orig_res, _ = resolve_file_ref(orig_fref, source_als)
            if st_o == "resolved" and orig_res is not None:
                if norm_path_key(orig_res) == active_key:
                    _set_file_ref_fields(orig_fref, media_folder_name, dest_file)
    save_als(root, dest_als)
