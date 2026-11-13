"""Percorre a spine do FCPXML e os itens que ela contem.

A spine e percorrida na ordem do documento: um gap e um asset-clip filhos
diretos da spine sao itens primarios, e os filhos de um gap ou de um asset-clip
primario sao os clips conectados, as storylines e os marcadores.

Cada container sabe onde esta no tempo de sequencia: o offset de um item da
spine ja e tempo de sequencia, e o de um item conectado e tempo local do
container, que vira tempo absoluto com o ponto do container. A lane de um item
e a lane do container mais a lane que o item declara.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import field
from fractions import Fraction

from ..ir import MarkerIR, MediaAsset
from ..report import Report
from ..timecode import quantize_samples
from . import fcpxml_audio
from .fcpxml_fix import tempo_fcpxml

# Itens de story que um clip pode carregar: os efeitos deles nao sao do clip.
ETIQUETAS_PRESAS = (
    "audio",
    "video",
    "title",
    "clip",
    "ref-clip",
    "sync-clip",
    "mc-clip",
    "audition",
    "caption",
    "live-drawing",
    "gap",
    "asset-clip",
    "spine",
)
# Fades e efeitos que o FCPXML pode trazer dentro do clip.
ETIQUETAS_FADE = ("fade-in", "fade-out", "fadeIn", "fadeOut")
ETIQUETAS_EFEITO = ("filter-audio", "conform-rate", "timeMap")
# As melhorias de audio do FCP (Inspector > Audio Enhancements): erro do clip.
MELHORIAS_AUDIO = (
    "adjust-EQ",
    "adjust-matchEQ",
    "adjust-loudness",
    "adjust-noiseReduction",
    "adjust-humReduction",
    "adjust-voiceIsolation",
)
# Os ajustes de video do FCPXML: eles nao mudam o audio e passam sem aviso.
AJUSTES_DE_VIDEO = (
    "adjust-crop",
    "adjust-corners",
    "adjust-conform",
    "adjust-transform",
    "adjust-blend",
    "adjust-stabilization",
    "adjust-rollingShutter",
    "adjust-360-transform",
    "adjust-reorient",
    "adjust-orientation",
    "adjust-cinematic",
    "adjust-colorConform",
    "adjust-stereo-3D",
)
# Os ajustes que ja tem regra propria: o volume, o pan e as seis melhorias.
AJUSTES_COM_REGRA = ("adjust-volume", "adjust-panner", *MELHORIAS_AUDIO)
# Metadados e parametros de efeito: o leitor ignora, eles nao ocupam tempo.
ETIQUETAS_IGNORADO = (
    "keyword",
    "rating",
    "note",
    "metadata",
    "analysis-marker",
    "hidden-clip-marker",
    "audio-role-source",
    "reserved",
    "object-tracker",
    "tracking-shape",
    "audio-channel-source",
    "keyframe",
    "mute",
    "param",
    "speed",
    "synctime",
    "trim",
    *ETIQUETAS_FADE,
    *ETIQUETAS_EFEITO,
)
# Prefixos das familias de parametro de efeito do FCPXML e do texto do title.
PREFIXOS_IGNORADO = ("adjust-", "filter-", "text")


@dataclass
class Asset:
    """Asset do FCPXML: o arquivo de midia, o inicio e os canais."""

    id: str
    nome: str
    inicio: Fraction
    canais: int
    caminho: str
    tem_audio: bool
    elemento: ET.Element


@dataclass
class Clip:
    """Clip de audio lido do FCPXML, antes de ganhar a track."""

    lane: int
    nome: str
    asset_id: str
    inicio: int
    fim: int
    offset: int
    canal: int
    canais: int
    ganho: float


@dataclass
class Contexto:
    """Estado da leitura: os recursos, os clips e os marcadores."""

    report: Report
    sample_rate: int
    assets: dict[str, Asset] = field(default_factory=dict)
    media: list[MediaAsset] = field(default_factory=list)
    midias: dict[str, MediaAsset] = field(default_factory=dict)
    clips: dict[int, list[Clip]] = field(default_factory=dict)
    markers: list[MarkerIR] = field(default_factory=list)
    video_pulado: bool = False

    def erro(self, codigo: str, mensagem: str) -> None:
        """Registra um erro no relatorio."""
        self.report.error(codigo, mensagem)


@dataclass(frozen=True)
class _Ponto:
    """Onde o container esta no tempo de sequencia e de onde o conteudo conta.

    A posicao e o tempo de sequencia do container; o inicio e o atributo start
    dele, que marca de onde o tempo local dos filhos conta. O ponto da spine
    comeca em zero: o offset de um item da spine ja e tempo de sequencia.
    """

    posicao: Fraction
    inicio: Fraction
    lane: int = 0

    def absoluto(self, local: Fraction) -> Fraction:
        """Tempo de sequencia de um tempo local do container."""
        return self.posicao + (local - self.inicio)

    def lane_do_item(self, item: ET.Element) -> int:
        """A lane do item: a do container mais a lane que ele declara."""
        return self.lane + _lane(item)


def percorrer_spine(sequencia: ET.Element, contexto: Contexto) -> None:
    """Percorre a spine da sequencia, na ordem do documento."""
    spine = sequencia.find("spine")
    if spine is None:
        return
    ponto = _Ponto(Fraction(0), Fraction(0))
    for item in spine:
        _item(item, contexto, ponto)


def _item(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Processa um item da spine, de um gap, de um clip ou de uma storyline."""
    manipulador = _MANIPULADORES.get(item.tag)
    if manipulador is not None:
        manipulador(item, contexto, ponto)
        return
    if _ignorado(item.tag):
        return
    _aninhado(item, contexto)


