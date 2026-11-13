"""Escrita das tracks de audio e dos marcadores dentro do AAF.

O modulo principal (aaf_write) monta a composition e o slot de timecode; aqui
cada track de audio vira um ou mais slots de timeline, com um Sequence por
canal, e os marcadores vao para um slot de metadata descritiva.
"""

import math
import re
from dataclasses import dataclass, field

from .ir import SessionIR
from .report import Report
from .aaf_write_defs import AafBuilder
from .aaf_write_fades import (
    fades_em_envelope,
    fades_para_silencio,
    escrever_silencio,
)
from .aaf_write_gain import (
    _aplicar_fade_in,
    _aplicar_fade_out,
    _dedup_keyframes,
    _ganho_base,
    _keyframes_automacao,
)
from .aaf_write_media import (
    MobResolver as _MobResolver,
    source_start as _source_start,
)
from .aaf_write_preflight import (
    _is_supported_transition,
    _transition_conflicts_with_automation,
)


_LAYOUT_SUFFIX_RE = re.compile(r"\s*\[(?P<suffix>[^\]]+)\]$")
_KNOWN_LAYOUT_SUFFIXES = {"L", "R", "C", "LFE", "LS", "RS", "W", "X", "Y", "Z"}


def _strip_channel_suffix(name: str) -> str:
    """Remove sufixos de canal adicionados na leitura (ex: '5.1 [C]' -> '5.1') para simetria no round-trip."""
    m = _LAYOUT_SUFFIX_RE.search(name)
    if m:
        suffix = m.group("suffix").strip()
        if suffix.upper() in _KNOWN_LAYOUT_SUFFIXES or re.match(r"^ch\d+$", suffix, re.IGNORECASE):
            return name[: m.start()].rstrip()
    return name


@dataclass
class TrackContext:
    """Estado compartilhado pela escrita das tracks de audio."""

    f: object
    comp: object
    session: SessionIR
    report: Report
    builder: AafBuilder
    resolver: _MobResolver
    consumed_in: set[int]
    consumed_out: set[int]
    physical_track_num: int = 1
    clips_reportados: set[int] = field(default_factory=set)
    slots_escritos: list[tuple[object, str]] = field(default_factory=list)


def write_tracks(ctx: TrackContext) -> None:
    """Escreve as tracks de audio da sessao nos slots da composition."""
    for track_id, track in enumerate(ctx.session.tracks, start=1):
        _escrever_track(ctx, track)


def write_markers(f, comp, session: SessionIR, report: Report) -> None:
    """Escreve os marcadores da sessao em um slot de metadata descritiva."""
    if getattr(session, "markers", None):
        marker_slot = comp.create_timeline_slot(edit_rate=session.sample_rate)
        marker_slot.name = "Markers"
        marker_seq = f.create.Sequence(media_kind="DescriptiveMetadata")
        marker_slot.segment = marker_seq
        for idx, m in enumerate(session.markers):
            marker_comp = f.create.DescriptiveMarker()
            rel_pos = max(0, m.position_samples - session.start_timecode_samples)
            marker_comp["Position"].value = rel_pos
            marker_comp["Comment"].value = str(m.name or f"Marker {idx + 1}")
            marker_seq.components.append(marker_comp)
        report.info(
            "MARKERS_WRITTEN",
            f"{len(session.markers)} markers written in the AAF",
            {"count": len(session.markers)},
        )


def _escrever_track(ctx: TrackContext, track) -> None:
    """Escreve uma track: reporta, escolhe os canais e cria um slot por canal."""
    if getattr(track, "is_video", False):
        ctx.report.info(
            "VIDEO_TRACK_SKIPPED_ON_EXPORT",
            f"The picture does not enter the AAF; video track {track.name!r} ignored.",
            {"track": track.name},
        )
        return

    if track.plugins:
        ctx.report.warning(
            "WRITE_PLUGIN",
            f"Track {track.name} has plugins that will be discarded in the AAF",
            {},
        )

    canais = _canais_da_track(track)
    base_track_name = _strip_channel_suffix(track.name)
    for canal in canais:
        _escrever_slot(ctx, track, canal, base_track_name)


def _canais_da_track(track) -> list[int]:
    """Canais da track que recebem slot no AAF."""
    track_channels = _track_channels(track)
    if track_channels == 2:
        return [0]
    if track_channels > 1:
        return list(range(track_channels))
    return sorted(list(set(c.channel_index for c in track.clips) or {0}))


