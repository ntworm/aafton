"""Testes do remapeamento dos alvos dos ClipEnvelopes levados para as tracks [saiu]."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_paths import TRACK_FIELDS
from aafton.als_write_base import SENTINEL_TIME
from aafton.als_xml import (
    classify_id_tags,
    format_value,
    get_value,
    load_als,
    save_als,
    validate_document,
)
from aafton.reconform_apply import apply_reconform
from aafton.reconform_diff import diff_cuts
from aafton.report import Report
from tests.unit.test_reconform_tracks import _delete_mid_cuts, _make_set, _track_spec

_EVENTS = TRACK_FIELDS["arrangement_events"]


def _attach_clip_envelope(clip_node: ET.Element, target_id: str) -> None:
    """Anexa ao clip um ClipEnvelope que aponta para o alvo informado."""
    outer = clip_node.find("Envelopes")
    if outer is None:
        outer = ET.SubElement(clip_node, "Envelopes")
    inner = outer.find("Envelopes")
    if inner is None:
        inner = ET.SubElement(outer, "Envelopes")
    env = ET.SubElement(inner, "ClipEnvelope", {"Id": str(len(list(inner)))})
    ET.SubElement(ET.SubElement(env, "EnvelopeTarget"), "PointeeId", {"Value": target_id})
    events = ET.SubElement(ET.SubElement(env, "Automation"), "Events")
    ET.SubElement(
        events,
        "FloatEvent",
        {"Id": "0", "Time": format_value(SENTINEL_TIME), "Value": "0.5"},
    )
    ET.SubElement(events, "FloatEvent", {"Id": "1", "Time": "0", "Value": "0.5"})


def _volume_target(track_node: ET.Element) -> str:
    """Id do AutomationTarget do volume da track."""
    alvo = track_node.find("DeviceChain/Mixer/Volume/AutomationTarget")
    assert alvo is not None
    return alvo.get("Id", "")


def _clip_target(clip_node: ET.Element) -> str | None:
    """Id do alvo apontado pelo ClipEnvelope do clip."""
    env = clip_node.find("Envelopes/Envelopes/ClipEnvelope")
    if env is None:
        return None
    ponteiro = env.find("EnvelopeTarget/PointeeId")
    return None if ponteiro is None else ponteiro.get("Value")


def test_clip_saiu_aponta_para_o_alvo_da_track_copiada(tmp_path: Path) -> None:
    """O clip que vai para [saiu] aponta para o alvo da copia; o que fica, para o da origem."""
    path = _make_set(tmp_path, [_track_spec(0, "Dialog", (0, 75))], bpm=60.0)
    root = load_als(path)
    source = root.find("./LiveSet/Tracks/AudioTrack")
    assert source is not None
    volume_id = _volume_target(source)
    clip = source.find(f"{_EVENTS}/AudioClip")
    assert clip is not None
    _attach_clip_envelope(clip, volume_id)
    save_als(root, path)

    old, new = _delete_mid_cuts(75)
    cl = diff_cuts(old, new, Report())
    assert cl is not None and cl.time_map is not None

    report = Report(strict=False)
    res = apply_reconform(path, cl, report, output=tmp_path / "reconformed.als")
    assert res.written is True
    assert res.output is not None

    out_root = load_als(res.output)
    tracks = out_root.findall("./LiveSet/Tracks/AudioTrack")
    assert [get_value(t, TRACK_FIELDS["name"]) for t in tracks] == ["Dialog", "Dialog [saiu]"]
    source_out, saiu_out = tracks

    source_clips = source_out.findall(f"{_EVENTS}/AudioClip")
    saiu_clips = saiu_out.findall(f"{_EVENTS}/AudioClip")
    assert len(source_clips) == 2
    assert len(saiu_clips) == 1

    assert _volume_target(saiu_out) != _volume_target(source_out)
    assert _clip_target(saiu_clips[0]) == _volume_target(saiu_out)
    assert [_clip_target(clip) for clip in source_clips] == [_volume_target(source_out)] * 2
    assert not [i for i in report.issues if i.code == "RECONFORM_CLIP_ENVELOPE_TARGET"]

    pos_tags, _ = classify_id_tags(out_root)
    validate_document(out_root, pos_tags)
