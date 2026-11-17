"""Copia em blocos, calculo de SHA-256, regra de nomes e escrita do sidecar."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from .sidecar import sidecar_path_for

CHUNK_SIZE = 1024 * 1024
ProgressCallback = Callable[[int, int, str], None]


class CollectError(ValueError):
    """Erro de validacao ou execucao durante a coleta de midia."""


def norm_path_key(path: str | Path) -> str:
    """Chave normalizada do caminho resolvido; usa casefold no Windows."""
    try:
        resolved = str(Path(path).resolve(strict=False))
    except (OSError, ValueError):
        resolved = str(path)
    return resolved.casefold() if sys.platform == "win32" else resolved


def resolved_str(path: str | Path) -> str:
    """Caminho resolvido como string, preservando capitalizacao."""
    try:
        return str(Path(path).resolve(strict=False))
    except (OSError, ValueError):
        return str(path)


def compute_sha256(path: Path) -> str:
    """Calcula o SHA-256 de um arquivo em blocos de 1 MiB."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def assign_dest_names(
    sources: list[str | Path],
    existing_names: set[str] | None = None,
) -> dict[str, str]:
    """Decide os nomes de destino antes de copiar, sem colisao.

    Ordena as origens pelo caminho resolvido (casefold no Windows). A primeira
    origem fica com o nome original; se o nome (casefold) ja estiver tomado,
    recebe ``<stem>__<N hex do sha256 do caminho de origem><suffix>`` com N=8,
    12, 16... ate ficar livre.
    """
    taken = {n.casefold() for n in (existing_names or set())}
    by_key: dict[str, str] = {}
    for src in sources:
        res = resolved_str(src)
        key = norm_path_key(res)
        if key not in by_key:
            by_key[key] = res

    ordered = [by_key[k] for k in sorted(by_key)]
    mapping: dict[str, str] = {}
    for src_str in ordered:
        key = norm_path_key(src_str)
        p = Path(src_str)
        candidate = p.name
        if candidate.casefold() not in taken:
            taken.add(candidate.casefold())
            mapping[key] = candidate
            continue
        path_hash = hashlib.sha256(src_str.encode("utf-8")).hexdigest()
        stem = p.stem
        suffix = p.suffix
        length = 8
        while True:
            piece = path_hash[:length]
            candidate = f"{stem}__{piece}{suffix}"
            if candidate.casefold() not in taken:
                taken.add(candidate.casefold())
                mapping[key] = candidate
                break
            length += 4
    return mapping


def copy_file_verified(
    src: Path,
    dest: Path,
    *,
    progress: ProgressCallback | None = None,
    copied_so_far: int = 0,
    total_bytes: int = 0,
) -> tuple[str, int]:
    """Copia src para dest em modo 'xb' (1 MiB), confere SHA-256 na releitura e preserva mtime."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    written = 0
    with open(src, "rb") as reader, open(dest, "xb") as writer:
        while chunk := reader.read(CHUNK_SIZE):
            writer.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            if progress is not None:
                progress(copied_so_far + written, total_bytes, src.name)
    shutil.copystat(src, dest)
    src_sha = digest.hexdigest()
    dest_sha = compute_sha256(dest)
    if dest_sha != src_sha:
        raise CollectError(f"hash mismatch after copying {src.name}")
    return src_sha, written


def rewrite_sidecar_data(
    source_als: Path,
    dest_als: Path,
    copied_map: dict[str, tuple[Path, str]],
    video_copied_dest: Path | None,
) -> Path | None:
    """Copia o .aafton.json mudando so video_path e media_hashes.

    O JSON e editado como esta, sem passar por write_sidecar: o
    format_version e todo campo que esta versao nao conhece ficam iguais, e
    um set de sidecar antigo continua lido com as regras da versao dele.
    """
    src_sidecar = sidecar_path_for(source_als)
    if not src_sidecar.is_file():
        return None
    dados = json.loads(src_sidecar.read_text(encoding="utf-8"))
    video = dados.get("video_path")
    if video_copied_dest is not None:
        dados["video_path"] = str(video_copied_dest.resolve())
    elif video:
        dados["video_path"] = _video_da_copia(source_als, video, copied_map)
    if dados.get("media_hashes") is not None or copied_map:
        dados["media_hashes"] = _update_media_hashes(dados.get("media_hashes"), copied_map)
    destino = sidecar_path_for(dest_als)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def _video_da_copia(source_als: Path, video_path: str, copied_map) -> str:
    """O video do sidecar coletado: a copia, ou o original em caminho absoluto."""
    # Caminho relativo vale a partir da pasta do set, como na leitura.
    video = Path(video_path)
    if not video.is_absolute():
        video = source_als.resolve().parent / video
    copiado = copied_map.get(norm_path_key(video))
    if copiado is not None:
        return str(copiado[0].resolve())
    # Video nao copiado continua apontando para o original, inclusive quando o
    # sidecar novo fica noutra pasta.
    return resolved_str(video)


def _update_media_hashes(
    media_hashes: dict[str, str] | None,
    copied_map: dict[str, tuple[Path, str]],
) -> dict[str, str] | None:
    """Atualiza media_hashes conforme a regra do plano."""
    if media_hashes is None and not copied_map:
        return None
    result: dict[str, str] = {}
    for k, h in (media_hashes or {}).items():
        if _is_abs_key(k):
            k_norm = norm_path_key(k)
            if k_norm in copied_map:
                dest_path, _ = copied_map[k_norm]
                result[str(dest_path.resolve())] = h
            else:
                result[k] = h
        else:
            result[k] = h
    for dest_path, sha in copied_map.values():
        result[dest_path.name] = sha
    return result


def _is_abs_key(key: str) -> bool:
    """Diz se a chave de media_hashes e um caminho absoluto (ou com separador)."""
    if "/" in key or "\\" in key:
        return True
    return Path(key).is_absolute()


def copy_into_existing_media_dir(
    items: list,
    media_dir: Path,
    include_video: bool,
    progress: ProgressCallback | None,
) -> dict[str, tuple[Path, str, str]]:
    """Copia arquivos prontos para media_dir existente, marcando already_there se ja estiver la."""
    existing_names = {p.name for p in media_dir.iterdir()} if media_dir.is_dir() else set()
    media_dir_resolved = media_dir.resolve()
    outcomes: dict[str, tuple[Path, str, str]] = {}
    need_copy = []

    for item in items:
        if item.state != "ready" or (not include_video and item.kind == "video"):
            continue
        src_path = Path(item.source).resolve()
        if src_path.parent == media_dir_resolved:
            sha = compute_sha256(src_path)
            outcomes[item.resolved_key] = (src_path, sha, "already_there")
            existing_names.add(src_path.name)
        else:
            need_copy.append(item)

    name_map = assign_dest_names([it.source for it in need_copy], existing_names)
    total_bytes = sum(it.size_bytes for it in need_copy)
    copied_so_far = 0
    for item in need_copy:
        src_path = Path(item.source)
        dest_file = media_dir / name_map[item.resolved_key]
        sha, written = copy_file_verified(
            src_path,
            dest_file,
            progress=progress,
            copied_so_far=copied_so_far,
            total_bytes=total_bytes,
        )
        copied_so_far += written
        outcomes[item.resolved_key] = (dest_file.resolve(), sha, "copied")
    return outcomes
