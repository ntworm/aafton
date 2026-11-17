"""Resolucao, planejamento e reescrita de Locators para arquivos AAF linkados."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import aaf2

from .aaf_read_components import _caminho_do_locator, _descritor_fisico, _url_para_caminho
from .als_render import e_video
from .collect_copy import norm_path_key, resolved_str


@dataclass(kw_only=True)
class AafRefInfo:
    """Referencia de midia de um descritor de SourceMob no AAF."""

    source: str
    resolved_key: str
    kind: str  # "video" ou "audio"
    state: str  # "ready", "embedded", "missing"
    size_bytes: int


def iter_descriptors(descriptor, seen: set[int] | None = None):
    """Percorre o descritor e seus FileDescriptors (MultipleDescriptor)."""
    if seen is None:
        seen = set()
    if descriptor is None or id(descriptor) in seen:
        return
    seen.add(id(descriptor))
    yield descriptor
    children = descriptor.get("FileDescriptors") if hasattr(descriptor, "get") else None
    if children:
        for child in children.value:
            yield from iter_descriptors(child, seen)


def _is_video_descriptor(descriptor, path_str: str) -> bool:
    """Diz se o descritor e DigitalImageDescriptor ou arquivo com extensao de video."""
    if isinstance(descriptor, aaf2.essence.DigitalImageDescriptor):
        return True
    return e_video(path_str)


def _resolve_locator_path(raw_path: str, aaf_dir: Path) -> tuple[bool, Path, str]:
    """Resolve caminho de um locator (absoluto ou relativo a pasta do AAF)."""
    try:
        cand = Path(raw_path)
        if not cand.is_absolute():
            cand = aaf_dir / cand
        if cand.is_file():
            res = cand.resolve()
            return True, res, str(res)
        return False, cand, resolved_str(cand)
    except (OSError, ValueError):
        return False, Path(raw_path), raw_path


def _embedded_label(mob, descriptor) -> str:
    """Rotulo de exibicao para um SourceMob com essencia embutida."""
    raw = _caminho_do_locator(descriptor)
    if raw:
        return raw
    fname = getattr(descriptor, "file_name", None)
    if fname:
        return str(fname)
    name = getattr(mob, "name", None)
    if name:
        return str(name)
    return str(getattr(mob, "mob_id", "<embedded>"))


def scan_aaf(aaf_path: Path) -> list[AafRefInfo]:
    """Inspeciona os SourceMobs do AAF e retorna itens desduplicados."""
    aaf_path = aaf_path.resolve()
    aaf_dir = aaf_path.parent
    items_by_key: dict[str, AafRefInfo] = {}

    with aaf2.open(str(aaf_path), "r") as handle:
        for mob in handle.content.sourcemobs():
            top_desc = getattr(mob, "descriptor", None)
            if top_desc is None or _descritor_fisico(top_desc):
                continue
            if getattr(mob, "essence", None) is not None:
                _record_embedded(mob, top_desc, items_by_key)
                continue
            for desc in iter_descriptors(top_desc):
                if _descritor_fisico(desc):
                    continue
                _record_linked_descriptor(desc, aaf_dir, items_by_key)

    return list(items_by_key.values())


def _record_embedded(mob, top_desc, items_by_key: dict[str, AafRefInfo]) -> None:
    label = _embedded_label(mob, top_desc)
    mob_id = str(getattr(mob, "mob_id", label))
    key = f"embedded::{mob_id}"
    kind = "video" if _is_video_descriptor(top_desc, label) else "audio"
    if key not in items_by_key:
        items_by_key[key] = AafRefInfo(
            source=label,
            resolved_key=key,
            kind=kind,
            state="embedded",
            size_bytes=0,
        )


def _record_linked_descriptor(desc, aaf_dir: Path, items_by_key: dict[str, AafRefInfo]) -> None:
    raw = _caminho_do_locator(desc)
    if raw is None:
        return
    exists, resolved, display = _resolve_locator_path(raw, aaf_dir)
    kind = "video" if _is_video_descriptor(desc, display) else "audio"
    if exists:
        key = norm_path_key(resolved)
        if key not in items_by_key:
            items_by_key[key] = AafRefInfo(
                source=str(resolved),
                resolved_key=key,
                kind=kind,
                state="ready",
                size_bytes=resolved.stat().st_size,
            )
        elif kind == "video":
            items_by_key[key].kind = "video"
    else:
        key = f"missing::{norm_path_key(display)}"
        if key not in items_by_key:
            items_by_key[key] = AafRefInfo(
                source=display,
                resolved_key=key,
                kind=kind,
                state="missing",
                size_bytes=0,
            )


def rewrite_aaf_locators(
    source_aaf: Path,
    dest_aaf: Path,
    copied_by_key: dict[str, Path],
    url_for: Callable[[Path], str] | None = None,
) -> None:
    """Copia os bytes do AAF (se diferente) e atualiza URLString de todos os Locators."""
    source_aaf = source_aaf.resolve()
    dest_aaf = dest_aaf.resolve()
    aaf_dir = source_aaf.parent
    if source_aaf != dest_aaf:
        dest_aaf.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_aaf, dest_aaf)
        # A copia herda o somente leitura do original; ela precisa abrir em "rw".
        dest_aaf.chmod(dest_aaf.stat().st_mode | stat.S_IWRITE)

    with aaf2.open(str(dest_aaf), "rw") as handle:
        for mob in handle.content.sourcemobs():
            if getattr(mob, "essence", None) is not None:
                continue
            top_desc = getattr(mob, "descriptor", None)
            if top_desc is None or _descritor_fisico(top_desc):
                continue
            for desc in iter_descriptors(top_desc):
                if _descritor_fisico(desc):
                    continue
                _relink_descriptor_locators(desc, aaf_dir, copied_by_key, url_for)


def _relink_descriptor_locators(
    desc,
    aaf_dir: Path,
    copied_by_key: dict[str, Path],
    url_for: Callable[[Path], str] | None = None,
) -> None:
    """Troca a URLString de todos os Locators do descritor cujo arquivo foi copiado."""
    raw = _caminho_do_locator(desc)
    if raw is None:
        return
    exists, resolved, _ = _resolve_locator_path(raw, aaf_dir)
    if not exists:
        return
    dest_file = copied_by_key.get(norm_path_key(resolved))
    if dest_file is None:
        return
    uri = url_for(dest_file) if url_for is not None else dest_file.resolve().as_uri()
    locators_prop = desc.get("Locator") if hasattr(desc, "get") else None
    if not locators_prop:
        return
    # Locator sem URLString (um TextLocator, por exemplo) nao aponta arquivo e
    # fica como esta.
    for locator in locators_prop.value:
        url_prop = locator.get("URLString") if hasattr(locator, "get") else None
        if url_prop is not None:
            url_prop.value = uri


def read_locator_urls(aaf_path: Path) -> list[list[str]]:
    """Lista as URLStrings de cada descritor linkado (auxiliar de inspecao/teste)."""
    out: list[list[str]] = []
    with aaf2.open(str(aaf_path), "r") as handle:
        for mob in handle.content.sourcemobs():
            for desc in iter_descriptors(getattr(mob, "descriptor", None)):
                locs = desc.get("Locator") if hasattr(desc, "get") else None
                if locs:
                    urls = [
                        loc["URLString"].value
                        for loc in locs.value
                        if loc.get("URLString") is not None
                    ]
                    out.append(urls)
    return out


__all__ = [
    "AafRefInfo",
    "iter_descriptors",
    "read_locator_urls",
    "rewrite_aaf_locators",
    "scan_aaf",
    "_url_para_caminho",
]
