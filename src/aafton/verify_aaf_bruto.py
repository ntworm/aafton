"""Leitura bruta do AAF para o verify sem sidecar: slots, eventos e offsets.

Separado de verify_sem_sidecar, que reexporta estes nomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import aaf2

from .aaf_read import _escolher_composicao_e_taxa, _slots_de_audio
from .aaf_read_components import (
    _descer_ate_sequence,
    _is_fade_source_clip,
    _type_name,
    _unwrap,
    _valor_de_propriedade,
)
from .aaf_read_session import _read_timecode
from .aaf_read_timewarp import _is_time_warp
from .report import Report
from .timecode import TimecodeRate, exact_edit_rate


@dataclass(frozen=True)
class _RawSlot:
    name: str
    edit_rate: float | None
    events: list[list[tuple[int, Fraction]]]


@dataclass(frozen=True)
class _AafInfo:
    frames_inicio: int
    tc_rate: TimecodeRate
    sample_rate: int
    slots: list[_RawSlot]


def _slot_edit_rate(slot) -> float | None:
    er = getattr(slot, "edit_rate", None)
    if not er:
        return None
    if hasattr(er, "num"):
        return er.num / er.den
    return float(er)


def _offset_fonte(sc, session_rate: int) -> tuple[int, Fraction]:
    mob = getattr(sc, "mob", None)
    slot_id = getattr(sc, "slot_id", None)
    mobs_slots = getattr(mob, "slots", None)
    slots = list(mobs_slots) if mobs_slots else []
    target = next((s for s in slots if s.slot_id == slot_id), None)
    raw_rate = getattr(target, "edit_rate", None)
    rate = exact_edit_rate(raw_rate) if raw_rate is not None else Fraction(session_rate)
    return int(getattr(sc, "start", 0) or 0), rate


def _alvo_do_selector(comp):
    """Ramo ativo do Selector, ou Alternate com audio quando selecionado e Filler."""
    selected = _valor_de_propriedade(comp, "Selected")
    if _type_name(selected) != "Filler":
        return comp if selected is None else selected
    alternates = _valor_de_propriedade(comp, "Alternates")
    for alt in list(alternates) if alternates else []:
        internos, _ = _unwrap(alt)
        if any(_type_name(item) == "SourceClip" for item in internos):
            return alt
    return None


def _sequencia_em_grupo(comp):
    """Sequence aninhada dentro de um OperationGroup."""
    if _type_name(comp) != "OperationGroup":
        return None
    segs = getattr(comp, "segments", None)
    for seg in list(segs) if segs else []:
        if _type_name(seg) == "Sequence":
            return seg
    return None


def _coletar_fontes(comp, session_rate: int, out: list, manter_fades: bool) -> None:
    internos, _ = _unwrap(comp)
    fontes = [item for item in internos if _type_name(item) == "SourceClip"]
    if not fontes:
        return
    if manter_fades or not all(_is_fade_source_clip(item) for item in fontes):
        out.append([_offset_fonte(sc, session_rate) for sc in fontes])


def _coletar_sequencia(seq, session_rate: int, out: list, manter_fades: bool) -> None:
    comps = getattr(seq, "components", None)
    for item in list(comps) if comps else []:
        _coletar_componente(item, session_rate, out, manter_fades)


def _coletar_componente(comp, session_rate: int, out: list, manter_fades: bool) -> None:
    tipo = _type_name(comp)
    if tipo in ("Filler", "Transition") or _is_time_warp(comp):
        return
    if tipo == "Sequence":
        _coletar_sequencia(comp, session_rate, out, manter_fades)
        return
    if tipo == "Selector":
        alvo = _alvo_do_selector(comp)
        if alvo is not None:
            _coletar_componente(alvo, session_rate, out, manter_fades)
        return
    seq = _sequencia_em_grupo(comp)
    if seq is not None:
        _coletar_sequencia(seq, session_rate, out, manter_fades)
        return
    _coletar_fontes(comp, session_rate, out, manter_fades)


def _extrair_slot(slot, session_rate: int, manter_fades: bool = False) -> _RawSlot:
    raw_name = getattr(slot, "name", None)
    name = str(raw_name) if raw_name else ""
    segment = getattr(slot, "segment", None)
    seq = _descer_ate_sequence(segment)
    events: list[list[tuple[int, Fraction]]] = []
    if seq is not None:
        _coletar_sequencia(seq, session_rate, events, manter_fades)
    elif segment is not None:
        _coletar_componente(segment, session_rate, events, manter_fades)
    return _RawSlot(name=name, edit_rate=_slot_edit_rate(slot), events=events)


def _ler_aaf_bruto(aaf_path: Path, manter_fades: bool = False) -> _AafInfo | None:
    if aaf_path.suffix.lower() != ".aaf":
        return None
    meta = Report(strict=False)
    with aaf2.open(str(aaf_path), "r") as f:
        comp, sample_rate = _escolher_composicao_e_taxa(f, aaf_path, meta)
        if comp is None:
            return None
        frames, rate = _read_timecode(comp, meta)
        slots = [
            _extrair_slot(slot, sample_rate, manter_fades)
            for _, slot in _slots_de_audio(comp, meta)
        ]
        return _AafInfo(frames, rate, sample_rate, slots)
