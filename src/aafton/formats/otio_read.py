"""Leitura de EDL e XML do FCP para o SessionIR, com tempos em fracao.

O FCPXML tem leitor proprio, em fcpxml_read: ele nao precisa do extra
formatos e le os tempos exatos. O que o leitor nao sabe ler vira erro no
relatorio e nao vira clip.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import field
from fractions import Fraction
from pathlib import Path
from urllib.parse import unquote

from ..ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from ..report import Report
from ..timecode import DROP_FRAME_RATES, TimecodeRate, quantize_samples
from . import ADAPTADORES, carregar_otio, edl_fix, xml_fix
from .fcpxml_read import read_fcpxml

APLICACAO = {".edl": "edl", ".xml": "fcp_xml"}
# Taxas de frames conhecidas: valor de comparacao e fracao exata.
TAXAS = {24: (24, 1), 25: (25, 1), 30: (30, 1), 50: (50, 1), 60: (60, 1)}
TAXAS.update({23.976: (24000, 1001), 29.97: (30000, 1001), 59.94: (60000, 1001)})


@dataclass
class _Contexto:
    """Decisoes do leitor que valem para a sessao inteira."""

    caminho: Path
    sufixo: str
    report: Report
    taxa: TimecodeRate
    sample_rate: int
    timeline: object
    media: dict[str, MediaAsset] = field(default_factory=dict)
    estado: xml_fix.Estado = field(default_factory=xml_fix.Estado)


def read_timeline(path, report, edl_rate=None, sample_rate=48000) -> SessionIR:
    """Le um EDL, um XML do FCP ou um FCPXML e devolve o SessionIR da cena."""
    caminho = Path(path)
    sufixo = caminho.suffix.lower()
    if sufixo == ".fcpxml":
        return read_fcpxml(caminho, report)
    adaptador = ADAPTADORES.get(sufixo)
    if adaptador is None:
        report.error("FORMAT_SUFIXO_DESCONHECIDO", f"format not supported: {sufixo}")
        return _sessao_vazia(caminho, sample_rate)
    if sufixo == ".edl" and edl_rate is None:
        report.error("FORMAT_EDL_RATE_REQUIRED", "EDL requires the frame rate in edl_rate")
        return _sessao_vazia(caminho, sample_rate)
    otio = carregar_otio()
    try:
        timeline = _ler_timeline(otio, caminho, adaptador, edl_rate)
    except edl_fix.ErroDropFrame as exc:
        report.error("FORMAT_EDL_DROP_FRAME_MISMATCH", str(exc))
        return _sessao_vazia(caminho, sample_rate)
    except Exception as exc:  # noqa: BLE001 - erro do adaptador vira erro do relatorio
        report.error("FORMAT_PARSE_ERROR", f"could not read {caminho.name}: {exc}")
        return _sessao_vazia(caminho, sample_rate)
    contexto = _contexto(timeline, caminho, sufixo, edl_rate, report, sample_rate)
    if contexto is None:
        return _sessao_vazia(caminho, sample_rate)
    return _montar_sessao(contexto)


def _sessao_vazia(caminho: Path, sample_rate: int) -> SessionIR:
    """Sessao vazia devolvida quando o arquivo nao pode ser lido."""
    return SessionIR(
        name=caminho.stem,
        sample_rate=sample_rate,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        source_path=str(caminho),
    )


def _ler_timeline(otio, caminho, adaptador, edl_rate):
    """Le o arquivo com o adaptador do OpenTimelineIO e devolve a Timeline."""
    if adaptador == "cmx_3600":
        return _timeline_de(otio, _edl_do_otio(otio, caminho, edl_rate))
    return _timeline_de(otio, otio.adapters.read_from_file(str(caminho), adaptador))


def _edl_do_otio(otio, caminho, edl_rate):
    """Le o EDL pelo adaptador, com o texto corrigido para o modo de contagem.

    O modo de contagem e decidido no edl_fix: o FCM do arquivo manda.
    """
    bruto = edl_fix.preparar(edl_fix.texto(caminho), edl_rate)
    return otio.adapters.read_from_string(bruto, "cmx_3600", rate=float(edl_rate.fps))


def _timeline_de(otio, resultado):
    """Timeline dentro do resultado do adaptador, que pode ser uma colecao."""
    if isinstance(resultado, otio.schema.Timeline):
        return resultado
    for item in resultado:
        if isinstance(item, otio.schema.Timeline):
            return item
    raise ValueError("the adapter did not return any Timeline")


def _contexto(timeline, caminho, sufixo, edl_rate, report, sample_rate) -> _Contexto | None:
    """Decide taxa de frames e taxa de amostras; None quando a taxa nao serve."""
    taxa = _taxa_do_timeline(timeline, caminho, sufixo, edl_rate, report)
    if taxa is None:
        return None
    return _Contexto(
        caminho=caminho,
        sufixo=sufixo,
        report=report,
        taxa=taxa,
        sample_rate=_sample_rate(caminho, sufixo, sample_rate, report),
        timeline=timeline,
        estado=_estado(caminho, sufixo, timeline, report),
    )


def _estado(caminho: Path, sufixo: str, timeline, report: Report) -> xml_fix.Estado:
    """O que o arquivo diz das tracks desligadas; vazio nos outros formatos."""
    if sufixo != ".xml":
        return xml_fix.Estado()
    return xml_fix.estado(caminho, report, getattr(timeline, "name", "") or "")


def _taxa_do_timeline(timeline, caminho, sufixo, edl_rate, report) -> TimecodeRate | None:
    """Taxa de frames da sessao, com o flag de drop frame declarado no arquivo.

    A taxa vem do primeiro tempo da cena que ja e de frames, ou do arquivo.
    """
    if sufixo == ".edl":
        fcm = _drop_frame(caminho, sufixo)
        drop = bool(edl_rate.drop_frame) or fcm is True
        edl_fix.avisar_fcm_misturado(edl_fix.texto(caminho), report, drop)
        if drop and not edl_rate.drop_frame:
            report.info("FORMAT_EDL_DROP_FRAME_FROM_FCM", "the FCM declares drop frame")
        if edl_rate.drop_frame and fcm is False:
            report.warning(
                "FORMAT_EDL_FCM_MISMATCH",
                "the FCM declares non-drop frame and the session frame rate counts drop frame; "
                "the EDL was read as drop frame",
                {},
            )
        return _com_drop(edl_rate, drop)
    fracao = _fracao_da_sessao(timeline, caminho)
    if fracao is None or fracao.denominator not in (1, 1001):
        report.error("FORMAT_RATE_UNSUPPORTED", "frame rate not supported")
        return None
    base = TimecodeRate(fracao.numerator, fracao.denominator)
    return _com_drop(base, bool(_drop_frame(caminho, sufixo)))


def _fracao_da_sessao(timeline, caminho) -> Fraction | None:
    """Fracao da taxa de frames: do primeiro tempo da cena ou do arquivo.

    A ordem e a do arquivo: clips, available_range, tracks e global_start_time.
    """
    clips = list(timeline.find_clips())
    taxas = [clip.source_range.start_time.rate for clip in clips if clip.source_range]
    for clip in clips:
        try:
            alcance = clip.available_range()
        except Exception:  # noqa: BLE001
            alcance = None
        if alcance is not None:
            taxas.append(alcance.start_time.rate)
    taxas += [t.source_range.start_time.rate for t in timeline.tracks if t.source_range]
    if timeline.global_start_time is not None:
        taxas.append(timeline.global_start_time.rate)
    fracao = next((f for f in map(_fracao_da_taxa, taxas) if f is not None), None)
    if fracao is not None:
        return fracao
    return _fracao_do_arquivo(caminho)


def _fracao_do_arquivo(caminho: Path) -> Fraction | None:
    """Fracao da taxa de frames declarada no arquivo; None quando nao declara."""
    raiz = _raiz(_texto(caminho))
    if raiz is None:
        return None
    for sequence in raiz.iter("sequence"):
        base = sequence.findtext("rate/timebase", "").strip()
        if base.isdigit():
            ntsc = sequence.findtext("rate/ntsc", "").strip().upper() == "TRUE"
            return Fraction(int(base) * 1000, 1001) if ntsc else Fraction(int(base))
    return None


def _com_drop(taxa: TimecodeRate, drop: bool) -> TimecodeRate:
    """Taxa com o flag de drop frame, quando a taxa aceita o flag."""
    if taxa.drop_frame == drop:
        return taxa
    if drop and (taxa.num, taxa.den) not in DROP_FRAME_RATES:
        return taxa
    return TimecodeRate(taxa.num, taxa.den, drop)


def _fracao_da_taxa(rate) -> Fraction | None:
    """Fracao exata da taxa de frames; None quando a taxa nao e conhecida."""
    if rate is None:
        return None
    for chave, (num, den) in TAXAS.items():
        if abs(float(rate) - chave) < 1e-3:
            return Fraction(num, den)
    return None


def _sample_rate(caminho: Path, sufixo: str, parametro: int, report: Report) -> int:
    """Taxa de amostras: a do arquivo quando ela existe, senao a do parametro."""
    if sufixo == ".edl":
        report.info("FORMAT_SAMPLE_RATE_ASSUMED", f"sample rate assumed: {parametro}")
        return parametro
    raiz = _raiz(_texto(caminho))
    if raiz is None:
        return parametro
    return _sample_rate_arquivo(raiz) or parametro


def _sample_rate_arquivo(raiz: ET.Element) -> int | None:
    """Taxa de amostras declarada no arquivo; None quando nao declara."""
    for elemento in raiz.iter("samplerate"):
        try:
            return int((elemento.text or "").strip())
        except ValueError:
            continue
    return None


def _texto(caminho: Path) -> str:
    """Texto do arquivo; string vazia quando o arquivo nao existe."""
    try:
        return caminho.read_text(encoding="utf-8")
    except OSError:
        return ""


def _raiz(texto: str) -> ET.Element | None:
    """Raiz do XML; None quando o texto nao e XML valido."""
    if not texto.strip():
        return None
    try:
        return ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError:
        return None


def _drop_frame(caminho: Path, sufixo: str) -> bool | None:
    """Flag de drop frame declarado no arquivo; None quando nao declara.

    No EDL e o FCM, no XML do FCP e o displayformat.
    """
    texto = _texto(caminho)
    if sufixo == ".edl":
        return edl_fix.fcm_drop_frame(edl_fix.texto(caminho))
    raiz = _raiz(texto)
    if raiz is None:
        return None
    return any(
        (elemento.text or "").strip().upper() == "DF" for elemento in raiz.iter("displayformat")
    )


def _montar_sessao(contexto: _Contexto) -> SessionIR:
    """Monta o SessionIR com tracks, clips, midia e marcadores."""
    report = contexto.report
    filhos = contexto.timeline.tracks
    if contexto.sufixo == ".edl":
        filhos = edl_fix.tracks_por_canal(filhos)
    tracks = []
    for filho in filhos:
        nome = getattr(filho, "name", "") or ""
        if _eh_tipo(filho, "Transition"):
            report.error("FORMAT_TRANSITION_UNSUPPORTED", f"transition not supported: {nome}")
            continue
        if not _eh_tipo(filho, "Track"):
            report.error("FORMAT_NESTED_UNSUPPORTED", f"nested item not supported: {nome}")
            continue
        if str(getattr(filho, "kind", "")) == "Video":
            report.info("FORMAT_VIDEO_TRACK_SKIPPED", f"video track skipped: {nome}")
            continue
        tracks.append(_track_do_otio(filho, len(tracks), contexto))
    return SessionIR(
        name=contexto.timeline.name or contexto.caminho.stem,
        sample_rate=contexto.sample_rate,
        timecode_rate=contexto.taxa,
        start_timecode_samples=_inicio_da_sessao(contexto, tracks),
        tracks=tracks,
        media=list(contexto.media.values()),
        markers=_marcadores(contexto),
        source_path=str(contexto.caminho),
        source_application=APLICACAO[contexto.sufixo],
    )


def _track_do_otio(track, indice: int, contexto: _Contexto) -> TrackIR:
    """TrackIR de uma track de audio do OpenTimelineIO."""
    track_ir = TrackIR(
        id=f"t{indice}",
        source_index=indice,
        original_source_indices=[indice],
        name=_nome_do_track(track, indice),
        kind="audio",
        channel_count=1,
    )
    if indice in contexto.estado.tracks_desabilitadas:
        contexto.report.error(
            "FORMAT_TRACK_DISABLED", f"track disabled in the FCP XML: {track_ir.name}"
        )
        return track_ir
    _apontar_itens_invalidos(track, contexto)
    for ordem, filho in enumerate(_clips_da_track(track)):
        if xml_fix.clip_desabilitado(filho, contexto.estado, indice, ordem):
            contexto.report.error(
                "FORMAT_CLIP_DISABLED", f"clipitem disabled in the FCP XML: {filho.name}"
            )
            continue
        clip = _clip_do_otio(filho, track, track_ir, contexto)
        if clip is not None:
            track_ir.clips.append(clip)
    return track_ir


def _clips_da_track(track) -> list:
    """Os clips da track, na ordem, sem os espacos vazios do adaptador."""
    return [filho for filho in track if _eh_tipo(filho, "Clip")]


def _apontar_itens_invalidos(track, contexto: _Contexto) -> None:
    """Reporta transicao e aninhamento, que o leitor nao sabe ler."""
    report = contexto.report
    for filho in track:
        nome = getattr(filho, "name", "") or ""
        if _eh_tipo(filho, "Transition"):
            report.error("FORMAT_TRANSITION_UNSUPPORTED", f"transition not supported: {nome}")
        elif _eh_tipo(filho, "Stack", "Track"):
            report.error("FORMAT_NESTED_UNSUPPORTED", f"nested item not supported: {nome}")


def _clip_do_otio(clip, track, track_ir: TrackIR, contexto: _Contexto) -> ClipIR | None:
    """ClipIR de um clip do OpenTimelineIO; None quando o clip nao entra."""
    report = contexto.report
    efeitos = xml_fix.avaliar_efeitos(clip)
    for codigo, mensagem in efeitos.problemas:
        report.error(codigo, mensagem)
    if efeitos.descarta:
        return None
    try:
        alcance = clip.range_in_parent()
    except Exception as exc:  # noqa: BLE001
        report.error("FORMAT_POSICAO_INDEFINIDA", f"clip {clip.name!r} without position: {exc}")
        return None
    posicao = _amostras_do_rt(alcance.start_time, contexto.sample_rate) + _offset(track, contexto)
    duracao = _amostras_do_rt(alcance.duration, contexto.sample_rate)
    inicio, residuo = quantize_samples(posicao)
    fim, _ = quantize_samples(posicao + duracao)
    if residuo:
        report.warning(
            "FORMAT_SUBSAMPLE_POSITION", f"clip {clip.name!r} does not fall on a whole sample"
        )
    offset, origem = _fonte_do_clip(clip, contexto)
    bruto = {} if offset is not None else {"source_tc_samples": origem}
    midia = _midia_do_clip(clip, contexto)
    return ClipIR(
        id=f"{track_ir.id}c{len(track_ir.clips)}_ch0",
        track_id=track_ir.id,
        channel_index=0,
        event_index=len(track_ir.clips),
        name=clip.name,
        media_id=midia.id,
        timeline_start_samples=inicio,
        timeline_end_samples=fim,
        source_offset_samples=offset,
        gain_linear=1.0 if efeitos.ganho is None else efeitos.ganho,
        raw=bruto,
    )


def _offset(track, contexto: _Contexto) -> Fraction:
    """Deslocamento entre o tempo local da track e o tempo absoluto da cena.

    O resultado sai em samples, porque cada tempo tem a sua propria taxa.
    """
    if track.source_range is not None:
        return -_amostras_do_rt(track.source_range.start_time, contexto.sample_rate)
    inicio = contexto.timeline.global_start_time
    if inicio is not None:
        return _amostras_do_rt(inicio, contexto.sample_rate)
    return Fraction(0)


def _fonte_do_clip(clip, contexto: _Contexto) -> tuple[int | None, int | None]:
    """Offset dentro do arquivo e timecode de origem, ambos em samples."""
    if clip.source_range is None:
        return None, None
    comeco = clip.source_range.start_time
    comeco_amostras = _amostras_do_rt(comeco, contexto.sample_rate)
    origem = quantize_samples(comeco_amostras)[0]
    try:
        alcance = clip.available_range()
    except Exception:  # noqa: BLE001
        return None, origem
    if alcance is None:
        return None, origem
    inicio = comeco_amostras - _amostras_do_rt(alcance.start_time, contexto.sample_rate)
    if inicio < 0:
        return None, origem
    return quantize_samples(inicio)[0], None


def _midia_do_clip(clip, contexto: _Contexto) -> MediaAsset:
    """MediaAsset da midia do clip, criando quando o arquivo e novo."""
    if contexto.sufixo == ".edl":
        nome = _arquivo_do_edl(clip)
        return _criar_midia(contexto, nome, nome, nome)
    referencia = getattr(clip, "media_reference", None)
    local = _caminho_local(getattr(referencia, "target_url", "") or "")
    nome = local.replace("\\", "/").rstrip("/").split("/")[-1] if local else (clip.name or "")
    return _criar_midia(contexto, local or nome, nome, local or None)


def _criar_midia(contexto: _Contexto, chave: str, nome: str, local: str | None) -> MediaAsset:
    """MediaAsset da midia, um por arquivo distinto."""
    existente = contexto.media.get(chave)
    if existente is not None:
        return existente
    asset = MediaAsset(
        id=f"m{len(contexto.media)}", filename=nome, declared_location=local, status="missing"
    )
    contexto.media[chave] = asset
    return asset


def _arquivo_do_edl(clip) -> str:
    """Nome do arquivo do EDL, pelo comentario SOURCE FILE ou pelo nome do clip."""
    comentarios = clip.metadata.get("cmx_3600", {}).get("comments", [])
    for comentario in comentarios:
        limpo = str(comentario).strip()
        if limpo.upper().startswith("SOURCE FILE:"):
            return limpo.split(":", 1)[1].strip()
    return clip.name or ""


def _caminho_local(url: str) -> str:
    """Caminho local de uma URL file://, com o prefixo do host removido."""
    bruto = unquote(url).replace("file://localhost", "").replace("file://", "")
    return bruto[1:] if bruto.startswith("/") and bruto[2:3] == ":" else bruto


