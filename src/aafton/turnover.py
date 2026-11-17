"""Gerador de pacote de turnover autocontido para entrega de pos-producao.

Cria uma pasta de entrega atomica contendo a sessao (AAF ou ALS), o sidecar
de ancoragem quando houver, as midias de audio e video copiadas e relinkadas,
o manifesto criptografico SHA-256 (manifest.json) e o relatorio tecnico
legivel (turnover_report.txt).
"""

from __future__ import annotations

import gzip
import json
import re
import struct
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aaf2.exceptions

from .collect import CollectPlan, CollectPlanItem, plan_collect
from .collect_aaf import rewrite_aaf_locators
from .collect_als import rewrite_als_xml
from .collect_copy import CollectError, assign_dest_names, norm_path_key
from .media_extract import file_sha256
from .sidecar import resolve_video_path, sidecar_path_for
from .turnover_media import (
    _classify_media_items,
    _copy_ready_items,
    _expected_hashes_for,
)
from .turnover_report import build_manifest_data, build_report_text

_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

CATCHABLE_ERRORS = (
    CollectError,
    ET.ParseError,
    gzip.BadGzipFile,
    EOFError,
    OSError,
    zlib.error,
    json.JSONDecodeError,
    UnicodeDecodeError,
    ValueError,
    struct.error,
    NotImplementedError,
    KeyError,
    AttributeError,
    aaf2.exceptions.AAFError,
)


class TurnoverError(RuntimeError):
    """Erro durante a geracao ou validacao do pacote de turnover."""


class TurnoverValidationError(TurnoverError):
    """Falha de integridade criptografica ou estrutural das midias no turnover."""


@dataclass(kw_only=True)
class TurnoverResult:
    """Resultado da geracao do pacote de turnover."""

    package_dir: Path
    manifest_path: Path
    report_path: Path
    session_path: Path
    sidecar_path: Path | None
    media_count: int
    manifest_data: dict[str, Any]
    is_valid: bool


def _rewrite_video_path(
    source_sidecar_data: dict[str, Any],
    source: Path,
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
) -> str | None:
    """Reescreve video_path apenas quando o video do sidecar corresponder a item copiado."""
    video_path = source_sidecar_data.get("video_path")
    if not video_path:
        return video_path
    resolved_video_key = norm_path_key(resolve_video_path(video_path, source))
    for it in ready_items:
        if it.kind == "video" and it.resolved_key == resolved_video_key:
            return f"media/{dest_name_map[it.resolved_key]}"
    return video_path


def _build_package_media_hashes(
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
    expected_map: dict[str, str | None],
    copy_hashes: dict[str, str],
) -> dict[str, str]:
    """Monta o mapa de media_hashes portateis para os audios copiados."""
    pkg_hashes: dict[str, str] = {}
    for it in ready_items:
        if it.kind == "video":
            continue
        dest_name = dest_name_map[it.resolved_key]
        is_wav = Path(it.source).suffix.lower() in (".wav", ".wave")
        exp_h = expected_map.get(it.resolved_key)
        if is_wav:
            pkg_hashes[dest_name] = exp_h or copy_hashes[it.resolved_key]
        elif exp_h is not None:
            pkg_hashes[dest_name] = exp_h
    return pkg_hashes


def _write_sidecar_for_package(
    source_sidecar_data: dict[str, Any],
    stage_sidecar: Path,
    source: Path,
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
    expected_map: dict[str, str | None],
    copy_hashes: dict[str, str],
) -> None:
    """Reescreve o sidecar para o pacote com video_path e media_hashes portateis."""
    data = dict(source_sidecar_data)
    rewritten_video = _rewrite_video_path(source_sidecar_data, source, ready_items, dest_name_map)
    if rewritten_video is not None:
        data["video_path"] = rewritten_video

    data["media_hashes"] = _build_package_media_hashes(
        ready_items, dest_name_map, expected_map, copy_hashes
    )
    stage_sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _verify_staging_session(stage_session: Path, staging: Path, expected_ready_count: int) -> None:
    """Verifica se a sessao reescrita no staging rele todas as midias internamente."""
    staged_plan = plan_collect(stage_session)
    staged_ready = [it for it in staged_plan.items if it.state == "ready"]
    if len(staged_ready) != expected_ready_count:
        msg = (
            f"staging session verification failed: expected {expected_ready_count} ready items, "
            f"found {len(staged_ready)}"
        )
        raise TurnoverError(msg)
    for it in staged_ready:
        src_path = Path(it.source).resolve()
        if not src_path.is_relative_to(staging.resolve()):
            msg = f"staging session verification failed: media {it.source} is outside staging"
            raise TurnoverError(msg)


def _collect_staging_files(staging: Path) -> dict[str, dict[str, Any]]:
    """Mapeia os arquivos do staging para o manifesto com SHA-256 e tamanho."""
    files_map: dict[str, dict[str, Any]] = {}
    for p in sorted(staging.rglob("*")):
        if p.is_file():
            rel = p.relative_to(staging).as_posix()
            if rel in ("manifest.json", "turnover_report.txt"):
                continue
            files_map[rel] = {
                "sha256": file_sha256(p),
                "size_bytes": p.stat().st_size,
            }
    return files_map


