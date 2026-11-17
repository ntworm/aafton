"""Parte de aaf_read: pan, sample rate, timecode, markers, tracks e checks.

Na fase 1, _read_static_track_pan, _session_sample_rate, _read_markers,
_resolve_track_names e _rank_composition_mob foram divididas em etapas (os
helpers novos saem delas); as demais definicoes vieram de aaf_read sem mudanca.
"""

from __future__ import annotations


import math


from typing import Any


from .aaf_channel_labels import CONJUNTO_51, rotulos_do_grupo

from .ir import MarkerIR, SessionIR

from .report import Report

from .timecode import TimecodeRate, exact_edit_rate

from .aaf_read_rates import classificar_diferenca_de_taxa, relatar_taxa_diferente

from .aaf_read_components import (
    _MONO_AUDIO_PAN_AUID,
    _PAN_PARAMETER_AUID,
    _edit_units_to_samples,
    _type_name,
)


def _pan_operations(component) -> list[object]:
    """Operacoes Mono Audio Pan, procuradas so por wrappers OperationGroup."""
    stack = [component]
    pan_operations: list[object] = []
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if _type_name(current) != "OperationGroup":
            continue

        operation_def = getattr(current, "operation", getattr(current, "operationdef", None))
        operation_auid = str(getattr(operation_def, "auid", "") or "").lower()
        if operation_auid == _MONO_AUDIO_PAN_AUID:
            pan_operations.append(current)

        for segment in list(getattr(current, "segments", []) or []):
            if _type_name(segment) == "OperationGroup":
                stack.append(segment)

    return pan_operations


def _pan_parameters(pan_operation) -> list[object]:
    """Parametros ParameterDef_Pan da operacao de pan."""
    parameters = []
    for parameter in list(getattr(pan_operation, "parameters", []) or []):
        parameter_def = getattr(parameter, "parameterdef", None)
        parameter_auid = str(getattr(parameter_def, "auid", "") or "").lower()
        if parameter_auid == _PAN_PARAMETER_AUID:
            parameters.append(parameter)
    return parameters


def _pan_value(parameter, report: Report, track_name: str) -> float | None:
    """Valor de pan do AAF, convertido para a escala do Live."""
    if "VaryingValue" in _type_name(parameter):
        report.warning(
            "AAF_PAN_AUTOMATION_UNSUPPORTED",
            f"the track {track_name!r} has continuous pan automation; only constant pan "
            "is supported and the track was kept centered",
            {"track": track_name},
        )
        return None

    raw_value = getattr(parameter, "value", None)
    try:
        aaf_pan = float(raw_value)
    except (TypeError, ValueError, OverflowError):
        aaf_pan = math.nan

    if not math.isfinite(aaf_pan) or not 0.0 <= aaf_pan <= 1.0:
        report.error(
            "AAF_PAN_VALUE_INVALID",
            f"the track {track_name!r} has AAF pan out of the [0, 1] range: "
            f"{raw_value!r}; the value was not applied",
            {"track": track_name, "value": repr(raw_value)},
        )
        return None

    return 2.0 * aaf_pan - 1.0


def _read_static_track_pan(
    component: object,
    report: Report,
    track_name: str,
) -> float | None:
    """Le o Mono Audio Pan que envolve a Sequence da track.

    O AAF Edit Protocol define ParameterDef_Pan em [0, 1], enquanto o Live
    usa [-1, 1]. A busca desce apenas por wrappers OperationGroup e nunca entra
    nos componentes da Sequence, para nao confundir pan de track com efeitos de
    clip.
    """
    pan_operations = _pan_operations(component)
    if not pan_operations:
        return None
    if len(pan_operations) != 1:
        report.error(
            "AAF_PAN_VALUE_INVALID",
            f"the track {track_name!r} has {len(pan_operations)} track pan operations; "
            "it is not possible to choose one without losing meaning",
            {"track": track_name, "operations": len(pan_operations)},
        )
        return None

    parameters = _pan_parameters(pan_operations[0])
    if len(parameters) != 1:
        report.error(
            "AAF_PAN_VALUE_INVALID",
            f"the track {track_name!r} has a Mono Audio Pan operation, but has "
            f"{len(parameters)} ParameterDef_Pan parameters",
            {"track": track_name, "parameters": len(parameters)},
        )
        return None

    return _pan_value(parameters[0], report, track_name)


