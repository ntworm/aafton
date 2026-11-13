"""Construcao dos AudioClip e do envelope de ganho de uma track do Live."""

import xml.etree.ElementTree as ET


from .als_paths import AUDIO_CLIP_FIELDS

from .als_xml import (
    IdAllocator,
    clone,
    format_value,
    set_value,
)

from .ir import ClipIR, MediaAsset

from .report import Report


from .als_write_base import (
    AnchorError,
    SENTINEL_TIME,
    SOURCE,
    TIMELINE,
    VOLUME_GAIN_MAX,
    VOLUME_GAIN_MIN,
    WriteOptions,
    _clip_times,
    clear_warp,
    samples_to_beats,
    to_beats,
)


def _beats_of(abs_samples: int, options: WriteOptions) -> float:
    return to_beats(abs_samples, options.live_origin_samples, options.sample_rate, options.bpm)


def _duration_beats(samples: int, options: WriteOptions, side: str = TIMELINE) -> float:
    return samples_to_beats(samples, options.sample_rate, options.bpm, side)


def sanitize_sample_ref(node: ET.Element, asset: MediaAsset) -> None:
    """Apaga do clone a identidade do arquivo que estava no template.

    O prototipo veio de um clip real, entao carrega tamanho, CRC, pack de origem
    e taxa do sample do template. E a referencia aparece mais de uma vez: alem do
    SampleRef/FileRef principal, ha copias em OriginalFileRef aninhadas dentro de
    SourceContext. Varrer todo FileRef da subarvore evita depender do nivel exato
    de aninhamento, que muda entre versoes do Live. Deixar qualquer uma delas
    apontando para o sample do template faz o Live registrar "could not be
    opened" no Log.txt ao carregar.
    """
    ref = node.find("SampleRef")
    if ref is None:
        return

    _clear_sample_ref_files(ref, asset)
    _set_sample_ref_defaults(ref, asset)


def _clear_sample_ref_files(ref: ET.Element, asset: MediaAsset) -> None:
    caminho = asset.resolved_path.replace("\\", "/") if asset.resolved_path else None
    for file_ref in ref.iter("FileRef"):
        for campo, valor in (
            ("OriginalFileSize", 0),
            ("OriginalCrc", 0),
            ("LivePackName", ""),
            ("LivePackId", ""),
            ("BrowserContentPath", ""),
            ("LocalFiltersJson", ""),
            ("SourceHint", ""),
        ):
            alvo = file_ref.find(campo)
            if alvo is not None:
                alvo.set("Value", format_value(valor))
        if caminho is not None:
            for campo in ("Path", "RelativePath"):
                alvo = file_ref.find(campo)
                if alvo is not None:
                    alvo.set("Value", caminho)


def _set_sample_ref_defaults(ref: ET.Element, asset: MediaAsset) -> None:
    for campo, valor in (
        ("DefaultDuration", asset.duration_samples),
        ("DefaultSampleRate", asset.sample_rate),
        ("SamplesToAutoWarp", 0),
    ):
        if valor is None:
            continue
        try:
            set_value(ref, campo, valor)
        except KeyError:
            pass


def _resolve_clip_path(clip: ClipIR, asset: MediaAsset, options: WriteOptions) -> str:
    if asset.status != "resolved" or not asset.resolved_path:
        if not options.permissive:
            raise ValueError(
                f"clip {clip.id!r} points to unresolved media ({asset.filename!r}); resolve the media or run in permissive mode"
            )
        return asset.filename
    return asset.resolved_path


def _set_clip_times(
    node: ET.Element,
    clip: ClipIR,
    options: WriteOptions,
    is_video: bool,
) -> None:
    if is_video:
        start_beats, end_beats = (
            _beats_of(clip.timeline_start_samples, options),
            _beats_of(clip.timeline_end_samples, options),
        )
    else:
        start_beats, end_beats = _clip_times(
            clip.timeline_start_samples, clip.duration_samples, options
        )
    # A posicao do clip no arranjo e o atributo XML Time do proprio <AudioClip>,
    # nao um elemento filho. O Live usa esse atributo para posicionar; deixar o
    # valor do prototipo faz o Live calcular comprimento negativo e apagar o clip
    # ao carregar, registrando no Log.txt "Delete clip because its length is too
    # small". Ele sempre acompanha CurrentStart.
    node.set("Time", format_value(start_beats))
    set_value(node, AUDIO_CLIP_FIELDS["current_start"], start_beats)
    set_value(node, AUDIO_CLIP_FIELDS["current_end"], end_beats)


