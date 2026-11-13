"""Escrita de XML do FCP 7 a partir do SessionIR, pelo adaptador fcp_xml.

O XML do FCP 7 carrega marcadores, lacunas e o inicio de cada arquivo, por
isso o escritor monta a timeline do OpenTimelineIO e deixa o adaptador fazer
a traducao para o xmeml. As conferencias que o EDL tambem faz estao em
escrita_comum.py.

O adaptador escreve o timecode da sequencia como nao drop frame e nao escreve
a taxa de amostras da sessao. Depois de escrever o XML, o texto passa pelo
ElementTree para receber as duas informacoes que faltam.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..ir import ClipIR, MediaAsset, SessionIR
from ..report import Report
from ..timecode import TimecodeRate, frames_to_label
from . import ExtraAusente, carregar_otio
from .escrita_comum import (
    Canal,
    Contexto,
    alocar_canais,
    avisar_arredondamento,
    conferir_multicanal,
    conferir_recursos,
    frame,
    gravar,
    origem,
)

CODIGO_RECURSO = "FORMAT_XML_FEATURE_UNSUPPORTED"
CODIGO_SOBREPOSTO = "FORMAT_XML_OVERLAPPING_CLIPS"
CODIGO_EXTRA = "FORMAT_EXTRA_MISSING"
CODIGO_MIDIA = "FORMAT_XML_MEDIA_MISSING"
# Profundidade em bits que o XML do FCP 7 declara para o audio.
PROFUNDIDADE = "24"
# Cabecalho do XML do FCP 7: declaracao XML e DOCTYPE que o ElementTree
# nao devolve, porque ele so devolve o elemento raiz.
CABECALHO = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'


@dataclass
class _Cena:
    """A cena montada: o contexto comum, a taxa de frames e o inicio."""

    contexto: Contexto
    fps: float
    inicio: int


def write_fcp_xml(session: SessionIR, destination: str | Path, report: Report) -> None:
    """Grava a sessao em um XML do FCP 7."""
    try:
        otio = carregar_otio()
    except ExtraAusente as erro:
        report.error(CODIGO_EXTRA, str(erro))
        return
    conferir_recursos(session, report, CODIGO_RECURSO, "the XML")
    conferir_multicanal(session, report, "the XML")
    contexto = Contexto(session=session, report=report)
    cena = _cena(contexto)
    timeline = _timeline(otio, cena)
    texto = otio.adapters.write_to_string(timeline, "fcp_xml")
    gravar(Path(destination), _corrigir_xml(texto, cena))
    avisar_arredondamento(contexto)


def _corrigir_xml(texto: str, cena: _Cena) -> str:
    """Corrige o XML do FCP depois que o adaptador escreve.

    O texto que nao parseia fica como estava: o XML gravado e melhor do que
    nenhum.
    """
    try:
        raiz = ET.fromstring(texto.encode("utf-8"))
    except ET.ParseError:
        return texto
    sequencia = next(raiz.iter("sequence"), None)
    if sequencia is None:
        return texto
    _corrigir_timecode(sequencia, cena)
    _corrigir_amostras(sequencia, cena)
    return CABECALHO + ET.tostring(raiz, encoding="unicode") + "\n"


def _corrigir_timecode(sequencia: ET.Element, cena: _Cena) -> None:
    """Marca o timecode da sequencia e o de cada arquivo como drop frame.

    O adaptador escreve o rotulo como nao drop frame, com o frame contado
    sobre 30 fps. Numa sessao de drop frame o rotulo leva o ponto e virgula
    e o displayformat vale DF.
    """
    rate = cena.contexto.session.timecode_rate
    if not rate.drop_frame:
        return
    timecode = sequencia.find("timecode")
    if timecode is not None:
        _texto(timecode, "displayformat", "DF")
        _texto(timecode, "string", frames_to_label(cena.inicio, rate))
        _texto(timecode, "frame", str(cena.inicio))
    for arquivo in sequencia.iter("file"):
        _corrigir_timecode_do_arquivo(arquivo, rate)


def _corrigir_timecode_do_arquivo(arquivo: ET.Element, rate: TimecodeRate) -> None:
    """Marca o timecode de um arquivo de midia como drop frame.

    O rotulo do arquivo sai do frame count que o adapter escreveu: o
    contador de frames nao muda, so o rotulo e o displayformat.
    """
    timecode = arquivo.find("timecode")
    if timecode is None:
        return
    frame = (timecode.findtext("frame") or "").strip()
    if not frame.isdigit():
        return
    _texto(timecode, "displayformat", "DF")
    _texto(timecode, "string", frames_to_label(int(frame), rate))


def _corrigir_amostras(sequencia: ET.Element, cena: _Cena) -> None:
    """Escreve a taxa de amostras da sessao no formato de audio da sequencia.

    Sem isso o leitor do XML assume 48 kHz e a sessao volta com outra taxa.
    """
    media = sequencia.find("media")
    if media is None:
        return
    audio = media.find("audio")
    if audio is None:
        return
    formato = _filho(audio, "format", 0)
    caracteristicas = _filho(formato, "samplecharacteristics")
    _texto(caracteristicas, "depth", PROFUNDIDADE)
    _texto(caracteristicas, "samplerate", str(cena.contexto.session.sample_rate))


def _filho(pai: ET.Element, nome: str, posicao: int | None = None) -> ET.Element:
    """Elemento filho pelo nome, criado na posicao dada (ou no fim) quando falta."""
    elemento = pai.find(nome)
    if elemento is None:
        elemento = ET.Element(nome)
        if posicao is None:
            pai.append(elemento)
        else:
            pai.insert(posicao, elemento)
    return elemento


def _texto(pai: ET.Element, nome: str, valor: str) -> None:
    """Escreve o texto de um elemento filho, criando-o quando falta."""
    _filho(pai, nome).text = valor


def _cena(contexto: Contexto) -> _Cena:
    """Taxa de frames da cena e inicio da sessao, em frames."""
    rate = contexto.session.timecode_rate
    fps = rate.num / rate.den
    inicio = frame(contexto, contexto.session.start_timecode_samples, rate)
    return _Cena(contexto=contexto, fps=fps, inicio=inicio)


def _timeline(otio, cena: _Cena):
    """Timeline do OpenTimelineIO com as tracks de audio e os marcadores."""
    sessao = cena.contexto.session
    timeline = otio.schema.Timeline(name=sessao.name)
    timeline.global_start_time = otio.opentime.RationalTime(cena.inicio, cena.fps)
    for canal in alocar_canais(sessao.tracks):
        timeline.tracks.append(_track(otio, cena, canal))
    _marcadores(otio, cena, timeline)
    return timeline


def _track(otio, cena: _Cena, canal: Canal):
    """Track de audio de um canal, com os itens na ordem do tempo."""
    nome = canal.track.name
    if canal.track.channel_count > 1:
        nome = f"{nome} {canal.indice}"
    track = otio.schema.Track(name=nome, kind=otio.schema.TrackKind.Audio)
    for item in _itens(otio, cena, canal):
        track.append(item)
    return track


def _itens(otio, cena: _Cena, canal: Canal) -> list:
    """Lacunas e clips do canal, na ordem do tempo.

    O clip que comeca antes do fim do anterior fica de fora: o XML do FCP nao
    tem dois clipitems no mesmo lugar da track.
    """
    itens = []
    cursor = 0
    for clip in canal.clips:
        inicio, fim = _tempo_do_clip(cena, clip)
        if inicio < cursor:
            cena.contexto.report.error(
                CODIGO_SOBREPOSTO,
                f"the clip {clip.name!r} starts before the end of the previous clip",
                {"clip": clip.name},
            )
            continue
        if inicio > cursor:
            itens.append(_gap(otio, cursor, inicio - cursor, cena.fps))
        item = _clip(otio, cena, clip, inicio, fim - inicio)
        if item is not None:
            itens.append(item)
        cursor = fim
    return itens


def _tempo_do_clip(cena: _Cena, clip: ClipIR) -> tuple[int, int]:
    """Inicio e fim do clip em frames, relativos ao inicio da sessao."""
    contexto = cena.contexto
    rate = contexto.session.timecode_rate
    inicio = frame(contexto, clip.timeline_start_samples, rate) - cena.inicio
    fim = frame(contexto, clip.timeline_end_samples, rate) - cena.inicio
    return inicio, fim


def _gap(otio, inicio: int, duracao: int, fps: float):
    """Lacuna da track, que empurra os clips seguintes."""
    return otio.schema.Gap(
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(inicio, fps),
            otio.opentime.RationalTime(duracao, fps),
        )
    )


def _clip(otio, cena: _Cena, clip: ClipIR, inicio: int, duracao: int):
    """Clip do OpenTimelineIO, com a midia e o tempo de origem."""
    contexto = cena.contexto
    asset = _asset(cena, clip)
    if asset is None:
        return None
    rate = contexto.session.timecode_rate
    comeco = frame(contexto, origem(contexto, asset), rate)
    deslocamento = frame(contexto, clip.source_offset_samples or 0, rate)
    return otio.schema.Clip(
        name=clip.name,
        media_reference=_referencia(otio, cena, asset, comeco),
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(comeco + deslocamento, cena.fps),
            otio.opentime.RationalTime(duracao, cena.fps),
        ),
    )


def _referencia(otio, cena: _Cena, asset: MediaAsset, comeco: int):
    """Referencia externa do arquivo, com o alcance disponivel."""
    contexto = cena.contexto
    duracao = _frames(contexto, asset.duration_samples or 0, contexto.session.timecode_rate)
    return otio.schema.ExternalReference(
        target_url=_url(asset),
        available_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(comeco, cena.fps),
            otio.opentime.RationalTime(duracao, cena.fps),
        ),
    )


def _frames(contexto: Contexto, samples: int, rate: TimecodeRate) -> int:
    """Duracao em frames, arredondada sem contar como posicao da cena."""
    exato = Fraction(samples) * Fraction(rate.num, rate.den) / contexto.session.sample_rate
    return round(exato)


def _asset(cena: _Cena, clip: ClipIR) -> MediaAsset | None:
    """Arquivo de midia do clip; None quando o clip nao tem midia na sessao."""
    sessao = cena.contexto.session
    asset = sessao.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None:
        cena.contexto.report.error(
            CODIGO_MIDIA,
            f"the clip {clip.name!r} has no media file in the session",
            {"clip": clip.name},
        )
    return asset


def _url(asset: MediaAsset) -> str:
    """URL do arquivo: o caminho local quando ele existe, o nome do arquivo senao."""
    if not asset.resolved_path:
        return asset.filename
    caminho = Path(asset.resolved_path)
    if not caminho.is_absolute():
        return asset.filename
    return caminho.as_uri()


def _marcadores(otio, cena: _Cena, timeline) -> None:
    """Marcadores da sessao, com o tempo relativo ao inicio da cena."""
    contexto = cena.contexto
    rate = contexto.session.timecode_rate
    for marcador in contexto.session.markers:
        posicao = frame(contexto, marcador.position_samples, rate) - cena.inicio
        timeline.tracks.markers.append(
            otio.schema.Marker(
                name=marcador.name,
                marked_range=otio.opentime.TimeRange(
                    otio.opentime.RationalTime(posicao, cena.fps),
                    otio.opentime.RationalTime(0, cena.fps),
                ),
            )
        )