def _session_url_for(p: Path, target: Path) -> str:
    """Gera a URL relativa para locators de AAF no pacote de turnover."""
    try:
        rel = p.relative_to(target).as_posix()
    except ValueError:
        rel = f"media/{p.name}"
    return "file:" + urllib.parse.quote(rel)


def _rewrite_session_files(
    plan: CollectPlan,
    source: Path,
    stage_session: Path,
    target: Path,
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
) -> None:
    """Reescreve os locators ou FileRefs da sessao no staging."""
    final_paths = {
        it.resolved_key: target / "media" / dest_name_map[it.resolved_key] for it in ready_items
    }
    if plan.source_format == "als":
        rewrite_als_xml(source, stage_session, "media", final_paths)
    else:
        rewrite_aaf_locators(
            source,
            stage_session,
            final_paths,
            url_for=lambda p: _session_url_for(p, target),
        )


def _read_source_sidecar(
    plan: CollectPlan, source: Path
) -> tuple[Path | None, dict[str, Any] | None, dict[str, str] | None]:
    """Le o sidecar da sessao de origem quando existir."""
    if plan.source_format != "als" or not plan.has_sidecar:
        return None, None, None
    sidecar_path = sidecar_path_for(source)
    sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    return sidecar_path, sidecar_data, sidecar_data.get("media_hashes")


def _raise_strict(counts: dict[str, int]) -> None:
    """Dispara TurnoverValidationError em modo estrito se o pacote for invalido."""
    if counts["corrupt"] > 0 or counts["missing"] > 0 or counts["hash_mismatch"] > 0:
        raise TurnoverValidationError(
            f"Turnover failed media verification in strict mode: "
            f"{counts['missing']} missing, {counts['corrupt']} corrupt, "
            f"{counts['hash_mismatch']} hash mismatch."
        )
    raise TurnoverValidationError("Turnover failed in strict mode: no media copied or embedded.")


def _write_manifest_and_report(
    *,
    staging: Path,
    name: str,
    source: Path,
    sidecar_name: str | None,
    timestamp_iso: str,
    media_items: list[dict[str, Any]],
    counts: dict[str, int],
    is_valid: bool,
    files_map: dict[str, dict[str, Any]],
    discarded_keys: list[str],
) -> dict[str, Any]:
    """Grava manifest.json e turnover_report.txt no staging e retorna os dados do manifesto."""
    manifest_data = build_manifest_data(
        session_name=name,
        session_file=source.name,
        sidecar_file=sidecar_name,
        created_at=timestamp_iso,
        files_map=files_map,
        media_items=media_items,
        counts=counts,
        is_valid=is_valid,
    )
    (staging / "manifest.json").write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report_text = build_report_text(
        session_name=name,
        created_at=timestamp_iso,
        session_file=source.name,
        sidecar_file=sidecar_name,
        is_valid=is_valid,
        counts=counts,
        media_items=media_items,
        files_map=files_map,
        discarded_keys=discarded_keys,
    )
    (staging / "turnover_report.txt").write_text(report_text, encoding="utf-8")
    return manifest_data


def _copy_and_rewrite_staging(
    plan: CollectPlan,
    source: Path,
    target: Path,
    staging: Path,
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
) -> tuple[dict[str, str], dict[str, int], Path]:
    """Copia as midias e reescreve os arquivos da sessao na pasta de staging."""
    staging_media = staging / "media"
    staging_media.mkdir(parents=True, exist_ok=True)
    copy_hashes, copy_sizes = _copy_ready_items(ready_items, staging_media, dest_name_map)
    stage_session = staging / source.name
    _rewrite_session_files(plan, source, stage_session, target, ready_items, dest_name_map)
    return copy_hashes, copy_sizes, stage_session


def _stage_sidecar_if_present(
    sidecar_data: dict[str, Any] | None,
    sidecar_path: Path | None,
    staging: Path,
    source: Path,
    ready_items: list[CollectPlanItem],
    dest_name_map: dict[str, str],
    expected_map: dict[str, str | None],
    copy_hashes: dict[str, str],
) -> None:
    """Escreve o sidecar portatil no staging se a sessao de origem tiver ancoragem."""
    if sidecar_data is not None and sidecar_path is not None:
        stage_sidecar = staging / sidecar_path.name
        _write_sidecar_for_package(
            sidecar_data,
            stage_sidecar,
            source,
            ready_items,
            dest_name_map,
            expected_map,
            copy_hashes,
        )