def _loop_values(clip: ClipIR, options: WriteOptions, is_video: bool) -> tuple[float, float]:
    offset = clip.source_offset_samples or 0
    source_end = offset + clip.duration_samples
    if is_video:
        # Video sempre em segundos, que e o beat a 60 BPM.
        loop_start_val = samples_to_beats(offset, options.sample_rate, 60.0, SOURCE)
        loop_end_val = samples_to_beats(source_end, options.sample_rate, 60.0, SOURCE)
    else:
        loop_start_val = _duration_beats(offset, options, SOURCE)
        loop_end_val = _duration_beats(source_end, options, SOURCE)
    return loop_start_val, loop_end_val


def _set_clip_core_values(
    node: ET.Element,
    clip: ClipIR,
    loop_start_val: float,
    loop_end_val: float,
    resolved: str,
) -> None:
    set_value(node, AUDIO_CLIP_FIELDS["loop_start"], loop_start_val)
    set_value(node, AUDIO_CLIP_FIELDS["loop_end"], loop_end_val)
    set_value(node, AUDIO_CLIP_FIELDS["out_marker"], loop_end_val)
    set_value(node, AUDIO_CLIP_FIELDS["loop_on"], False)
    set_value(node, AUDIO_CLIP_FIELDS["start_relative"], 0.0)
    set_value(node, AUDIO_CLIP_FIELDS["is_warped"], False)
    set_value(node, AUDIO_CLIP_FIELDS["name"], clip.name)
    set_value(node, AUDIO_CLIP_FIELDS["sample_path"], resolved.replace("\\\\", "/"))
    set_value(node, AUDIO_CLIP_FIELDS["clip_gain"], float(clip.gain_linear))


def _set_video_warp_fields(node: ET.Element) -> None:
    try:
        set_value(node, "WarpMode", 4)
        # FileRef Type is 1 for video, RelativePathType is 1
        set_value(node, "SampleRef/FileRef/Type", 1)
        set_value(node, "SampleRef/FileRef/RelativePathType", 1)
    except KeyError:
        pass


def _apply_fades(
    node: ET.Element,
    clip: ClipIR,
    options: WriteOptions,
    report: Report,
) -> tuple[int, int]:
    fade_in_samples = clip.fade_in.duration_samples if clip.fade_in is not None else 0
    fade_out_samples = clip.fade_out.duration_samples if clip.fade_out is not None else 0
    if fade_in_samples + fade_out_samples > clip.duration_samples:
        orig_in, orig_out = fade_in_samples, fade_out_samples
        fator = clip.duration_samples / (fade_in_samples + fade_out_samples)
        fade_in_samples = int(fade_in_samples * fator)
        fade_out_samples = clip.duration_samples - fade_in_samples
        report.warning(
            "FADES_EXCEED_CLIP",
            f"fades proportional to the clip {clip.id!r}: in {orig_in}->{fade_in_samples} out {orig_out}->{fade_out_samples}",
            {"clip_id": clip.id},
        )
    fade_in_beats = _duration_beats(fade_in_samples, options)
    fade_out_beats = _duration_beats(fade_out_samples, options)
    set_value(node, AUDIO_CLIP_FIELDS["fade_in_length"], fade_in_beats)
    set_value(node, AUDIO_CLIP_FIELDS["fade_out_length"], fade_out_beats)
    return fade_in_samples, fade_out_samples


def _set_crossfade_and_default_fades(node: ET.Element, clip: ClipIR) -> None:
    try:
        set_value(
            node,
            AUDIO_CLIP_FIELDS["crossfade_in_state"],
            1 if getattr(clip, "crossfade_in_state", False) else 0,
        )
    except KeyError:
        pass

    try:
        set_value(node, AUDIO_CLIP_FIELDS["is_default_fade_in"], False)
        set_value(node, AUDIO_CLIP_FIELDS["is_default_fade_out"], False)
    except KeyError:
        pass


def _reset_zero_fade_curves(
    node: ET.Element,
    fade_in_samples: int,
    fade_out_samples: int,
) -> None:
    for attr in (
        "fade_in_curve_skew",
        "fade_in_curve_slope",
        "fade_out_curve_skew",
        "fade_out_curve_slope",
    ):
        if (attr.startswith("fade_in") and fade_in_samples == 0) or (
            attr.startswith("fade_out") and fade_out_samples == 0
        ):
            try:
                set_value(node, AUDIO_CLIP_FIELDS[attr], 0.0)
            except KeyError:
                pass


def _set_hidden_loop_fields(node: ET.Element, loop_end_val: float) -> None:
    for chave in ("hidden_loop_start", "hidden_loop_end"):
        if chave in AUDIO_CLIP_FIELDS:
            valor = 0.0 if chave == "hidden_loop_start" else loop_end_val
            try:
                set_value(node, AUDIO_CLIP_FIELDS[chave], valor)
            except KeyError:
                pass