def _track_channels(track) -> int:
    """Numero de canais da track; 1 quando a track nao informa."""
    return getattr(track, "channel_count", 1)


def _escrever_slot(ctx: TrackContext, track, canal, base_track_name) -> None:
    """Cria o slot do canal, escreve os clips e fecha o slot."""
    track_channels = _track_channels(track)
    slot = ctx.comp.create_timeline_slot(edit_rate=ctx.session.sample_rate)
    slot.name = base_track_name
    ctx.slots_escritos.append((slot, track.name))
    slot["PhysicalTrackNumber"].value = ctx.physical_track_num
    if track_channels == 2:
        slot["TimelineMobAttributeList"].append(
            ctx.f.create.TaggedValue("_TRACK_FORMAT", 2, "aafInt32")
        )
    ctx.physical_track_num += 1
    sequence = ctx.f.create.Sequence(media_kind="sound")

    cursor = _escrever_clips(ctx, track, canal, sequence)
    _finalizar_slot(ctx, track, slot, sequence, cursor)


def _clips_do_canal(track, canal) -> list:
    """Clips da track que vao para o canal, na ordem da timeline."""
    track_channels = _track_channels(track)
    if track_channels == 2:
        return _clips_estereo(track)
    if track_channels > 1 and all(c.channel_index == 0 for c in track.clips):
        return sorted(track.clips, key=lambda x: x.timeline_start_samples)
    return sorted(
        (c for c in track.clips if c.channel_index == canal),
        key=lambda x: x.timeline_start_samples,
    )


def _clips_estereo(track) -> list:
    """Clips do canal esquerdo da track stereo; todos quando nao ha canal 0."""
    has_ch0 = any(c.channel_index == 0 for c in track.clips)
    return sorted(
        (c for c in track.clips if (c.channel_index == 0 if has_ch0 else True)),
        key=lambda x: x.timeline_start_samples,
    )


def _escrever_clips(ctx: TrackContext, track, canal, sequence) -> int:
    """Percorre os clips do canal e devolve o cursor final em samples.

    O fade de saida de um clip e escrito quando o clip seguinte chega, e o de
    entrada quando o clip entra: cada um vira uma Transition ao lado de um
    Filler, que e a forma que o leitor ja le.
    """
    clips_do_canal = _clips_do_canal(track, canal)
    pulados = _clips_pulados(clips_do_canal, track, ctx.session)
    cursor = 0
    saida = 0
    for clip in clips_do_canal:
        start_local = clip.timeline_start_samples - ctx.session.start_timecode_samples
        if start_local < cursor:
            if id(clip) in pulados:
                _reporta_clip_pulado(ctx, track, clip)
                continue
            overlap = cursor - start_local
            ctx.builder.append_audio_dissolve(sequence, overlap)
            cursor -= overlap
        else:
            entrada, _ = fades_para_silencio(
                clip, ctx.consumed_in, ctx.consumed_out, track.gain_points
            )
            escrever_silencio(ctx, sequence, saida, start_local - cursor, entrada)
            cursor = start_local
        _escrever_clip(ctx, track, canal, clip, sequence)
        cursor += clip.timeline_end_samples - clip.timeline_start_samples
        _, saida = fades_para_silencio(clip, ctx.consumed_in, ctx.consumed_out, track.gain_points)
    escrever_silencio(ctx, sequence, saida, 0, 0)
    return cursor


def _clips_pulados(clips_do_canal: list, track, session) -> set[int]:
    """Ids dos clips que nao entram no AAF de um canal.

    A conta e a mesma que o writer faz: o clip que vem depois de uma
    sobreposicao que nao vira Transition fica fora do AAF.
    """
    pulados: set[int] = set()
    cursor = 0
    previous_clip = None
    for clip in clips_do_canal:
        start_local = clip.timeline_start_samples - session.start_timecode_samples
        if start_local > cursor:
            cursor = start_local
        if start_local < cursor:
            overlap = cursor - start_local
            if not _is_supported_transition(
                previous_clip, clip, overlap
            ) or _transition_conflicts_with_automation(
                previous_clip, clip, overlap, track.gain_points
            ):
                pulados.add(id(clip))
                continue
            cursor -= overlap
        cursor += clip.timeline_end_samples - clip.timeline_start_samples
        previous_clip = clip
    return pulados


