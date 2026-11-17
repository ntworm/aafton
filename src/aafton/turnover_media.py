"""Auditoria, hashing e classificacao de midias do pacote de turnover."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .collect import CollectPlan, CollectPlanItem
from .collect_copy import copy_file_verified, norm_path_key
from .media_oracle import verify_media_file


def _is_path_key(key: str) -> bool:
    """Verifica se a chave representa um caminho (absoluto ou com separadores)."""
    return Path(key).is_absolute() or "/" in key or "\\" in key


def _match_path_key(
    source_media_hashes: dict[str, str],
    item_source_key: str,
    set_parent: Path,
) -> tuple[str | None, str | None]:
    """Tenta casar o item contra chaves de caminho no media_hashes."""
    for k, val in source_media_hashes.items():
        if _is_path_key(k):
            kp = Path(k)
            if not kp.is_absolute():
                kp = set_parent / kp
            if norm_path_key(kp) == item_source_key:
                return val, k
    return None, None


def _match_name_key(
    source_media_hashes: dict[str, str],
    item_name: str,
    copied_items: list[CollectPlanItem],
) -> tuple[str | None, str | None]:
    """Tenta casar o item contra chaves de nome puro quando nao ambiguo."""
    matching = [ci for ci in copied_items if Path(ci.source).name.casefold() == item_name]
    if len(matching) != 1:
        return None, None
    for k, val in source_media_hashes.items():
        if not _is_path_key(k) and k.casefold() == item_name:
            return val, k
    return None, None


def _expected_hash(
    source_media_hashes: dict[str, str] | None,
    item: CollectPlanItem,
    copied_items: list[CollectPlanItem],
    set_parent: Path,
) -> tuple[str | None, str | None]:
    """Descobre o hash esperado de um item segundo as regras do plano."""
    if not source_media_hashes:
        return None, None

    item_source_key = norm_path_key(Path(item.source))
    matched = _match_path_key(source_media_hashes, item_source_key, set_parent)
    if matched[0] is not None:
        return matched

    item_name = Path(item.source).name.casefold()
    return _match_name_key(source_media_hashes, item_name, copied_items)


def _expected_hashes_for(
    source_media_hashes: dict[str, str] | None,
    ready_items: list[CollectPlanItem],
    set_parent: Path,
) -> tuple[dict[str, str | None], list[str]]:
    """Calcula mapa de hashes esperados e lista chaves descartadas do sidecar."""
    expected_map: dict[str, str | None] = {}
    used_keys: set[str] = set()
    for it in ready_items:
        exp_h, matched_k = _expected_hash(source_media_hashes, it, ready_items, set_parent)
        expected_map[it.resolved_key] = exp_h
        if matched_k:
            used_keys.add(matched_k)
    discarded_keys = (
        [k for k in source_media_hashes if k not in used_keys] if source_media_hashes else []
    )
    return expected_map, discarded_keys


def _copy_ready_items(
    ready_items: list[CollectPlanItem],
    staging_media: Path,
    dest_name_map: dict[str, str],
) -> tuple[dict[str, str], dict[str, int]]:
    """Copia cada item ready para staging_media usando copy_file_verified."""
    copy_hashes: dict[str, str] = {}
    copy_sizes: dict[str, int] = {}
    total_bytes = sum(it.size_bytes for it in ready_items)
    copied_so_far = 0
    for it in ready_items:
        dest_name = dest_name_map[it.resolved_key]
        stage_file = staging_media / dest_name
        sha, written = copy_file_verified(
            Path(it.source),
            stage_file,
            copied_so_far=copied_so_far,
            total_bytes=total_bytes,
        )
        copied_so_far += written
        copy_hashes[it.resolved_key] = sha
        copy_sizes[it.resolved_key] = written
    return copy_hashes, copy_sizes


def _media_entry(
    *,
    source: str,
    destination: str | None,
    kind: str,
    state: str,
    sha256: str | None,
    expected_sha256: str | None,
    size_bytes: int,
) -> dict[str, Any]:
    """Cria um registro estruturado de midia para o manifesto."""
    return {
        "source": source,
        "destination": destination,
        "kind": kind,
        "state": state,
        "sha256": sha256,
        "expected_sha256": expected_sha256,
        "size_bytes": size_bytes,
    }


def _classify_ready_item(
    it: CollectPlanItem,
    staging_media: Path,
    dest_name: str,
    actual_sha: str,
    exp_h: str | None,
    sz: int,
) -> tuple[dict[str, Any], str]:
    """Audita e gera a entrada do manifesto para um item pronto copiado."""
    stage_file = staging_media / dest_name
    st = "copied"
    is_wav = Path(it.source).suffix.lower() in (".wav", ".wave")
    if is_wav:
        audit = verify_media_file(stage_file, expected_hash=exp_h)
        if audit.status == "corrupt":
            st = "corrupt"
        elif exp_h is not None and actual_sha != exp_h:
            st = "hash_mismatch"
    elif exp_h is not None and actual_sha != exp_h:
        st = "hash_mismatch"

    entry = _media_entry(
        source=it.source,
        destination=f"media/{dest_name}",
        kind=it.kind,
        state=st,
        sha256=actual_sha,
        expected_sha256=exp_h,
        size_bytes=sz,
    )
    return entry, st


def _is_package_valid(counts: dict[str, int]) -> bool:
    """Verifica se o pacote atende aos criterios de integridade do turnover."""
    return (
        counts["missing"] == 0
        and counts["corrupt"] == 0
        and counts["hash_mismatch"] == 0
        and (counts["copied"] > 0 or counts["embedded"] > 0)
    )


def _classify_media_items(
    plan: CollectPlan,
    staging_media: Path,
    dest_name_map: dict[str, str],
    expected_map: dict[str, str | None],
    copy_hashes: dict[str, str],
    copy_sizes: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    """Classifica cada item do plano nos estados do turnover e calcula is_valid."""
    media_items: list[dict[str, Any]] = []
    counts = {
        "copied": 0,
        "missing": 0,
        "library": 0,
        "embedded": 0,
        "corrupt": 0,
        "hash_mismatch": 0,
        "history_kept": plan.history_kept,
    }
    for it in plan.items:
        if it.state == "ready":
            entry, st = _classify_ready_item(
                it=it,
                staging_media=staging_media,
                dest_name=dest_name_map[it.resolved_key],
                actual_sha=copy_hashes[it.resolved_key],
                exp_h=expected_map.get(it.resolved_key),
                sz=copy_sizes[it.resolved_key],
            )
            counts[st] += 1
            media_items.append(entry)
        else:
            counts[it.state] += 1
            media_items.append(
                _media_entry(
                    source=it.source,
                    destination=None,
                    kind=it.kind,
                    state=it.state,
                    sha256=None,
                    expected_sha256=None,
                    size_bytes=it.size_bytes,
                )
            )

    return media_items, counts, _is_package_valid(counts)
