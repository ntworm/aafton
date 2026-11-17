"""Verificacao de set do Live sem sidecar contra o AAF exportado."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

from .verify_aaf_bruto import (
    _AafInfo,
    _ler_aaf_bruto,
    _RawSlot,
)
from .aaf_write_tracks import _clip_par_do_stereo, clips_pulados_da_track, expected_slots
from .als_ancora import StartSpec, _fps, _locator_tc
from .als_crossfade_ler import crossfades_em_sobreposicao
from .als_read import read_session
from .als_xml import load_als
from .ir import ClipIR, SessionIR
from .ir_trim import media_end_status, samples_per_frame
from .report import Report
from .timecode import (
    TimecodeRate,
    frames_to_label,
    label_to_frames,
    samples_to_label,
)
from .verify_coverage import formatar_cobertura

_ERROR_CODES = {"AAF_UNKNOWN_COMPONENT", "AAF_NO_SEQUENCE", "ALS_CLIP_UNREADABLE"}
_SEM_SIDECAR_OUT_OF_SCOPE = "audio content, clip gain/fader/pan and envelopes were not verified"


@dataclass(frozen=True)
class _StartResolution:
    spec: StartSpec
    note: str
    errors: list[str]


@dataclass
class _VerifyCtx:
    resultado: object
    destino: SessionIR
    aaf_info: _AafInfo | None
    tolerance: int
    quadro: Fraction
    rotulo: Callable[[int], str]
    offsets_compared: int = 0
    offset_errors: int = 0


def _tc_esperado(als_path: Path, start: StartSpec) -> str:
    if not getattr(start, "assumed", False):
        return start.start_tc
    locator = _locator_tc(load_als(als_path))
    return locator[1] if locator is not None else start.start_tc


def _inicio_da_origem(
    aaf_info: _AafInfo | None, origem: SessionIR
) -> tuple[int, TimecodeRate, str]:
    if aaf_info is not None:
        return aaf_info.frames_inicio, aaf_info.tc_rate, "AAF"
    rate = origem.timecode_rate
    frames = round(Fraction(origem.start_timecode_samples) * rate.fps / origem.sample_rate)
    return frames, rate, "source"


def _resolver_inicio(
    als_path: Path, aaf_info: _AafInfo | None, origem: SessionIR, start: StartSpec | None
) -> _StartResolution:
    orig_frames, orig_rate, rotulo = _inicio_da_origem(aaf_info, origem)
    orig_tc = frames_to_label(orig_frames, orig_rate)
    orig_desc = f"{orig_tc} at {_fps(orig_rate)} fps"
    orig_spec = StartSpec(orig_tc, orig_rate.fps, orig_rate.drop_frame, assumed=False)
    if start is None:
        return _StartResolution(orig_spec, f"start taken from the {rotulo} ({orig_desc})", [])
    esperado_tc = _tc_esperado(als_path, start)
    esperado_rate = start.timecode_rate
    esperado_desc = f"{esperado_tc} at {_fps(esperado_rate)} fps"
    msg = f"start timecode differs: expected {esperado_desc}, {rotulo} has {orig_desc}"
    try:
        esperado_frames = label_to_frames(esperado_tc, esperado_rate)
    except (ValueError, ZeroDivisionError):
        return _StartResolution(orig_spec, "", [msg])
    if esperado_frames != orig_frames or esperado_rate != orig_rate:
        return _StartResolution(start, "", [msg])
    return _StartResolution(start, "", [])


def _tolerancia_e_regime(aaf_info: _AafInfo | None, override: int | None) -> tuple[int, str]:
    if override is not None:
        return override, "Forced Tolerance"
    if aaf_info is None:
        return 0, "Sample-exact (edit_rate == sample_rate)"
    rates = [s.edit_rate for s in aaf_info.slots if s.edit_rate]
    if not rates or aaf_info.sample_rate in rates:
        return 0, "Sample-exact (edit_rate == sample_rate)"
    tolerance = min(int(aaf_info.sample_rate / er) for er in rates)
    return tolerance, f"Frame-exact (edit_rates = {sorted(set(rates))})"


def _coletar_erros(reports: tuple[Report, ...], extra: list[str]) -> list[str]:
    erros = list(extra)
    for rep in reports:
        rep_erros = [i for i in rep.issues if i.severity == "error"]
        for issue in rep.issues:
            if issue.severity == "error" or issue.code in _ERROR_CODES:
                erros.append(f"{issue.code}: {issue.message}")
        if rep.incomplete and not rep_erros:
            erros.append("Incomplete read")
    return erros


def _resumo_deixados(als_report: Report) -> str:
    clips = 0
    midi = 0
    video = 0
    for issue in als_report.issues:
        if issue.code == "ALS_CLIPS_LEFT_OUT":
            clips += int(issue.context.get("count", 0))
        elif issue.code == "ALS_MIDI_TRACKS_LEFT_OUT":
            tracks = issue.context.get("tracks")
            midi += len(tracks) if tracks else 0
        elif issue.code == "ALS_VIDEO_LEFT_OUT":
            video += 1
    return f"left out: {clips} clips (render in Live), {midi} MIDI tracks, {video} video"


def _eventos_do_track_aaf(aaf_track) -> list[ClipIR]:
    clips = list(aaf_track.clips)
    if getattr(aaf_track, "channel_count", 1) > 1 and any(c.channel_index == 0 for c in clips):
        clips = [c for c in clips if c.channel_index == 0]
    return sorted(clips, key=lambda c: (c.timeline_start_samples, c.event_index))


def _conferir_offset(
    ctx: _VerifyCtx, slot_idx: int, exp_slot, c_set: ClipIR, raw_events: list, pos: int
) -> None:
    if pos >= len(raw_events) or not raw_events[pos]:
        return
    ctx.offsets_compared += 1
    is_stereo = getattr(exp_slot.track, "channel_count", 1) == 2
    session_rate = Fraction(ctx.destino.sample_rate)
    for ch_idx, (aaf_start, file_rate) in enumerate(raw_events[pos]):
        canal_clip = _clip_par_do_stereo(exp_slot.track, c_set, ch_idx) if is_stereo else c_set
        offset_samples = canal_clip.source_offset_samples or 0
        esperado = round(Fraction(offset_samples) * file_rate / session_rate)
        if aaf_start != esperado:
            ctx.offset_errors += 1
            ctx.resultado.errors.append(
                f"media offset differs on clip {c_set.name!r} (slot {slot_idx}): "
                f"expected {esperado}, found {aaf_start}"
            )
            return


def _linha_par(ctx: _VerifyCtx, track_name: str, c_aaf: ClipIR, c_set: ClipIR, row_cls):
    delta_start = c_set.timeline_start_samples - c_aaf.timeline_start_samples
    delta_end = c_set.timeline_end_samples - c_aaf.timeline_end_samples
    status = media_end_status(c_aaf, c_set, ctx.destino, ctx.tolerance, ctx.quadro)
    return row_cls(
        track=track_name,
        clip=c_aaf.name,
        aaf_in=ctx.rotulo(c_aaf.timeline_start_samples),
        aaf_out=ctx.rotulo(c_aaf.timeline_end_samples),
        als_in=ctx.rotulo(c_set.timeline_start_samples),
        als_out=ctx.rotulo(c_set.timeline_end_samples),
        delta_samples=delta_start,
        delta_frames=delta_start / float(ctx.quadro),
        delta_end_samples=delta_end,
        status=status,
        anchor_source=c_aaf.anchor_source,
        is_audio=True,
    )


def _linha_faltando(ctx: _VerifyCtx, track_name: str, c_aaf: ClipIR, row_cls):
    return row_cls(
        track=track_name,
        clip=c_aaf.name,
        aaf_in=ctx.rotulo(c_aaf.timeline_start_samples),
        aaf_out=ctx.rotulo(c_aaf.timeline_end_samples),
        als_in="-",
        als_out="-",
        delta_samples=c_aaf.duration_samples,
        delta_frames=c_aaf.duration_samples / float(ctx.quadro),
        delta_end_samples=None,
        status="missing in the ALS",
        anchor_source=c_aaf.anchor_source,
        is_audio=True,
    )


def _linha_extra(ctx: _VerifyCtx, track_name: str, c_set: ClipIR, row_cls):
    return row_cls(
        track=track_name,
        clip=c_set.name,
        aaf_in="-",
        aaf_out="-",
        als_in=ctx.rotulo(c_set.timeline_start_samples),
        als_out=ctx.rotulo(c_set.timeline_end_samples),
        delta_samples=c_set.duration_samples,
        delta_frames=c_set.duration_samples / float(ctx.quadro),
        delta_end_samples=None,
        status="extra in the ALS",
        is_audio=True,
    )


def _comparar_slot(
    ctx: _VerifyCtx, idx: int, aaf_track, exp_slot, raw_slot: _RawSlot | None, row_cls
) -> None:
    aaf_name = raw_slot.name if raw_slot is not None else aaf_track.name
    if aaf_name != exp_slot.name:
        ctx.resultado.errors.append(f"slot {idx}: expected {exp_slot.name}, found {aaf_name}")
    aaf_clips = _eventos_do_track_aaf(aaf_track)
    set_clips = list(exp_slot.clips)
    raw_events = raw_slot.events if raw_slot is not None else []
    track_name = aaf_name if aaf_name else exp_slot.name
    ctx.resultado.audio_expected += len(aaf_clips)
    for pos in range(max(len(aaf_clips), len(set_clips))):
        if pos < len(aaf_clips) and pos < len(set_clips):
            ctx.resultado.audio_compared += 1
            row = _linha_par(ctx, track_name, aaf_clips[pos], set_clips[pos], row_cls)
            ctx.resultado.rows.append(row)
            _conferir_offset(ctx, idx, exp_slot, set_clips[pos], raw_events, pos)
        elif pos < len(aaf_clips):
            ctx.resultado.audio_missing += 1
            ctx.resultado.rows.append(_linha_faltando(ctx, track_name, aaf_clips[pos], row_cls))
        else:
            ctx.resultado.audio_extra += 1
            ctx.resultado.rows.append(_linha_extra(ctx, track_name, set_clips[pos], row_cls))


def _registrar_sobra(ctx: _VerifyCtx, aaf_tracks: list, esp_slots: list, row_cls) -> None:
    n_aaf = len(aaf_tracks)
    n_esp = len(esp_slots)
    for idx in range(n_aaf, n_esp):
        exp_slot = esp_slots[idx]
        ctx.resultado.errors.append(f"slot {idx}: expected {exp_slot.name}, missing in AAF")
        for clip in exp_slot.clips:
            ctx.resultado.audio_extra += 1
            ctx.resultado.rows.append(_linha_extra(ctx, exp_slot.name, clip, row_cls))
    slots = ctx.aaf_info.slots if ctx.aaf_info is not None else []
    for idx in range(n_esp, n_aaf):
        raw_name = slots[idx].name if idx < len(slots) else None
        aaf_name = raw_name if raw_name is not None else aaf_tracks[idx].name
        ctx.resultado.errors.append(f"slot {idx}: unexpected slot {aaf_name}")
        for clip in _eventos_do_track_aaf(aaf_tracks[idx]):
            ctx.resultado.audio_expected += 1
            ctx.resultado.audio_missing += 1
            ctx.resultado.rows.append(_linha_faltando(ctx, aaf_name, clip, row_cls))


def _registrar_pulados(ctx: _VerifyCtx, row_cls) -> None:
    """Clip do set que o writer deixa fora do AAF e perda: sobra no .als e reprova.

    ``expected_slots`` lista so os clips que o writer grava; sem esta conta um
    clip pulado no export nao apareceria em lugar nenhum.
    """
    for track in ctx.destino.tracks:
        if getattr(track, "is_video", False):
            continue
        pulados = clips_pulados_da_track(track, ctx.destino)
        vistos: set[tuple[int, int]] = set()
        for clip in track.clips:
            trecho = (clip.timeline_start_samples, clip.timeline_end_samples)
            if id(clip) not in pulados or trecho in vistos:
                continue
            vistos.add(trecho)
            ctx.resultado.audio_extra += 1
            ctx.resultado.rows.append(_linha_extra(ctx, track.name, clip, row_cls))
            ctx.resultado.errors.append(
                f"clip {clip.name!r} of the track {track.name!r} was not written to the AAF"
            )


def _ler_destino(
    als_path: Path, als_report: Report, opts: dict, tracks: Collection[int] | None
) -> SessionIR:
    """Le o set e, com selecao de tracks, mantem so as selecionadas como no export."""
    if tracks is None:
        return read_session(als_path, als_report, **opts)
    from .pipeline import replay_issues, select_tracks

    escolhidos = list(tracks)
    reading = Report(strict=False)
    destino = read_session(als_path, reading, **opts)
    removidas = select_tracks(destino, escolhidos, reading)
    replay_issues(reading, als_report, removidas, escolhidos)
    return destino


def _comparar_sessoes(
    resultado,
    origem: SessionIR,
    destino: SessionIR,
    aaf_info: _AafInfo | None,
    tolerance: int,
    row_cls,
) -> bool:
    """Compara tracks e slots na mesma escala de amostras; reprova se taxas divergirem."""
    aaf_tracks = [t for t in origem.tracks if not getattr(t, "is_video", False)]
    if origem.sample_rate != destino.sample_rate:
        resultado.audio_expected = sum(len(_eventos_do_track_aaf(t)) for t in aaf_tracks)
        resultado.errors.append(
            f"sample rate differs: AAF is {origem.sample_rate} Hz, "
            f"Live set is {destino.sample_rate} Hz"
        )
        return False
    rate = origem.timecode_rate
    sample_rate = origem.sample_rate
    ctx = _VerifyCtx(
        resultado=resultado,
        destino=destino,
        aaf_info=aaf_info,
        tolerance=tolerance,
        quadro=samples_per_frame(origem),
        rotulo=lambda s: samples_to_label(s, rate, sample_rate),
    )
    esp_slots = expected_slots(destino)
    slots = aaf_info.slots if aaf_info is not None else []
    for idx in range(min(len(aaf_tracks), len(esp_slots))):
        raw_slot = slots[idx] if idx < len(slots) else None
        _comparar_slot(ctx, idx, aaf_tracks[idx], esp_slots[idx], raw_slot, row_cls)
    _registrar_sobra(ctx, aaf_tracks, esp_slots, row_cls)
    _registrar_pulados(ctx, row_cls)
    return (
        ctx.offsets_compared > 0
        and ctx.offsets_compared == resultado.audio_compared
        and ctx.offset_errors == 0
    )


def verify_sem_sidecar(
    aaf_path: str | Path,
    als_path: str | Path,
    override_tolerance_samples: int | None,
    start: StartSpec | None,
    leave_out_render: bool,
    leitor_aaf: Callable[..., SessionIR],
    tracks: Collection[int] | None = None,
    fades: str = "absorb",
):
    """Verifica um .als contra o AAF exportado dele, pela ordem de slots do writer.

    ``fades="keep"`` le o AAF sem absorver clips de fade: e o modo da volta de
    um set com sidecar, em que um clip "Fade N" do set e um clip comum.
    """
    from .formats.origem import ler_origem_verify
    from .verify import VerifyResult, VerifyRow

    caminho_aaf = Path(aaf_path)
    if fades == "keep":
        aaf_info = _ler_aaf_bruto(caminho_aaf, manter_fades=True)
    else:
        aaf_info = _ler_aaf_bruto(caminho_aaf)
    aaf_report = Report(strict=False)
    origem = ler_origem_verify(aaf_path, aaf_report, fades, als_path, leitor_aaf)
    inicio = _resolver_inicio(Path(als_path), aaf_info, origem, start)

    als_report = Report(strict=False)
    opts = {"start": inicio.spec, "leave_out_render": leave_out_render}
    destino = _ler_destino(Path(als_path), als_report, opts, tracks)
    crossfades_em_sobreposicao(origem)
    crossfades_em_sobreposicao(destino)

    tolerance, regime = _tolerancia_e_regime(aaf_info, override_tolerance_samples)
    erros = _coletar_erros((aaf_report, als_report), inicio.errors)
    resultado = VerifyResult(
        tolerance_samples=tolerance,
        regime_name=regime,
        errors=erros,
        out_of_scope=_SEM_SIDECAR_OUT_OF_SCOPE,
    )
    offsets_ok = _comparar_sessoes(resultado, origem, destino, aaf_info, tolerance, VerifyRow)
    partes = [formatar_cobertura(resultado)]
    if inicio.note:
        partes.append(inicio.note)
    partes.append(_resumo_deixados(als_report))
    if caminho_aaf.suffix.lower() != ".aaf":
        partes.append("media offset not verified")
    elif offsets_ok:
        partes.append("media offset verified")
    resultado.coverage_msg = "; ".join(partes)
    return resultado