def clips_pulados_da_track(track, session) -> set[int]:
    """Ids dos clips da track que nao entram no AAF."""
    pulados: set[int] = set()
    for canal in _canais_da_track(track):
        pulados |= _clips_pulados(_clips_do_canal(track, canal), track, session)
    return pulados | _pares_pulados(track, pulados)


def _pares_pulados(track, pulados: set[int]) -> set[int]:
    """Ids da outra metade dos clips pulados: o par stereo do clip pulado.

    O par stereo e um clip so: quando a metade da esquerda fica fora do AAF,
    a da direita nao entra tambem, e o fade dela nao vira erro de fade.
    """
    if _track_channels(track) != 2:
        return set()
    ids: set[int] = set()
    for clip in track.clips:
        if id(clip) not in pulados:
            continue
        par = _clip_par_do_stereo(track, clip, 1 - clip.channel_index)
        if par is not clip:
            ids.add(id(par))
    return ids


def _reporta_clip_pulado(ctx: TrackContext, track, clip) -> None:
    """Avisa que o clip ficou fora do AAF, uma vez por clip."""
    if id(clip) in ctx.clips_reportados:
        return
    ctx.clips_reportados.add(id(clip))
    ctx.report.warning(
        "WRITE_CLIP_SKIPPED",
        f"the clip {clip.name!r} of the track {track.name!r} did not enter the AAF: "
        "the overlap with the previous clip did not become a Transition",
        {"clip": clip.name, "track": track.name},
    )


def _escrever_clip(ctx: TrackContext, track, canal, clip, sequence) -> None:
    """Escreve o clip no sequence: ganho fixo ou envelope de ganho."""
    start_local = clip.timeline_start_samples - ctx.session.start_timecode_samples
    duration = clip.timeline_end_samples - clip.timeline_start_samples
    has_gain_automation = bool(track.gain_points)
    has_indep_fade_in, has_indep_fade_out = fades_em_envelope(
        clip, ctx.consumed_in, ctx.consumed_out, track.gain_points
    )
    base_gain = _ganho_base(track, clip)

    if not has_gain_automation and not has_indep_fade_in and not has_indep_fade_out:
        sequence.components.append(
            ctx.builder.wrap_audio_gain(
                _component_do_clip(ctx, track, canal, clip, 0, duration),
                duration,
                base_gain,
            )
        )
        return

    dedup_kfs = _keyframes_do_clip(
        ctx,
        track,
        clip,
        start_local,
        duration,
        base_gain,
        has_indep_fade_in,
        has_indep_fade_out,
    )
    sequence.components.append(
        ctx.builder.wrap_varying_gain(
            _component_do_clip(ctx, track, canal, clip, 0, duration),
            duration,
            dedup_kfs,
        )
    )
    if _curva_perdida(ctx, dedup_kfs):
        _reporta_curva_perdida(ctx, track, clip)
        return
    if has_gain_automation:
        ctx.report.info(
            "GAIN_AUTOMATION_WRITTEN",
            f"automation of {track.name!r} written with VaryingValue (LinearInterp)",
            {
                "track": track.name,
                "clip": clip.name,
                "keyframes": len(dedup_kfs),
            },
        )
    if has_indep_fade_in or has_indep_fade_out:
        ctx.report.info(
            "FADE_CONVERTED_TO_ENVELOPE",
            f"independent fade of {clip.name!r} converted to a gain envelope in the AAF",
            {"clip": clip.name},
        )


def _reporta_curva_perdida(ctx: TrackContext, track, clip) -> None:
    """Erro quando a curva de ganho nao cabe no AAF e se perde."""
    contexto = {
        "track": track.name,
        "clip": clip.name,
        "dependency": "pyaaf2",
        "property": "VaryingValue.Interpolation",
        "cause": "the installed pyaaf2 lacks the definitions VaryingValue needs",
    }
    ctx.report.error(
        "WRITE_GAIN_AUTOMATION_DEPENDENCY_MISSING",
        f"the gain automation of {track.name!r} cannot become a curve in the AAF: {contexto['cause']}",
        contexto,
    )


def _curva_perdida(ctx: TrackContext, keyframes) -> bool:
    """Diz se a curva de ganho se perde porque o pyaaf2 nao tem VaryingValue."""
    if ctx.builder.curva_suportada():
        return False
    return ctx.builder.keyframes_variam(keyframes)