def _taxa_do_descritor(descritor) -> int | None:
    """Taxa de amostragem declarada por um descritor de midia."""
    if descritor is None:
        return None
    sr_prop = descritor.get("SampleRate")
    if not sr_prop:
        return None
    if sr_prop.value is None:
        return None
    v = sr_prop.value
    try:
        return int(v.num / v.den) if hasattr(v, "num") else int(v)
    except Exception:
        return None


def _taxas_dos_descritores(f) -> dict[int, int]:
    """Contagem das taxas de amostragem declaradas nos descritores de midia."""
    taxas: dict[int, int] = {}
    for mob in f.content.mobs:
        taxa = _taxa_do_descritor(getattr(mob, "descriptor", None))
        if not taxa:
            continue
        try:
            valor = int(taxa)
        except (TypeError, ValueError):
            continue
        if valor > 1000:
            taxas[valor] = taxas.get(valor, 0) + 1
    return taxas


def _session_sample_rate(f, report: Report, slot_rate: int | None = None) -> int:
    """Taxa da sessao: slots de audio quando unanimes (>1000 Hz), senao descritores.

    Premiere e Pro Tools declaram edit_rate 24 (taxa de frame, nao de sample)
    nos slots; nesses casos vale a maioria dos descritores de midia.
    """
    taxas = _taxas_dos_descritores(f)
    if slot_rate is not None:
        escolhida, motivo = slot_rate, "from the composition audio slots' edit_rate"
    elif not taxas:
        msg = "no media descriptor declares a sample rate; assuming 48000"
        report.warning("AAF_NO_SAMPLE_RATE", msg, {})
        return 48000
    else:
        escolhida = max(taxas, key=lambda k: taxas[k])
        motivo = "by media descriptor majority"
    if len(taxas) > 1:
        msg = f"the AAF mixes sample rates {sorted(taxas)}; {escolhida} was adopted {motivo}"
        report.warning("AAF_MIXED_SAMPLE_RATES", msg, {"rates": sorted(taxas)})
    return escolhida


def _read_timecode(composition, report: Report) -> tuple[int, TimecodeRate]:
    """Devolve (frames_de_inicio, taxa). Avisa e assume zero quando nao ha."""
    for slot in composition.slots:
        segmento = getattr(slot, "segment", None)
        if _type_name(segmento) == "Timecode":
            drop = bool(getattr(segmento, "drop", False))
            slot_rate = getattr(slot, "edit_rate", None)
            if slot_rate is not None:
                frac = exact_edit_rate(slot_rate)
                if frac.denominator == 1001 and frac.numerator in (24000, 30000, 60000):
                    return int(getattr(segmento, "start", 0) or 0), TimecodeRate(
                        frac.numerator, 1001, drop
                    )
            fps = int(getattr(segmento, "fps", 25) or 25)
            num, den = (fps * 1000, 1001) if drop else (fps, 1)
            return int(getattr(segmento, "start", 0) or 0), TimecodeRate(num, den, drop)
    report.warning(
        "AAF_NO_TIMECODE",
        "the AAF has no TimecodeMobSlot; assuming a start at 00:00:00:00",
        {"composition": getattr(composition, "name", None)},
    )
    return 0, TimecodeRate(25, 1)


def _nome_do_marker(componente, indice: int) -> str:
    """Nome do marker: comentario, nome proprio ou indice."""
    comment_prop = componente.get("Comment") if hasattr(componente, "get") else None
    comment_val = (
        str(comment_prop.value).strip()
        if comment_prop and getattr(comment_prop, "value", None)
        else None
    )
    name_val = getattr(componente, "name", None)
    if name_val == "DescriptiveMarker":
        name_val = None
    nome = comment_val or name_val or getattr(componente, "comment", None)
    if not nome:
        nome = f"marker {indice + 1}"
    return str(nome)


def _posicao_do_marker(posicao: int, edit_rate, sample_rate: int) -> int:
    """Converte a posicao do marker da unidade do slot para samples."""
    if edit_rate is not None:
        return int(_edit_units_to_samples(posicao, edit_rate, sample_rate))
    return posicao


