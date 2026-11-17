"""Construcao do manifesto e relatorio tecnico de turnover.

Gera o manifesto estruturado (formato 2) e o relatorio textual
(turnover_report.txt) com status, contagens e inventario de midia.
"""

from __future__ import annotations

from typing import Any


def build_manifest_data(
    *,
    session_name: str,
    session_file: str,
    sidecar_file: str | None,
    created_at: str,
    files_map: dict[str, dict[str, Any]],
    media_items: list[dict[str, Any]],
    counts: dict[str, int],
    is_valid: bool,
) -> dict[str, Any]:
    """Monta o dicionario do manifesto v2 para o manifest.json."""
    return {
        "generator": "aafton-turnover",
        "format_version": 2,
        "created_at": created_at,
        "session_name": session_name,
        "session_file": session_file,
        "sidecar_file": sidecar_file,
        "total_files": len(files_map),
        "files": files_map,
        "media": media_items,
        "counts": counts,
        "is_valid": is_valid,
    }


def _format_media_line(m: dict[str, Any]) -> str:
    """Formata uma linha de sumario de midia para o relatorio."""
    src = m["source"]
    dst = m["destination"] or "-"
    st = str(m["state"]).upper()
    h = m.get("sha256") or "N/A"
    return f"  - [{st}] {src} -> {dst} (SHA-256: {h})"


def build_report_text(
    *,
    session_name: str,
    created_at: str,
    session_file: str,
    sidecar_file: str | None,
    is_valid: bool,
    counts: dict[str, int],
    media_items: list[dict[str, Any]],
    files_map: dict[str, dict[str, Any]],
    discarded_keys: list[str] | None = None,
) -> str:
    """Gera o texto plano legivel para turnover_report.txt."""
    status_label = "STATUS: OK (VALID)" if is_valid else "STATUS: DEGRADED (INVALID)"
    lines = [
        "=" * 70,
        "AAFTON TURNOVER PACKAGE REPORT",
        "=" * 70,
        f"Session Name:     {session_name}",
        f"Created At:       {created_at}",
        f"Session File:     {session_file}",
        f"Sidecar File:     {sidecar_file or 'None'}",
        f"Package Status:   {status_label}",
        "",
        "COUNTS:",
        f"- Copied:           {counts.get('copied', 0)}",
        f"- Missing:          {counts.get('missing', 0)}",
        f"- Library:          {counts.get('library', 0)}",
        f"- Embedded:         {counts.get('embedded', 0)}",
        f"- Corrupt:          {counts.get('corrupt', 0)}",
        f"- Hash Mismatch:    {counts.get('hash_mismatch', 0)}",
        f"- History Kept:     {counts.get('history_kept', 0)}",
        "",
        "MEDIA SUMMARY:",
    ]
    for m in media_items:
        lines.append(_format_media_line(m))

    if discarded_keys:
        lines.extend(["", "DISCARDED SOURCE SIDECAR KEYS:"])
        for dk in sorted(discarded_keys):
            lines.append(f"  - {dk}")

    lines.extend(["", "FILES IN PACKAGE:"])
    for rel_path, meta in files_map.items():
        sz = meta["size_bytes"]
        sha = meta["sha256"]
        lines.append(f"  - {rel_path} ({sz} bytes) [SHA-256: {sha}]")

    lines.extend(["=" * 70, ""])
    return "\n".join(lines)