def _keyframes_do_clip(
    ctx, track, clip, start_local, duration, base_gain, has_indep_fade_in, has_indep_fade_out
):
    """Keyframes de ganho do clip, com os fades independentes aplicados."""
    clip_start_abs = ctx.session.start_timecode_samples + start_local
    clip_end_abs = clip_start_abs + duration
    keyframes: list[tuple[float, float]] = _keyframes_automacao(
        track.gain_points,
        base_gain,
        clip_start_abs,
        clip_end_abs,
        duration,
    )
    if has_indep_fade_in:
        keyframes = _aplicar_fade_in(keyframes, clip.fade_in.duration_samples, duration)
    if has_indep_fade_out:
        keyframes = _aplicar_fade_out(keyframes, clip.fade_out.duration_samples, duration)
    return _dedup_keyframes(keyframes)


def _component_do_clip(ctx: TrackContext, track, canal, clip, offset, comprimento):
    """SourceClip do trecho [offset, offset+comprimento) do clip."""
    track_channels = _track_channels(track)
    if track_channels == 2:
        return _component_estereo(ctx, track, clip, offset, comprimento)
    channel_index = canal if track_channels > 1 else 0
    mob, slot_id = ctx.resolver.get_mob(clip.media_id, channel_index, clip.name)
    return _posicionar(ctx, clip, mob, slot_id, offset, comprimento)


def _component_estereo(ctx: TrackContext, track, clip, offset, comprimento):
    """Combina os dois canais do trecho stereo em um unico SourceClip."""
    fontes = []
    for channel_index in range(2):
        canal_clip = _clip_par_do_stereo(track, clip, channel_index)
        mob, slot_id = ctx.resolver.get_mob(canal_clip.media_id, channel_index, canal_clip.name)
        fontes.append(_posicionar(ctx, canal_clip, mob, slot_id, offset, comprimento))
    return ctx.builder.combine_audio_channels(fontes, comprimento)


def _clip_par_do_stereo(track, clip, channel_index):
    """Clip do canal stereo com o mesmo trecho; o proprio clip como reserva."""
    for c in track.clips:
        if (
            c.channel_index == channel_index
            and c.timeline_start_samples == clip.timeline_start_samples
            and c.timeline_end_samples == clip.timeline_end_samples
        ):
            return c
    return clip


def _posicionar(ctx: TrackContext, canal_clip, mob, slot_id, offset, comprimento):
    """SourceClip do canal_clip posicionado no mob do asset."""
    inicio = _source_start(ctx.session, ctx.report, canal_clip, mob, slot_id, offset=offset)
    sc = ctx.f.create.SourceClip(media_kind="sound", start=inicio)
    sc.length = comprimento
    sc.mob = mob
    sc.slot_id = slot_id
    return sc


def _finalizar_slot(ctx: TrackContext, track, slot, sequence, cursor) -> None:
    """Fecha o slot: stereo, mono com pan (automatica ou estatica) ou multicanal."""
    track_channels = _track_channels(track)
    if track_channels == 2:
        sequence.length = cursor
        slot.segment = sequence
    elif track_channels == 1:
        if track.pan_points and ctx.builder.curva_suportada():
            slot.segment = ctx.builder.wrap_varying_pan(sequence, cursor, track.pan_points)
        else:
            # Sem VaryingValue no pyaaf2 o pan automatico nao cabe no AAF; o
            # pan estatico do mixer e o que o arquivo consegue carregar.
            pan_value = _pan_estatico(ctx, track)
            slot.segment = ctx.builder.wrap_audio_pan(sequence, cursor, pan_value)
    else:
        sequence.length = cursor
        slot.segment = sequence


def _pan_estatico(ctx: TrackContext, track) -> float:
    """Pan estatico normalizado para [0, 1]; o centro quando o valor e invalido."""
    try:
        live_pan = float(track.pan)
    except (TypeError, ValueError, OverflowError):
        live_pan = math.nan
    if not math.isfinite(live_pan) or not -1.0 <= live_pan <= 1.0:
        ctx.report.error(
            "PAN_VALUE_INVALID",
            f"the track {track.name!r} has pan outside the interval "
            f"[-1, 1]: {track.pan!r}; the center was used",
            {"track": track.name, "value": repr(track.pan)},
        )
        live_pan = 0.0
    return (live_pan + 1.0) / 2.0
