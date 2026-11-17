"""AAF -> SessionIR.

O percurso da sequencia segue tres regras, e a terceira e a que erra facil:

- SourceClip vira ClipIR e avanca o cursor pelo seu comprimento.
- Filler avanca o cursor sem gerar clip.
- Transition **recua** o cursor pelo seu comprimento, porque em AAF a transicao
  representa a sobreposicao entre os dois clips vizinhos, nao um trecho a mais
  de timeline.

Unidade de tempo: todo slot tem um edit_rate que define a unidade de posicao e
comprimento. No Resolve e 48000 (sample); no Premiere e Pro Tools e 24 (frame).
Ler o comprimento sem converter erra por um fator de 2000 naqueles dois dialetos.
"""

from __future__ import annotations

import math
from pathlib import Path

import aaf2

from .ir import SessionIR
from .report import Report
from .timecode import TimecodeRate, exact_edit_rate, frames_to_samples_exact, quantize_samples

from .aaf_read_components import (
    _AUDIO_CHANNEL_COMBINER_AUID as _AUDIO_CHANNEL_COMBINER_AUID,
    _FADE_MOB_RE as _FADE_MOB_RE,
    _MONO_AUDIO_PAN_AUID as _MONO_AUDIO_PAN_AUID,
    _PAN_PARAMETER_AUID as _PAN_PARAMETER_AUID,
    _SEPARADOR_ANTES_DA_UNIDADE as _SEPARADOR_ANTES_DA_UNIDADE,
    _declared_source_samples as _declared_source_samples,
    _descer_ate_sequence as _descer_ate_sequence,
    _edit_units_to_samples as _edit_units_to_samples,
    _fonte_cobre_o_fade as _fonte_cobre_o_fade,
    _is_audio_channel_combiner as _is_audio_channel_combiner,
    _is_fade_source_clip as _is_fade_source_clip,
    _source_filename as _source_filename,
    _source_mob_ref as _source_mob_ref,
    _source_offset_samples as _source_offset_samples,
    _type_name as _type_name,
    _unwrap as _unwrap,
    _url_para_caminho as _url_para_caminho,
)


from .aaf_read_points import (
    append_control_point,
    choose_points,
    constant_points,
    is_varying,
    point_list_of,
)


from .aaf_read_reports import (
    _avisar_clips_curtos as _avisar_clips_curtos,
    _avisar_pan_estatico as _avisar_pan_estatico,
    _avisar_plugins_ignorados as _avisar_plugins_ignorados,
    _resumir_fades as _resumir_fades,
    _resumir_ganho as _resumir_ganho,
)


from .aaf_read_session import (
    _rank_composition_mob as _rank_composition_mob,
    _read_markers as _read_markers,
    _read_static_track_pan as _read_static_track_pan,
    _read_timecode as _read_timecode,
    _resolve_track_names as _resolve_track_names,
    _session_sample_rate as _session_sample_rate,
    _slot_de_som,
    _taxas_dos_descritores,
    check_frame_alignment as check_frame_alignment,
    check_plugins as check_plugins,
    check_pulldown as check_pulldown,
)


from .aaf_read_tracks import AssetIndex, TrackContext, read_track


# Medido nos fixtures: o Resolve chama o parametro de "Amplitude" e o Premiere
# de "Amplitude multiplier". O Pro Tools nao expoe ganho por AAF, so
# identificadores opacos da Avid, e por isso aparece na lista de ignorados.
GAIN_PARAMETER_NAMES = {
    "amplitude",
    "amplitude multiplier",
    "level",
    "gain",
    "audiogain",
}
OPAQUE_PARAMETER_NAMES = {"avideffectid", "avidparameterbyteorder"}


def _read_point_list(parameters) -> list[tuple[float, float]]:
    """Extrai lista de pontos (tempo_normalizado, valor) de um OperationGroup.

    Retorna lista vazia se nao ha parametro de ganho ou nao ha PointList.
    """
    for parametro in parameters:
        nome = str(getattr(parametro, "name", "") or "").lower()
        if nome and nome not in GAIN_PARAMETER_NAMES:
            continue
        if not is_varying(parametro):
            return constant_points(parametro)

        # VaryingValue: extrair PointList
        point_list = point_list_of(parametro)
        if point_list is None:
            return []

        result: list[tuple[float, float]] = []
        raw_values: list[float] = []
        for cp in point_list:
            append_control_point(cp, result, raw_values)

        return choose_points(result, raw_values)
    return []


