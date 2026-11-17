"""Aplicacao ponta a ponta de uma ChangeList sobre copia de um Live set."""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from . import locators_write
from .als_paths import TRACK_FIELDS
from .als_xml import IdAllocator, get_value
from .reconform_anchor import resolve_reconform_anchor
from .reconform_apply_locators import (
    _is_fixed_locator,
    _locator_abs_samples,
    _saiu_splice_point,
    shift_locators,
)
from .reconform_automation import _TRACK_TAGS, _saiu_splice_sample, shift_track_automation
from .reconform_automation_points import _parse_events
from .reconform_clips import _read_clip_range, _track_label, reconform_clips
from .reconform_diff import ChangeList
from .reconform_saiu_targets import remap_saiu_clip_envelopes
from .reconform_samples import SampleMap
from .reconform_tracks import SaiuTracks, place_saiu_pieces
from .report import Report
from .sidecar import Anchor, sidecar_path_for, write_sidecar

# Caminho das AudioTracks no LiveSet, na ordem em que o Live as le.
_TRACKS_XML = "./LiveSet/Tracks/AudioTrack"


@dataclass(frozen=True)
class ApplyResult:
    """Resultado da aplicacao da reconformacao em uma copia do set."""

    output: Path | None
    written: bool
    clips_moved: int = 0
    clips_sliced: int = 0
    saiu_tracks: int = 0
    locators_added: int = 0


def _clip_goes_before_origin(
    clip_node: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
) -> bool:
    """Verdadeiro se alguma fatia do AudioClip cair antes da origem."""
    bounds = _read_clip_range(clip_node, anchor)
    if bounds is None:
        return False
    abs_start, abs_end = bounds
    if abs_end <= abs_start:
        return False
    for sp_in, _sp_out, kind, val in smap.spans(abs_start, abs_end):
        dest = sp_in + val if kind == "map" else val
        if dest < anchor.live_origin_samples:
            return True
    return False


