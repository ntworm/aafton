"""Leitura dos clipitem desligados e dos filtros do XML do FCP 7 (xmeml).

O adaptador do OpenTimelineIO nao olha o <enabled>FALSE</enabled> de um
clipitem nem de uma track, e nao sabe o que fazer com os filtros de audio
que o Premiere escreve em todo clipitem de audio. Aqui esses pontos sao
lidos.

O XML de projeto do FCP 7 pode ter mais de uma sequencia: a da cena e as
que estao no bin. O arquivo nao diz qual delas e a cena, entao isso e um
erro, e o estado das tracks vem da sequencia que o adaptador leu.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from ..report import Report

# Chave do metadata que o adaptador do FCP XML usa.
CHAVE = "fcp_xml"
# Nome do filtro de nivel de audio do Premiere.
GANHO = "Audio Levels"
# Nome do filtro de pan do Premiere.
PAN = "Audio Pan"
# Identificadores dos parametros de nivel e de pan.
ID_NIVEL = "level"
ID_PAN = "pan"


@dataclass
class Efeitos:
    """Ganho e problemas dos efeitos de um clip.

    `ganho` e o nivel linear do clip, quando o filtro de audio diz qual e.
    `problemas` sao os pares de codigo e mensagem do que nao deu para ler.
    `descarta` marca o clip que sai da sessao: o efeito dele muda o tempo
    do clip e o leitor nao sabe ler esse tempo.
    """

    ganho: float | None = None
    problemas: list[tuple[str, str]] = field(default_factory=list)
    descarta: bool = False


@dataclass
class Estado:
    """O que o XML do FCP diz sobre as tracks e os clipitem desligados.

    `ids_desabilitados` sao os ids dos clipitem desligados.
    `tracks_desabilitadas` sao os indices das tracks de audio desligadas.
    `clips_desabilitados` sao os pares (indice da track, ordem do clipitem)
    dos clipitem desligados que nao tem id.
    """

    ids_desabilitados: set[str] = field(default_factory=set)
    tracks_desabilitadas: set[int] = field(default_factory=set)
    clips_desabilitados: set[tuple[int, int]] = field(default_factory=set)


def estado(caminho: Path, report: Report, nome: str = "") -> Estado:
    """O estado das tracks de audio e dos clipitem do XML do FCP.

    O `nome` e o da timeline que o adaptador leu: e a sequencia com esse
    nome que traz o estado das tracks. Mais de uma sequencia no arquivo e
    um erro, porque o arquivo nao diz qual delas e a cena.
    """
    try:
        raiz = ET.fromstring(caminho.read_bytes())
    except (OSError, ET.ParseError):
        return Estado()
    sequencias = _sequencias(raiz)
    if len(sequencias) > 1:
        nomes = _nomes(sequencias)
        report.error(
            "FORMAT_XML_MULTIPLE_SEQUENCES",
            f"the XML has more than one sequence: {', '.join(nomes)}",
            {"sequences": nomes},
        )
    sequence = _escolher(sequencias, nome)
    if sequence is None:
        return Estado()
    resultado = Estado()
    for indice, track in enumerate(_tracks_de_audio(sequence)):
        if _desligado(track):
            resultado.tracks_desabilitadas.add(indice)
        for ordem, item in enumerate(track.findall("clipitem")):
            if not _desligado(item):
                continue
            identificador = item.get("id")
            if identificador:
                resultado.ids_desabilitados.add(identificador)
            else:
                resultado.clips_desabilitados.add((indice, ordem))
    return resultado


def _sequencias(raiz: ET.Element) -> list[ET.Element]:
    """As sequencias do arquivo que nao estao dentro de um clipitem.

    Uma sequencia aninhada dentro de um clipitem tem tracks proprias, que
    nao sao as da cena.
    """
    pais = _pais(raiz)
    return [s for s in raiz.iter("sequence") if not _dentro_de_clipitem(s, pais)]


def _pais(raiz: ET.Element) -> dict:
    """O pai de cada elemento da arvore."""
    return {filho: pai for pai in raiz.iter() for filho in pai}


def _dentro_de_clipitem(elemento: ET.Element, pais: dict) -> bool:
    """True quando o elemento esta dentro de um clipitem."""
    pai = pais.get(elemento)
    while pai is not None:
        if pai.tag == "clipitem":
            return True
        pai = pais.get(pai)
    return False


def _nomes(sequencias: list[ET.Element]) -> list[str]:
    """O nome de cada sequencia; o id quando ela nao tem nome."""
    nomes = []
    for sequence in sequencias:
        nome = (sequence.findtext("name") or "").strip()
        nomes.append(nome or sequence.get("id") or "no name")
    return nomes


def _escolher(sequencias: list[ET.Element], nome: str) -> ET.Element | None:
    """A sequencia que o adaptador leu; a primeira quando o nome nao bate."""
    if nome:
        for sequence in sequencias:
            if (sequence.findtext("name") or "").strip() == nome:
                return sequence
    return sequencias[0] if sequencias else None


def _tracks_de_audio(sequence: ET.Element) -> list[ET.Element]:
    """As tracks de audio da sequencia, na ordem do arquivo."""
    media = sequence.find("media")
    if media is None:
        return []
    tracks = []
    for audio in media.findall("audio"):
        tracks.extend(audio.findall("track"))
    return tracks


def clip_desabilitado(clip, estado: Estado, indice: int, ordem: int) -> bool:
    """True quando o clip do adaptador vem de um clipitem desligado.

    O clipitem sem id e achado pela track em que ele esta e pela ordem dele
    dentro da track.
    """
    identificador = id_do_clip(clip)
    if identificador is not None:
        return identificador in estado.ids_desabilitados
    return (indice, ordem) in estado.clips_desabilitados


def id_do_clip(clip) -> str | None:
    """Id do clipitem que deu origem ao clip; None quando nao ha id."""
    valor = _do_metadata(clip, "@id")
    return str(valor) if valor else None


def avaliar_efeitos(clip) -> Efeitos:
    """Ganho e problemas dos efeitos de um clip."""
    resultado = Efeitos()
    for efeito in clip.effects:
        nome = (getattr(efeito, "name", "") or "").strip()
        if not _do_fcp(efeito):
            resultado.descarta = True
            resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, nome)))
        elif nome == GANHO:
            _avalia_ganho(efeito, clip, resultado)
        elif nome == PAN:
            _avalia_pan(efeito, clip, resultado)
        else:
            resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, nome)))
    return resultado


def _desligado(item: ET.Element) -> bool:
    """True quando o clipitem do XML esta desligado."""
    return (item.findtext("enabled") or "TRUE").strip().upper() == "FALSE"


def _do_fcp(efeito) -> bool:
    """True quando o efeito veio de um filtro do XML do FCP."""
    try:
        return efeito.metadata.get(CHAVE) is not None
    except Exception:  # noqa: BLE001 - metadata invalido nao derruba a leitura
        return False


def _do_metadata(item, chave: str):
    """Valor de uma chave do metadata do FCP XML; None quando nao existe."""
    try:
        meta = item.metadata.get(CHAVE)
        if meta is None:
            return None
        return meta.get(chave)
    except Exception:  # noqa: BLE001 - metadata invalido nao derruba a leitura
        return None


def _parametros(efeito) -> list:
    """Parametros de um filtro do FCP XML, sempre como lista."""
    valor = _do_metadata(efeito, "parameter")
    if valor is None:
        return []
    if isinstance(valor, list):
        return list(valor)
    return [valor]


def _id(parametro) -> str:
    """parameterid de um parametro de filtro."""
    try:
        valor = parametro.get("parameterid")
    except Exception:  # noqa: BLE001 - parametro invalido nao derruba a leitura
        return ""
    return str(valor) if valor else ""


def _valor(parametro) -> float | None:
    """Valor numerico de um parametro; None quando o valor tem curva."""
    try:
        if parametro.get("keyframe"):
            return None
        valor = parametro.get("value")
    except Exception:  # noqa: BLE001 - parametro invalido nao derruba a leitura
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def _avalia_ganho(efeito, clip, resultado: Efeitos) -> None:
    """Le o nivel do filtro Audio Levels como ganho linear do clip."""
    parametros = _parametros(efeito)
    niveis = [p for p in parametros if _id(p) == ID_NIVEL]
    if len(niveis) != 1:
        resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, GANHO)))
        return
    nivel = _valor(niveis[0])
    if nivel is None:
        resultado.problemas.append(
            ("FORMAT_GAIN_UNSUPPORTED", f"gain with a curve in the clip {clip.name}")
        )
        return
    resultado.ganho = nivel
    for pan in [p for p in parametros if _id(p) == ID_PAN]:
        if _valor(pan) != 0.0:
            resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, PAN)))


def _avalia_pan(efeito, clip, resultado: Efeitos) -> None:
    """Le o pan do filtro Audio Pan; so o pan no centro e neutro."""
    parametros = _parametros(efeito)
    pans = [p for p in parametros if _id(p) == ID_PAN]
    if len(pans) != 1:
        resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, PAN)))
        return
    if _valor(pans[0]) != 0.0:
        resultado.problemas.append(("FORMAT_EFFECT_UNSUPPORTED", _msg(clip, PAN)))


def _msg(clip, nome: str) -> str:
    """Mensagem de um efeito que o leitor nao sabe ler."""
    return f"unsupported effect in the clip {clip.name}: {nome}"