def build_audio_clip(
    clip: ClipIR,
    asset: MediaAsset,
    prototype: ET.Element,
    allocator: IdAllocator,
    posicionais: frozenset[str],
    unicos: frozenset[int],
    options: WriteOptions,
    report: Report,
    is_video: bool = False,
) -> ET.Element:
    resolved = _resolve_clip_path(clip, asset, options)

    node = clone(prototype, allocator, posicionais, unicos)
    _set_clip_times(node, clip, options, is_video)

    loop_start_val, loop_end_val = _loop_values(clip, options, is_video)
    _set_clip_core_values(node, clip, loop_start_val, loop_end_val, resolved)

    if is_video:
        _set_video_warp_fields(node)

    fade_in_samples, fade_out_samples = _apply_fades(node, clip, options, report)
    _set_crossfade_and_default_fades(node, clip)
    _reset_zero_fade_curves(node, fade_in_samples, fade_out_samples)
    _set_hidden_loop_fields(node, loop_end_val)

    sanitize_sample_ref(node, asset)
    clear_warp(node)
    return node


def _clamped_gain_val(val: float, abs_s: int, track_name: str, report: Report) -> float:
    if val > VOLUME_GAIN_MAX:
        report.warning(
            "GAIN_ENVELOPE_CLAMPED",
            f"gain point {val:.6f} exceeds the maximum {VOLUME_GAIN_MAX:.6f} in track {track_name!r} "
            f"at sample {abs_s}; clamped to the maximum",
            {
                "track": track_name,
                "sample": abs_s,
                "requested": val,
                "applied": VOLUME_GAIN_MAX,
            },
        )
        return VOLUME_GAIN_MAX
    if val < VOLUME_GAIN_MIN:
        report.warning(
            "GAIN_ENVELOPE_CLAMPED",
            f"gain point {val:.6f} is below the minimum {VOLUME_GAIN_MIN:.6f} in track {track_name!r} "
            f"at sample {abs_s}; clamped to the minimum",
            {
                "track": track_name,
                "sample": abs_s,
                "requested": val,
                "applied": VOLUME_GAIN_MIN,
            },
        )
        return VOLUME_GAIN_MIN
    return val


def _add_gain_envelope_events(
    events_container: ET.Element,
    gain_points: list[tuple[int, float]],
    track_name: str,
    report: Report,
    options: WriteOptions,
) -> None:
    event_id = 0
    first_val = _clamped_gain_val(gain_points[0][1], gain_points[0][0], track_name, report)
    sentinel = ET.SubElement(events_container, "FloatEvent")
    sentinel.set("Id", str(event_id))
    sentinel.set("Time", format_value(SENTINEL_TIME))
    sentinel.set("Value", format_value(first_val))
    event_id += 1

    for abs_s, val in gain_points:
        cval = _clamped_gain_val(val, abs_s, track_name, report)
        try:
            t_beats = _beats_of(abs_s, options)
        except AnchorError:
            continue
        ev = ET.SubElement(events_container, "FloatEvent")
        ev.set("Id", str(event_id))
        ev.set("Time", format_value(t_beats))
        ev.set("Value", format_value(cval))
        event_id += 1


def build_gain_envelope(
    gain_points: list[tuple[int, float]],
    volume_automation_target_id: str,
    envelope_prototype: ET.Element,
    allocator: "IdAllocator",
    posicionais: frozenset[str],
    unicos: frozenset[int],
    options: "WriteOptions",
    track_name: str,
    report: Report,
) -> ET.Element:
    """Constroi um AutomationEnvelope de FloatEvent para a curva de ganho da track.

    O primeiro evento usa Time = SENTINEL_TIME com o valor do primeiro ponto.
    Os demais eventos usam posicao em beats absolutos na timeline do Live.
    Valores fora do range [VOLUME_GAIN_MIN, VOLUME_GAIN_MAX] sao cortados e
    reportados como GAIN_ENVELOPE_CLAMPED.
    """
    node = clone(envelope_prototype, allocator, posicionais, unicos)

    # Apontar para o AutomationTarget certo
    pointee = node.find("EnvelopeTarget/PointeeId")
    if pointee is not None:
        pointee.set("Value", volume_automation_target_id)

    events_container = node.find(".//Events")
    if events_container is None:
        return node

    # Limpar eventos do prototype
    for old in list(events_container):
        events_container.remove(old)

    _add_gain_envelope_events(events_container, gain_points, track_name, report, options)

    return node