def _build_turnover_result(
    *,
    target: Path,
    source: Path,
    sidecar_name: str | None,
    counts: dict[str, int],
    manifest_data: dict[str, Any],
    is_valid: bool,
) -> TurnoverResult:
    """Monta o objeto TurnoverResult final."""
    media_count = counts["copied"] + counts["corrupt"] + counts["hash_mismatch"]
    sidecar_target = target / sidecar_name if sidecar_name else None
    return TurnoverResult(
        package_dir=target,
        manifest_path=target / "manifest.json",
        report_path=target / "turnover_report.txt",
        session_path=target / source.name,
        sidecar_path=sidecar_target,
        media_count=media_count,
        manifest_data=manifest_data,
        is_valid=is_valid,
    )


def _generate_manifest_and_report(
    *,
    staging: Path,
    name: str,
    source: Path,
    sidecar_path: Path | None,
    media_items: list[dict[str, Any]],
    counts: dict[str, int],
    is_valid: bool,
    discarded_keys: list[str],
) -> tuple[dict[str, Any], str | None]:
    """Coleta arquivos, grava manifest/report e devolve o manifesto e nome do sidecar."""
    files_map = _collect_staging_files(staging)
    timestamp_iso = datetime.now(timezone.utc).isoformat()
    sidecar_name = sidecar_path.name if sidecar_path else None
    manifest_data = _write_manifest_and_report(
        staging=staging,
        name=name,
        source=source,
        sidecar_name=sidecar_name,
        timestamp_iso=timestamp_iso,
        media_items=media_items,
        counts=counts,
        is_valid=is_valid,
        files_map=files_map,
        discarded_keys=discarded_keys,
    )
    return manifest_data, sidecar_name


def _do_build_turnover_in_staging(
    source: Path,
    name: str,
    target: Path,
    staging: Path,
    strict: bool,
) -> TurnoverResult:
    """Executa a construcao do pacote na pasta temporaria de staging."""
    plan = plan_collect(source)
    sidecar_path, sidecar_data, media_hashes = _read_source_sidecar(plan, source)

    ready_items = [it for it in plan.items if it.state == "ready"]
    dest_name_map = assign_dest_names([it.source for it in ready_items])
    expected_map, discarded_keys = _expected_hashes_for(media_hashes, ready_items, source.parent)

    copy_hashes, copy_sizes, stage_session = _copy_and_rewrite_staging(
        plan, source, target, staging, ready_items, dest_name_map
    )
    _stage_sidecar_if_present(
        sidecar_data,
        sidecar_path,
        staging,
        source,
        ready_items,
        dest_name_map,
        expected_map,
        copy_hashes,
    )

    staging_media = staging / "media"
    media_items, counts, is_valid = _classify_media_items(
        plan, staging_media, dest_name_map, expected_map, copy_hashes, copy_sizes
    )
    _verify_staging_session(stage_session, staging, len(ready_items))

    if strict and not is_valid:
        _raise_strict(counts)

    manifest_data, sidecar_name = _generate_manifest_and_report(
        staging=staging,
        name=name,
        source=source,
        sidecar_path=sidecar_path,
        media_items=media_items,
        counts=counts,
        is_valid=is_valid,
        discarded_keys=discarded_keys,
    )

    return _build_turnover_result(
        target=target,
        source=source,
        sidecar_name=sidecar_name,
        counts=counts,
        manifest_data=manifest_data,
        is_valid=is_valid,
    )


def _build_turnover_in_staging(
    source: Path,
    name: str,
    target: Path,
    staging: Path,
    strict: bool,
) -> TurnoverResult:
    """Executa a criacao com tratamento e envelopamento em TurnoverError."""
    try:
        return _do_build_turnover_in_staging(source, name, target, staging, strict)
    except TurnoverError:
        raise
    except CATCHABLE_ERRORS as exc:
        raise TurnoverError(str(exc)) from exc


def _validate_package_name(name: str) -> None:
    """Valida se o nome do pacote e seguro para arquivos e pastas."""
    if not name or _INVALID_NAME.search(name) or name in {".", ".."}:
        raise TurnoverError(f"invalid session name: {name!r}")


def _resolve_target_dir(source: Path, name: str, output_dir: str | Path | None) -> Path:
    """Resolve o diretorio final e garante que nao exista previamente."""
    if output_dir is not None:
        target = Path(output_dir).resolve()
    else:
        target = source.parent / f"{name}_turnover"
    if target.exists() or target.is_symlink():
        raise TurnoverError(
            "destination already exists; preserve the previous delivery "
            f"and choose another folder: {target}"
        )
    return target


def create_turnover_package(
    session_file: str | Path,
    output_dir: str | Path | None = None,
    session_name: str | None = None,
    strict: bool = True,
) -> TurnoverResult:
    """Cria um pacote de turnover autocontido com sessao, midias, manifesto e relatorio."""
    source = Path(session_file).resolve()
    name = session_name or source.stem
    _validate_package_name(name)
    target = _resolve_target_dir(source, name, output_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".turnover-", dir=target.parent) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir(parents=True, exist_ok=True)
        res = _build_turnover_in_staging(source, name, target, staging, strict)
        if target.is_dir():
            target.rmdir()
        staging.rename(target)
        return res