def _check_clips_before_origin(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> None:
    """Recusa clips do arranjo cujo destino caia antes da origem do Live."""
    for track_node in root.findall("./LiveSet/Tracks/AudioTrack"):
        events = track_node.find(TRACK_FIELDS["arrangement_events"])
        if events is None:
            continue
        t_name = _track_label(track_node)
        for clip_node in events.findall("AudioClip"):
            if _clip_goes_before_origin(clip_node, anchor, smap):
                report.error(
                    "RECONFORM_BEFORE_ORIGIN",
                    "the reconformed clip falls before the origin",
                    {"track": t_name, "clip_id": clip_node.get("Id")},
                )
                return


def _envelope_goes_before_origin(
    events_box: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
) -> bool:
    """Verdadeiro se algum ponto nao-sentinela ou fronteira cair antes da origem."""
    _, points = _parse_events(events_box, anchor)
    if not points:
        return False
    origin = anchor.live_origin_samples
    for pt in points:
        mapped = smap.map_sample(pt.abs_samples)
        dest = mapped if mapped is not None else _saiu_splice_sample(smap, pt.abs_samples)
        if dest < origin:
            return True
    if points[-1].abs_samples > points[0].abs_samples:
        for lo, _hi, kind, val in smap.spans(points[0].abs_samples, points[-1].abs_samples):
            dest = lo + val if kind == "map" else val
            if dest < origin:
                return True
    return False


def _check_envelopes_before_origin(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> None:
    """Recusa envelopes com pontos nao-sentinela mapeados antes da origem."""
    tracks = [node for tag in _TRACK_TAGS for node in root.iter(tag)]
    for track_node in tracks:
        for env in track_node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
            events_box = env.find("Automation/Events")
            if events_box is not None and _envelope_goes_before_origin(events_box, anchor, smap):
                t_name = _track_label(track_node)
                report.error(
                    "RECONFORM_BEFORE_ORIGIN",
                    f"automation in track {t_name!r} falls before origin",
                    {"track": t_name},
                )
                return


def _locator_dest_samples(
    loc: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
) -> tuple[str, int] | None:
    """Nome e posicao final em samples de um locator de usuario, ou None."""
    name = get_value(loc, "Name") or ""
    if _is_fixed_locator(loc, name):
        return None
    abs_samples = _locator_abs_samples(loc, anchor)
    if abs_samples is None:
        return None
    mapped = smap.map_sample(abs_samples)
    dest = mapped if mapped is not None else _saiu_splice_point(smap, abs_samples)
    if dest is None:
        return None
    return name, dest


def _check_locators_before_origin(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> None:
    """Recusa locators de usuario que mapeiem antes da origem do Live."""
    locs_container = root.find(".//LiveSet/Locators/Locators")
    if locs_container is None:
        return
    for loc in locs_container.findall("Locator"):
        info = _locator_dest_samples(loc, anchor, smap)
        if info is None:
            continue
        name, dest = info
        if dest < anchor.live_origin_samples:
            report.error(
                "RECONFORM_BEFORE_ORIGIN",
                f"locator {name!r} maps before the set origin",
                {"locator": name, "dest_samples": dest},
            )
            return


def _check_before_origin(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    report: Report,
) -> bool:
    """Executa a guarda antes da origem em clips, envelopes e locators."""
    _check_clips_before_origin(root, anchor, smap, report)
    _check_envelopes_before_origin(root, anchor, smap, report)
    _check_locators_before_origin(root, anchor, smap, report)
    return not report.incomplete


def _compute_clip_ranges(root: ET.Element, anchor: Anchor) -> list[dict]:
    """Recalcula clip_ranges a partir dos AudioClips escritos em ordem de track."""
    out: list[dict] = []
    for track_node in root.findall("./LiveSet/Tracks/AudioTrack"):
        events = track_node.find(TRACK_FIELDS["arrangement_events"])
        pairs: set[tuple[int, int]] = set()
        if events is not None:
            for clip_node in events.findall("AudioClip"):
                bounds = _read_clip_range(clip_node, anchor)
                if bounds is not None and bounds[1] > bounds[0]:
                    pairs.add(bounds)
        ranges = [list(r) for r in sorted(pairs)]
        out.append({"track": _track_label(track_node), "ranges": ranges})
    return out


def _bindings_de_origem(anchor: Anchor) -> tuple[list[dict], dict]:
    """Bindings do sidecar de origem e o indice deles pelo Id do Live."""
    bindings = [b for b in (anchor.track_bindings or []) if isinstance(b, dict)]
    por_id = {b.get("live_track_id"): b for b in bindings if b.get("live_track_id")}
    return bindings, por_id


def _canais_da_origem(anchor: Anchor, nome: str) -> int | None:
    """Canais de uma track de origem, pelo sidecar do set de origem."""
    canais = (anchor.track_channels or {}).get(nome)
    if canais is not None:
        return int(canais)
    for binding in anchor.track_bindings or ():
        if isinstance(binding, dict) and binding.get("track_name") == nome:
            return binding.get("channel_count")
    return None


def _indice_da_track(mapa_orig: list | None, pos: int | None) -> list | int:
    """Entrada do mapa de origem de uma track: a da posicao, ou a vazia."""
    if mapa_orig is None:
        return []
    if pos is not None and pos < len(mapa_orig):
        return mapa_orig[pos]
    if any(isinstance(v, list) for v in mapa_orig):
        return []
    return -1


def _refaz_mapas_do_sidecar(
    root: ET.Element, anchor: Anchor, saiu_res: SaiuTracks
) -> tuple[list[dict], dict[str, int], list | None]:
    """Refaz os mapas do sidecar para bater com as tracks da copia escrita.

    Cada mapa leva uma entrada por AudioTrack da copia, na ordem do XML.
    """
    tracks = list(root.findall(_TRACKS_XML))
    nomes = {t.get("Id", ""): _track_label(t) for t in tracks}
    origem_de = {id(node): src for src, group in saiu_res.by_source.items() for node in group}
    bindings_orig, por_id = _bindings_de_origem(anchor)
    mapa_orig = anchor.audio_source_indices
    bindings: list[dict] = []
    canais: dict[str, int] = dict(anchor.track_channels or {})
    indices: list = []
    pos = 0
    for idx, node in enumerate(tracks):
        source_id = origem_de.get(id(node))
        if source_id is None:
            original = por_id.get(node.get("Id"))
            if original is None and pos < len(bindings_orig):
                original = bindings_orig[pos]
            entrada = dict(original) if original else {}
            entrada.setdefault("track_name", _track_label(node))
            entrada.setdefault("live_track_id", node.get("Id"))
            indices.append(_indice_da_track(mapa_orig, pos))
            pos += 1
        else:
            nome = nomes.get(source_id, source_id)
            entrada = {
                "track_name": _track_label(node),
                "live_track_id": node.get("Id"),
                "reconform_saiu": True,
                "source_track": nome,
            }
            n_canais = _canais_da_origem(anchor, nome)
            if n_canais is not None:
                entrada["channel_count"] = n_canais
                canais[_track_label(node)] = n_canais
            indices.append(_indice_da_track(mapa_orig, None))
        entrada["index"] = idx
        bindings.append(entrada)
    return bindings, canais, indices if mapa_orig is not None else None


def _build_reconform_anchor(
    root: ET.Element,
    anchor: Anchor,
    saiu_res: SaiuTracks,
    report: Report,
) -> Anchor:
    """Monta a ancora atualizada para o sidecar do set reconformado."""
    if anchor.clip_origins is not None:
        report.info(
            "RECONFORM_SIDECAR_FIELD_DROPPED",
            "clip_origins was dropped from the reconformed sidecar",
            {"field": "clip_origins"},
        )
    bindings, canais, indices = _refaz_mapas_do_sidecar(root, anchor, saiu_res)
    return replace(
        anchor,
        clip_ranges=_compute_clip_ranges(root, anchor),
        clip_origins=None,
        track_channels=canais or None,
        audio_source_indices=indices,
        track_bindings=bindings,
    )


def _fim_do_material(root: ET.Element, anchor: Anchor) -> int:
    """Fim do material do set, em samples absolutos do corte antigo.

    E o material do set que diz ate onde vai a cauda: o mapa de tempo nao
    sabe o tamanho do material depois do ultimo evento do corte antigo.
    """
    fim = 0
    for track_node in root.findall(_TRACKS_XML):
        events = track_node.find(TRACK_FIELDS["arrangement_events"])
        if events is None:
            continue
        for clip_node in events.findall("AudioClip"):
            bounds = _read_clip_range(clip_node, anchor)
            if bounds is not None and bounds[1] > fim:
                fim = bounds[1]
    return fim


def _prepare_apply(
    set_p: Path,
    cl: ChangeList,
    report: Report,
    start_tc: str | None,
) -> tuple[ET.Element, Anchor, SampleMap, IdAllocator, set[str]] | None:
    """Carrega o set, resolve a ancora e valida o limite de origem antes de mutar."""
    assert cl.time_map is not None
    root = locators_write._load_root(set_p, report)
    if root is None:
        return None
    anchor = resolve_reconform_anchor(set_p, root, report, cl.rate, start_tc=start_tc)
    if anchor is None:
        return None
    report.metadata["anchor"] = locators_write._anchor_summary(anchor)
    fim_material = _fim_do_material(root, anchor)
    smap = SampleMap.from_time_map(cl.time_map, anchor.sample_rate, fim_material)
    allocator = IdAllocator.from_root(root)
    before = locators_write._document_problems(root)
    if not _check_before_origin(root, anchor, smap, report):
        return None
    return root, anchor, smap, allocator, before


def _remove_output(out_p: Path) -> None:
    """Apaga a copia gravada e o sidecar copiado depois de uma falha."""
    for caminho in (out_p, sidecar_path_for(out_p)):
        try:
            caminho.unlink(missing_ok=True)
        except OSError:
            pass


def _write_sidecar_atomic(out_p: Path, anchor: Anchor, report: Report) -> bool:
    """Grava o sidecar da copia por um temporario e troca no lugar.

    O sidecar e a ancora da copia: se algo falhar, o .als gravado e o sidecar
    copiado sao apagados, para nao deixar no disco um set sem ancora.
    """
    destino = sidecar_path_for(out_p)
    temp_fd, temp_str = tempfile.mkstemp(
        dir=out_p.parent,
        prefix="aafton_sidecar_",
        suffix=".als",
    )
    os.close(temp_fd)
    temp_als = Path(temp_str)
    temp_sidecar = sidecar_path_for(temp_als)
    try:
        write_sidecar(temp_als, anchor)
        os.replace(temp_sidecar, destino)
    except OSError as exc:
        report.error("WRITE_ERROR", f"could not write sidecar: {exc}")
        _remove_output(out_p)
        return False
    finally:
        for temporario in (temp_als, temp_sidecar):
            try:
                temporario.unlink(missing_ok=True)
            except OSError:
                pass
    return True


def _apply_and_save(
    root: ET.Element,
    anchor: Anchor,
    smap: SampleMap,
    cl: ChangeList,
    allocator: IdAllocator,
    before: set[str],
    set_p: Path,
    out_p: Path,
    report: Report,
) -> ApplyResult:
    """Executa as etapas de mutacao, salva o .als e grava o sidecar atualizado."""
    clips_res = reconform_clips(root, anchor, smap, report, allocator)
    if report.incomplete:
        return ApplyResult(output=out_p, written=False)
    pecas = [replace(p, region_new=smap.saiu_region(p.old_abs_start)) for p in clips_res.saiu]
    saiu_res = place_saiu_pieces(root, anchor, pecas, report, allocator)
    if report.incomplete:
        return ApplyResult(output=out_p, written=False)
    remap_saiu_clip_envelopes(root, saiu_res, report)
    shift_track_automation(root, anchor, smap, saiu_res, report)
    if report.incomplete:
        return ApplyResult(output=out_p, written=False)
    locators_added = shift_locators(root, anchor, smap, cl, report)
    if report.incomplete:
        return ApplyResult(output=out_p, written=False)
    if not locators_write._save_set_atomic(root, out_p, set_p, report, before):
        return ApplyResult(output=out_p, written=False)
    anchor2 = _build_reconform_anchor(root, anchor, saiu_res, report)
    if not _write_sidecar_atomic(out_p, anchor2, report):
        return ApplyResult(output=out_p, written=False)
    return ApplyResult(
        output=out_p,
        written=True,
        clips_moved=clips_res.moved,
        clips_sliced=clips_res.sliced,
        saiu_tracks=len(saiu_res),
        locators_added=locators_added,
    )


def apply_reconform(
    set_path: str | Path,
    cl: ChangeList,
    report: Report,
    *,
    output: str | Path | None = None,
    start_tc: str | None = None,
) -> ApplyResult:
    """Aplica a reconformacao em uma copia do set e reescreve seu sidecar."""
    set_p = Path(set_path)
    target = Path(output) if output is not None else set_p.parent / f"{set_p.stem} reconform.als"
    out_p = locators_write._validate_output_path(set_p, target, report)
    if out_p is None:
        return ApplyResult(output=None, written=False)
    if cl.ambiguous or cl.time_map is None:
        report.error(
            "RECONFORM_AMBIGUOUS",
            "ambiguous change list or missing time map cannot be applied",
            {"ambiguous": cl.ambiguous, "has_time_map": cl.time_map is not None},
        )
        return ApplyResult(output=out_p, written=False)
    prepared = _prepare_apply(set_p, cl, report, start_tc)
    if prepared is None:
        return ApplyResult(output=out_p, written=False)
    root, anchor, smap, allocator, before = prepared
    return _apply_and_save(root, anchor, smap, cl, allocator, before, set_p, out_p, report)
