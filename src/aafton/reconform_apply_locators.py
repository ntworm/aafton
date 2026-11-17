"""Ajuste de locators existentes e criacao de marcadores RC na reconformacao."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction

from . import locators_write
from .als_read_clips import _beats_to_samples
from .als_write_base import TIMELINE, AnchorError, to_beats
from .als_xml import get_value, set_value
from .reconform_diff import ChangeList
from .reconform_locator_rules import _is_fixed_locator
from .reconform_samples import SampleMap
from .report import Report
from .sidecar import Anchor
from .timecode import (
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    quantize_samples,
    samples_per_frame,
)


@dataclass(frozen=True)
class _LocatorItem:
    """Item de marcador novo preparado para locators_write._insert_sorted."""

    sample_pos: int
    final_name: str
    annotation: str


def _locator_abs_samples(loc: ET.Element, anchor: Anchor) -> int | None:
    """Posicao absoluta em samples de um Locator pela ancora do set."""
    time_node = loc.find("Time")
    if time_node is None:
        return None
    try:
        time_beats = float(time_node.get("Value", "0"))
    except ValueError:
        return None
    if not math.isfinite(time_beats):
        return None
    return anchor.live_origin_samples + _beats_to_samples(
        time_beats, anchor.sample_rate, anchor.bpm
    )


def _saiu_splice_point(smap: SampleMap, abs_samples: int) -> int | None:
    """Ponto de emenda no corte novo para um sample dentro de um trecho saiu."""
    for item in smap.saiu:
        if item.old_in <= abs_samples < item.old_out:
            return item.splice_new
    return None


def _target_locator_state(
    abs_samples: int,
    name: str,
    smap: SampleMap,
) -> tuple[int | None, str | None]:
    """Calcula nova posicao e eventual sufixo [saiu] para um locator existente."""
    mapped = smap.map_sample(abs_samples)
    if mapped is not None:
        return mapped, None
    splice = _saiu_splice_point(smap, abs_samples)
    if splice is None:
        return None, None
    return splice, f"{name} [saiu]"


def _set_locator_name(loc: ET.Element, name: str) -> None:
    """Atualiza ou cria o subelemento Name do Locator."""
    name_node = loc.find("Name")
    if name_node is None:
        ET.SubElement(loc, "Name", {"Value": name})
    else:
        name_node.set("Value", name)


def _shift_one_locator(
    loc: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> bool:
    """Desloca um locator existente; devolve False se cair antes da origem."""
    name = get_value(loc, "Name") or ""
    if _is_fixed_locator(loc, name):
        return True
    abs_samples = _locator_abs_samples(loc, anchor)
    if abs_samples is None:
        return True
    new_abs, new_name = _target_locator_state(abs_samples, name, smap)
    if new_abs is None:
        return True
    try:
        new_beats = to_beats(
            new_abs, anchor.live_origin_samples, anchor.sample_rate, anchor.bpm, TIMELINE
        )
    except AnchorError:
        report.error(
            "RECONFORM_BEFORE_ORIGIN",
            f"locator {name!r} maps before the set origin",
            {
                "locator": name,
                "abs_samples": new_abs,
                "live_origin_samples": anchor.live_origin_samples,
            },
        )
        return False
    set_value(loc, "Time", new_beats)
    if new_name is not None:
        _set_locator_name(loc, new_name)
    return True


def _shift_existing_locators(
    locs_container: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> bool:
    """Atualiza os locators de usuario existentes segundo smap."""
    for loc in locs_container.findall("Locator"):
        if not _shift_one_locator(loc, anchor, smap, report):
            return False
    return True


def _frame_sample_pos(
    frame: int,
    rate: TimecodeRate,
    anchor: Anchor,
    report: Report,
) -> int:
    """Converte frame para sample inteiro, registrando eventual residuo."""
    exact = frames_to_samples_exact(frame, rate, anchor.sample_rate)
    sample_pos, residue = quantize_samples(exact)
    if residue != 0:
        report.info(
            "RECONFORM_SAMPLE_QUANTIZED",
            f"frame {frame} quantized by {residue} samples at {anchor.sample_rate} Hz",
            {"frame": frame, "residue": str(residue)},
        )
    return sample_pos


def _append_rc_item(
    out: list[_LocatorItem],
    frame: int,
    name: str,
    annotation: str,
    rate: TimecodeRate,
    anchor: Anchor,
    report: Report,
) -> None:
    """Adiciona marcador RC novo ou registra aviso se cair antes da origem."""
    sample_pos = _frame_sample_pos(frame, rate, anchor, report)
    if sample_pos < anchor.live_origin_samples:
        report.warning(
            "RECONFORM_LOCATOR_BEFORE_ORIGIN",
            f"locator {name!r} falls before the set origin and was skipped",
            {
                "name": name,
                "sample_pos": sample_pos,
                "live_origin_samples": anchor.live_origin_samples,
            },
        )
        return
    out.append(_LocatorItem(sample_pos=sample_pos, final_name=name, annotation=annotation))


def _append_change_locators(
    out: list[_LocatorItem],
    cl: ChangeList,
    anchor: Anchor,
    report: Report,
) -> None:
    """Adiciona marcadores RC para mudancas nao-shift com posicao no corte novo."""
    for change in cl.changes:
        if change.kind in ("shift", "ambiguous") or change.new_in is None:
            continue
        new_tc = frames_to_label(change.new_in, cl.rate)
        name = f"RC {change.kind} {new_tc}"
        _append_rc_item(out, change.new_in, name, change.detail, cl.rate, anchor, report)


def _frames_do_material(samples: int, rate: TimecodeRate, sample_rate: int) -> int:
    """Frames inteiros que cobrem o material medido em samples.

    O arredondamento e para cima: um material que nao completa um frame ainda
    tira um frame do set, e reportar zero frames esconderia a remocao.
    """
    frames = Fraction(samples) / samples_per_frame(rate, sample_rate)
    return math.ceil(frames)


def _detalhe_da_cauda(smap: SampleMap, cl: ChangeList, anchor: Anchor) -> str | None:
    """Detalhe da remocao da cauda: o material real do set, em frames.

    None quando o set nao tem material depois do ultimo evento do corte
    antigo: a cauda nao remove material nenhum do set. O material e medido
    em frames inteiros, arredondados para cima.
    """
    cauda = smap.cauda_material()
    if cauda is None:
        return None
    frames = _frames_do_material(cauda[2] - cauda[1], cl.rate, anchor.sample_rate)
    return f"deleted {frames} frames"


def _append_saiu_locators(
    out: list[_LocatorItem],
    cl: ChangeList,
    smap: SampleMap,
    anchor: Anchor,
    report: Report,
) -> None:
    """Adiciona marcadores 'RC delete <old TC>' na emenda de cada trecho removido."""
    if cl.time_map is None:
        return
    details = {(c.old_in, c.old_out): c.detail for c in cl.changes if c.kind == "delete"}
    for item in cl.time_map.saiu:
        if item.cauda:
            detail = _detalhe_da_cauda(smap, cl, anchor)
            if detail is None:
                continue
        else:
            detail = details.get(
                (item.old_in, item.old_out),
                f"deleted {item.old_out - item.old_in} frames",
            )
        old_tc = frames_to_label(item.old_in, cl.rate)
        _append_rc_item(
            out,
            item.splice_new,
            f"RC delete {old_tc}",
            detail,
            cl.rate,
            anchor,
            report,
        )


def _append_lacuna_locators(
    out: list[_LocatorItem],
    cl: ChangeList,
    anchor: Anchor,
    report: Report,
) -> None:
    """Adiciona marcadores 'RC lacuna <new TC>' no inicio e fim de cada lacuna."""
    if cl.time_map is None:
        return
    for lac in cl.time_map.lacunas:
        detail = f"gap {lac.new_out - lac.new_in} frames"
        start_tc = frames_to_label(lac.new_in, cl.rate)
        end_tc = frames_to_label(lac.new_out, cl.rate)
        _append_rc_item(out, lac.new_in, f"RC gap {start_tc}", detail, cl.rate, anchor, report)
        _append_rc_item(out, lac.new_out, f"RC gap {end_tc}", detail, cl.rate, anchor, report)


def _build_new_locators(
    cl: ChangeList,
    smap: SampleMap,
    anchor: Anchor,
    report: Report,
) -> list[_LocatorItem]:
    """Monta a lista de marcadores RC novos para insercao no set reconformado."""
    out: list[_LocatorItem] = []
    _append_change_locators(out, cl, anchor, report)
    _append_saiu_locators(out, cl, smap, anchor, report)
    _append_lacuna_locators(out, cl, anchor, report)
    return out


def shift_locators(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    cl: ChangeList,
    report: Report,
) -> int:
    """Desloca locators existentes e adiciona marcadores RC nas posicoes novas."""
    locs_container = root.find(".//LiveSet/Locators/Locators")
    if locs_container is None:
        report.error("NO_LOCATORS", "set missing LiveSet/Locators/Locators container")
        return 0
    proto = locators_write._get_prototype(root, report)
    if proto is None:
        return 0
    if not _shift_existing_locators(locs_container, anchor, smap, report):
        return 0
    to_add = _build_new_locators(cl, smap, anchor, report)
    locators_write._insert_sorted(locs_container, proto, to_add, anchor)
    return len(to_add)