def _has_varying_gain(parameters) -> bool:
    """Retorna True se o OperationGroup tem automacao de ganho real (valores variando)."""
    pts = _read_point_list(parameters)
    if len(pts) < 2:
        return False
    return not all(math.isclose(p[1], pts[0][1], rel_tol=1e-9) for p in pts)


def _constant_gain(
    parameters, report: Report, track_name: str, clip_name: str = "", is_fade: bool = False
) -> float:
    """Extrai ganho constante dos parametros de um OperationGroup.

    Devolve 1.0 quando nao ha ganho. Quando o parametro e VaryingValue, inspeciona
    a PointList: se todos os pontos tiverem o mesmo valor, devolve o valor linear
    sem aviso. Se os valores variarem (automacao real), emite aviso com o nome
    do clip e rampa de ganho e devolve 1.0 (0 dB).
    """
    pts = _read_point_list(parameters)
    if not pts:
        return 1.0

    values = [p[1] for p in pts]
    if all(math.isclose(v, values[0], rel_tol=1e-9) for v in values):
        return float(values[0])

    start_val = values[0]
    end_val = values[-1]

    if is_fade:
        msg = (
            f"the gain automation ramp (from {start_val:.3f} to {end_val:.3f}) "
            f"came from a fade segment absorbed in the track {track_name!r}"
        )
        if clip_name:
            msg += f", affecting the fade handle of the neighbour {clip_name!r}"
        report.warning(
            "AAF_GAIN_AUTOMATION_IN_FADE",
            msg,
            {"track": track_name, "clip": clip_name, "gain_start": start_val, "gain_end": end_val},
        )
    else:
        msg = (
            f"the clip {clip_name!r} in the track {track_name!r} has gain automation "
            f"(from {start_val:.3f} to {end_val:.3f}), which v1 does not convert "
            f"into an envelope; the clip gain stays at 0 dB"
            if clip_name
            else f"the track {track_name!r} has gain automation "
            f"(from {start_val:.3f} to {end_val:.3f}), which v1 does not convert "
            f"into an envelope; the clip gain stays at 0 dB"
        )
        ctx = {"track": track_name, "gain_start": start_val, "gain_end": end_val}
        if clip_name:
            ctx["clip"] = clip_name
        report.warning("AAF_GAIN_AUTOMATION", msg, ctx)
    return 1.0


def _extract_gain_curve(raw_items: list[dict], inicio_absoluto: int) -> list[tuple[int, float]]:
    """Monta a curva de automacao de ganho absoluta em samples a partir dos raw_items.

    Percorre os segmentos (clip e fade) na ordem da Sequence, extraindo os pontos
    normalizados de cada OperationGroup e convertendo para posicao absoluta em samples.
    Descarta pontos duplicados consecutivos (mesmo instante OU mesmo valor seguido).
    Retorna lista de (sample_absoluto, amplitude_linear).
    """
    points: list[tuple[int, float]] = []

    for item in raw_items:
        if item["kind"] not in ("CLIP", "FADE"):
            continue
        params = item.get("parameters", [])
        if not params:
            continue

        seg_start = inicio_absoluto + item["cursor_samples"]
        seg_len = item["length_samples"]

        pts = _read_point_list(params)
        if not pts:
            pts = [(0.0, 1.0), (1.0, 1.0)]

        for t_norm, v in pts:
            abs_sample = seg_start + int(round(t_norm * seg_len))
            points.append((abs_sample, v))

    if not points:
        return []

    deduped: list[tuple[int, float]] = [points[0]]
    for abs_s, val in points[1:]:
        prev_s, prev_v = deduped[-1]
        if abs_s == prev_s and val == prev_v:
            continue
        deduped.append((abs_s, val))

    return deduped


def _sessao_vazia(path: Path, sample_rate: int) -> SessionIR:
    """Monta o SessionIR vazio devolvido quando o AAF nao tem CompositionMob."""
    return SessionIR(
        name=path.stem,
        sample_rate=sample_rate,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )


def _escolher_composition(f, path: Path, report: Report, sample_rate: int):
    """Escolhe a CompositionMob principal do AAF, ou None quando nao ha nenhuma."""
    composicoes = [m for m in f.content.mobs if _type_name(m) == "CompositionMob"]
    if not composicoes:
        report.error("AAF_NO_COMPOSITION", f"{path.name} has no CompositionMob", {})
        return None
    # Deterministic ranking:
    # Rank 1: UsageCode == 'Usage_TopLevel'
    # Rank 2: Count of timeline sound slots (TimelineMobSlot containing sound DataDef)
    # Rank 3: Total duration of audio timeline slots
    composicoes.sort(
        key=lambda m: _rank_composition_mob(m, sample_rate),
        reverse=True,
    )
    return composicoes[0]


def _edit_rate_inteiro(edit_rate) -> int | None:
    """Converte edit_rate para taxa inteira em Hz se > 1000, senao None."""
    if edit_rate is None:
        return None
    try:
        frac = exact_edit_rate(edit_rate)
    except (TypeError, ValueError):
        return None
    if frac.denominator != 1 or frac.numerator <= 1000:
        return None
    return int(frac.numerator)


def _slot_de_som_timeline(slot) -> bool:
    """Indica se o slot de timeline da CompositionMob e de audio."""
    if _type_name(slot) != "TimelineMobSlot":
        return False
    segment = getattr(slot, "segment", None)
    if segment is None or _type_name(segment) == "Timecode":
        return False
    return _slot_de_som(slot, segment)


def _taxa_dos_slots_de_audio(composition) -> int | None:
    """Edit rate unanime dos slots de audio (>1000 Hz), ou None se divergirem."""
    taxas: set[int] = set()
    for slot in getattr(composition, "slots", []) or []:
        if not _slot_de_som_timeline(slot):
            continue
        taxa = _edit_rate_inteiro(getattr(slot, "edit_rate", None))
        if taxa is None:
            return None
        taxas.add(taxa)
    if len(taxas) == 1:
        return next(iter(taxas))
    return None


_TAXAS_AUDIO_PADRAO = {
    8000,
    11025,
    16000,
    22050,
    32000,
    44100,
    48000,
    88200,
    96000,
    176400,
    192000,
    352800,
    384000,
}


def _slot_rate_confirmada(comp, taxas: dict[int, int]) -> int | None:
    """Taxa unanime dos slots de audio se for taxa padrao ou declarada por descritor."""
    slot_rate = _taxa_dos_slots_de_audio(comp)
    if slot_rate is None:
        return None
    # Edit_rate padrao de audio vale mesmo sem descritor restante (ex.: set
    # misto onde os clips na taxa da sessao ficaram de fora); inteiros
    # nao padrao (ex.: 2997) ainda exigem confirmacao em algum descritor.
    if slot_rate in _TAXAS_AUDIO_PADRAO or slot_rate in taxas:
        return slot_rate
    return None


def _escolher_composicao_e_taxa(f, path: Path, report: Report):
    """Escolhe CompositionMob e taxa da sessao sem dependencia circular.

    Parte da maioria dos descritores para ranquear a composicao, le os slots
    de audio e so refaz a escolha se eles impuserem outra taxa unanime.
    """
    taxas = _taxas_dos_descritores(f)
    taxa_inicial = max(taxas, key=lambda k: taxas[k]) if taxas else 48000
    comp = _escolher_composition(f, path, report, taxa_inicial)
    if comp is None:
        return None, _session_sample_rate(f, report)
    slot_rate = _slot_rate_confirmada(comp, taxas)
    if slot_rate is not None and slot_rate != taxa_inicial:
        comp = _escolher_composition(f, path, report, slot_rate)
        slot_rate = _slot_rate_confirmada(comp, taxas)
    return comp, _session_sample_rate(f, report, slot_rate=slot_rate)


def _iniciar_sessao(composition, path: Path, report: Report, sample_rate: int):
    """Le o timecode de inicio e monta o SessionIR da sessao."""
    frames_inicio, rate = _read_timecode(composition, report)

    inicio_absoluto, residuo = quantize_samples(
        frames_to_samples_exact(frames_inicio, rate, sample_rate)
    )
    if residuo:
        report.info(
            "TIMECODE_ROUNDING",
            f"the session start fell between samples; residual of {float(residuo):+.3f} sample",
            {"frames": frames_inicio, "rate": f"{rate.num}/{rate.den}"},
        )

    session = SessionIR(
        name=getattr(composition, "name", None) or path.stem,
        sample_rate=sample_rate,
        timecode_rate=rate,
        start_timecode_samples=inicio_absoluto,
        source_path=str(path),
    )
    return session, inicio_absoluto