def _ignorado(tag: str) -> bool:
    """True quando o elemento e metadado ou parametro e nao item de story."""
    return tag in ETIQUETAS_IGNORADO or tag.startswith(PREFIXOS_IGNORADO)


def _aninhado(item: ET.Element, contexto: Contexto) -> None:
    """Item de story que o leitor nao sabe ler."""
    nome = item.get("name") or ""
    alvo = f"{item.tag} {nome}".strip()
    contexto.erro("FORMAT_NESTED_UNSUPPORTED", f"nested item not supported: {alvo}")


def _transicao(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Transicao na spine: o FCPXML nao carrega a transicao de audio."""
    nome = item.get("name") or ""
    contexto.erro("FORMAT_TRANSITION_UNSUPPORTED", f"transition not supported: {nome}")


def _audition(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Audition: o leitor nao sabe comparar duas trilhas."""
    nome = item.get("name") or ""
    contexto.erro("FORMAT_NESTED_UNSUPPORTED", f"audition not supported: {nome}")


def _video_item(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Item so de video: o leitor pula e avisa uma vez, mas olha os filhos.

    Um title ou um gerador de video pode carregar clips, storylines e
    marcadores: eles sao lidos com o ponto do proprio item.
    """
    _video(contexto)
    _filhos(item, contexto, _ponto_do_item(item, contexto, ponto))


def _gap(gap: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Percorre um gap da spine: os clips conectados e os marcadores."""
    dentro = _ponto_do_item(gap, contexto, ponto)
    for item in gap:
        _item(item, contexto, dentro)


def _storyline(spine: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Percorre uma storyline secundaria, que fica na lane que ela declara.

    A storyline fica no offset que declara dentro do pai, e os itens dela
    contam a partir de zero: o offset de cada item e local da storyline. A
    lane dela e a lane do pai mais a lane que ela declara.
    """
    posicao = ponto.absoluto(tempo_atributo(contexto, spine, "offset"))
    dentro = _Ponto(posicao, Fraction(0), lane=ponto.lane_do_item(spine))
    for item in spine:
        _item(item, contexto, dentro)


def _nome_do_item(item: ET.Element, contexto: Contexto) -> str:
    """Nome do item: o que ele declara ou o nome do asset que ele aponta."""
    nome = item.get("name") or ""
    asset = contexto.assets.get(item.get("ref", ""))
    return nome or (asset.nome if asset is not None else "")


def _clip(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Le um asset-clip: posicao, ganho, canais e o clip de audio.

    Um clip desligado e erro e nao vira clip, mas os clips presos a ele
    continuam na cena: eles tocam no FCP mesmo com o clip desligado.
    """
    nome = _nome_do_item(item, contexto)
    dentro = _ponto_do_item(item, contexto, ponto)
    if item.get("enabled") == "0":
        contexto.erro("FORMAT_CLIP_DISABLED", f"the clip {nome!r} is disabled")
    else:
        elementos = _elementos(item)
        _efeitos(item, elementos, nome, contexto)
        _audio_do_clip(item, contexto, ponto, dentro, elementos)
    _filhos(item, contexto, dentro)


def _audio(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Le um item <audio> como o audio de um asset-clip, e olha os filhos.

    O fade e o efeito do item de audio sao erro do proprio item: o pai nao os
    ve como seus.
    """
    nome = _nome_do_item(item, contexto)
    dentro = _ponto_do_item(item, contexto, ponto)
    if item.get("enabled") == "0":
        contexto.erro("FORMAT_CLIP_DISABLED", f"the clip {nome!r} is disabled")
    else:
        elementos = _elementos(item)
        _efeitos(item, elementos, nome, contexto)
        _audio_do_clip(item, contexto, ponto, dentro, elementos)
    _filhos(item, contexto, dentro)


def _audio_do_clip(
    item: ET.Element,
    contexto: Contexto,
    ponto: _Ponto,
    dentro: _Ponto,
    elementos: list[ET.Element],
) -> None:
    """Le o audio do clip: asset, ganho, canais, offset e posicao."""
    nome = _nome_do_item(item, contexto)
    asset = contexto.assets.get(item.get("ref", ""))
    if asset is None:
        contexto.erro("FORMAT_FCPXML_MEDIA_MISSING", f"asset missing in the clip {nome!r}")
        return
    if _sem_audio(item, asset):
        _video(contexto)
        return
    audio = fcpxml_audio.avaliar(item, asset.elemento, asset.canais, elementos, nome, contexto)
    if audio is None:
        return
    fonte, inicio, duracao = _tempos_do_audio(item, asset, dentro, contexto)
    offset = _offset_do_clip(fonte, asset, nome, contexto)
    if offset is None:
        return
    lane = ponto.lane_do_item(item)
    clip = Clip(
        lane=lane,
        nome=nome,
        asset_id=asset.id,
        inicio=amostras(contexto, inicio, f"the clip {nome!r}"),
        fim=amostras(contexto, inicio + duracao, f"the clip {nome!r}"),
        offset=offset,
        canal=audio.canal,
        canais=audio.canais,
        ganho=audio.ganho,
    )
    contexto.clips.setdefault(lane, []).append(clip)


def _tempos_do_audio(
    item: ET.Element,
    asset: Asset,
    dentro: _Ponto,
    contexto: Contexto,
) -> tuple[Fraction, Fraction, Fraction]:
    """A fonte, o inicio e a duracao do audio do clip.

    O audioStart corta o audio em outro ponto da fonte: o audio comeca no
    tempo de sequencia corrigido pelo corte e dura o audioDuration.
    """
    video = tempo_atributo(contexto, item, "start") if item.get("start") else asset.inicio
    fonte = tempo_atributo(contexto, item, "audioStart") if item.get("audioStart") else video
    duracao = tempo_atributo(contexto, item, "audioDuration")
    if not item.get("audioDuration"):
        duracao = tempo_atributo(contexto, item, "duration")
    return fonte, dentro.posicao + (fonte - video), duracao


def _offset_do_clip(fonte: Fraction, asset: Asset, nome: str, contexto: Contexto) -> int | None:
    """Offset do audio dentro do arquivo, em samples; None quando nao fecha."""
    offset = amostras(contexto, fonte - asset.inicio, f"the offset of the clip {nome!r}")
    if offset < 0:
        contexto.erro("FORMAT_POSICAO_INDEFINIDA", f"the clip {nome!r} starts before the file")
        return None
    return offset


def _sem_audio(item: ET.Element, asset: Asset | None) -> bool:
    """True quando o clip nao traz audio: so video ou asset sem audio."""
    if asset is None:
        return False
    if (item.get("hasAudio") or "1") == "0" or not asset.tem_audio:
        return True
    return fcpxml_audio.so_video(item)


def _ponto_do_item(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> _Ponto:
    """Ponto de um item: onde ele esta e de onde o conteudo dele conta."""
    posicao = ponto.absoluto(tempo_atributo(contexto, item, "offset"))
    return _Ponto(
        posicao,
        tempo_atributo(contexto, item, "start"),
        lane=ponto.lane_do_item(item),
    )


def _video(contexto: Contexto) -> None:
    """Pula um item so de video, uma vez por documento."""
    if contexto.video_pulado:
        return
    contexto.video_pulado = True
    contexto.report.info("FORMAT_VIDEO_TRACK_SKIPPED", "video item skipped")


def _filhos(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Visita os itens que o item contem."""
    for filho in item:
        _item(filho, contexto, ponto)


def _elementos(item: ET.Element) -> list[ET.Element]:
    """Os elementos do clip, sem os dos itens que ele carrega.

    Os efeitos de um item preso sao erro dele: nao do clip que o carrega. Os
    elementos de um componente desligado tambem ficam de fora: eles nao soam.
    """
    protegidos = {elem for filho in item if filho.tag in ETIQUETAS_PRESAS for elem in filho.iter()}
    for filho in item:
        if filho.tag == "audio-channel-source" and fcpxml_audio.desligado(filho):
            protegidos.update(filho.iter())
    return [elem for elem in item.iter() if elem not in protegidos]


def _lane(item: ET.Element) -> int:
    """Lane declarada no clip; zero quando o atributo nao diz uma lane."""
    try:
        return int(item.get("lane", "0"))
    except ValueError:
        return 0


def _efeitos(item: ET.Element, elementos: list[ET.Element], nome: str, contexto: Contexto) -> None:
    """Reporta fade, efeito, melhoria de audio e ajuste desconhecido do clip.

    O ajuste desconhecido so vira aviso num clip que soa: num clip sem audio
    ele nao muda nada que o .als leve.
    """
    soa = not _sem_audio(item, contexto.assets.get(item.get("ref", "")))
    for elemento in elementos:
        if elemento.tag in ETIQUETAS_FADE:
            contexto.erro("FORMAT_FADE_UNSUPPORTED", f"the clip {nome!r} has a fade")
        elif elemento.tag in MELHORIAS_AUDIO and _muda_o_som(elemento):
            contexto.erro(
                "FORMAT_EFFECT_UNSUPPORTED",
                f"the clip {nome!r} has the audio enhancement {elemento.tag}",
            )
        elif elemento.tag in ETIQUETAS_EFEITO and _muda_o_som(elemento):
            contexto.erro("FORMAT_EFFECT_UNSUPPORTED", f"the clip {nome!r} has an effect")
        elif soa and _ajuste_desconhecido(elemento.tag) and not fcpxml_audio.desligado(elemento):
            contexto.report.warning(
                "FORMAT_EFFECT_UNKNOWN",
                f"the clip {nome!r} has the unknown adjustment {elemento.tag}",
            )


def _ajuste_desconhecido(tag: str) -> bool:
    """True quando o elemento e um ajuste que o leitor nao conhece.

    Os ajustes de video do FCP sao conhecidos e passam em silencio; o volume,
    o pan e as seis melhorias tem regra propria. Qualquer outro `adjust-` e um
    ajuste que o leitor nao sabe ler, e vira aviso quando esta ligado.
    """
    if not tag.startswith("adjust-"):
        return False
    return tag not in AJUSTES_DE_VIDEO and tag not in AJUSTES_COM_REGRA


def _muda_o_som(elemento: ET.Element) -> bool:
    """True quando a melhoria ou o efeito mexe no som do clip."""
    if fcpxml_audio.desligado(elemento):
        return False
    if elemento.tag == "adjust-EQ" and elemento.get("mode") == "flat":
        return next(elemento.iter("param"), None) is not None
    return elemento.tag != "conform-rate" or elemento.get("scaleEnabled") != "0"


def _marcador(item: ET.Element, contexto: Contexto, ponto: _Ponto) -> None:
    """Marcador do gap ou da spine, na posicao que ele declara."""
    posicao = ponto.absoluto(tempo_atributo(contexto, item, "start"))
    nome = item.get("value") or ""
    marcador = MarkerIR(
        id=f"k{len(contexto.markers)}",
        name=nome,
        position_samples=amostras(contexto, posicao, f"the marker {nome!r}"),
    )
    contexto.markers.append(marcador)


def tempo_atributo(contexto: Contexto, elemento: ET.Element, atributo: str) -> Fraction:
    """Tempo exato de um atributo do FCPXML; zero quando o atributo falta."""
    bruto = elemento.get(atributo)
    if bruto is None:
        return Fraction(0)
    try:
        return tempo_fcpxml(bruto)
    except ValueError:
        contexto.erro("FORMAT_POSICAO_INDEFINIDA", f"invalid time {bruto!r} in {atributo!r}")
        return Fraction(0)


def amostras(contexto: Contexto, segundos: Fraction, nome: str) -> int:
    """Samples de um tempo em segundos; avisa quando o tempo nao cai inteiro."""
    inteiro, residuo = quantize_samples(segundos * contexto.sample_rate)
    if residuo:
        contexto.report.warning(
            "FORMAT_SUBSAMPLE_POSITION", f"{nome} does not fall on a whole sample"
        )
    return inteiro


_MANIPULADORES = {
    "asset-clip": _clip,
    "audio": _audio,
    "gap": _gap,
    "spine": _storyline,
    "marker": _marcador,
    "chapter-marker": _marcador,
    "transition": _transicao,
    "audition": _audition,
    "video": _video_item,
    "title": _video_item,
    "caption": _video_item,
    "live-drawing": _video_item,
}
