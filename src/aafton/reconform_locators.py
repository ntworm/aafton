"""Escrita dos locators de mudancas em uma copia do set do Live."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import locators_write
from .locators_write import LocatorsResult
from .reconform_anchor import resolve_reconform_anchor
from .reconform_diff import ChangeList
from .report import Report
from .sidecar import Anchor
from .timecode import (
    frames_to_label,
    frames_to_samples_exact,
    quantize_samples,
    samples_to_frames_exact,
)


@dataclass(frozen=True)
class _LocatorItem:
    """Item de mudanca preparado para locators_write._insert_sorted."""

    sample_pos: int
    final_name: str
    annotation: str


def _old_cut_start(cl: ChangeList, anchor: Anchor) -> int:
    """Retorna o frame inicial conhecido do corte antigo."""
    candidates: list[int] = [c.old_in for c in cl.changes if c.old_in is not None]
    if cl.time_map is not None:
        candidates.extend(seg.old_in for seg in cl.time_map.segments)
        candidates.extend(item.old_in for item in cl.time_map.saiu)
    if candidates:
        return min(candidates)
    return samples_to_frames_exact(anchor.session_start_samples, cl.rate, anchor.sample_rate)


def _change_old_frame(cl: ChangeList, index: int, anchor: Anchor) -> int:
    """Determina o frame no corte antigo para posicionar o locator da mudanca."""
    change = cl.changes[index]
    if change.old_in is not None:
        return change.old_in
    for following in cl.changes[index + 1 :]:
        if following.old_in is not None:
            return following.old_in
    # Sem mudanca seguinte, o material novo entra depois da ultima anterior.
    for previous in reversed(cl.changes[:index]):
        if previous.old_out is not None:
            return previous.old_out
    return _old_cut_start(cl, anchor)


def _build_locator_items(
    cl: ChangeList,
    anchor: Anchor,
    report: Report,
) -> tuple[list[_LocatorItem], list[dict]]:
    """Converte as mudancas em locators no corte antigo e pula posicoes antes da origem.

    O 'shift' nao vira marcador: ele esta na change list, mas nao e um evento
    do set. O 'unresolved' tambem nao vira: o trecho sem correspondencia nao
    e um evento do set, e o aviso RECONFORM_REUSE_UNRESOLVED e que o lista.
    A copia aplicada tambem nao marca 'shift'.
    """
    to_add: list[_LocatorItem] = []
    skipped: list[dict] = []
    for idx, change in enumerate(cl.changes):
        if change.kind in ("shift", "unresolved"):
            continue
        old_frame = _change_old_frame(cl, idx, anchor)
        old_tc = frames_to_label(old_frame, cl.rate)
        exact = frames_to_samples_exact(old_frame, cl.rate, anchor.sample_rate)
        sample_pos, residue = quantize_samples(exact)
        if residue != 0:
            report.info(
                "RECONFORM_SAMPLE_QUANTIZED",
                f"frame {old_frame} quantized by {residue} samples at {anchor.sample_rate} Hz",
                {"frame": old_frame, "residue": str(residue)},
            )
        name = f"RC {change.kind} {old_tc}"
        if sample_pos < anchor.live_origin_samples:
            ctx = {
                "kind": change.kind,
                "old_tc": old_tc,
                "sample_pos": sample_pos,
                "live_origin_samples": anchor.live_origin_samples,
            }
            report.warning(
                "RECONFORM_LOCATOR_BEFORE_ORIGIN",
                f"locator {name!r} falls before the set origin and was skipped",
                ctx,
            )
            skipped.append(
                {
                    "kind": change.kind,
                    "old_tc": old_tc,
                    "code": "RECONFORM_LOCATOR_BEFORE_ORIGIN",
                }
            )
            continue
        item = _LocatorItem(sample_pos=sample_pos, final_name=name, annotation=change.detail)
        to_add.append(item)
    return to_add, skipped


def _prepare_set(
    set_p: Path,
    cl: ChangeList,
    report: Report,
    start_tc: str | None,
) -> tuple[ET.Element, Anchor, ET.Element] | None:
    """Carrega o set, localiza o container de locators e resolve a ancora."""
    root = locators_write._load_root(set_p, report)
    if root is None:
        return None
    locs_container = root.find(".//LiveSet/Locators/Locators")
    if locs_container is None:
        report.error(
            "NO_LOCATORS",
            "set missing LiveSet/Locators/Locators container",
            {"path": str(set_p)},
        )
        return None
    anchor = resolve_reconform_anchor(set_p, root, report, cl.rate, start_tc=start_tc)
    if anchor is None:
        return None
    report.metadata["anchor"] = locators_write._anchor_summary(anchor)
    return root, anchor, locs_container


def write_change_locators(
    set_path: str | Path,
    cl: ChangeList,
    report: Report,
    *,
    output: str | Path | None = None,
    start_tc: str | None = None,
) -> LocatorsResult:
    """Escreve locators de reconformacao em uma copia do set nas posicoes antigas."""
    set_p = Path(set_path)
    if output is not None:
        target = Path(output)
    else:
        target = set_p.parent / f"{set_p.stem} reconform locators.als"
    out_p = locators_write._validate_output_path(set_p, target, report)
    prepared = _prepare_set(set_p, cl, report, start_tc) if out_p is not None else None
    if out_p is None or prepared is None:
        return LocatorsResult(report=report, output=out_p, written=False, added=0)
    root, anchor, locs_container = prepared
    to_add, skipped = _build_locator_items(cl, anchor, report)
    proto = locators_write._get_prototype(root, report)
    if proto is None:
        return LocatorsResult(report=report, output=out_p, written=False, added=0, skipped=skipped)
    before = locators_write._document_problems(root)
    locators_write._insert_sorted(locs_container, proto, to_add, anchor)
    if not locators_write._save_set_atomic(root, out_p, set_p, report, before):
        return LocatorsResult(report=report, output=out_p, written=False, added=0, skipped=skipped)
    return LocatorsResult(
        report=report,
        output=out_p,
        written=True,
        added=len(to_add),
        skipped=skipped,
    )
