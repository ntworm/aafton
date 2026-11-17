"""Expansao de clips em loop de um set do Live sem registro do aafton.

Medido no Live 12.4.25b1 a 48 kHz em 15/11/2026: cada passada toca o trecho da
midia e cada volta do loop toca um crossfade de L amostras antes da volta com o
material antes do LoopStart. Quando o clip termina exatamente numa volta, o Live
toca uma cauda de T amostras depois de CurrentEnd em fade out. So 48 kHz foi
medido.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction

from .als_paths import AUDIO_CLIP_FIELDS

# Reexportado: os testes e os leitores do loop usam o nome deste modulo.
from .als_leitura import DESLOCAMENTO_DO_MODO as DESLOCAMENTO_DO_MODO
from .als_leitura import leitura_na_midia
from .als_render import (
    MODOS_MEDIDOS,
    _diferente_de_zero,
    _fracao,
    warp_um_para_um,
)
from .als_xml import get_value
from .ir import ClipIR, FadeIR, TrackIR
from .report import Report

# So 48 kHz foi medido (Live 12.4.25b1, 15/11/2026): L = 192 e T = 47 a 48 kHz.
XFADE_VOLTA_S = 0.004
CAUDA_S = 47 / 48000

_TOLERANCIA_VOLTA = Fraction(1, 1000)


@dataclass(frozen=True)
class Passada:
    """Uma passada do loop na timeline da sessao, em amostras absolutas."""

    timeline_start: int
    timeline_end: int
    source_offset: int
    fade_in_volta: int
    fade_out_volta: int


@dataclass(frozen=True)
class _GeometriaLoop:
    modo: str
    inicio_beat: Fraction
    fim_beat: Fraction
    loop_start: Fraction
    periodo: Fraction
    start_relative: Fraction
    L: int
    T: int


def _marcadores_crescentes(clip_node: ET.Element) -> bool:
    """Os WarpMarker existem (>= 2) e crescem em BeatTime e em SecTime."""
    pontos: list[tuple[Fraction, Fraction]] = []
    for marcador in clip_node.findall("WarpMarkers/WarpMarker"):
        beat = _fracao(marcador.get("BeatTime"))
        segundo = _fracao(marcador.get("SecTime"))
        if beat is None or segundo is None:
            return False
        pontos.append((beat, segundo))
    pontos.sort()
    if len(pontos) < 2:
        return False
    return all(b1 > b0 and s1 > s0 for (b0, s0), (b1, s1) in zip(pontos, pontos[1:]))


def _leitura(clip_node: ET.Element, beat: Fraction, taxa: int, modo: str) -> int | None:
    """Posicao na midia de um beat do clip, pela regra medida (``leitura_na_midia``).

    ``modo`` e o WarpMode ja conferido em ``_modo_elegivel``; a leitura usa o do no.
    """
    return leitura_na_midia(clip_node, beat, taxa)


def _ler_loop_beats(
    clip_node: ET.Element,
) -> tuple[Fraction, Fraction, Fraction, Fraction, Fraction] | None:
    """Le CurrentStart, CurrentEnd, LoopStart, P e StartRelative em Fraction."""
    inicio = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"]))
    fim = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"]))
    loop_start = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["loop_start"]))
    loop_end = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["loop_end"]))
    texto_sr = get_value(clip_node, AUDIO_CLIP_FIELDS["start_relative"])
    start_rel = Fraction(0) if texto_sr is None else _fracao(texto_sr)
    if inicio is None or fim is None or loop_start is None or loop_end is None or start_rel is None:
        return None
    periodo = loop_end - loop_start
    if fim <= inicio or periodo <= 0 or not (0 <= start_rel < periodo):
        return None
    return inicio, fim, loop_start, periodo, start_rel


def _modo_elegivel(clip_node: ET.Element, tempo: Fraction, taxa: int) -> str | None:
    """Loop e warp ligados, modo medido e taxa e tempo positivos; devolve o modo."""
    if get_value(clip_node, AUDIO_CLIP_FIELDS["loop_on"]) != "true":
        return None
    if get_value(clip_node, AUDIO_CLIP_FIELDS["is_warped"]) != "true":
        return None
    modo = (get_value(clip_node, "WarpMode") or "").strip()
    if modo not in MODOS_MEDIDOS or taxa <= 0 or tempo <= 0:
        return None
    return modo


def _warp_exato(clip_node: ET.Element, tempo: Fraction, taxa: int) -> bool:
    """Sem transposicao, com marcadores crescentes e warp um para um."""
    if _diferente_de_zero(clip_node, "PitchCoarse") or _diferente_de_zero(clip_node, "PitchFine"):
        return False
    return _marcadores_crescentes(clip_node) and warp_um_para_um(clip_node, tempo, taxa)


def _geometria(
    clip_node: ET.Element,
    tempo: Fraction,
    taxa: int,
    inicio_amostras: int,
    fim_amostras: int,
) -> _GeometriaLoop | None:
    """Valida e devolve os parametros basicos do loop, ou None."""
    modo = _modo_elegivel(clip_node, tempo, taxa)
    if modo is None:
        return None
    L = round(XFADE_VOLTA_S * taxa)
    T = round(CAUDA_S * taxa)
    if L <= 0 or T <= 0 or fim_amostras <= inicio_amostras:
        return None
    if not _warp_exato(clip_node, tempo, taxa):
        return None
    beats = _ler_loop_beats(clip_node)
    if beats is None:
        return None
    inicio, fim, loop_start, periodo, start_rel = beats
    if round(periodo * 60 / tempo * taxa) <= L:
        return None
    return _GeometriaLoop(modo, inicio, fim, loop_start, periodo, start_rel, L, T)


def _classificar_volta(
    geo: _GeometriaLoop,
    k: int,
    tempo: Fraction,
    taxa: int,
    origem: int,
    fim_amostras: int,
) -> tuple[str, int]:
    """Classifica a volta k em ('volta', 'meio', 'entra') e devolve volta_k."""
    b_k = geo.inicio_beat + k * geo.periodo - geo.start_relative
    x_k = b_k * 60 / tempo * taxa
    e = geo.fim_beat * 60 / tempo * taxa
    volta_k = origem + round(x_k)
    if abs(x_k - e) < _TOLERANCIA_VOLTA or volta_k == fim_amostras:
        return "volta", volta_k
    if volta_k > fim_amostras:
        return "meio", volta_k
    return "entra", volta_k


def loop_expansivel(
    clip_node: ET.Element,
    tempo: Fraction,
    taxa: int,
    origem: int,
    inicio_amostras: int,
    fim_amostras: int,
) -> bool:
    """Diz se o clip em loop e exato e pode ser expandido em passadas no AAF."""
    geo = _geometria(clip_node, tempo, taxa, inicio_amostras, fim_amostras)
    if geo is None:
        return False
    leitura_loop = _leitura(clip_node, geo.loop_start, taxa, geo.modo)
    leitura_ini = _leitura(clip_node, geo.loop_start + geo.start_relative, taxa, geo.modo)
    if leitura_loop is None or leitura_ini is None:
        return False
    if leitura_loop < geo.L or leitura_ini < 0:
        return False
    tipo_1, volta_1 = _classificar_volta(geo, 1, tempo, taxa, origem, fim_amostras)
    if tipo_1 == "entra" and volta_1 - inicio_amostras <= geo.L:
        return False
    return True


def _coletar_voltas(
    geo: _GeometriaLoop,
    tempo: Fraction,
    taxa: int,
    origem: int,
    fim_amostras: int,
) -> tuple[list[int], bool]:
    """Calcula a lista de voltas que entram e se o clip termina numa volta."""
    voltas: list[int] = []
    k = 1
    while True:
        tipo, volta_k = _classificar_volta(geo, k, tempo, taxa, origem, fim_amostras)
        if tipo == "volta":
            return voltas, True
        if tipo == "meio":
            return voltas, False
        voltas.append(volta_k)
        k += 1


def passadas(
    clip_node: ET.Element,
    tempo: Fraction,
    taxa: int,
    origem: int,
    inicio_amostras: int,
    fim_amostras: int,
) -> list[Passada]:
    """Expande um clip em loop nas passadas que o Live toca, em amostras absolutas."""
    if not loop_expansivel(clip_node, tempo, taxa, origem, inicio_amostras, fim_amostras):
        raise ValueError("clip is not an expandable loop")
    geo = _geometria(clip_node, tempo, taxa, inicio_amostras, fim_amostras)
    assert geo is not None
    leitura_loop = _leitura(clip_node, geo.loop_start, taxa, geo.modo)
    leitura_ini = _leitura(clip_node, geo.loop_start + geo.start_relative, taxa, geo.modo)
    assert leitura_loop is not None and leitura_ini is not None
    voltas, fim_em_volta = _coletar_voltas(geo, tempo, taxa, origem, fim_amostras)
    fim_final = fim_amostras + geo.T if fim_em_volta else fim_amostras
    fade_final = geo.T if fim_em_volta else 0
    leituras = (leitura_ini, leitura_loop)
    return _montar_passadas(geo, voltas, inicio_amostras, fim_final, fade_final, leituras)


def _montar_passadas(
    geo: _GeometriaLoop,
    voltas: list[int],
    inicio_amostras: int,
    fim_final: int,
    fade_final: int,
    leituras: tuple[int, int],
) -> list[Passada]:
    """Monta uma passada por volta mais a final, com fades de volta e cauda."""
    leitura_ini, leitura_loop = leituras
    total = len(voltas) + 1
    resultado: list[Passada] = []
    for idx in range(total):
        t_start = inicio_amostras if idx == 0 else voltas[idx - 1] - geo.L
        t_end = voltas[idx] if idx < len(voltas) else fim_final
        offset = leitura_ini if idx == 0 else leitura_loop - geo.L
        f_in = 0 if idx == 0 else geo.L
        f_out = geo.L if idx < len(voltas) else fade_final
        resultado.append(Passada(t_start, t_end, offset, f_in, f_out))
    return resultado


def _fade_limitado(fade: FadeIR | None, kind: str, limite: int) -> FadeIR | None:
    """Limita o fade do clip ao espaco disponivel; None quando fica <= 0."""
    if fade is None:
        return None
    duracao = min(fade.duration_samples, max(0, limite))
    return FadeIR(kind=kind, duration_samples=duracao) if duracao > 0 else None


def _fades_da_passada(
    item: Passada,
    idx: int,
    ultimo: int,
    fade_in_clip: FadeIR | None,
    fade_out_clip: FadeIR | None,
) -> tuple[FadeIR | None, FadeIR | None]:
    """Escolhe fade_in e fade_out de uma passada, combinando voltas, cauda e clip."""
    duracao = item.timeline_end - item.timeline_start
    if idx == 0:
        f_in = _fade_limitado(fade_in_clip, "in", duracao - item.fade_out_volta)
    else:
        f_in = FadeIR(kind="in", duration_samples=item.fade_in_volta)

    if idx < ultimo:
        f_out: FadeIR | None = FadeIR(kind="out", duration_samples=item.fade_out_volta)
    elif item.fade_out_volta > 0:
        f_out = FadeIR(kind="out", duration_samples=item.fade_out_volta)
    else:
        f_out = _fade_limitado(fade_out_clip, "out", duracao - item.fade_in_volta)
    return f_in, f_out


def expandir_clip_em_passadas(
    lista_passadas: list[Passada],
    base: ClipIR,
    fade_in_clip: FadeIR | None,
    fade_out_clip: FadeIR | None,
    proximo_evento: list[int],
) -> list[ClipIR]:
    """Transforma as passadas em ClipIR, anotando a cauda na ultima quando houver."""
    ultimo = len(lista_passadas) - 1
    fim_sem_cauda = lista_passadas[ultimo].timeline_end - lista_passadas[ultimo].fade_out_volta
    clips: list[ClipIR] = []
    for idx, item in enumerate(lista_passadas):
        if idx == 0:
            clip_id = base.id
            ev_idx = base.event_index
            xfade = base.crossfade_in_state
        else:
            clip_id = f"{base.id}p{idx}"
            ev_idx = proximo_evento[0]
            proximo_evento[0] += 1
            xfade = False
        f_in, f_out = _fades_da_passada(item, idx, ultimo, fade_in_clip, fade_out_clip)
        raw: dict = {}
        if idx == ultimo and item.fade_out_volta > 0:
            raw["_loop_tail"] = {
                "fim_amostras": fim_sem_cauda,
                "fade_in_volta": item.fade_in_volta,
                "fade_out_clip": fade_out_clip,
            }
        clips.append(
            ClipIR(
                id=clip_id,
                track_id=base.track_id,
                channel_index=base.channel_index,
                event_index=ev_idx,
                name=base.name,
                media_id=base.media_id,
                timeline_start_samples=item.timeline_start,
                timeline_end_samples=item.timeline_end,
                source_offset_samples=item.source_offset,
                gain_linear=base.gain_linear,
                muted=base.muted,
                fade_in=f_in,
                fade_out=f_out,
                crossfade_in_state=xfade,
                raw=raw,
            )
        )
    return clips


def cortar_caudas_de_loop(track: TrackIR, report: Report) -> None:
    """Corta a cauda da ultima passada quando ela passa do inicio do clip seguinte."""
    ordenados = sorted(track.clips, key=lambda c: c.timeline_start_samples)
    for idx, clip in enumerate(ordenados):
        cauda = clip.raw.pop("_loop_tail", None)
        if cauda is None or idx + 1 >= len(ordenados):
            continue
        seguinte = ordenados[idx + 1]
        if clip.timeline_end_samples <= seguinte.timeline_start_samples:
            continue
        clip.timeline_end_samples = cauda["fim_amostras"]
        duracao = clip.timeline_end_samples - clip.timeline_start_samples
        limite = duracao - cauda["fade_in_volta"]
        clip.fade_out = _fade_limitado(cauda["fade_out_clip"], "out", limite)
        report.info(
            "ALS_LOOP_TAIL_DROPPED",
            f"loop tail of clip {clip.name!r} in track {track.name!r} dropped at the next clip",
            {"track": track.name, "track_id": track.id, "clip": clip.name, "clip_id": clip.id},
        )