def _read_markers(
    composition, start_absolute: int, report: Report, sample_rate: int = 48000
) -> list[MarkerIR]:
    """Le markers do AAF.

    Cada marker com a sua propria posicao, nunca compartilhada. A propriedade
    AAF se chama 'Position' (capital P), acessivel como c['Position'].value.
    A posicao e declarada na unidade do slot (edit_rate), entao e convertida
    para samples da sessao.
    """
    encontrados: list[MarkerIR] = []
    for slot in composition.slots:
        segmento = getattr(slot, "segment", None)
        componentes = getattr(segmento, "components", None) or []
        edit_rate = getattr(slot, "edit_rate", None)
        for indice, componente in enumerate(componentes):
            if "Marker" not in _type_name(componente):
                continue
            # A propriedade AAF e 'Position', nao o atributo Python 'position'
            try:
                posicao = int(componente["Position"].value)
            except (KeyError, AttributeError, TypeError, ValueError):
                report.warning(
                    "AAF_MARKER_NO_POSITION",
                    f"marker {indice} without its own position was discarded",
                    {"slot": slot.name},
                )
                continue
            posicao_samples = _posicao_do_marker(posicao, edit_rate, sample_rate)
            encontrados.append(
                MarkerIR(
                    id=f"k{len(encontrados)}",
                    name=_nome_do_marker(componente, indice),
                    position_samples=start_absolute + posicao_samples,
                )
            )
    return encontrados


def check_pulldown(session: SessionIR, report: Report) -> None:
    """Compara a taxa de amostragem da sessao com a de cada arquivo.

    Nunca faz resample: corrigir pull-down em silencio produz uma sessao que
    soa certa e esta errada, conforme a secao 5.6 do spec. Taxa padrao
    diferente da sessao passa com aviso, porque o Live reamostra ao tocar.
    """
    for asset in session.media:
        if asset.id == "m_video":
            continue
        if asset.sample_rate is None or asset.sample_rate == session.sample_rate:
            continue
        classificacao = classificar_diferenca_de_taxa(asset.sample_rate, session.sample_rate)
        relatar_taxa_diferente(
            classificacao,
            asset.filename,
            asset.sample_rate,
            session.sample_rate,
            report,
        )


def check_frame_alignment(session: SessionIR, report: Report) -> None:
    """Avisa quando um clip nao cai em fronteira de frame. Nunca move nada."""
    from .timecode import samples_per_frame

    por_frame = samples_per_frame(session.timecode_rate, session.sample_rate)
    if por_frame.denominator != 1:
        report.info(
            "FRAME_GRID_FRACTIONAL",
            f"at {session.timecode_rate.num}/{session.timecode_rate.den} and "
            f"{session.sample_rate} Hz one frame has {float(por_frame):.4f} samples, "
            f"so no cut falls on a whole sample and the frame alignment is not "
            f"verifiable",
            {"samples_per_frame": str(por_frame)},
        )
        return

    passo = int(por_frame)
    for track in session.tracks:
        for clip in track.clips:
            relativo = clip.timeline_start_samples - session.start_timecode_samples
            resto = relativo % passo
            if resto:
                report.warning(
                    "CLIP_OFF_FRAME",
                    f"the clip {clip.name!r} on the track {track.name!r} starts "
                    f"{resto} sample(s) after the frame boundary",
                    {
                        "track": track.name,
                        "clip": clip.name,
                        "offset_samples": resto,
                        "samples_per_frame": passo,
                    },
                )


def check_plugins(session: SessionIR, report: Report) -> None:
    """Plug-ins do AAF viram manifesto no relatorio, nunca device do Live."""
    for track in session.tracks:
        for plugin in track.plugins:
            report.warning(
                "PLUGIN_NOT_CONVERTED",
                f"the plug-in {plugin.name!r} on the track {track.name!r} has no "
                f"automatic equivalent in Live and was not converted",
                {"track": track.name, "plugin": plugin.name},
            )


def _ptn(item: tuple[int, Any]) -> int:
    """Numero da trilha fisica do slot, usado como chave de ordenacao."""
    idx, s = item
    ptn_prop = s.get("PhysicalTrackNumber") if hasattr(s, "get") else None
    if ptn_prop is not None:
        try:
            return int(ptn_prop.value)
        except (AttributeError, ValueError, TypeError):
            pass
    attr = getattr(s, "physical_track_number", None)
    if attr is not None:
        try:
            return int(attr)
        except (ValueError, TypeError):
            pass
    return idx


def _layout_de_canais(nome: str, count: int) -> tuple[list[str], str]:
    """Sufixos de canal e layout de um grupo de slots com o mesmo nome."""
    if count == 2:
        return ["L", "R"], "stereo"
    if count == 4 and "AMB" in nome.upper():
        return ["W", "X", "Y", "Z"], "ambisonic 1st order"
    if count == 4:
        return ["L", "R", "Ls", "Rs"], "quad"
    if count == 6:
        return ["L", "R", "C", "LFE", "Ls", "Rs"], "5.1"
    return [f"ch{i + 1}" for i in range(count)], f"generic {count}-channel"


