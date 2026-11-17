"""Remapeamento dos alvos de automacao dos clips levados para as tracks [saiu].

A track '<nome> [saiu]' e uma copia da track de origem e cada AutomationTarget
da copia tem Id proprio. O ClipEnvelope que veio junto com o clip continua
apontando para o alvo da track de origem; aqui o ponteiro passa a apontar para
o alvo da copia, pelo mesmo caminho estrutural.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .als_paths import TRACK_FIELDS
from .reconform_automation import _index_targets
from .reconform_tracks import SaiuTracks, _track_label
from .report import Report

_CLIP_ENVELOPES = "Envelopes/Envelopes/ClipEnvelope"
_POINTEES = "EnvelopeTarget/PointeeId"


def _source_track(root: ET.Element, source_id: str) -> ET.Element | None:
    """Acha a AudioTrack de origem com o Id informado."""
    for node in root.iter("AudioTrack"):
        if node.get("Id") == source_id:
            return node
    return None


def _target_map(
    source_node: ET.Element,
    saiu_node: ET.Element,
) -> tuple[dict[str, str], set[str]]:
    """Mapa do Id do alvo na origem para o Id do alvo na copia [saiu].

    O caminho estrutural e o mesmo nas duas tracks: a copia tem os mesmos
    parametros, com Id proprio. Devolve tambem os Ids do Speaker da origem, que
    ficam de fora do mapa porque a automacao do Speaker nao e levada para a
    track [saiu].
    """
    by_id, by_path = _index_targets(source_node)
    _, saiu_by_path = _index_targets(saiu_node)
    mapa: dict[str, str] = {}
    speakers: set[str] = set()
    for path, source_id in by_path.items():
        info = by_id.get(source_id)
        if info is not None and info.is_speaker:
            speakers.add(source_id)
            continue
        saiu_id = saiu_by_path.get(path)
        if saiu_id is not None:
            mapa[source_id] = saiu_id
    return mapa, speakers


def _remap_clip(
    clip_node: ET.Element,
    mapa: dict[str, str],
    speakers: set[str],
    report: Report,
    label: str,
) -> int:
    """Aponta os ClipEnvelopes de um clip para os alvos da track [saiu]."""
    total = 0
    for env in clip_node.findall(_CLIP_ENVELOPES):
        ponteiro = env.find(_POINTEES)
        if ponteiro is None:
            continue
        old = ponteiro.get("Value", "")
        new = mapa.get(old)
        if new is None:
            report.warning(
                "RECONFORM_CLIP_ENVELOPE_TARGET",
                f"clip envelope in track {label!r} was left pointing at the target {old!r}",
                {
                    "track": label,
                    "target": old,
                    "reason": "speaker" if old in speakers else "missing",
                    "clip_id": clip_node.get("Id"),
                },
            )
            continue
        if new == old:
            continue
        ponteiro.set("Value", new)
        total += 1
    return total


def _remap_track(
    saiu_node: ET.Element,
    mapa: dict[str, str],
    speakers: set[str],
    report: Report,
) -> int:
    """Aponta os ClipEnvelopes de todos os clips de uma track [saiu]."""
    events = saiu_node.find(TRACK_FIELDS["arrangement_events"])
    if events is None:
        return 0
    label = _track_label(saiu_node)
    return sum(
        _remap_clip(clip, mapa, speakers, report, label) for clip in events.findall("AudioClip")
    )


def remap_saiu_clip_envelopes(
    root: ET.Element,
    saiu_tracks: SaiuTracks,
    report: Report,
) -> int:
    """Aponta os ClipEnvelopes dos clips [saiu] para os alvos da track copiada.

    Devolve quantos ponteiros foram reescritos. As pecas que ficam na track de
    origem nao sao tocadas: os envelopes delas ja apontam para os alvos da
    propria track.
    """
    total = 0
    for source_id, saiu_nodes in saiu_tracks.by_source.items():
        source_node = _source_track(root, source_id)
        if source_node is None:
            continue
        for saiu_node in saiu_nodes:
            mapa, speakers = _target_map(source_node, saiu_node)
            total += _remap_track(saiu_node, mapa, speakers, report)
    return total
