# src/aafton/als_read.py
""".als -> SessionIR.

A ancoragem vem do sidecar, nunca de suposicao. Este modulo e o espelho de
als_write: onde o escritor subtrai a origem, o leitor soma de volta, e o teste
de ida e volta exige igualdade sample a sample.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .als_xml import get_value, load_als
from .als_write import SENTINEL_TIME
from .ir import MarkerIR, SessionIR, TrackIR
from .ltc_track import drop_generated_tracks
from .report import Report
from .sidecar import read_anchor, parse_anchor_locator_name, SidecarMissingError, Anchor
from .timecode import TimecodeRate, _LABEL_RE

from .als_read_bindings import (
    BindingLookup,
    apply_binding,
    apply_legacy_channels,
    build_binding_indexes,
    check_multichannel_members,
    infer_channels_from_media,
    match_binding,
    uses_legacy_channels,
)
from .als_read_clips import (
    ReadContext,
    _beats_to_samples as _beats_to_samples,
    _media_path as _media_path,
    _same_media_path as _same_media_path,
    read_track_clips,
    track_name as _track_name,
)
from .als_read_mix import read_unsupported_mix

# Mixers cujo envelope de automacao tem representacao no AAF, e o atributo do
# TrackIR que recebe a curva. Qualquer envelope fora daqui continua sem
# representacao e por isso continua reportado.
AUTOMATION_TARGETS = (("Volume", "gain_points"), ("Pan", "pan_points"))


def _float_event_points(envelope: ET.Element) -> list[tuple[float, float]]:
    """(beats, value) de cada FloatEvent real do envelope.

    O escritor emite um evento sentinela antes do primeiro ponto, com
    ``Time = SENTINEL_TIME`` e o valor do primeiro ponto. Ele nao e uma posicao
    na timeline, entao e descartado aqui; sem isso a curva ganharia um ponto
    milhoes de beats antes da origem.
    """
    events = envelope.find("Automation/Events")
    if events is None:
        return []
    pontos: list[tuple[float, float]] = []
    for ev in events:
        if ev.tag != "FloatEvent":
            continue
        tempo = ev.get("Time")
        valor = ev.get("Value")
        if tempo is None or valor is None:
            continue
        try:
            t_beats = float(tempo)
            v = float(valor)
        except ValueError:
            continue
        if t_beats == SENTINEL_TIME:
            continue
        pontos.append((t_beats, v))
    return pontos


def _automation_destinations(track_node: ET.Element) -> dict[str, str]:
    """Id do AutomationTarget do mixer -> atributo do TrackIR que recebe a curva."""
    destinos: dict[str, str] = {}
    for mixer, atributo in AUTOMATION_TARGETS:
        alvo = track_node.find(f"DeviceChain/Mixer/{mixer}/AutomationTarget")
        if alvo is not None and alvo.get("Id"):
            destinos[alvo.get("Id", "")] = atributo
    return destinos


def _automation_curves(
    envelopes: list[ET.Element],
    destinos: dict[str, str],
    absolute,
) -> tuple[dict[str, dict[int, float]], list[str]]:
    """Curvas por atributo e os envelopes que ficaram sem destino."""
    curvas: dict[str, dict[int, float]] = {}
    sem_destino: list[str] = []
    for envelope in envelopes:
        if not envelope.findall("Automation/Events/*"):
            continue
        ponteiro = envelope.find("EnvelopeTarget/PointeeId")
        alvo_id = ponteiro.get("Value", "") if ponteiro is not None else ""
        atributo = destinos.get(alvo_id)
        if atributo is None:
            sem_destino.append(alvo_id or "<without PointeeId>")
            continue
        pontos = curvas.setdefault(atributo, {})
        for t_beats, valor in _float_event_points(envelope):
            pontos[absolute(t_beats)] = valor
    return curvas, sem_destino


def _extract_track_automation(
    track: TrackIR,
    track_node: ET.Element,
    absolute,
    report: Report,
) -> bool:
    """Le Volume e Pan da track para gain_points e pan_points.

    O envelope e identificado pelo ``PointeeId``, que aponta para o Id do
    ``AutomationTarget`` correspondente no DeviceChain. Dois envelopes do mesmo
    mixer sao fundidos ponto a ponto, e o evento mais recente vence.

    Devolve ``True`` quando sobrou envelope sem representacao no AAF, caso em que
    quem chama registra o aviso ALS_AUTOMATION_UNSUPPORTED. Envelope reconciliado
    nao gera aviso: a curva viaja em SessionIR.
    """
    envelopes = track_node.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope")
    if not envelopes:
        return False

    destinos = _automation_destinations(track_node)
    curvas, sem_destino = _automation_curves(envelopes, destinos, absolute)

    for atributo, pontos in curvas.items():
        if pontos:
            setattr(track, atributo, sorted(pontos.items()))

    if sem_destino:
        report.warning(
            "ALS_AUTOMATION_UNSUPPORTED",
            f"automation of {track.name!r} is not yet reconciled for export",
            {"track": track.name, "envelopes": sorted(sem_destino)},
        )
        return True
    return False


def _anchor_from_locator(path: Path, root: ET.Element, report: Report) -> Anchor | None:
    """Recupera a ancoragem de um locator 'TC ' quando o sidecar sumiu."""
    rate_padrao = TimecodeRate(24, 1)
    for locator in root.findall(".//LiveSet/Locators/Locators/Locator"):
        nome_node = locator.find("Name")
        if nome_node is None:
            continue
        nome = nome_node.get("Value", "")
        if not nome.startswith("TC "):
            continue
        samples = parse_anchor_locator_name(nome, rate_padrao)
        if samples is None:
            continue
        report.warning(
            "SIDECAR_RECOVERED",
            "the anchoring came from the locator because the sidecar was not there",
            {"locator": nome},
        )
        return Anchor(
            live_origin_samples=samples,
            session_start_samples=samples,
            sample_rate=48000,
            timecode_rate=rate_padrao,
            bpm=60.0,
            source_path=str(path),
        )
    return None


def _load_anchor(path: Path, root: ET.Element, report: Report) -> Anchor:
    """Ancoragem do sidecar; sem sidecar, tenta o locator 'TC '."""
    try:
        return read_anchor(path)
    except SidecarMissingError as e:
        anchor = _anchor_from_locator(path, root, report)
        if anchor is None:
            raise e
        return anchor


def _main_track(root: ET.Element) -> ET.Element | None:
    """A track master do Live: MainTrack no Live 12, MasterTrack no Live 11."""
    for tipo in ("MainTrack", "MasterTrack"):
        node = root.find(f".//{tipo}")
        if node is not None:
            return node
    return None


def _check_tempo(anchor: Anchor | None, root: ET.Element, report: Report) -> None:
    """Avisa quando o tempo do Live difere do BPM da ancoragem."""
    main_track = _main_track(root)
    if main_track is None:
        return
    tempo = get_value(main_track, "DeviceChain/Mixer/Tempo/Manual")
    if tempo is not None and anchor is not None and float(tempo) != anchor.bpm:
        report.error(
            "ALS_TEMPO_CHANGED",
            "the Live tempo differs from the anchoring; the tempo map is not supported yet",
            {"sidecar_bpm": anchor.bpm, "live_bpm": tempo},
        )


def _tempo_automation_envelopes(main_track: ET.Element, tempo_id: str) -> list[ET.Element]:
    """Envelopes da MainTrack que apontam para o AutomationTarget de tempo."""
    envelopes: list[ET.Element] = []
    for env in main_track.findall("AutomationEnvelopes/Envelopes/AutomationEnvelope"):
        ponteiro = env.find("EnvelopeTarget/PointeeId")
        if ponteiro is not None and ponteiro.get("Value") == tempo_id:
            envelopes.append(env)
    return envelopes


def _tempo_points_vary(valores: list[float], anchor: Anchor | None, tempo: str | None) -> bool:
    """Diz se a curva de tempo muda de verdade, e nao so repete o BPM."""
    ref_bpm = anchor.bpm if anchor else float(tempo or 120.0)
    return any(abs(v - valores[0]) > 1e-4 for v in valores) or (
        anchor is not None and abs(valores[0] - ref_bpm) > 1e-4
    )


def _check_tempo_automation(anchor: Anchor | None, root: ET.Element, report: Report) -> None:
    """Avisa quando a automacao de tempo da MainTrack nao e exportavel."""
    main_track = _main_track(root)
    if main_track is None:
        return
    tempo = get_value(main_track, "DeviceChain/Mixer/Tempo/Manual")
    tempo_target = main_track.find("DeviceChain/Mixer/Tempo/AutomationTarget")
    tempo_id = tempo_target.get("Id") if tempo_target is not None else None
    if not tempo_id:
        return
    for env in _tempo_automation_envelopes(main_track, tempo_id):
        pontos = list(_float_event_points(env))
        if not pontos:
            continue
        valores = [v for _, v in pontos]
        if not _tempo_points_vary(valores, anchor, tempo):
            continue
        report.error(
            "ALS_TEMPO_AUTOMATION_UNSUPPORTED",
            "tempo automation in the MainTrack is not supported for strict export",
            {
                "target": "Tempo",
                "points": len(pontos),
                "bpm_min": min(valores),
                "bpm_max": max(valores),
            },
        )
        break


def _group_names(root: ET.Element) -> dict[str, str]:
    """Nome de cada grupo de faixas do LiveSet."""
    group_names: dict[str, str] = {}
    for group_node in root.iter("GroupTrack"):
        gid = group_node.get("Id")
        if gid:
            group_names[gid] = _track_name(group_node)
    return group_names


def _set_group_path(track: TrackIR, track_node: ET.Element, group_names: dict[str, str]) -> None:
    """Acrescenta o nome do grupo da faixa ao group_path."""
    tgid_elem = track_node.find("TrackGroupId")
    if tgid_elem is None:
        return
    tgid = tgid_elem.get("Value")
    if not tgid or tgid == "-1" or tgid not in group_names:
        return
    gname = group_names[tgid]
    if gname not in track.group_path:
        track.group_path.append(gname)


def _read_track(
    ctx: ReadContext,
    track_node: ET.Element,
    indice: int,
    group_names: dict[str, str],
    lookup: BindingLookup,
) -> TrackIR:
    """Le uma faixa de audio: mixer, automacao, clips e binding de origem."""
    track = TrackIR(
        id=f"t{indice}",
        source_index=indice,
        name=_track_name(track_node),
        kind="audio",
        channel_count=1,
    )
    pan = get_value(track_node, "DeviceChain/Mixer/Pan/Manual")
    if pan is not None:
        track.pan = float(pan)
    track.muted = get_value(track_node, "DeviceChain/Mixer/Speaker/Manual") == "false"
    volume = get_value(track_node, "DeviceChain/Mixer/Volume/Manual")
    if volume is not None:
        track.gain_linear = float(volume)
    if _extract_track_automation(track, track_node, ctx.absolute, ctx.report):
        message = f"automation of {track.name!r} is not yet reconciled for export"
        ctx.session.unsupported_export_features.append(message)
    _set_group_path(track, track_node, group_names)
    read_track_clips(ctx, track, track_node, indice)

    binding = match_binding(lookup, track_node, track, indice, ctx.report)
    if binding is not None:
        apply_binding(track, binding, ctx.session)
    elif uses_legacy_channels(lookup, track):
        apply_legacy_channels(track, ctx.anchor, indice)
    elif track.clips:
        infer_channels_from_media(track, ctx.session, ctx.report)
    return track


def _read_markers(ctx: ReadContext, root: ET.Element) -> None:
    """Le os marcadores do LiveSet, ignorando os de timecode."""
    for indice, locator in enumerate(root.iter("Locator")):
        nome_node = locator.find("Name")
        tempo_node = locator.find("Time")
        if nome_node is None or tempo_node is None:
            continue
        nome = nome_node.get("Value", "")
        if nome.startswith("TC ") or _LABEL_RE.match(nome):
            continue
        ctx.session.markers.append(
            MarkerIR(
                id=f"k{indice}",
                name=nome,
                position_samples=ctx.absolute(float(tempo_node.get("Value", "0"))),
                comment=get_value(locator, "Annotation"),
            )
        )


def read_session(als_path: str | Path, report: Report) -> SessionIR:
    """Le um .als gerado por esta ferramenta e reconstroi o SessionIR."""
    path = Path(als_path)
    root = load_als(path)
    anchor = _load_anchor(path, root, report)
    _check_tempo(anchor, root, report)
    _check_tempo_automation(anchor, root, report)

    session = SessionIR(
        name=path.stem,
        sample_rate=anchor.sample_rate,
        timecode_rate=anchor.timecode_rate,
        start_timecode_samples=anchor.session_start_samples,
        source_path=str(path),
        source_application="Ableton Live",
    )
    ctx = ReadContext(path, anchor, report, session)

    audio_nodes = root.findall("./LiveSet/Tracks/AudioTrack")
    lookup = BindingLookup(
        anchor=anchor,
        indexes=build_binding_indexes(anchor),
        current_names={_track_name(n) for n in audio_nodes},
    )
    group_names = _group_names(root)
    for indice, track_node in enumerate(audio_nodes):
        session.tracks.append(_read_track(ctx, track_node, indice, group_names, lookup))

    read_unsupported_mix(ctx, root)
    check_multichannel_members(anchor, session, report)
    _read_markers(ctx, root)
    drop_generated_tracks(session, report)
    return session