def _rotulos_da_fonte(nome: str, sorted_items: list[tuple[int, Any]], report: Report):
    """Rotulos de canal que os proprios slots do AAF declaram, ou None.

    None significa que a evidencia nao cobre o grupo: quem decide o rotulo
    passa a ser o layout padrao.
    """
    if len(sorted_items) != len(CONJUNTO_51):
        return None
    rotulos = rotulos_do_grupo([slot for _, slot in sorted_items])
    if rotulos is None:
        return None
    report.info(
        "CHANNEL_LABELS_FROM_SOURCE",
        f"the channel labels of the track {nome!r} came from the AAF itself",
        {"track": nome, "channels": len(rotulos), "labels": list(rotulos)},
    )
    return rotulos


def _resolve_track_names(audio_slots: list[tuple[int, Any]], report: Report) -> dict[int, str]:
    """Agrupa slots por nome e atribui sufixos de canal quando ha multiplos slots com o mesmo nome."""
    groups: dict[str, list[tuple[int, Any]]] = {}
    for orig_idx, slot in audio_slots:
        nome = getattr(slot, "name", None)
        if nome:
            groups.setdefault(nome, []).append((orig_idx, slot))

    resolved: dict[int, str] = {}
    for nome, group_items in groups.items():
        count = len(group_items)
        if count == 1:
            orig_idx, _ = group_items[0]
            resolved[orig_idx] = nome
            continue

        sorted_items = sorted(group_items, key=_ptn)
        rotulos = _rotulos_da_fonte(nome, sorted_items, report)
        if rotulos is not None:
            for (orig_idx, _), rotulo in zip(sorted_items, rotulos):
                resolved[orig_idx] = f"{nome} [{rotulo}]"
            continue

        suffixes, layout = _layout_de_canais(nome, count)

        if count > 2:
            report.info(
                "CHANNEL_LAYOUT_GUESSED",
                f"assumed layout {layout} for the track {nome!r} with {count} channels",
                {"track": nome, "channels": count, "layout": layout},
            )

        for (orig_idx, _), suffix in zip(sorted_items, suffixes):
            resolved[orig_idx] = f"{nome} [{suffix}]"

    return resolved


def _slot_de_som(slot, segment) -> bool:
    """O slot de timeline e de audio, pelo media kind ou pelo DataDef."""
    media_kind = getattr(slot, "media_kind", None) or getattr(segment, "media_kind", None)
    datadef = getattr(slot, "datadef", None) or getattr(segment, "datadef", None)
    if media_kind is not None and str(media_kind).lower() == "sound":
        return True
    if datadef is not None and "sound" in str(getattr(datadef, "name", "")).lower():
        return True
    return False


def _samples_do_slot(slot, segment, default_sample_rate: int) -> int:
    """Comprimento do slot de audio, em samples."""
    length = 0
    try:
        length = int(getattr(slot, "length", 0) or 0)
    except Exception:
        pass
    if length <= 0 and segment is not None:
        try:
            length = int(getattr(segment, "length", 0) or 0)
        except Exception:
            pass
    edit_rate = getattr(slot, "edit_rate", None)
    if edit_rate is not None and length > 0:
        try:
            return int(_edit_units_to_samples(length, edit_rate, default_sample_rate))
        except Exception:
            return length
    return length


def _rank_composition_mob(mob: object, default_sample_rate: int = 48000) -> tuple[int, int, int]:
    """Pontua um CompositionMob para selecao deterministica.

    Rank 1: UsageCode == 'Usage_TopLevel'
    Rank 2: Quantidade de slots de timeline de audio (TimelineMobSlot com DataDef de Sound)
    Rank 3: Duracao total dos slots de timeline de audio (em samples)
    """
    usage = getattr(mob, "usage", None)
    is_top_level = 1 if (usage == "Usage_TopLevel" or str(usage).endswith("Usage_TopLevel")) else 0

    sound_slots_count = 0
    total_audio_samples = 0

    for slot in getattr(mob, "slots", []) or []:
        if _type_name(slot) != "TimelineMobSlot":
            continue
        segment = getattr(slot, "segment", None)
        if segment is None or _type_name(segment) == "Timecode":
            continue

        if _slot_de_som(slot, segment):
            sound_slots_count += 1
            total_audio_samples += _samples_do_slot(slot, segment, default_sample_rate)

    return (is_top_level, sound_slots_count, total_audio_samples)
