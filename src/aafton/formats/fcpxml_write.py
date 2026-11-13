"""Escrita de FCPXML 1.9 a partir do SessionIR, sem o OpenTimelineIO.

O escritor fcpx_xml do OpenTimelineIO recusa uma timeline sem track de video
e escreve FCPXML 1.8, sem tcStart e sem os marcadores que ficam dentro do
gap da spine. Por isso o produto escreve o FCPXML com o proprio codigo: a
biblioteca padrao, a aritmetica exata de fracao e o que o formato nao carrega
vira erro no relatorio.

Os tempos sao exatos: uma posicao de samples vira uma fracao de segundos
reduzida, e o FCPXML nunca carrega um tempo arredondado para o frame.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ..ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from ..report import Report
from .escrita_comum import Contexto, conferir_recursos, gravar, origem

# Taxas de audio que o FCPXML nomeia; as outras nao tem nome no formato.
TAXAS_AUDIO = {48000: "48k", 44100: "44.1k", 96000: "96k"}
CODIGO_RECURSO = "FORMAT_FCPXML_FEATURE_UNSUPPORTED"
CODIGO_TAXA = "FORMAT_FCPXML_SAMPLE_RATE_UNSUPPORTED"
CODIGO_MIDIA = "FORMAT_FCPXML_MEDIA_MISSING"
# Papel do audio dos clips: o FCPXML nao carrega o papel do Live.
PAPEL = "dialogue"


@dataclass
class _Midia:
    """Um arquivo de midia da sessao, com o id que ele ganha no FCPXML."""

    asset: MediaAsset
    id: str
    origem: int


@dataclass
class _Cena:
    """A sessao que o FCPXML carrega, com os tempos em segundos exatos."""

    sessao: SessionIR
    taxa: int
    inicio: Fraction
    duracao: Fraction
    frame: Fraction
    midias: dict[str, _Midia]
    audio: str

    @property
    def drop(self) -> bool:
        """True quando o timecode da sessao conta drop frame."""
        return self.sessao.timecode_rate.drop_frame


def write_fcpxml(session: SessionIR, destination: str | Path, report: Report) -> None:
    """Grava a sessao em um FCPXML 1.9."""
    conferir_recursos(session, report, CODIGO_RECURSO, "the FCPXML")
    audio = TAXAS_AUDIO.get(session.sample_rate)
    if audio is None:
        report.error(
            CODIGO_TAXA,
            f"the FCPXML does not name the audio rate of {session.sample_rate} Hz",
        )
        return
    contexto = Contexto(session=session, report=report)
    raiz = _documento(contexto, audio)
    ET.indent(raiz, space="  ")
    gravar(Path(destination), _texto(raiz))


def _documento(contexto: Contexto, audio: str) -> ET.Element:
    """Arvore do FCPXML: os recursos, a biblioteca e a sequencia."""
    cena = _cena(contexto, audio)
    raiz = ET.Element("fcpxml", {"version": "1.9"})
    raiz.append(_recursos(cena))
    biblioteca = ET.SubElement(raiz, "library")
    evento = ET.SubElement(biblioteca, "event", {"name": cena.sessao.name})
    projeto = ET.SubElement(evento, "project", {"name": cena.sessao.name})
    sequencia = _sequencia(projeto, cena)
    _spine(sequencia, cena)
    return raiz


def _recursos(cena: _Cena) -> ET.Element:
    """Elemento resources, com o formato da sequencia e os assets."""
    recursos = ET.Element("resources")
    formato = {"id": "r1", "frameDuration": _segundos(cena.frame)}
    ET.SubElement(recursos, "format", formato)
    for midia in cena.midias.values():
        _asset(recursos, midia, cena)
    return recursos


def _cena(contexto: Contexto, audio: str) -> _Cena:
    """Tempos e midias da sessao, como o FCPXML carrega."""
    sessao = contexto.session
    frame = Fraction(sessao.timecode_rate.den, sessao.timecode_rate.num)
    return _Cena(
        sessao=sessao,
        taxa=sessao.sample_rate,
        inicio=Fraction(sessao.start_timecode_samples, sessao.sample_rate),
        duracao=_duracao(contexto, frame),
        frame=frame,
        midias=_midias(contexto),
        audio=audio,
    )


def _duracao(contexto: Contexto, frame: Fraction) -> Fraction:
    """Comprimento da sessao: do inicio ate o fim do ultimo clip."""
    sessao = contexto.session
    fim = max(_fins(contexto), default=sessao.start_timecode_samples)
    return max(Fraction(fim - sessao.start_timecode_samples, sessao.sample_rate), frame)


def _fins(contexto: Contexto) -> list[int]:
    """Fim de cada clip de audio que o FCPXML carrega."""
    return [
        clip.timeline_end_samples
        for track in _tracks_de_audio(contexto.session.tracks)
        for clip in track.clips
    ]


def _tracks_de_audio(tracks: list[TrackIR]) -> list[TrackIR]:
    """Tracks de audio da sessao, na ordem em que aparecem."""
    return [track for track in tracks if not track.is_video]


def _sequencia(projeto: ET.Element, cena: _Cena) -> ET.Element:
    """Elemento sequence, com o inicio e o formato do timecode."""
    atributos = {
        "format": "r1",
        "duration": _segundos(cena.duracao),
        "tcStart": _segundos(cena.inicio),
        "tcFormat": "DF" if cena.drop else "NDF",
        "audioLayout": "stereo",
        "audioRate": cena.audio,
    }
    return ET.SubElement(projeto, "sequence", atributos)


def _spine(sequencia: ET.Element, cena: _Cena) -> None:
    """Spine com o gap da sessao, os asset-clips e os marcadores."""
    spine = ET.SubElement(sequencia, "spine")
    atributos = {
        "name": cena.sessao.name,
        "offset": _segundos(cena.inicio),
        "start": _segundos(cena.inicio),
        "duration": _segundos(cena.duracao),
    }
    gap = ET.SubElement(spine, "gap", atributos)
    for indice, track in enumerate(_tracks_de_audio(cena.sessao.tracks)):
        for clip in track.clips:
            _asset_clip(gap, cena, clip, -(indice + 1))
    for marcador in cena.sessao.markers:
        _marcador(gap, cena, marcador)


def _marcador(gap: ET.Element, cena: _Cena, marcador: MarkerIR) -> None:
    """Marker do FCPXML, que dura um frame."""
    atributos = {
        "start": _segundos(Fraction(marcador.position_samples, cena.taxa)),
        "duration": _segundos(cena.frame),
        "value": marcador.name,
    }
    ET.SubElement(gap, "marker", atributos)


def _asset_clip(gap: ET.Element, cena: _Cena, clip: ClipIR, lane: int) -> None:
    """Asset-clip do FCPXML, na lane da track e com os tempos exatos."""
    midia = cena.midias.get(clip.media_id)
    if midia is None:
        return
    comeco = midia.origem + (clip.source_offset_samples or 0)
    duracao = clip.timeline_end_samples - clip.timeline_start_samples
    atributos = {
        "name": clip.name,
        "ref": midia.id,
        "offset": _segundos(Fraction(clip.timeline_start_samples, cena.taxa)),
        "start": _segundos(Fraction(comeco, cena.taxa)),
        "duration": _segundos(Fraction(duracao, cena.taxa)),
        "lane": str(lane),
        "audioRole": PAPEL,
    }
    ET.SubElement(gap, "asset-clip", atributos)


def _asset(recursos: ET.Element, midia: _Midia, cena: _Cena) -> None:
    """Asset do FCPXML, com o media-rep que aponta para o arquivo."""
    asset = midia.asset
    atributos = {
        "id": midia.id,
        "name": asset.filename,
        "start": _segundos(Fraction(midia.origem, cena.taxa)),
        "duration": _segundos(Fraction(asset.duration_samples or 0, cena.taxa)),
        "hasAudio": "1",
        "audioSources": "1",
        "audioChannels": str(asset.channels or 1),
        "audioRate": str(asset.sample_rate or cena.taxa),
    }
    elemento = ET.SubElement(recursos, "asset", atributos)
    rep = {"kind": "original-media", "src": _src(asset)}
    ET.SubElement(elemento, "media-rep", rep)


def _src(asset: MediaAsset) -> str:
    """Endereco do arquivo: a URI do caminho resolvido, ou o nome do arquivo."""
    if not asset.resolved_path:
        return asset.filename
    return Path(asset.resolved_path).as_uri()


def _midias(contexto: Contexto) -> dict[str, _Midia]:
    """Midias dos clips de audio, na ordem do primeiro uso."""
    midias: dict[str, _Midia] = {}
    for track in _tracks_de_audio(contexto.session.tracks):
        for clip in track.clips:
            asset = _asset_do_clip(contexto, clip)
            if asset is None or asset.id in midias:
                continue
            midias[asset.id] = _Midia(
                asset=asset,
                id=f"r{len(midias) + 2}",
                origem=origem(contexto, asset),
            )
    return midias


def _asset_do_clip(contexto: Contexto, clip: ClipIR) -> MediaAsset | None:
    """Arquivo de midia do clip; None quando a sessao nao tem o arquivo."""
    asset = contexto.session.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None:
        contexto.report.error(
            CODIGO_MIDIA,
            f"the clip {clip.name!r} has no media file in the session",
            {"clip": clip.name},
        )
    return asset


def _segundos(fracao: Fraction) -> str:
    """Tempo do FCPXML: segundos inteiros ou fracao reduzida."""
    if fracao.denominator == 1:
        return f"{fracao.numerator}s"
    return f"{fracao.numerator}/{fracao.denominator}s"


def _texto(raiz: ET.Element) -> str:
    """Documento FCPXML com a declaracao XML e o DOCTYPE do formato."""
    corpo = ET.tostring(raiz, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{corpo}\n'
