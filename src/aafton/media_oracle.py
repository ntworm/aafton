"""Oraculo independente de integridade de midia e amostras de audio.

Verifica conformidade de formato WAV (cabecalhos RIFF/WAVE e chunks basicos)
e calcula/audita hashes criptograficos SHA-256 contra sidecars de sessao ou
especificacoes explicitas de turnover de pos-producao.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import struct


@dataclass(kw_only=True)
class MediaFileResult:
    """Resultado da verificacao de um arquivo de midia individual."""

    path: Path
    status: str  # "ok", "missing", "corrupt", "hash_mismatch"
    sha256: str | None = None
    expected_hash: str | None = None
    size_bytes: int = 0
    error_message: str | None = None
    is_valid: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path)
        return d


@dataclass(kw_only=True)
class MediaAuditReport:
    """Relatorio agregado da auditoria de midias de uma sessao."""

    total_files: int
    ok_count: int
    missing_count: int
    corrupt_count: int
    mismatch_count: int
    is_valid: bool
    results: list[MediaFileResult]

    def to_dict(self) -> dict:
        return {
            "total_files": self.total_files,
            "ok_count": self.ok_count,
            "missing_count": self.missing_count,
            "corrupt_count": self.corrupt_count,
            "mismatch_count": self.mismatch_count,
            "is_valid": self.is_valid,
            "results": [r.to_dict() for r in self.results],
        }


def verify_media_file(
    path: str | Path,
    expected_hash: str | None = None,
) -> MediaFileResult:
    """Valida integridade fisica, estrutura WAV basica e SHA-256 de um arquivo de audio."""
    file_path = Path(path)

    if not file_path.exists() or not file_path.is_file():
        return MediaFileResult(
            path=file_path,
            status="missing",
            is_valid=False,
            expected_hash=expected_hash,
            error_message=f"File does not exist: {file_path}",
        )

    size = file_path.stat().st_size
    if size < 12:
        return MediaFileResult(
            path=file_path,
            status="corrupt",
            size_bytes=size,
            is_valid=False,
            expected_hash=expected_hash,
            error_message=f"Size ({size} bytes) below the minimum RIFF header (12 bytes)",
        )

    # Validacao de cabecalho RIFF/WAVE e chunks essenciais
    try:
        with open(file_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return MediaFileResult(
                    path=file_path,
                    status="corrupt",
                    size_bytes=size,
                    is_valid=False,
                    expected_hash=expected_hash,
                    error_message="Failure to read the RIFF header",
                )
            riff, riff_size, wave = struct.unpack("<4sI4s", header)
            if riff != b"RIFF" or wave != b"WAVE":
                return MediaFileResult(
                    path=file_path,
                    status="corrupt",
                    size_bytes=size,
                    is_valid=False,
                    expected_hash=expected_hash,
                    error_message=f"Header is not RIFF/WAVE (magic={riff!r}, sub={wave!r})",
                )

            # O tamanho declarado do RIFF deve coincidir com os dados disponiveis
            if size < riff_size + 8:
                return MediaFileResult(
                    path=file_path,
                    status="corrupt",
                    size_bytes=size,
                    is_valid=False,
                    expected_hash=expected_hash,
                    error_message=f"Truncated WAV file: expected {riff_size + 8} bytes, disk has {size} bytes",
                )

            # Inspecao dos chunks internos: 'fmt ' e 'data'
            has_fmt = False
            has_data = False
            while f.tell() < size:
                chunk_head = f.read(8)
                if not chunk_head:
                    break
                if len(chunk_head) < 8:
                    return MediaFileResult(
                        path=file_path,
                        status="corrupt",
                        size_bytes=size,
                        is_valid=False,
                        expected_hash=expected_hash,
                        error_message="Incomplete chunk header",
                    )
                chunk_id, chunk_len = struct.unpack("<4sI", chunk_head)
                if chunk_id == b"fmt ":
                    has_fmt = True
                    fmt_data = f.read(chunk_len)
                    if len(fmt_data) < chunk_len:
                        return MediaFileResult(
                            path=file_path,
                            status="corrupt",
                            size_bytes=size,
                            is_valid=False,
                            expected_hash=expected_hash,
                            error_message="Truncated 'fmt ' chunk",
                        )
                elif chunk_id == b"data":
                    has_data = True
                    cur_pos = f.tell()
                    if cur_pos + chunk_len > size:
                        return MediaFileResult(
                            path=file_path,
                            status="corrupt",
                            size_bytes=size,
                            is_valid=False,
                            expected_hash=expected_hash,
                            error_message=f"Truncated 'data' chunk: {cur_pos + chunk_len - size} bytes missing",
                        )
                    f.seek(chunk_len, 1)
                else:
                    f.seek(chunk_len, 1)

                if chunk_len % 2 == 1:
                    f.seek(1, 1)

            if not has_fmt or not has_data:
                return MediaFileResult(
                    path=file_path,
                    status="corrupt",
                    size_bytes=size,
                    is_valid=False,
                    expected_hash=expected_hash,
                    error_message="Incomplete WAV structure: 'fmt ' or 'data' not found",
                )

    except Exception as exc:  # noqa: BLE001
        return MediaFileResult(
            path=file_path,
            status="corrupt",
            size_bytes=size,
            is_valid=False,
            expected_hash=expected_hash,
            error_message=f"error while analyzing the WAV file: {exc}",
        )

    # Calculo do SHA-256
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    actual_hash = hasher.hexdigest()

    if expected_hash is not None and actual_hash.lower() != expected_hash.lower():
        return MediaFileResult(
            path=file_path,
            status="hash_mismatch",
            sha256=actual_hash,
            expected_hash=expected_hash,
            size_bytes=size,
            is_valid=False,
            error_message=f"Divergent SHA-256: expected {expected_hash}, obtained {actual_hash}",
        )

    return MediaFileResult(
        path=file_path,
        status="ok",
        sha256=actual_hash,
        expected_hash=expected_hash,
        size_bytes=size,
        is_valid=True,
        error_message=None,
    )


def verify_session_media(
    media_dir: str | Path,
    sidecar_path: str | Path | None = None,
    media_hashes: dict[str, str] | None = None,
) -> MediaAuditReport:
    """Audita todas as midias de uma sessao contra lista de hashes ou escaneando diretorio."""
    target_dir = Path(media_dir)
    hashes_map: dict[str, str] = {}

    if media_hashes is not None:
        hashes_map = dict(media_hashes)
    elif sidecar_path is not None:
        sc_file = Path(sidecar_path)
        if not sc_file.exists():
            raise FileNotFoundError(f"Sidecar not found: {sc_file}")
        data = json.loads(sc_file.read_text(encoding="utf-8"))
        raw = data.get("media_hashes") or {}
        hashes_map = dict(raw)

    results: list[MediaFileResult] = []

    if hashes_map:
        # Desduplicar chaves que representam o mesmo arquivo (nome base vs caminho completo)
        deduped: dict[tuple[str, str], tuple[str, str]] = {}
        for k, v in hashes_map.items():
            name = Path(k).name
            identity = (name.casefold(), v.lower())
            if identity not in deduped:
                deduped[identity] = (k, v)
            elif not Path(k).is_absolute() and Path(deduped[identity][0]).is_absolute():
                deduped[identity] = (k, v)

        for _, (orig_key, exp_h) in deduped.items():
            name = Path(orig_key).name
            candidate = target_dir / name
            if not candidate.exists() and Path(orig_key).is_file():
                candidate = Path(orig_key)
            results.append(verify_media_file(candidate, expected_hash=exp_h))
    else:
        if target_dir.exists():
            for p in sorted(target_dir.rglob("*")):
                if p.is_file() and p.suffix.lower() in (".wav", ".wave"):
                    results.append(verify_media_file(p, expected_hash=None))

    ok_count = sum(1 for r in results if r.status == "ok")
    missing_count = sum(1 for r in results if r.status == "missing")
    corrupt_count = sum(1 for r in results if r.status == "corrupt")
    mismatch_count = sum(1 for r in results if r.status == "hash_mismatch")
    total_files = len(results)

    is_valid = total_files > 0 and missing_count == 0 and corrupt_count == 0 and mismatch_count == 0

    return MediaAuditReport(
        total_files=total_files,
        ok_count=ok_count,
        missing_count=missing_count,
        corrupt_count=corrupt_count,
        mismatch_count=mismatch_count,
        is_valid=is_valid,
        results=results,
    )
