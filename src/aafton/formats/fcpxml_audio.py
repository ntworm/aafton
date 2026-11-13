"""As regras de audio do clip de FCPXML: componente, ganho, pan e mute.

O FCPXML descreve o audio de um clip com audio-channel-source: cada
componente escolhe os canais do asset e pode trazer ganho e mute. Um
componente desligado nao soa; dois componentes ligados viram erro. O ClipIR
do produto nao tem pan, e por isso um panner que nao e neutro vira erro: o
nivel vem do atributo amount ou do parametro amount.

O produto nao extrai canal de arquivo de troca: o .als aponta para o arquivo
inteiro e o Live toca todos os canais dele. Um clip que escolhe parte dos
canais do asset e erro, e o clip fica com todos os canais do asset. Um asset
que nao declara a quantidade de canais nao serve: o leitor assumiu um canal so
e nao sabe que a escolha e parcial.

O canal que o clip escolhe volta no Audio: quando o clip nao escolhe canal
nenhum do arquivo, o canal e SEM_CANAL, e o leitor sabe que a escolha nao
aconteceu e que o arquivo inteiro toca.

O volume vem do atributo amount ou do parametro amount: sem nenhum dos dois
vale 0 dB. Um arquivo com mais de dois canais e erro: o clip fica fora da cena.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING

from .fcpxml_fix import tempo_fcpxml

if TYPE_CHECKING:
    from .fcpxml_itens import Contexto

# Os atributos que o FCPXML usa para desligar um componente de audio.
DESLIGADO = ("enabled", "active")
# As saidas que o Live toca, por numero de canais de origem do audio. O par
# numerado (1, 2) e o mesmo par L, R da saida estereo.
SAIDAS_PERMITIDAS = {
    1: (("C",), ("L", "R"), ("1", "2")),
    2: (("L", "R"), ("1", "2")),
}
# O FCPXML nao le um arquivo com mais de dois canais: o Live toca o par stereo.
MAXIMO_DE_CANAIS = 2
# O canal do clip quando ele nao escolhe canal nenhum do arquivo: o arquivo
# inteiro toca, e o leitor precisa saber que a escolha nao aconteceu.
SEM_CANAL = -1


@dataclass
class Audio:
    """O audio do clip depois das regras: o canal, quantos canais e o ganho."""

    canal: int
    canais: int
    ganho: float


def so_video(clip: ET.Element) -> bool:
    """True quando o clip traz so video: o srcEnable deixa o audio de fora."""
    habilitados = set((clip.get("srcEnable") or "").split())
    return "video" in habilitados and "audio" not in habilitados


def avaliar(
    clip: ET.Element,
    asset: ET.Element,
    canais_asset: int,
    elementos: list[ET.Element],
    nome: str,
    contexto: "Contexto",
) -> Audio | None:
    """Aplica as regras de audio do clip; None quando o clip nao soa."""
    if _multicanal(asset, canais_asset, nome, contexto):
        return None
    componentes = clip.findall("audio-channel-source") or asset.findall("audio-channel-source")
    ligados = [comp for comp in componentes if not desligado(comp)]
    if componentes and not ligados:
        contexto.erro("FORMAT_CLIP_DISABLED", f"the clip {nome!r} has no audio enabled")
        return None
    if len(ligados) > 1:
        contexto.erro(
            "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
            f"the clip {nome!r} has {len(ligados)} audio components enabled",
        )
    if _mudo(clip, ligados, nome, contexto):
        return None
    _pan(elementos, nome, contexto)
    _papel(elementos, nome, contexto)
    canal, canais = _canais(clip, asset, ligados, canais_asset, nome, contexto)
    return Audio(canal=canal, canais=canais, ganho=_ganho(clip, ligados, nome, contexto))


def _multicanal(asset: ET.Element, canais_asset: int, nome: str, contexto: "Contexto") -> bool:
    """True quando o arquivo tem mais de dois canais: o clip fica fora da cena."""
    if canais_asset <= MAXIMO_DE_CANAIS:
        return False
    contexto.erro(
        "FORMAT_MULTICHANNEL_UNSUPPORTED",
        f"the clip {nome!r} uses the file {_arquivo(asset)!r}, which has {canais_asset} channels:"
        " Live does not play a file with more than two channels, and the clip is left out",
    )
    return True


def desligado(elemento: ET.Element) -> bool:
    """True quando o elemento esta desligado por enabled ou active."""
    return any(elemento.get(atributo) == "0" for atributo in DESLIGADO)


def _canais(
    clip: ET.Element,
    asset: ET.Element,
    ligados: list[ET.Element],
    canais_asset: int,
    nome: str,
    contexto: "Contexto",
) -> tuple[int, int]:
    """Os canais que o clip usa: os do asset, quando a escolha nao e parcial.

    O canal que volta e SEM_CANAL quando o clip nao escolhe canal nenhum do
    arquivo: o arquivo inteiro toca.
    """
    grupos = _selecionados(clip, ligados)
    if grupos is None:
        contexto.erro("FORMAT_CHANNEL_SELECTION_UNSUPPORTED", f"bad channels in the clip {nome!r}")
        return SEM_CANAL, canais_asset
    selecionados = {canal for grupo in grupos for canal in grupo}
    _fonte_do_item(clip, asset, nome, contexto)
    # Quantos canais de origem o clip escolhe; o asset todo quando nao escolhe.
    quantos = len(selecionados) or canais_asset
    _componentes(ligados, clip, asset, quantos, nome, contexto)
    if _fora_do_arquivo(selecionados, asset, canais_asset, nome, contexto):
        return SEM_CANAL, canais_asset
    if _ordem_trocada(grupos, nome, contexto):
        return SEM_CANAL, canais_asset
    declarado = asset.get("audioChannels", "").strip().isdecimal()
    if canais_asset > 1:
        parcial = _selecao_parcial(selecionados, asset, canais_asset, declarado, nome, contexto)
        return (SEM_CANAL if parcial else _canal_escolhido(selecionados)), canais_asset
    # Com um canal so no asset, o canal escolhido e o canal do clip.
    if len(selecionados) == 1 and declarado:
        return selecionados.pop() - 1, 1
    _selecao_parcial(selecionados, asset, canais_asset, declarado, nome, contexto)
    return SEM_CANAL, canais_asset


def _canal_escolhido(selecionados: set[int]) -> int:
    """O canal que o clip escolhe; SEM_CANAL quando ele nao escolhe nenhum."""
    if not selecionados:
        return SEM_CANAL
    return min(selecionados) - 1


def _fora_do_arquivo(
    selecionados: set[int],
    asset: ET.Element,
    canais_asset: int,
    nome: str,
    contexto: "Contexto",
) -> bool:
    """O canal escolhido que o arquivo nao tem e erro; True quando ha um."""
    for canal in sorted(selecionados):
        if 1 <= canal <= canais_asset:
            continue
        contexto.erro(
            "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
            f"the clip {nome!r} chooses channel {canal} of the file {_arquivo(asset)!r},"
            f" {_canais_do_arquivo(asset, canais_asset)}: Live would play the whole file",
        )
        return True
    return False


def _canais_do_arquivo(asset: ET.Element, canais_asset: int) -> str:
    """Os canais do arquivo: os declarados no documento ou os que o leitor assumiu."""
    declarado = (asset.get("audioChannels") or "").strip()
    if declarado.isdecimal() and max(1, int(declarado)) == canais_asset:
        return f"which has {canais_asset} channels"
    return "which the document does not declare the channel count and the reader assumed 1 channel"


def _selecionados(clip: ET.Element, ligados: list[ET.Element]) -> list[list[int]] | None:
    """Os canais escolhidos, um grupo por srcCh; None quando nao sao numeros."""
    grupos: list[list[int]] = []
    for valor in (clip.get("srcCh"), *(comp.get("srcCh") for comp in ligados)):
        if valor is None:
            continue
        numeros: list[int] = []
        for parte in valor.split(","):
            try:
                numeros.append(int(parte.strip()))
            except ValueError:
                return None
        grupos.append(numeros)
    return grupos


def _ordem_trocada(grupos: list[list[int]], nome: str, contexto: "Contexto") -> bool:
    """True quando algum srcCh escolhe os canais fora de ordem crescente."""
    for numeros in grupos:
        if len(numeros) < 2 or numeros == sorted(numeros):
            continue
        ordem = ", ".join(str(canal) for canal in numeros)
        contexto.erro(
            "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
            f"the clip {nome!r} chooses the channels {ordem} in inverted order:"
            " Live plays the file in its own order",
        )
        return True
    return False


def _selecao_parcial(
    selecionados: set[int],
    asset: ET.Element,
    canais_asset: int,
    declarado: bool,
    nome: str,
    contexto: "Contexto",
) -> bool:
    """A escolha de parte dos canais do asset e erro: o Live toca o arquivo inteiro.

    Um asset que nao declara a quantidade de canais nao serve: o leitor assumiu
    um canal so e nao sabe que a escolha dos canais e parcial. O retorno diz
    se a escolha foi parcial, para o leitor saber que o clip nao escolheu canal.
    """
    if not selecionados or (declarado and selecionados == set(range(1, canais_asset + 1))):
        return False
    lista = ", ".join(str(canal) for canal in sorted(selecionados))
    quantos = "the channel" if len(selecionados) == 1 else "the channels"
    contexto.erro(
        "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
        f"the clip {nome!r} chooses only {quantos} {lista} of the file {_arquivo(asset)!r},"
        f" {_canais_do_arquivo(asset, canais_asset)}: Live would play the whole file",
    )
    return True


def _fonte_do_item(clip: ET.Element, asset: ET.Element, nome: str, contexto: "Contexto") -> None:
    """O item que aponta para outra fonte de audio do asset e erro."""
    fonte = clip.get("srcID") or clip.get("srcId")
    if fonte is None or fonte == "1":
        return
    contexto.erro(
        "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
        f"the clip {nome!r} uses the audio source {fonte!r} of the file {_arquivo(asset)!r}:"
        " Live would play the whole file",
    )


def _componentes(
    ligados: list[ET.Element],
    clip: ET.Element,
    asset: ET.Element,
    quantos: int,
    nome: str,
    contexto: "Contexto",
) -> None:
    """Os componentes ligados: a faixa e as saidas deles tem de ser do asset."""
    faixas = _faixas_do_clip(clip)
    for componente in ligados:
        _faixa(componente, faixas, nome, contexto)
    for elemento in (clip, *ligados):
        _saidas(elemento, asset, quantos, nome, contexto)


def _faixas_do_clip(clip: ET.Element) -> list[tuple[Fraction | None, Fraction | None]]:
    """As faixas que um componente pode repetir: a do audio e a do video.

    Num split edit o audio tem faixa propria (audioStart, audioDuration); um
    componente que repete a faixa do audio ou a do video nao corta nada.
    """
    video = (_tempo(clip.get("start")), _tempo(clip.get("duration")))
    audio = (
        _tempo(clip.get("audioStart") or clip.get("start")),
        _tempo(clip.get("audioDuration") or clip.get("duration")),
    )
    return [audio, video]


def _faixa(
    componente: ET.Element,
    faixas: list[tuple[Fraction | None, Fraction | None]],
    nome: str,
    contexto: "Contexto",
) -> None:
    """O componente cortado em outra faixa e erro: o corte dele nao cabe no clip."""
    comeco = _tempo(componente.get("start"))
    tamanho = _tempo(componente.get("duration"))
    if any(_mesma_faixa(comeco, tamanho, faixa) for faixa in faixas):
        return
    inicio, duracao = faixas[0]
    contexto.erro(
        "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
        f"the clip {nome!r} has a channel component of {comeco}s with {tamanho}s,"
        f" and the clip audio starts at {inicio}s and lasts {duracao}s",
    )


def _mesma_faixa(
    comeco: Fraction | None,
    tamanho: Fraction | None,
    faixa: tuple[Fraction | None, Fraction | None],
) -> bool:
    """True quando o comeco e o tamanho do componente cabem na faixa dada."""
    inicio, duracao = faixa
    if comeco is not None and inicio is not None and comeco != inicio:
        return False
    return tamanho is None or duracao is None or tamanho == duracao


def _saidas(
    elemento: ET.Element,
    asset: ET.Element,
    quantos: int,
    nome: str,
    contexto: "Contexto",
) -> None:
    """O audio roteado para fora do centro e do par L, R e erro.

    O Live toca o arquivo inteiro: o roteamento so e lido quando ele e o
    centro (C) ou o par L, R na ordem.
    """
    outch = elemento.get("outCh")
    if not outch or quantos not in SAIDAS_PERMITIDAS:
        return
    saidas = tuple(parte.strip() for parte in outch.split(",") if parte.strip())
    if saidas in SAIDAS_PERMITIDAS[quantos]:
        return
    contexto.erro(
        "FORMAT_CHANNEL_SELECTION_UNSUPPORTED",
        f"the clip {nome!r} routes the audio of the file {_arquivo(asset)!r} to"
        f" {', '.join(saidas)}: Live would play the whole file",
    )


def _arquivo(asset: ET.Element) -> str:
    """O nome do arquivo do asset: o do media-rep ou o nome do asset."""
    rep = asset.find("media-rep")
    src = (rep.get("src") if rep is not None else "") or ""
    partes = src.replace("\\", "/").rstrip("/").split("/")
    return partes[-1] if partes[-1] else asset.get("name") or ""


def _tempo(texto: str | None) -> Fraction | None:
    """O tempo de um atributo do FCPXML; None quando ele falta ou e invalido."""
    if texto is None:
        return None
    try:
        return tempo_fcpxml(texto)
    except ValueError:
        return None


def _mudo(clip: ET.Element, ligados: list[ET.Element], nome: str, contexto: "Contexto") -> bool:
    """True quando um mute sem inicio apaga o clip inteiro."""
    apagado = False
    for mute in _mutes(clip, ligados):
        if mute.get("start") is None and mute.get("duration") is None:
            contexto.erro("FORMAT_CLIP_DISABLED", f"the clip {nome!r} is muted")
            apagado = True
        else:
            contexto.erro("FORMAT_GAIN_UNSUPPORTED", f"mute with a range in the clip {nome!r}")
    return apagado


def _mutes(clip: ET.Element, ligados: list[ET.Element]) -> list[ET.Element]:
    """Os mutes do clip e dos componentes ligados."""
    mutes = list(clip.findall("mute"))
    for componente in ligados:
        mutes.extend(componente.iter("mute"))
    return mutes


def _papel(elementos: list[ET.Element], nome: str, contexto: "Contexto") -> None:
    """O audio-role-source que muda o som e erro: o leitor nao sabe aplica-lo."""
    for elemento in elementos:
        if elemento.tag != "audio-role-source":
            continue
        desligado = any(elemento.get(atributo) == "0" for atributo in DESLIGADO)
        if desligado or _muda_o_som(elemento):
            contexto.erro(
                "FORMAT_EFFECT_UNSUPPORTED",
                f"the clip {nome!r} has an audio-role-source that changes the sound",
            )


def _muda_o_som(papel: ET.Element) -> bool:
    """True quando o audio-role-source mexe no som do clip."""
    for filho in papel.iter():
        if filho.tag in ("mute", "keyframe"):
            return True
        if filho.tag == "adjust-volume" and _nivel(filho) != 0:
            return True
    return False


def _pan(elementos: list[ET.Element], nome: str, contexto: "Contexto") -> None:
    """O panner que nao e neutro e erro: o ClipIR nao tem pan."""
    for elemento in elementos:
        if elemento.tag != "adjust-panner":
            continue
        if next(elemento.iter("keyframe"), None) is not None:
            contexto.erro("FORMAT_PAN_UNSUPPORTED", f"pan with keyframe in the clip {nome!r}")
            continue
        valores = list(elemento.attrib.values())
        valores += [p.get("value") for p in elemento.iter("param")]
        if any(_numero(valor) not in (None, 0) for valor in valores):
            contexto.erro("FORMAT_PAN_UNSUPPORTED", f"pan in the clip {nome!r}")


def _numero(texto: str | None) -> float | None:
    """O numero de um texto; None quando o texto nao e numero."""
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def _ganho(clip: ET.Element, ligados: list[ET.Element], nome: str, contexto: "Contexto") -> float:
    """O ganho do clip: o do clip vezes o do componente ligado."""
    ganho = _ganho_dos(clip.findall("adjust-volume"), nome, contexto)
    for componente in ligados:
        ganho *= _ganho_dos(componente.findall("adjust-volume"), nome, contexto)
    return ganho


def _ganho_dos(volumes: list[ET.Element], nome: str, contexto: "Contexto") -> float:
    """O ganho linear de uma lista de adjust-volume."""
    ganho = 1.0
    for volume in volumes:
        if next(volume.iter("keyframe"), None) is not None:
            contexto.erro("FORMAT_GAIN_UNSUPPORTED", f"gain with keyframe in the clip {nome!r}")
            continue
        decibels = _nivel(volume)
        if decibels is None:
            contexto.erro("FORMAT_GAIN_UNSUPPORTED", f"invalid gain in the clip {nome!r}")
            continue
        ganho *= 10 ** (decibels / 20)
    return ganho


def _nivel(volume: ET.Element) -> float | None:
    """O nivel constante do adjust-volume, em decibels.

    O nivel vem do atributo amount ou do parametro amount do FCPXML. Um
    adjust-volume sem nenhum dos dois vale 0 dB.
    """
    amount = volume.get("amount")
    if amount is not None and amount.strip():
        return _decibels(amount)
    param = next((p for p in volume.findall("param") if p.get("name") == "amount"), None)
    if param is None:
        return 0.0
    valor = param.get("value")
    if valor is None or not valor.strip():
        return 0.0
    return _decibels(valor)


def _decibels(texto: str) -> float | None:
    """Os decibels de um texto; None quando o texto nao e decibels."""
    bruto = texto.strip().lower()
    try:
        return float(bruto[:-2]) if bruto.endswith("db") else None
    except ValueError:
        return None
