import xml.etree.ElementTree as ET
from pathlib import Path

from .als_paths import find_prototype
from .als_xml import (
    IdAllocator,
    classify_id_tags,
    collect_ids,
    format_value,
    load_als,
    renumber_siblings,
    save_als,
    unique_ids,
    validate_document,
)
from .ir import SessionIR
from .report import Report
from .timecode import samples_to_label

from .als_write_base import (
    AnchorError as AnchorError,
    LIVE_CLIP_TICKS_PER_BEAT as LIVE_CLIP_TICKS_PER_BEAT,
    SENTINEL_TIME as SENTINEL_TIME,
    SOURCE as SOURCE,
    TIMELINE as TIMELINE,
    VOLUME_GAIN_MAX as VOLUME_GAIN_MAX,
    VOLUME_GAIN_MIN as VOLUME_GAIN_MIN,
    WriteOptions as WriteOptions,
    _clip_times as _clip_times,
    _live_samples as _live_samples,
    attach_to_group as attach_to_group,
    clear_tempo_automation as clear_tempo_automation,
    clear_warp as clear_warp,
    detach_from_group as detach_from_group,
    samples_to_beats as samples_to_beats,
    timecode_rate_to_smpte_format as timecode_rate_to_smpte_format,
    to_beats as to_beats,
)


from .als_write_clips import (
    build_audio_clip as build_audio_clip,
    build_gain_envelope as build_gain_envelope,
    sanitize_sample_ref as sanitize_sample_ref,
)

from .als_write_groups import expansion_groups, infer_track_groups, organize_groups
from .als_write_locators import write_locators
from .als_write_sidecar import write_anchor_sidecar
from .als_write_tracks import WriteContext, emit_tracks


def _resolve_origin(session: SessionIR, options: WriteOptions) -> tuple[int, WriteOptions]:
    origin = (
        session.start_timecode_samples
        if options.live_origin_samples is None
        else options.live_origin_samples
    )
    if options.live_origin_samples is None:
        all_starts = [c.timeline_start_samples for c in session.all_clips()]
        all_starts.extend([m.position_samples for m in session.markers])
        if all_starts and min(all_starts) < origin:
            origin = min(all_starts)

    origin_options = WriteOptions(
        sample_rate=options.sample_rate,
        bpm=options.bpm,
        live_origin_samples=origin,
        permissive=options.permissive,
        fades=options.fades,
        stereo=options.stereo,
        groups=options.groups,
    )
    return origin, origin_options


def _apply_smpte_format(root: ET.Element, session: SessionIR) -> None:
    smpte_format_node = root.find(".//LiveSet/SmpteFormat")
    if smpte_format_node is not None:
        smpte_format_node.set("Value", timecode_rate_to_smpte_format(session.timecode_rate))
    else:
        liveset = root.find(".//LiveSet")
        if liveset is not None:
            ET.SubElement(
                liveset,
                "SmpteFormat",
                {"Value": timecode_rate_to_smpte_format(session.timecode_rate)},
            )


def _update_report_metadata(
    root: ET.Element,
    session: SessionIR,
    options: WriteOptions,
    origin: int,
    template_path: str | Path,
    report: Report,
) -> None:
    report.metadata.update(
        {
            "template": str(template_path),
            "live_version": root.get("Creator"),
            "sample_rate": session.sample_rate,
            "bpm": options.bpm,
            "live_origin_timecode": samples_to_label(
                origin, session.timecode_rate, session.sample_rate
            ),
            "session_start_timecode": samples_to_label(
                session.start_timecode_samples, session.timecode_rate, session.sample_rate
            ),
        }
    )


def _find_prototypes(root: ET.Element) -> tuple[ET.Element, ET.Element, ET.Element, ET.Element]:
    track_prototype = find_prototype(root, "AudioTrack")
    group_prototype = find_prototype(root, "GroupTrack")
    clip_prototype = find_prototype(root, "AudioClip")
    locator_prototype = find_prototype(root, "Locator")
    if (
        track_prototype is None
        or clip_prototype is None
        or locator_prototype is None
        or group_prototype is None
    ):
        raise ValueError(
            "the template does not have the needed prototypes; run "
            "pytest tests/unit/test_template_completeness.py"
        )
    return track_prototype, group_prototype, clip_prototype, locator_prototype


def _find_gain_envelope_prototype(root: ET.Element) -> ET.Element | None:
    # Encontrar o prototipo de AutomationEnvelope com FloatEvent (da MainTrack)
    gain_envelope_prototype: ET.Element | None = None
    for main_track in root.iter("MainTrack"):
        for ae in main_track.iter("AutomationEnvelope"):
            events = ae.find(".//Events")
            if events is not None and any(ev.tag == "FloatEvent" for ev in events):
                gain_envelope_prototype = ae
                break
        if gain_envelope_prototype is not None:
            break
    return gain_envelope_prototype


def _first_free_track_id(root: ET.Element) -> int:
    ids_de_track = {
        int(t.get("Id"))
        for tag in ("AudioTrack", "MidiTrack", "GroupTrack", "ReturnTrack", "MainTrack")
        for t in root.iter(tag)
        if (t.get("Id") or "").lstrip("-").isdigit()
    }
    return max(ids_de_track, default=0) + 1


