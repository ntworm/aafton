"""Leitura da lista de corte de imagem (EDL CMX 3600, XML do FCP 7 e AAF).

Le apenas a primeira track de video (V1) por meio do OpenTimelineIO (EDL/XML)
ou do leitor AAF dedicado em ``cut_read_aaf``.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from .cut_ir import CutEvent, CutIR, normalize_source
from .cut_read_aaf import read_cut_aaf
from .formats import carregar_otio, edl_fix, otio_read
from .report import Report
from .timecode import TimecodeRate

CUT_FORMATS = {".edl": "cmx_3600", ".xml": "fcp_xml"}


def read_cut(
    path: str | Path,
    report: Report,
    frame_rate: TimecodeRate | None = None,
) -> CutIR | None:
    """Le a primeira track de video de um EDL, XML do FCP 7 ou AAF para CutIR."""
    caminho = Path(path)
    sufixo = caminho.suffix.lower()
    if sufixo == ".aaf":
        return read_cut_aaf(caminho, report)
    adaptador = CUT_FORMATS.get(sufixo)
    if adaptador is None:
        report.error("RECONFORM_FORMAT_UNSUPPORTED", f"cut format not supported: {sufixo}")
        return None
    if sufixo == ".edl" and frame_rate is None:
        report.error("RECONFORM_EDL_RATE_REQUIRED", "EDL requires the frame rate in frame_rate")
        return None

    otio = carregar_otio()
    timeline = _carregar_timeline(otio, caminho, adaptador, frame_rate, report)
    if timeline is None:
        return None
    taxa = otio_read._taxa_do_timeline(timeline, caminho, sufixo, frame_rate, report)
    if taxa is None:
        return None

    track = _primeira_track_video(timeline)
    offset = _offset_frames(track, timeline, taxa)
    eventos = _eventos_da_track(track, sufixo, offset, taxa, report) if track is not None else []
    inicio = _inicio_em_frames(timeline, track, sufixo, taxa, eventos)
    return CutIR(path=str(caminho), rate=taxa, start_frames=inicio, events=eventos)


def _carregar_timeline(
    otio,
    caminho: Path,
    adaptador: str,
    frame_rate: TimecodeRate | None,
    report: Report,
):
    """Carrega a timeline do OTIO pelo mesmo caminho de leitura do otio_read."""
    try:
        return otio_read._ler_timeline(otio, caminho, adaptador, frame_rate)
    except edl_fix.ErroDropFrame as exc:
        report.error("FORMAT_EDL_DROP_FRAME_MISMATCH", str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - erro do adaptador vira erro do relatorio
        report.error("FORMAT_PARSE_ERROR", f"could not read {caminho.name}: {exc}")
        return None


def _primeira_track_video(timeline):
    """Primeira track de video da timeline; None quando nao ha track de video."""
    for filho in timeline.tracks:
        if otio_read._eh_tipo(filho, "Track") and str(getattr(filho, "kind", "")) == "Video":
            return filho
    return None


def _offset_frames(track, timeline, rate: TimecodeRate) -> int:
    """Deslocamento entre o tempo local da track e o absoluto da cena, em frames."""
    if track is not None and track.source_range is not None:
        return -_frames_do_rt(track.source_range.start_time, rate)
    if timeline.global_start_time is not None:
        return _frames_do_rt(timeline.global_start_time, rate)
    return 0


def _inicio_em_frames(
    timeline,
    track,
    sufixo: str,
    rate: TimecodeRate,
    eventos: list[CutEvent],
) -> int:
    """Inicio da sequencia em frames absolutos desde 00:00:00:00."""
    if sufixo == ".xml":
        if timeline.global_start_time is not None:
            return _frames_do_rt(timeline.global_start_time, rate)
        return 0
    if track is not None and track.source_range is not None:
        return -_frames_do_rt(track.source_range.start_time, rate)
    return eventos[0].record_in if eventos else 0


def _eventos_da_track(
    track,
    sufixo: str,
    offset: int,
    rate: TimecodeRate,
    report: Report,
) -> list[CutEvent]:
    """Eventos da track de video na ordem, ligando transicoes ao evento entrante."""
    eventos: list[CutEvent] = []
    transicao = None
    for filho in track:
        if otio_read._eh_tipo(filho, "Transition"):
            transicao = filho
            continue
        if otio_read._eh_tipo(filho, "Clip"):
            evento = _evento_clip(filho, len(eventos), sufixo, offset, rate, transicao, report)
        elif otio_read._eh_tipo(filho, "Gap"):
            evento = _evento_gap(filho, len(eventos), offset, rate, transicao, report)
        else:
            nome = getattr(filho, "name", "") or ""
            report.error("FORMAT_NESTED_UNSUPPORTED", f"nested item not supported: {nome}")
            transicao = None
            continue
        transicao = None
        if evento is not None:
            eventos.append(evento)
    return eventos


def _evento_clip(
    clip,
    indice: int,
    sufixo: str,
    offset: int,
    rate: TimecodeRate,
    transicao,
    report: Report,
) -> CutEvent | None:
    """Converte um Clip do OTIO em CutEvent."""
    limites = _record_bounds(clip, offset, rate, report)
    if limites is None:
        return None
    record_in, record_out = limites
    reel = _reel_do_clip(clip, sufixo)
    arquivo = _arquivo_do_clip(clip, sufixo)
    fonte = normalize_source(reel, arquivo)
    source_in, source_out = _source_bounds(clip, rate)
    trans_start, trans_frames = _dados_transicao(transicao, record_in, rate)
    return CutEvent(
        index=indice,
        kind="clip",
        source=fonte,
        reel=reel,
        source_in=source_in,
        source_out=source_out,
        record_in=record_in,
        record_out=record_out,
        transition_start=trans_start,
        transition_frames=trans_frames,
    )


def _evento_gap(
    gap,
    indice: int,
    offset: int,
    rate: TimecodeRate,
    transicao,
    report: Report,
) -> CutEvent | None:
    """Converte um Gap do OTIO em CutEvent com campos de origem nulos."""
    limites = _record_bounds(gap, offset, rate, report)
    if limites is None:
        return None
    record_in, record_out = limites
    trans_start, trans_frames = _dados_transicao(transicao, record_in, rate)
    return CutEvent(
        index=indice,
        kind="gap",
        source=None,
        reel=None,
        source_in=None,
        source_out=None,
        record_in=record_in,
        record_out=record_out,
        transition_start=trans_start,
        transition_frames=trans_frames,
    )


def _record_bounds(item, offset: int, rate: TimecodeRate, report: Report) -> tuple[int, int] | None:
    """Posicao inicial e final de um item na track, em frames absolutos."""
    try:
        alcance = item.range_in_parent()
    except Exception as exc:  # noqa: BLE001
        nome = getattr(item, "name", "") or ""
        report.error("FORMAT_POSICAO_INDEFINIDA", f"clip {nome!r} without position: {exc}")
        return None
    return _limites_em_frames(alcance, offset, rate)


def _source_bounds(clip, rate: TimecodeRate) -> tuple[int | None, int | None]:
    """Inicio e fim na midia de origem, em frames absolutos."""
    if clip.source_range is None:
        return None, None
    return _limites_em_frames(clip.source_range, 0, rate)


def _dados_transicao(transicao, record_in: int, rate: TimecodeRate) -> tuple[int | None, int]:
    """Inicio e duracao em frames de uma Transition que precede o evento entrante."""
    if transicao is None:
        return None, 0
    in_offset = getattr(transicao, "in_offset", None)
    in_frames = _frames_do_rt(in_offset, rate) if in_offset is not None else 0
    duracao = transicao.duration()
    total_frames = _frames_do_rt(duracao, rate) if duracao is not None else in_frames
    return record_in - in_frames, total_frames


def _reel_do_clip(clip, sufixo: str) -> str | None:
    """Nome do reel do clip quando o formato e o adaptador expoem esse dado."""
    if sufixo == ".edl":
        reel = _mapa_get(_mapa_get(getattr(clip, "metadata", None), "cmx_3600"), "reel")
        if isinstance(reel, str) and reel.strip():
            return reel.strip()
        return None
    referencia = getattr(clip, "media_reference", None)
    for meta in (getattr(referencia, "metadata", None), getattr(clip, "metadata", None)):
        reel = _reel_do_fcp_metadata(meta)
        if reel is not None:
            return reel
    return None


def _reel_do_fcp_metadata(metadata) -> str | None:
    """Extrai o nome de reel do dicionario fcp_xml quando presente."""
    fcp = _mapa_get(metadata, "fcp_xml")
    if fcp is None:
        return None
    reel = _mapa_get(fcp, "reel")
    if reel is None:
        reel = _mapa_get(_mapa_get(fcp, "file"), "reel")
    if not isinstance(reel, str):
        reel = _mapa_get(reel, "name") or _mapa_get(reel, "#text")
    if isinstance(reel, str) and reel.strip():
        return reel.strip()
    return None


def _mapa_get(obj, chave: str):
    """Le uma chave de dict ou AnyDictionary do OTIO; None quando nao existe."""
    if obj is None or isinstance(obj, (str, bytes, list, tuple)):
        return None
    try:
        return obj.get(chave)
    except AttributeError:
        return None


def _arquivo_do_clip(clip, sufixo: str) -> str:
    """Nome do arquivo de midia do clip no EDL ou no XML do FCP."""
    if sufixo == ".edl":
        return otio_read._arquivo_do_edl(clip)
    referencia = getattr(clip, "media_reference", None)
    local = otio_read._caminho_local(getattr(referencia, "target_url", "") or "")
    if local:
        return local.replace("\\", "/").rstrip("/").split("/")[-1]
    return getattr(referencia, "name", None) or clip.name or ""


def _limites_em_frames(alcance, offset: int, rate: TimecodeRate) -> tuple[int, int]:
    """Inicio e fim de um TimeRange em frames inteiros na taxa de corte."""
    inicio_seg = _segundos_do_rt(alcance.start_time)
    fim_seg = inicio_seg + _segundos_do_rt(alcance.duration)
    return offset + round(inicio_seg * rate.fps), offset + round(fim_seg * rate.fps)


def _frames_do_rt(rt, rate: TimecodeRate) -> int:
    """Converte um RationalTime em inteiro de frames na taxa de corte."""
    fracao = _fracao_da_taxa(rt.rate)
    if fracao == rate.fps:
        return round(Fraction(str(rt.value)))
    segundos = Fraction(str(rt.value)) / fracao
    return round(segundos * rate.fps)


def _segundos_do_rt(rt) -> Fraction:
    """Duracao ou instante de um RationalTime em fracao exata de segundos."""
    return Fraction(str(rt.value)) / _fracao_da_taxa(rt.rate)


def _fracao_da_taxa(rate) -> Fraction:
    """Fracao exata de uma taxa do OTIO, usando a tabela do otio_read."""
    fracao = otio_read._fracao_da_taxa(rate)
    return fracao if fracao is not None else Fraction(str(rate))
