"""Coleta de midia de sets do Ableton Live (.als) e de arquivos AAF linkados.

Expoe ``plan_collect``, ``run_collect`` e ``collect_into_output``, alem das
estruturas de plano, item e relatorio (``CollectReport`` com ``to_markdown`` e
``to_json``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .collect_aaf import rewrite_aaf_locators, scan_aaf
from .collect_als import rewrite_als_xml, scan_als
from .collect_copy import (
    CollectError,
    ProgressCallback,
    assign_dest_names,
    copy_file_verified,
    copy_into_existing_media_dir,
    norm_path_key,
    rewrite_sidecar_data,
)
from .sidecar import sidecar_path_for

ITEM_STATES = (
    "copied",
    "already_there",
    "missing",
    "library",
    "video_skipped",
    "embedded",
)
_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(kw_only=True)
class CollectPlanItem:
    """Item descoberto no planejamento antes da copia."""

    source: str
    resolved_key: str
    kind: str  # "audio" ou "video"
    state: str  # "ready", "missing", "library", "embedded"
    size_bytes: int


@dataclass(kw_only=True)
class CollectPlan:
    """Plano de coleta lido de um .als ou .aaf."""

    source_path: Path
    source_format: str  # "als" ou "aaf"
    items: list[CollectPlanItem] = field(default_factory=list)
    history_kept: int = 0
    has_sidecar: bool = False


@dataclass(kw_only=True)
class CollectReportItem:
    """Resultado de um arquivo de midia no relatorio do collect."""

    source: str
    destination: str | None
    kind: str  # "audio" ou "video"
    size_bytes: int
    sha256: str | None
    state: str  # um de ITEM_STATES


@dataclass(kw_only=True)
class CollectReport:
    """Relatorio completo da coleta, serializavel em Markdown/texto e JSON."""

    source_path: str
    output_path: str
    media_dir: str
    items: list[CollectReportItem] = field(default_factory=list)
    history_kept: int = 0
    total_bytes: int = 0

    def count(self, state: str) -> int:
        """Quantidade de itens num dado estado."""
        return sum(1 for item in self.items if item.state == state)

    @property
    def counts(self) -> dict[str, int]:
        """Contagem de itens por estado mais history_kept."""
        result = {state: self.count(state) for state in ITEM_STATES}
        result["history_kept"] = self.history_kept
        return result

    @property
    def missing_count(self) -> int:
        return self.count("missing")

    def to_dict(self) -> dict:
        """Dicionario estruturado do relatorio."""
        return {
            "source_path": self.source_path,
            "output_path": self.output_path,
            "media_dir": self.media_dir,
            "total_bytes": self.total_bytes,
            "history_kept": self.history_kept,
            "counts": self.counts,
            "items": [asdict(item) for item in self.items],
        }

    def to_json(self) -> str:
        """Serializacao JSON do relatorio."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """Relatorio legivel em Markdown/texto para collect_report.txt e Studio."""
        summary = ", ".join(
            f"{label}: {self.count(key)}"
            for label, key in (
                ("Copied", "copied"),
                ("Already there", "already_there"),
                ("Missing", "missing"),
                ("Library", "library"),
                ("Video skipped", "video_skipped"),
                ("Embedded", "embedded"),
            )
        )
        lines = [
            "# Collect media report",
            "",
            f"- **Source**: {self.source_path}",
            f"- **Output**: {self.output_path}",
            f"- **Media folder**: {self.media_dir}",
            f"- **Total bytes**: {self.total_bytes}",
            f"- **History kept**: {self.history_kept}",
            "",
            summary,
            "",
            "| State | Kind | Source | Destination | Size | SHA-256 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for item in self.items:
            dest = item.destination or "-"
            sha = item.sha256 or "-"
            lines.append(
                f"| {item.state} | {item.kind} | {_esc(item.source)} "
                f"| {_esc(dest)} | {item.size_bytes} | {sha} |"
            )
        return "\n".join(lines) + "\n"


def _esc(text: str) -> str:
    return text.replace("|", r"\|").replace("\r", " ").replace("\n", " ")


@dataclass(kw_only=True)
class CollectResult:
    """Resultado devolvido por run_collect e collect_into_output."""

    dest_dir: Path
    session_path: Path
    media_dir: Path
    sidecar_path: Path | None
    report_path: Path | None
    report: CollectReport

    @property
    def exit_code(self) -> int:
        """Codigo de saida da CLI: 0 quando tudo resolveu, 3 quando faltou midia."""
        return 3 if self.report.missing_count > 0 else 0


def _validate_source(source: str | Path) -> tuple[Path, str]:
    path = Path(source).resolve()
    if not path.exists() or not path.is_file():
        raise CollectError(f"Source file not found: {path}")
    suffix = path.suffix.lower()
    if suffix not in (".als", ".aaf"):
        raise CollectError(f"Unsupported format {suffix or '<none>'}: expected .als or .aaf")
    return path, suffix[1:]


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or _INVALID_NAME.search(cleaned) or cleaned in {".", ".."}:
        raise CollectError(f"Invalid session name: {name!r}")
    return cleaned


def plan_collect(source: str | Path) -> CollectPlan:
    """Inspeciona um .als ou .aaf e devolve o CollectPlan sem copiar nada."""
    path, fmt = _validate_source(source)
    if fmt == "als":
        raw_items, history_kept = scan_als(path)
        items = [CollectPlanItem(**asdict(r)) for r in raw_items]
        return CollectPlan(
            source_path=path,
            source_format="als",
            items=items,
            history_kept=history_kept,
            has_sidecar=sidecar_path_for(path).is_file(),
        )
    items = [CollectPlanItem(**asdict(r)) for r in scan_aaf(path)]
    return CollectPlan(source_path=path, source_format="aaf", items=items)


def _check_empty_dest(target: Path) -> None:
    """Exige que o destino nao exista ou seja uma pasta vazia."""
    if target.is_symlink() or target.is_file():
        raise CollectError(f"Destination is not an empty folder: {target}")
    if target.is_dir() and any(target.iterdir()):
        raise CollectError(f"Destination folder is not empty: {target}")


def run_collect(
    plan_or_source: CollectPlan | str | Path,
    dest_dir: str | Path,
    name: str | None = None,
    include_video: bool = True,
    progress: ProgressCallback | None = None,
) -> CollectResult:
    """Executa a coleta para dest_dir via pasta temporaria de staging."""
    plan = (
        plan_or_source if isinstance(plan_or_source, CollectPlan) else plan_collect(plan_or_source)
    )
    target = Path(dest_dir).resolve()
    _check_empty_dest(target)
    stem = _validate_name(name or plan.source_path.stem)
    if stem.lower().endswith(f".{plan.source_format}"):
        stem = _validate_name(stem[: -(len(plan.source_format) + 1)])

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".collect-", dir=target.parent) as tmp_root:
        staging = Path(tmp_root) / "package"
        staging.mkdir(parents=True, exist_ok=True)
        _build_staged_collect(plan, staging, target, stem, include_video, progress)
        if target.is_dir():
            target.rmdir()
        staging.rename(target)

    return _finalize_result(plan, target, stem)


def _build_staged_collect(
    plan: CollectPlan,
    staging: Path,
    final_target: Path,
    stem: str,
    include_video: bool,
    progress: ProgressCallback | None,
) -> None:
    media_folder_name = f"{stem} Media"
    staging_media = staging / media_folder_name
    staging_media.mkdir(parents=True, exist_ok=True)
    final_media = final_target / media_folder_name

    to_copy = [
        item
        for item in plan.items
        if item.state == "ready" and (include_video or item.kind != "video")
    ]
    name_map = assign_dest_names([item.source for item in to_copy])
    total_bytes = sum(item.size_bytes for item in to_copy)
    copied_so_far = 0

    copied_staging: dict[str, tuple[Path, Path, str]] = {}
    for item in to_copy:
        src_path = Path(item.source)
        dest_name = name_map[item.resolved_key]
        stage_file = staging_media / dest_name
        final_file = final_media / dest_name
        sha, written = copy_file_verified(
            src_path,
            stage_file,
            progress=progress,
            copied_so_far=copied_so_far,
            total_bytes=total_bytes,
        )
        copied_so_far += written
        copied_staging[item.resolved_key] = (stage_file, final_file, sha)

    _write_staged_session_and_report(
        plan, staging, final_target, stem, media_folder_name, include_video, copied_staging
    )


def _write_staged_session_and_report(
    plan: CollectPlan,
    staging: Path,
    final_target: Path,
    stem: str,
    media_folder_name: str,
    include_video: bool,
    copied_staging: dict[str, tuple[Path, Path, str]],
) -> None:
    ext = f".{plan.source_format}"
    stage_session = staging / f"{stem}{ext}"
    final_session = final_target / f"{stem}{ext}"
    final_media = final_target / media_folder_name
    final_paths = {k: info[1] for k, info in copied_staging.items()}

    if plan.source_format == "als":
        rewrite_als_xml(plan.source_path, stage_session, media_folder_name, final_paths)
        sidecar_map = {k: (info[1], info[2]) for k, info in copied_staging.items()}
        rewrite_sidecar_data(plan.source_path, stage_session, sidecar_map, None)
    else:
        rewrite_aaf_locators(plan.source_path, stage_session, final_paths)

    report = _build_report(
        plan,
        final_session,
        final_media,
        include_video,
        {k: (info[1], info[2], "copied") for k, info in copied_staging.items()},
    )
    (staging / "collect_report.txt").write_text(report.to_markdown(), encoding="utf-8")
    (staging / ".collect_report.json").write_text(report.to_json(), encoding="utf-8")


def _finalize_result(plan: CollectPlan, target: Path, stem: str) -> CollectResult:
    ext = f".{plan.source_format}"
    session_path = target / f"{stem}{ext}"
    media_dir = target / f"{stem} Media"
    sidecar = sidecar_path_for(session_path)
    sidecar_path = sidecar if sidecar.is_file() else None
    report_path = target / "collect_report.txt"
    meta_json = target / ".collect_report.json"
    data = json.loads(meta_json.read_text(encoding="utf-8"))
    meta_json.unlink(missing_ok=True)
    report = CollectReport(
        source_path=data["source_path"],
        output_path=data["output_path"],
        media_dir=data["media_dir"],
        history_kept=data["history_kept"],
        total_bytes=data["total_bytes"],
        items=[CollectReportItem(**it) for it in data["items"]],
    )
    return CollectResult(
        dest_dir=target,
        session_path=session_path,
        media_dir=media_dir,
        sidecar_path=sidecar_path,
        report_path=report_path,
        report=report,
    )


def _item_to_report_item(
    item: CollectPlanItem,
    include_video: bool,
    resolved_outcomes: dict[str, tuple[Path, str, str]],
) -> tuple[CollectReportItem, int]:
    if item.state != "ready":
        return (
            CollectReportItem(
                source=item.source,
                destination=None,
                kind=item.kind,
                size_bytes=item.size_bytes,
                sha256=None,
                state=item.state,
            ),
            0,
        )
    if not include_video and item.kind == "video":
        return (
            CollectReportItem(
                source=item.source,
                destination=None,
                kind=item.kind,
                size_bytes=item.size_bytes,
                sha256=None,
                state="video_skipped",
            ),
            0,
        )
    dest_path, sha, st = resolved_outcomes[item.resolved_key]
    return (
        CollectReportItem(
            source=item.source,
            destination=str(dest_path),
            kind=item.kind,
            size_bytes=item.size_bytes,
            sha256=sha,
            state=st,
        ),
        item.size_bytes,
    )


def _build_report(
    plan: CollectPlan,
    final_session: Path,
    final_media: Path,
    include_video: bool,
    resolved_outcomes: dict[str, tuple[Path, str, str]],
) -> CollectReport:
    report_items: list[CollectReportItem] = []
    total_bytes = 0
    for item in plan.items:
        rep_item, added_bytes = _item_to_report_item(item, include_video, resolved_outcomes)
        report_items.append(rep_item)
        total_bytes += added_bytes
    return CollectReport(
        source_path=str(plan.source_path),
        output_path=str(final_session),
        media_dir=str(final_media),
        items=report_items,
        history_kept=plan.history_kept,
        total_bytes=total_bytes,
    )


def collect_into_output(
    session_path: str | Path,
    *,
    include_video: bool = True,
    progress: ProgressCallback | None = None,
) -> CollectResult:
    """Coleta a midia de uma saida recem-escrita para '<saida> Media/' ao lado dela."""
    target_session = Path(session_path).resolve()
    plan = plan_collect(target_session)
    media_folder_name = f"{target_session.stem} Media"
    media_dir = target_session.parent / media_folder_name
    media_dir.mkdir(parents=True, exist_ok=True)

    outcomes = copy_into_existing_media_dir(plan.items, media_dir, include_video, progress)
    _atomic_rewrite_in_place(plan, target_session, media_folder_name, outcomes)

    report = _build_report(plan, target_session, media_dir, include_video, outcomes)
    sidecar = sidecar_path_for(target_session)
    return CollectResult(
        dest_dir=target_session.parent,
        session_path=target_session,
        media_dir=media_dir,
        sidecar_path=sidecar if sidecar.is_file() else None,
        report_path=None,
        report=report,
    )


def _atomic_rewrite_in_place(
    plan: CollectPlan,
    target_session: Path,
    media_folder_name: str,
    outcomes: dict[str, tuple[Path, str, str]],
) -> None:
    """Reescreve a sessao e o sidecar em arquivos temporarios e troca com os.replace."""
    parent = target_session.parent
    paths_by_key = {k: info[0] for k, info in outcomes.items()}
    with tempfile.TemporaryDirectory(prefix=".collect-inplace-", dir=parent) as tmp_dir:
        tmp_root = Path(tmp_dir)
        tmp_session = tmp_root / target_session.name
        if plan.source_format == "als":
            rewrite_als_xml(target_session, tmp_session, media_folder_name, paths_by_key)
            sidecar_map = {k: (info[0], info[1]) for k, info in outcomes.items()}
            tmp_sidecar = rewrite_sidecar_data(target_session, tmp_session, sidecar_map, None)
            # A sessao troca primeiro: se ela falhar, o sidecar continua o do
            # export e os dois seguem combinando.
            os.replace(tmp_session, target_session)
            if tmp_sidecar is not None and tmp_sidecar.is_file():
                os.replace(tmp_sidecar, sidecar_path_for(target_session))
        else:
            shutil.copy2(target_session, tmp_session)
            rewrite_aaf_locators(target_session, tmp_session, paths_by_key)
            os.replace(tmp_session, target_session)


__all__ = [
    "CollectError",
    "CollectPlan",
    "CollectPlanItem",
    "CollectReport",
    "CollectReportItem",
    "CollectResult",
    "ITEM_STATES",
    "collect_into_output",
    "norm_path_key",
    "plan_collect",
    "run_collect",
]