def _slots_de_audio(composition, report: Report):
    """Coleta os slots de audio da CompositionMob, ignorando video e marcadores."""
    audio_slots = []
    for orig_idx, slot in enumerate(composition.slots):
        segmento = getattr(slot, "segment", None)
        if segmento is None or _type_name(segmento) == "Timecode":
            continue
        if getattr(segmento, "media_kind", "") == "Picture":
            report.info(
                "VIDEO_TRACK_SKIPPED_ON_IMPORT",
                f"Embedded video slot ignored: {slot.name or 'Video Track'}",
                {"slot_name": slot.name},
            )
            continue
        if getattr(segmento, "media_kind", "") != "Sound":
            # Marcadores sao lidos separadamente; nao sao faixas de audio.
            continue
        audio_slots.append((orig_idx, slot))
    return audio_slots


def _extrair_essencia_embutida(
    f, session: SessionIR, extract_dir: str | Path, report: Report
) -> None:
    """Extrai a essencia embutida dos SourceMobs referenciados pelos assets."""
    from .media_extract import extract_embedded_essence

    # Indice de SourceMobs por mob_id (UMID string) para busca O(1).
    # Nao usamos o nome do mob porque o pyaaf2 adiciona sufixos como
    # '<SOURCE MOB>' e '<TAPE MOB>' que nao estao no asset.filename.
    source_mobs_por_id: dict[str, object] = {}
    for mob in f.content.mobs:
        if _type_name(mob) == "SourceMob":
            mob_id_str = str(getattr(mob, "mob_id", "") or "")
            if mob_id_str:
                source_mobs_por_id[mob_id_str] = mob
                source_mobs_por_id[mob_id_str.lower()] = mob

    used_destinations: set[Path] = set()
    for asset in session.media:
        if asset.embedded_mob_id is None:
            # Nao ha referencia guardada; nao e possivel extrair.
            continue
        origem = source_mobs_por_id.get(asset.embedded_mob_id) or source_mobs_por_id.get(
            asset.embedded_mob_id.lower()
        )
        if origem is None:
            continue
        escrito = extract_embedded_essence(
            f,
            origem,
            extract_dir,
            session.start_timecode_samples,
            report,
            suggested_filename=asset.filename,
            existing_destinations=used_destinations,
        )
        if escrito is not None:
            asset.resolved_path = str(escrito)
            asset.status = "resolved"


def read_aaf(
    path: str | Path,
    report: Report,
    extract_dir: str | Path | None = None,
    fades: str = "absorb",
) -> SessionIR:
    """Le um AAF e devolve o SessionIR em samples absolutos."""
    path = Path(path)
    with aaf2.open(str(path), "r") as f:
        composition, sample_rate = _escolher_composicao_e_taxa(f, path, report)
        if composition is None:
            return _sessao_vazia(path, sample_rate)

        session, inicio_absoluto = _iniciar_sessao(composition, path, report, sample_rate)
        assets = AssetIndex(session=session, sample_rate=sample_rate)
        audio_slots = _slots_de_audio(composition, report)
        slot_names = _resolve_track_names(audio_slots, report)

        ctx = TrackContext(
            report=report,
            session=session,
            sample_rate=sample_rate,
            inicio_absoluto=inicio_absoluto,
            fades=fades,
            assets=assets,
            constant_gain=_constant_gain,
            has_varying_gain=_has_varying_gain,
            extract_gain_curve=_extract_gain_curve,
        )

        for indice_track, (orig_idx, slot) in enumerate(audio_slots):
            read_track(ctx, indice_track, orig_idx, slot, slot_names)

        _resumir_fades(ctx, fades, report)
        _avisar_clips_curtos(session, report)
        _avisar_pan_estatico(ctx.static_pan_count, report)
        session.markers.extend(_read_markers(composition, inicio_absoluto, report, sample_rate))
        if extract_dir is not None:
            _extrair_essencia_embutida(f, session, extract_dir, report)

    check_pulldown(session, report)
    check_plugins(session, report)
    _resumir_ganho(session, report)
    _avisar_plugins_ignorados(report)
    return session