def _nome_do_track(track, indice: int) -> str:
    """Nome da track; A1, A2... quando o arquivo nao nomeia a track."""
    nome = (track.name or "").strip()
    if not nome or nome.lstrip("-").isdigit():
        return f"A{indice + 1}"
    return nome


def _inicio_da_sessao(contexto: _Contexto, tracks: list[TrackIR]) -> int:
    """Inicio da sessao em samples absolutos."""
    if contexto.sufixo == ".xml":
        inicio = contexto.timeline.global_start_time
        if inicio is not None:
            return quantize_samples(_amostras_do_rt(inicio, contexto.sample_rate))[0]
    inicios = [clip.timeline_start_samples for track in tracks for clip in track.clips]
    return min(inicios) if inicios else 0


def _marcadores(contexto: _Contexto) -> list[MarkerIR]:
    """Marcadores da sessao, em samples absolutos."""
    if contexto.sufixo != ".xml":
        return []
    inicio = contexto.timeline.global_start_time
    base = _amostras_do_rt(inicio, contexto.sample_rate) if inicio is not None else Fraction(0)
    marcadores = []
    for indice, marcador in enumerate(contexto.timeline.tracks.markers):
        instante = base + _amostras_do_rt(marcador.marked_range.start_time, contexto.sample_rate)
        posicao = quantize_samples(instante)[0]
        marcadores.append(MarkerIR(id=f"k{indice}", name=marcador.name, position_samples=posicao))
    return marcadores


def _amostras_do_rt(rt, sample_rate: int) -> Fraction:
    """Samples exatos de um RationalTime, pela taxa do proprio tempo.

    O valor do tempo vem do texto; a taxa vem da tabela de frames ou do numero.
    """
    fracao = _fracao_da_taxa(rt.rate)
    if fracao is None:
        fracao = Fraction(str(rt.rate))
    segundos = Fraction(str(rt.value)) / fracao
    return segundos * sample_rate


def _eh_tipo(item, *nomes: str) -> bool:
    """True quando o item do OpenTimelineIO e de um dos tipos dados."""
    return item.schema_name() in nomes
