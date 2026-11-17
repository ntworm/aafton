"""Leitura da lista de corte de imagem (V1) a partir de um arquivo AAF."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path

import aaf2

from .aaf_read import _escolher_composition
from .aaf_read_components import (
    _caminho_do_locator,
    _contar_source_clips,
    _descer_ate_sequence,
    _descritor_fisico,
    _filhos_do_componente,
    _filhos_do_selector,
    _segmento_do_slot,
    _source_filename,
    _type_name,
    _unwrap,
    _valor_de_propriedade,
)
from .aaf_read_session import _read_timecode
from .aaf_read_tracks import _is_time_warp
from .cut_ir import CutEvent, CutIR, normalize_source
from .report import Report
from .timecode import DROP_FRAME_RATES, TimecodeRate, exact_edit_rate

_SUPPORTED_RATES = frozenset(
    {Fraction(24), Fraction(25), Fraction(30), Fraction(24000, 1001), Fraction(30000, 1001)}
)


@dataclass
class _WalkState:
    """Estado mutavel da leitura sequencial da faixa de imagem V1."""

    rate: TimecodeRate
    start_frames: int
    report: Report
    cursor: int = 0
    events: list[CutEvent] = field(default_factory=list)
    pending_transition: tuple[int, int, int] | None = None
    aborted: bool = False


def read_cut_aaf(path: str | Path, report: Report) -> CutIR | None:
    """Le a faixa principal de video (V1) de um AAF para CutIR."""
    caminho = Path(path)
    with aaf2.open(str(caminho), "r") as f:
        composition = _escolher_composition(f, caminho, report, 48000)
        if composition is None:
            return None
        slot = _escolher_slot_picture(composition)
        if slot is None:
            report.error("RECONFORM_NO_PICTURE_TRACK", "no picture slot found in AAF", {})
            return None
        rate_frac = _validar_taxa_picture(slot, report)
        if rate_frac is None:
            return None
        rate, start_frames = _taxa_e_inicio(composition, rate_frac, report)
        state = _WalkState(rate=rate, start_frames=start_frames, report=report)
        if not _percorrer_slot(state, slot):
            return None
    return CutIR(path=str(caminho), rate=rate, start_frames=start_frames, events=state.events)


def _taxa_e_inicio(composition, rate_frac: Fraction, report: Report) -> tuple[TimecodeRate, int]:
    """Taxa de corte com o drop do timecode e o inicio em frames dessa taxa."""
    tc_start, tc_rate = _read_timecode(composition, report)
    pair = (rate_frac.numerator, rate_frac.denominator)
    drop = bool(tc_rate.drop_frame) and pair in DROP_FRAME_RATES
    rate = TimecodeRate(rate_frac.numerator, rate_frac.denominator, drop)
    if tc_start and tc_rate.fps != rate.fps:
        return rate, round(Fraction(tc_start) * rate.fps / tc_rate.fps)
    return rate, int(tc_start)


def _percorrer_slot(state: _WalkState, slot) -> bool:
    """Percorre a Sequence do slot de imagem; False quando a leitura abortou."""
    seq = _descer_ate_sequence(getattr(slot, "segment", None))
    comps = list(getattr(seq, "components", None) or []) if seq is not None else [slot.segment]
    for componente in comps:
        _processar_componente(state, componente)
        if state.aborted:
            return False
    return True


def _slot_de_picture(slot) -> bool:
    """True quando o TimelineMobSlot aponta um segmento de Picture."""
    if _type_name(slot) != "TimelineMobSlot":
        return False
    seg = getattr(slot, "segment", None)
    if seg is None or _type_name(seg) == "Timecode":
        return False
    mk = getattr(seg, "media_kind", None) or getattr(slot, "media_kind", None)
    if mk is not None and str(mk).lower() == "picture":
        return True
    dd = getattr(seg, "datadef", None) or getattr(slot, "datadef", None)
    return dd is not None and "picture" in str(getattr(dd, "name", "")).lower()


def _escolher_slot_picture(composition):
    """Seleciona o slot V1 de Picture ou o primeiro Picture com SourceClip."""
    candidatos = [s for s in getattr(composition, "slots", []) or [] if _slot_de_picture(s)]
    for slot in candidatos:
        if (getattr(slot, "name", None) or "").strip().casefold() == "v1":
            return slot
    return next((s for s in candidatos if _contar_source_clips(s.segment) > 0), None)


def _validar_taxa_picture(slot, report: Report) -> Fraction | None:
    """Valida a edit_rate do slot de imagem contra as taxas suportadas."""
    raw_rate = getattr(slot, "edit_rate", None)
    try:
        frac = exact_edit_rate(raw_rate) if raw_rate is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        frac = None
    if frac not in _SUPPORTED_RATES:
        report.error(
            "RECONFORM_RATE_UNSUPPORTED",
            f"unsupported picture edit_rate: {raw_rate}",
            {"edit_rate": str(raw_rate)},
        )
        return None
    return frac


def _processar_componente(
    state: _WalkState, componente, fallback_length: int | None = None
) -> None:
    """Processa um componente da Sequence de imagem e atualiza o cursor."""
    if state.aborted or componente is None:
        return
    raw_len = getattr(componente, "length", None)
    if raw_len is not None and (raw_len > 0 or fallback_length is None):
        comprimento = int(raw_len)
    else:
        comprimento = int(fallback_length or 0)
    tipo = _type_name(componente)
    if tipo == "Transition":
        _processar_transition(state, componente, comprimento)
    elif tipo == "Filler":
        _registrar_evento(state, "gap", comprimento)
    elif tipo == "SourceClip":
        _registrar_evento(state, "clip", comprimento, componente)
    else:
        _processar_composto(state, componente, tipo, comprimento)


def _processar_composto(state: _WalkState, componente, tipo: str, comprimento: int) -> None:
    """Desembrulha Sequence, Selector, OperationGroup e NestedScope."""
    if tipo == "Sequence":
        for interno in getattr(componente, "components", None) or []:
            _processar_componente(state, interno)
    elif tipo == "Selector":
        _processar_selector(state, componente, comprimento)
    elif tipo == "OperationGroup":
        _processar_operation_group(state, componente, comprimento)
    elif tipo == "NestedScope":
        slots = list(getattr(componente, "slots", None) or [])
        segs = [_segmento_do_slot(s) for s in slots]
        padrao = segs[0] if segs else None
        alvo = next((seg for seg in segs if _contar_source_clips(seg) > 0), padrao)
        _processar_componente(state, alvo, fallback_length=comprimento)
    else:
        _registrar_evento(state, "gap", comprimento)


def _processar_selector(state: _WalkState, componente, comprimento: int) -> None:
    """Le o segmento selecionado de um Selector."""
    selected = _valor_de_propriedade(componente, "Selected")
    if selected is None:
        filhos = _filhos_do_selector(componente)
        selected = filhos[0] if filhos else None
    if selected is None:
        _registrar_evento(state, "gap", comprimento)
        return
    _processar_componente(state, selected, fallback_length=comprimento)


def _processar_operation_group(state: _WalkState, componente, comprimento: int) -> None:
    """Rejeita TimeWarp e desembrulha demais OperationGroups."""
    if _is_time_warp(componente):
        state.report.error(
            "RECONFORM_TIMEWARP",
            "picture track contains a TimeWarp effect, which is not supported",
            {"cursor_frames": state.cursor},
        )
        state.aborted = True
        return
    internos, _ = _unwrap(componente)
    primeiro = internos[0] if internos else None
    if primeiro is not None and _type_name(primeiro) in ("Sequence", "Selector", "NestedScope"):
        _processar_componente(state, primeiro, fallback_length=comprimento)
        return
    clip = next((item for item in internos if _type_name(item) == "SourceClip"), None)
    _registrar_evento(state, "clip" if clip is not None else "gap", comprimento, clip)


def _processar_transition(state: _WalkState, componente, comprimento: int) -> None:
    """Recua o cursor pela sobreposicao e apara o evento anterior ao cut point."""
    raw_cp = getattr(componente, "cutpoint", None)
    if raw_cp is None:
        raw_cp = _valor_de_propriedade(componente, "CutPoint")
    cutpoint = int(raw_cp) if raw_cp is not None else 0
    state.cursor -= comprimento
    transition_start = state.start_frames + state.cursor
    cut_point = transition_start + cutpoint
    if state.events:
        prev = state.events[-1]
        novo_out = max(prev.record_in, cut_point)
        if novo_out < prev.record_out:
            trim = prev.record_out - novo_out
            src_out = prev.source_out - trim if prev.source_out is not None else None
            state.events[-1] = replace(prev, record_out=novo_out, source_out=src_out)
    state.pending_transition = (transition_start, comprimento, cut_point)


def _registrar_evento(state: _WalkState, kind: str, comprimento: int, source_clip=None) -> None:
    """Registra um CutEvent (clip ou gap), consumindo transicao pendente."""
    raw_in = state.start_frames + state.cursor
    raw_out = raw_in + comprimento
    pend = state.pending_transition
    state.pending_transition = None
    if pend is None:
        rec_in, t_start, t_frames = raw_in, None, 0
    else:
        t_start, t_frames, cut_point = pend
        rec_in = max(raw_in, min(cut_point, raw_out))
    head_trim = rec_in - raw_in

    fonte, reel, src_in, src_out = None, None, None, None
    if source_clip is not None:
        fonte, reel, raw_src = _resolver_fonte(source_clip, state.rate, state.report)
        src_in, src_out = raw_src + head_trim, raw_src + comprimento

    ev = CutEvent(
        len(state.events), kind, fonte, reel, src_in, src_out, rec_in, raw_out, t_start, t_frames
    )
    state.events.append(ev)
    state.cursor += comprimento


def _resolver_fonte(
    source_clip, picture_rate: TimecodeRate, report: Report
) -> tuple[str | None, str | None, int]:
    """Resolve (source, reel, raw_source_in) percorrendo a cadeia de mobs."""
    offset, tc_frac, final_mob, has_tc, tem_arquivo = _caminhar_mobs(source_clip, picture_rate)
    if tc_frac is None:
        report.warning(
            "RECONFORM_SOURCE_TC_MISSING",
            "source mob chain has no Timecode segment; using accumulated offset",
            {},
        )
        raw_source_in = round(offset)
    else:
        raw_source_in = round(tc_frac + offset)

    reel = _reel_do_mob(final_mob, has_tc)
    master_mob = getattr(source_clip, "mob", None)
    master_name = getattr(master_mob, "name", None) if master_mob is not None else None
    filename = _source_filename(source_clip) if tem_arquivo else master_name
    return normalize_source(reel, filename), reel, raw_source_in


def _caminhar_mobs(
    source_clip, picture_rate: TimecodeRate
) -> tuple[Fraction, Fraction | None, object | None, bool, bool]:
    """Percorre MasterMob -> file SourceMob -> TapeMob acumulando starts na taxa de corte."""
    offset, curr_sc, curr_rate = Fraction(0), source_clip, picture_rate.fps
    visitados: set[int] = set()
    final_mob, tem_arquivo = None, False

    while curr_sc is not None:
        next_mob = getattr(curr_sc, "mob", None)
        if next_mob is None or id(next_mob) in visitados:
            break
        visitados.add(id(next_mob))
        target_slot = _slot_alvo(next_mob, curr_sc)
        ref_rate = _edit_rate_do_slot(target_slot, curr_rate)
        offset += Fraction(int(getattr(curr_sc, "start", 0) or 0)) * picture_rate.fps / ref_rate
        final_mob = next_mob
        tem_arquivo = tem_arquivo or _tem_caminho_arquivo(next_mob)
        tc_frac = _tc_em_picture_frames(next_mob, picture_rate.fps)
        if tc_frac is not None:
            return offset, tc_frac, final_mob, True, tem_arquivo
        curr_sc = _proximo_source_clip(next_mob, target_slot, visitados)
        curr_rate = ref_rate
    return offset, None, final_mob, False, tem_arquivo


def _slot_alvo(mob, source_clip):
    """Slot do mob referenciado por source_clip, quando presente."""
    slot_id = getattr(source_clip, "slot_id", None)
    if slot_id is None:
        slot_id = _valor_de_propriedade(source_clip, "SourceMobSlotID")
    if slot_id is None:
        return None
    for slot in getattr(mob, "slots", []) or []:
        if getattr(slot, "slot_id", None) == slot_id:
            return slot
    return None


def _edit_rate_do_slot(slot, fallback: Fraction) -> Fraction:
    """edit_rate exata do slot, ou fallback quando ausente ou invalida."""
    raw_rate = getattr(slot, "edit_rate", None) if slot is not None else None
    if raw_rate is None:
        return fallback
    try:
        frac = exact_edit_rate(raw_rate)
        return frac if frac > 0 else fallback
    except (TypeError, ValueError, ZeroDivisionError):
        return fallback


def _tc_em_picture_frames(mob, pic_fps: Fraction) -> Fraction | None:
    """Start do primeiro Timecode do mob convertido para frames na taxa de corte."""
    for slot in getattr(mob, "slots", []) or []:
        tc = _timecode_do_slot(slot)
        if tc is None:
            continue
        start = int(getattr(tc, "start", 0) or 0)
        tc_rate = _taxa_do_timecode(slot, tc) or pic_fps
        return Fraction(start) * pic_fps / tc_rate
    return None


def _timecode_do_slot(slot):
    """Segmento Timecode do slot, direto ou dentro de uma Sequence."""
    seg = getattr(slot, "segment", None)
    if _type_name(seg) == "Timecode":
        return seg
    if _type_name(seg) != "Sequence":
        return None
    comps = getattr(seg, "components", None) or []
    return next((c for c in comps if _type_name(c) == "Timecode"), None)


def _taxa_do_timecode(slot, tc) -> Fraction | None:
    """Taxa exata do segmento Timecode a partir do slot ou de tc.fps/drop."""
    slot_rate = getattr(slot, "edit_rate", None)
    if slot_rate is not None:
        try:
            frac = exact_edit_rate(slot_rate)
            if frac > 0:
                return frac
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    fps = int(getattr(tc, "fps", 0) or 0)
    if fps <= 0:
        return None
    return Fraction(fps * 1000, 1001) if getattr(tc, "drop", False) else Fraction(fps, 1)


def _proximo_source_clip(mob, target_slot, visitados: set[int]):
    """Proximo SourceClip na cadeia a partir do slot alvo ou dos slots de Picture."""
    slots = [target_slot] if target_slot is not None else getattr(mob, "slots", []) or []
    for slot in slots:
        if target_slot is not None or _slot_de_picture(slot):
            achado = _primeiro_source_clip(getattr(slot, "segment", None), visitados)
            if achado is not None:
                return achado
    return None


def _primeiro_source_clip(componente, visitados: set[int], prof: int = 0):
    """Primeiro SourceClip com mob ainda nao visitado dentro de um segmento."""
    if componente is None or prof > 16:
        return None
    if _type_name(componente) == "SourceClip":
        mob = getattr(componente, "mob", None)
        return componente if (mob is not None and id(mob) not in visitados) else None
    for filho in _filhos_do_componente(componente):
        achado = _primeiro_source_clip(filho, visitados, prof + 1)
        if achado is not None:
            return achado
    return None


def _tem_caminho_arquivo(mob) -> bool:
    """True quando o mob traz descritor nao-fisico com Locator ou file_name."""
    desc = getattr(mob, "descriptor", None)
    if desc is None or _descritor_fisico(desc):
        return False
    return _caminho_do_locator(desc) is not None or bool(getattr(desc, "file_name", None))


def _reel_do_mob(mob, has_tc: bool) -> str | None:
    """Nome do mob final quando ele carrega TapeDescriptor ou Timecode."""
    if mob is None:
        return None
    desc = getattr(mob, "descriptor", None)
    has_tape = desc is not None and any(
        b.__name__ == "TapeDescriptor" for b in getattr(desc.__class__, "__mro__", [])
    )
    if not (has_tape or has_tc):
        return None
    nome = getattr(mob, "name", None)
    limpo = str(nome).strip() if nome else ""
    return limpo or None
