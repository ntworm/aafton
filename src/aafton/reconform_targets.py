"""Indexacao dos AutomationTargets das tracks do Live.

Fica em um modulo proprio para que o verify do reconform possa ler os alvos
de automacao sem carregar a reconformacao, que depende do mapa de tempo.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

_PathKey = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _TargetInfo:
    """Metadados de um AutomationTarget indexado dentro de uma track."""

    target_id: str
    path: _PathKey
    param_name: str
    is_speaker: bool


def _walk_targets(
    node: ET.Element,
    path: _PathKey,
    parent_tag: str,
    by_id: dict[str, _TargetInfo],
    by_path: dict[_PathKey, str],
) -> None:
    """Indexa AutomationTargets por Id e por caminho estrutural relativo a track."""
    if node.tag == "AutomationTarget":
        tid = node.get("Id", "")
        if tid:
            tags = tuple(tag for tag, _ in path)
            is_speaker = tags == ("DeviceChain", "Mixer", "Speaker", "AutomationTarget")
            by_id[tid] = _TargetInfo(tid, path, parent_tag or "AutomationTarget", is_speaker)
            by_path[path] = tid
        return
    if node.tag in ("AutomationEnvelopes", "MainSequencer", "ClipSlotList"):
        return
    counts: dict[str, int] = {}
    for child in node:
        idx = counts.get(child.tag, 0)
        counts[child.tag] = idx + 1
        _walk_targets(child, (*path, (child.tag, idx)), node.tag, by_id, by_path)


def _index_targets(track_node: ET.Element) -> tuple[dict[str, _TargetInfo], dict[_PathKey, str]]:
    """Constroi os mapas de AutomationTarget de uma track."""
    by_id: dict[str, _TargetInfo] = {}
    by_path: dict[_PathKey, str] = {}
    _walk_targets(track_node, (), "", by_id, by_path)
    return by_id, by_path