def _build_context(
    session: SessionIR,
    options: WriteOptions,
    report: Report,
    allocator: IdAllocator,
    posicionais: list[str],
    unicos: set[str],
    track_prototype: ET.Element,
    group_prototype: ET.Element,
    clip_prototype: ET.Element,
    gain_envelope_prototype: ET.Element | None,
    next_track_id: int,
    output_path: str | Path,
) -> WriteContext:
    return WriteContext(
        session=session,
        options=options,
        report=report,
        allocator=allocator,
        posicionais=posicionais,
        unicos=unicos,
        track_prototype=track_prototype,
        group_prototype=group_prototype,
        clip_prototype=clip_prototype,
        gain_envelope_prototype=gain_envelope_prototype,
        next_track_id=next_track_id,
        track_to_group=infer_track_groups(session, options),
        novas_tracks=[],
        created_groups={},
        emitted_tracks=set(),
        audio_source_indices=[],
        track_bindings=[],
        audio_track_idx=0,
        clip_ranges=[],
        # A pasta do set: a midia que esta dentro dela e escrita com o
        # caminho relativo, para a pasta copiada continuar achando os
        # arquivos que ela leva junto.
        als_dir=Path(output_path).resolve().parent,
    )


def _report_track_groups(ctx: WriteContext, options: WriteOptions, report: Report) -> None:
    # Os grupos da expansao de canais e da organizacao vem do IR, nao do prefixo: ficam fora da nota.
    do_ir = set(expansion_groups(ctx.session).values()) | set(organize_groups(ctx.session).values())
    prefixos = [g for g in ctx.created_groups if g not in do_ir]
    if options.groups == "prefix" and prefixos:
        msg = "Grouping by prefix rebuilt folders from the track names (the AAF has no folders): "
        group_to_count = {g: 0 for g in prefixos}
        for t_id, g in ctx.track_to_group.items():
            if g in group_to_count:
                group_to_count[g] += 1
        details = [f"{prefix} ({group_to_count[prefix]} tracks)" for prefix in prefixos]
        msg += ", ".join(details)
        msg += f". {ctx.total_soltas} loose tracks."
        report.info("TRACK_GROUPS_RECONSTRUCTED", msg)


def _replace_tracks_container(root: ET.Element, ctx: WriteContext) -> None:
    tracks_container = root.find(".//LiveSet/Tracks")
    if tracks_container is not None:
        preservadas = [n for n in tracks_container if n.tag in ("ReturnTrack", "MainTrack")]
        for filho in list(tracks_container):
            tracks_container.remove(filho)
        for t in ctx.novas_tracks + preservadas:
            tracks_container.append(t)


def _apply_tempo(root: ET.Element, options: WriteOptions, report: Report) -> None:
    tempo = root.find(".//MainTrack//Tempo/Manual")
    if tempo is None:
        tempo = root.find(".//Tempo/Manual")
    if tempo is not None:
        tempo.set("Value", format_value(float(options.bpm)))
    clear_tempo_automation(root, report)


def _drop_orphan_envelopes(root: ET.Element, report: Report) -> None:
    presentes = collect_ids(root)
    for envelopes in root.iter("AutomationEnvelopes"):
        for lista in envelopes.iter("Envelopes"):
            for envelope in list(lista):
                alvo = envelope.find("EnvelopeTarget/PointeeId")
                if alvo is None:
                    continue
                bruto = alvo.get("Value", "")
                if bruto.lstrip("-").isdigit() and int(bruto) not in presentes:
                    lista.remove(envelope)
                    report.info(
                        "ENVELOPE_DROPPED",
                        f"automation envelope pointing to the object {bruto} was "
                        f"removed because the object does not exist in the generated set",
                        {"pointee_id": bruto},
                    )
            renumber_siblings(lista, "AutomationEnvelope")


def write_session(
    session: SessionIR,
    template_path: str | Path,
    output_path: str | Path,
    options: WriteOptions,
    report: Report,
) -> None:
    root = load_als(template_path)
    allocator = IdAllocator.from_root(root)
    posicionais, _ = classify_id_tags(root)
    unicos = unique_ids(root)

    origin, options = _resolve_origin(session, options)
    _apply_smpte_format(root, session)
    _update_report_metadata(root, session, options, origin, template_path, report)

    track_prototype, group_prototype, clip_prototype, locator_prototype = _find_prototypes(root)
    gain_envelope_prototype = _find_gain_envelope_prototype(root)
    next_track_id = _first_free_track_id(root)

    ctx = _build_context(
        session,
        options,
        report,
        allocator,
        posicionais,
        unicos,
        track_prototype,
        group_prototype,
        clip_prototype,
        gain_envelope_prototype,
        next_track_id,
        output_path,
    )
    emit_tracks(ctx)

    _report_track_groups(ctx, options, report)
    _replace_tracks_container(root, ctx)

    write_locators(
        root,
        session,
        options,
        report,
        allocator,
        posicionais,
        unicos,
        locator_prototype,
        origin,
    )

    _apply_tempo(root, options, report)
    _drop_orphan_envelopes(root, report)

    allocator.write_next_pointee_id(root)
    validate_document(root, posicionais)
    save_als(root, output_path)

    write_anchor_sidecar(output_path, session, options, ctx, origin)
