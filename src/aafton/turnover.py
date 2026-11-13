"""Gerador de pacote de turnover autocontido para entrega de pos-producao.

Cria uma pasta de entrega atômica contendo a sessao (AAF ou ALS), o sidecar
de ancoragem, as midias de audio verificadas pelo oraculo, um manifesto criptografico
SHA-256 (manifest.json) e um relatorio tecnico legivel (turnover_report.txt).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import re

from .media_oracle import verify_session_media
from .media_extract import file_sha256


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
    manifest_data: dict
    is_valid: bool


def create_turnover_package(
    session_file: str | Path,
    media_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    session_name: str | None = None,
    strict: bool = True,
) -> TurnoverResult:
    """Publica a pasta somente após copiar, relinkar e conferir seus arquivos."""
    source = Path(session_file).resolve()
    name = session_name or source.stem
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name in {".", ".."}:
        raise TurnoverError("invalid session name")
    target = (
        Path(output_dir).resolve() if output_dir is not None else source.parent / f"{name}_turnover"
    )
    if target.exists() or target.is_symlink():
        raise TurnoverError(
            f"destination already exists; preserve the previous delivery and choose another folder: {target}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".turnover-", dir=target.parent) as temporary:
        staging = Path(temporary) / "package"
        result = _build_turnover_package(source, media_dir, staging, name, strict)
        staging.rename(target)
    return replace(
        result,
        package_dir=target,
        manifest_path=target / result.manifest_path.name,
        report_path=target / result.report_path.name,
        session_path=target / result.session_path.name,
        sidecar_path=target / result.sidecar_path.name if result.sidecar_path else None,
    )


def _iter_descriptors(descriptor, seen=None):
    if seen is None:
        seen = set()
    if descriptor is None or id(descriptor) in seen:
        return
    seen.add(id(descriptor))
    yield descriptor
    children = descriptor.get("FileDescriptors")
    if children:
        for child in children.value:
            yield from _iter_descriptors(child, seen)


def _relink_session(session: Path, source: Path, package: Path, copied: dict[str, Path]) -> None:
    """Referências internas permanecem relativas quando a pasta é movida."""

    def relative(raw: str) -> str:
        from .aaf_read import _url_para_caminho

        candidate = Path(_url_para_caminho(raw))
        target = copied.get(candidate.name.casefold())
        if target is None:
            raise TurnoverValidationError(
                f"referenced media not copied for turnover: {candidate.name}"
            )
        return target.relative_to(package).as_posix()

    if session.suffix.lower() == ".als":
        from .als_read import _media_path
        from .als_xml import load_als, save_als

        root = load_als(source)
        for clip in root.iter("AudioClip"):
            ref = clip.find("SampleRef/FileRef")
            if ref is None:
                continue
            rel = relative(_media_path(clip, source))
            for tag, value in (("Path", ""), ("RelativePath", rel), ("RelativePathType", "3")):
                node = ref.find(tag)
                if node is None:
                    import xml.etree.ElementTree as ET

                    node = ET.SubElement(ref, tag)
                node.set("Value", value)
        save_als(root, session)
    elif session.suffix.lower() == ".aaf":
        import aaf2

        with aaf2.open(str(session), "rw") as f:
            for mob in f.content.sourcemobs():
                for descriptor in _iter_descriptors(getattr(mob, "descriptor", None)):
                    locators = descriptor.get("Locator")
                    if locators:
                        for locator in locators.value:
                            url = locator.get("URLString")
                            if url and url.value:
                                url.value = "file:" + relative(url.value)
    else:
        raise TurnoverError("turnover supports only AAF and ALS")


def _build_turnover_package(
    session_file: str | Path,
    media_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    session_name: str | None = None,
    strict: bool = True,
) -> TurnoverResult:
    """Cria um pacote autocontido de turnover com sessao, midias, manifesto e relatorio."""
    sess_path = Path(session_file).resolve()
    if not sess_path.exists() or not sess_path.is_file():
        raise FileNotFoundError(f"Session file not found: {sess_path}")

    name = session_name or sess_path.stem

    if output_dir is not None:
        pkg_dir = Path(output_dir).resolve()
    else:
        pkg_dir = sess_path.parent / f"{name}_turnover"

    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Localizar sidecar se existir
    sidecar_cand = sess_path.with_name(sess_path.stem + ".aafton.json")
    sidecar_path = sidecar_cand if sidecar_cand.exists() else None

    # Resolver diretorio de midias
    resolved_media_dir: Path | None = None
    if media_dir is not None:
        resolved_media_dir = Path(media_dir).resolve()
    else:
        candidates = [
            sess_path.parent / f"{sess_path.stem} Project" / "Samples" / "Imported",
            sess_path.parent / "media",
            sess_path.parent / "Audio Files",
            sess_path.parent,
        ]
        for c in candidates:
            if c.is_dir():
                resolved_media_dir = c
                break
        if resolved_media_dir is None:
            resolved_media_dir = sess_path.parent

    # Executar auditoria atraves do oraculo de midia
    aaf_hashes = None
    if sess_path.suffix.lower() == ".aaf":
        import aaf2
        from .aaf_read import _url_para_caminho

        aaf_hashes = {}
        with aaf2.open(str(sess_path), "r") as f:
            for mob in f.content.sourcemobs():
                for descriptor in _iter_descriptors(getattr(mob, "descriptor", None)):
                    locators = descriptor.get("Locator")
                    if locators:
                        for locator in locators.value:
                            url = locator.get("URLString")
                            if url and url.value:
                                candidate = Path(_url_para_caminho(url.value))
                                if not candidate.is_absolute():
                                    candidate = sess_path.parent / candidate
                                if (
                                    not candidate.is_file()
                                    and (resolved_media_dir / candidate.name).is_file()
                                ):
                                    candidate = resolved_media_dir / candidate.name
                                aaf_hashes[str(candidate)] = (
                                    file_sha256(candidate) if candidate.is_file() else "missing"
                                )
    audit = verify_session_media(
        resolved_media_dir, sidecar_path=sidecar_path, media_hashes=aaf_hashes
    )

    if strict and not audit.is_valid:
        raise TurnoverValidationError(
            f"Turnover failed the media verification in strict mode: "
            f"{audit.missing_count} missing, {audit.corrupt_count} corrupt, "
            f"{audit.mismatch_count} with a divergent hash."
        )

    # Copiar sessao para o pacote
    dest_session = pkg_dir / sess_path.name
    shutil.copy2(sess_path, dest_session)

    # Copiar sidecar se existir
    dest_sidecar: Path | None = None
    if sidecar_path is not None:
        dest_sidecar = pkg_dir / sidecar_path.name
        shutil.copy2(sidecar_path, dest_sidecar)

    # Copiar midias para a subpasta media/
    dest_media_dir = pkg_dir / "media"
    dest_media_dir.mkdir(exist_ok=True)
    copied_media = []
    copied_by_name = {}

    for item in audit.results:
        if item.path.exists() and item.path.is_file():
            target_media = dest_media_dir / item.path.name
            key = item.path.name.casefold()
            if key in copied_by_name:
                if file_sha256(copied_by_name[key]) != file_sha256(item.path):
                    raise TurnoverValidationError(f"same-name media differ: {item.path.name}")
                continue
            shutil.copy2(item.path, target_media)
            if item.sha256 and file_sha256(target_media) != item.sha256:
                raise TurnoverValidationError(f"media changed during the copy: {item.path.name}")
            copied_by_name[key] = target_media
            copied_media.append(target_media)

    _relink_session(dest_session, sess_path, pkg_dir, copied_by_name)
    if dest_sidecar is not None:
        data = json.loads(dest_sidecar.read_text(encoding="utf-8"))
        # Modo permissivo não pode transformar um hash divergente em nova
        # referência aprovada ao reescrever o sidecar para caminhos portáteis.
        expected = {r.path.name.casefold(): r.expected_hash for r in audit.results}
        data["media_hashes"] = {
            p.name: expected.get(p.name.casefold()) or file_sha256(p) for p in copied_media
        }
        if data.get("video_path"):
            video_name = Path(data["video_path"]).name.casefold()
            if video_name in copied_by_name:
                data["video_path"] = copied_by_name[video_name].relative_to(pkg_dir).as_posix()
        dest_sidecar.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Construir mapa de arquivos do manifesto (calculando SHA-256 e tamanho exato em disco)
    files_map: dict[str, dict] = {}
    for p in sorted(pkg_dir.rglob("*")):
        if p.is_file():
            rel_name = p.relative_to(pkg_dir).as_posix()
            if rel_name in ("manifest.json", "turnover_report.txt"):
                continue
            h = file_sha256(p)
            files_map[rel_name] = {
                "sha256": h,
                "size_bytes": p.stat().st_size,
            }

    timestamp_iso = datetime.now(timezone.utc).isoformat()
    manifest_data = {
        "generator": "aafton-turnover",
        "format_version": 1,
        "created_at": timestamp_iso,
        "session_name": name,
        "session_file": sess_path.name,
        "sidecar_file": sidecar_path.name if sidecar_path else None,
        "total_files": len(files_map),
        "files": files_map,
        "audit_summary": audit.to_dict(),
    }

    manifest_path = pkg_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Gerar relatorio tecnico turnover_report.txt
    status_label = "STATUS: OK (VALID)" if audit.is_valid else "STATUS: DEGRADED (INVALID)"
    report_lines = [
        "=" * 70,
        "AAFOUT / AAFTON POST-PRODUCTION TURNOVER PACKAGE REPORT",
        "=" * 70,
        f"Session Name:     {name}",
        f"Created At:       {timestamp_iso}",
        f"Session File:     {sess_path.name}",
        f"Sidecar File:     {sidecar_path.name if sidecar_path else 'None'}",
        f"Package Status:   {status_label}",
        "",
        "AUDIT SUMMARY:",
        f"- Total Media Files:  {audit.total_files}",
        f"- OK:                 {audit.ok_count}",
        f"- Missing:            {audit.missing_count}",
        f"- Corrupt:            {audit.corrupt_count}",
        f"- Hash Mismatch:      {audit.mismatch_count}",
        "",
        "FILES IN PACKAGE:",
    ]
    for rel_path, meta in files_map.items():
        report_lines.append(
            f"  - {rel_path} ({meta['size_bytes']} bytes) [SHA-256: {meta['sha256']}]"
        )

    report_lines.extend(
        [
            "",
            "MEDIA VERIFICATION DETAILS:",
        ]
    )
    for r in audit.results:
        err = f" - ERROR: {r.error_message}" if r.error_message else ""
        report_lines.append(
            f"  - {r.path.name}: {r.status.upper()} (SHA-256: {r.sha256 or 'N/A'}){err}"
        )

    report_lines.extend(
        [
            "=" * 70,
            "",
        ]
    )

    report_path = pkg_dir / "turnover_report.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return TurnoverResult(
        package_dir=pkg_dir,
        manifest_path=manifest_path,
        report_path=report_path,
        session_path=dest_session,
        sidecar_path=dest_sidecar,
        media_count=len(copied_media),
        manifest_data=manifest_data,
        is_valid=audit.is_valid,
    )
