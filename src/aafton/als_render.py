"""Clips de um set sem registro do aafton que precisam de render no Live.

Um set que nao nasceu no aafton chega com clips que o AAF nao leva como o Live
toca: warp que nao e 1:1, modo de warp nao medido, transposicao, loop nao
expansivel ou StartRelative fora de um loop expansivel. ``precisa_render`` diz
as causas de um clip; a leitura junta tudo num ``RenderLog`` e, no fim, emite um
unico ``ALS_RENDER_IN_LIVE`` com a lista inteira das tracks, para o usuario
consertar tudo de uma vez no Live.

Warp 1:1 e exato (medido no Live em 15/11/2026: Consolidate e Separate Stems
gravam ``IsWarped`` com marcadores em velocidade 1:1), mas so nos modos
Re-Pitch (3), Complex (4) e Complex Pro (6), os modos medidos. Num clip assim
o offset na midia e a amostra que o Live le no beat ``LoopStart``
(``als_leitura``), e um loop expansivel (``als_loop.loop_expansivel``) deixa de
pedir render tanto para ``loop`` quanto para ``StartRelative``.

O video e reconhecido pela extensao do arquivo, sem abrir o arquivo, antes de
qualquer checagem de warp: ele nunca entra na lista de render.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .als_paths import AUDIO_CLIP_FIELDS
from .als_xml import get_value, set_value
from .report import Report

VIDEO_EXTENSOES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mxf", ".mkv"})

# Modos de warp em que a passagem a 1:1 foi medida: Re-Pitch, Complex, Complex Pro.
MODOS_MEDIDOS = frozenset({"3", "4", "6"})
MODO_BEATS = "0"

# Desvio acumulado aceito num clip com warp 1:1, em amostras da sessao.
TOLERANCIA_WARP = Fraction(1, 2)

CONSELHO = (
    "Consolidate them in Live (Ctrl+J), or freeze and flatten the tracks, save the set "
    "and read it again; or export with --leave-out-render"
)
MISTA = "mono and stereo files on one track; move them to separate tracks in Live"

# Erros que param o export mesmo num relatorio permissivo.
CODIGOS_QUE_PARAM = ("ALS_RENDER_IN_LIVE", "ALS_NOTHING_TO_EXPORT")

_CROSSFADE = AUDIO_CLIP_FIELDS["crossfade_in_state"]


class RenderInLiveError(ValueError):
    """O set tem clips que precisam de render no Live, ou nao sobrou nada."""


def e_video(caminho: str) -> bool:
    """O arquivo e um video, pela extensao."""
    return Path(caminho).suffix.lower() in VIDEO_EXTENSOES


def _fracao(texto: str | None) -> Fraction | None:
    """O numero exato do texto do XML; None quando nao e numero finito."""
    if texto is None:
        return None
    try:
        return Fraction(texto.strip())
    except (ValueError, ZeroDivisionError):
        return None


def _diferente_de_zero(clip_node: ET.Element, caminho: str) -> bool:
    """O valor existe e nao e zero; um valor que nao e numero tambem conta."""
    texto = get_value(clip_node, caminho)
    if texto is None:
        return False
    valor = _fracao(texto)
    return valor is None or valor != 0


def _marcadores(clip_node: ET.Element) -> list[tuple[Fraction, Fraction]] | None:
    """(BeatTime, SecTime) dos WarpMarker em ordem; None sem dois marcadores legiveis."""
    pontos = []
    for marcador in clip_node.findall("WarpMarkers/WarpMarker"):
        beat = _fracao(marcador.get("BeatTime"))
        segundo = _fracao(marcador.get("SecTime"))
        if beat is None or segundo is None:
            return None
        pontos.append((beat, segundo))
    pontos.sort()
    return pontos if len(pontos) >= 2 else None


def _duracao_em_amostras(clip_node: ET.Element, tempo: Fraction, taxa: int) -> Fraction | None:
    """Duracao do clip no arranjo, em amostras da sessao."""
    if tempo <= 0:
        return None
    inicio = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"]))
    fim = _fracao(get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"]))
    if inicio is None or fim is None:
        return None
    return (fim - inicio) * 60 / tempo * taxa


def warp_um_para_um(clip_node: ET.Element, tempo: Fraction, taxa: int) -> bool:
    """Todo segmento de marcadores toca a 1:1, com desvio de ate meia amostra no clip."""
    if tempo <= 0:
        return False
    pontos = _marcadores(clip_node)
    duracao = _duracao_em_amostras(clip_node, tempo, taxa)
    if pontos is None or duracao is None:
        return False
    segundos_por_beat = Fraction(60) / tempo
    for (b0, s0), (b1, s1) in zip(pontos, pontos[1:]):
        if b1 <= b0:
            return False
        razao = (s1 - s0) / ((b1 - b0) * segundos_por_beat)
        if abs(razao - 1) * abs(duracao) > TOLERANCIA_WARP:
            return False
    return True


def _causas_do_warp(clip_node: ET.Element, tempo: Fraction, taxa: int) -> list[str]:
    """Causas de render de um clip com warp ligado."""
    if get_value(clip_node, AUDIO_CLIP_FIELDS["is_warped"]) != "true":
        return []
    modo = (get_value(clip_node, "WarpMode") or "").strip()
    if modo == MODO_BEATS:
        return ["beats mode"]
    if modo not in MODOS_MEDIDOS:
        return ["warp mode"]
    if not warp_um_para_um(clip_node, tempo, taxa) or _inicio_antes_do_arquivo(clip_node, taxa):
        return ["warp"]
    return []


def precisa_render(
    clip_node: ET.Element,
    tempo: Fraction,
    taxa: int,
    origem: int | None = None,
    inicio_amostras: int | None = None,
    fim_amostras: int | None = None,
) -> list[str]:
    """As causas que pedem render no Live antes do AAF; vazia quando o clip e exato.

    Warp que nao e 1:1 ("warp"), modo Beats ("beats mode"), Tones, Texture ou
    outro modo nao medido ("warp mode"), loop ligado ("loop"), StartRelative
    diferente de zero ("StartRelative") e transposicao ("transpose"), esta com
    ou sem warp: o AAF levaria o arquivo na afinacao original. Quando ``origem``,
    ``inicio_amostras`` e ``fim_amostras`` sao passados e ``loop_expansivel``
    vale, ``loop`` e ``StartRelative`` saem das causas.
    """
    from .als_loop import loop_expansivel

    expansivel = (
        origem is not None
        and inicio_amostras is not None
        and fim_amostras is not None
        and loop_expansivel(clip_node, tempo, taxa, origem, inicio_amostras, fim_amostras)
    )
    causas = _causas_do_warp(clip_node, tempo, taxa)
    if not expansivel and get_value(clip_node, AUDIO_CLIP_FIELDS["loop_on"]) == "true":
        causas.append("loop")
    if not expansivel and _diferente_de_zero(clip_node, AUDIO_CLIP_FIELDS["start_relative"]):
        causas.append("StartRelative")
    if _diferente_de_zero(clip_node, "PitchCoarse") or _diferente_de_zero(clip_node, "PitchFine"):
        causas.append("transpose")
    return causas


def segundos_no_beat(clip_node: ET.Element, beat: Fraction) -> Fraction | None:
    """O SecTime interpolado nos marcadores no beat dado.

    Fora dos marcadores o valor e extrapolado pelo segmento mais proximo, que
    ja passou na checagem 1:1.
    """
    pontos = _marcadores(clip_node)
    if pontos is None:
        return None
    i = 0
    while i < len(pontos) - 2 and beat > pontos[i + 1][0]:
        i += 1
    (b0, s0), (b1, s1) = pontos[i], pontos[i + 1]
    if b1 == b0:
        return None
    return s0 + (beat - b0) * (s1 - s0) / (b1 - b0)


def _inicio_antes_do_arquivo(clip_node: ET.Element, taxa: int) -> bool:
    """Warp 1:1 extrapolado para antes do segundo 0 do arquivo: o AAF nao leva."""
    from .als_leitura import offset_do_warp

    offset = offset_do_warp(clip_node, taxa)
    return offset is not None and offset < 0


@dataclass
class TrackDeRender:
    """O que a leitura deixou anotado de uma track."""

    nome: str
    clips: list[tuple[str, list[str]]] = field(default_factory=list)
    deixados: int = 0
    mista: bool = False
    crossfades: list[str] = field(default_factory=list)
    videos: list[str] = field(default_factory=list)


@dataclass
class RenderLog:
    """Clips de render, videos e tracks mistas de uma leitura sem registro do aafton."""

    leave_out: bool = False
    tracks: dict[str, TrackDeRender] = field(default_factory=dict)
    nos_deixados: set[int] = field(default_factory=set)
    # Mensagem da feature de automacao de cada track, pelo id da track.
    features: dict[str, str] = field(default_factory=dict)
    # Nome de cada track lida, pelo id da track.
    track_nomes: dict[str, str] = field(default_factory=dict)

    def da_track(self, track) -> TrackDeRender:
        return self.tracks.setdefault(track.id, TrackDeRender(track.name))

    def clip(self, track, nome: str, causas: list[str], node: ET.Element) -> None:
        anotada = self.da_track(track)
        anotada.clips.append((nome, causas))
        if self.leave_out:
            anotada.deixados += 1
            self.nos_deixados.add(id(node))

    def deixado(self, node: ET.Element) -> bool:
        return id(node) in self.nos_deixados

    def video(self, track, nome: str) -> None:
        self.da_track(track).videos.append(nome)

    def mista(self, track) -> None:
        self.da_track(track).mista = True


def _inicio_em_beats(node: ET.Element) -> float:
    try:
        return float(get_value(node, AUDIO_CLIP_FIELDS["current_start"]) or "inf")
    except ValueError:
        return float("inf")


def soltar_vizinhos(log: RenderLog, track, nodes: list[ET.Element], pares: list) -> None:
    """Tira o estado de crossfade do clip que entrava num clip deixado de fora.

    O no do XML lido muda so na memoria (o set do usuario nunca e escrito):
    assim nem o modelo do crossfade nem a prova de estado o veem.
    """
    if not log.leave_out:
        return
    mantidos = {id(node): clip for clip, node in pares}
    ordem = sorted(nodes, key=_inicio_em_beats)
    for anterior, seguinte in zip(ordem, ordem[1:]):
        clip = mantidos.get(id(seguinte))
        if not log.deixado(anterior) or clip is None:
            continue
        if get_value(seguinte, _CROSSFADE) != "1":
            continue
        set_value(seguinte, _CROSSFADE, 0)
        clip.crossfade_in_state = False
        log.da_track(track).crossfades.append(clip.name)


def marcar_video(log: RenderLog, track, nodes: list[ET.Element]) -> None:
    """A track e de video quando todos os clips dela sao video."""
    anotada = log.tracks.get(track.id)
    if nodes and anotada is not None and len(anotada.videos) == len(nodes):
        track.is_video = True


def _plural(n: int, um: str, varios: str) -> str:
    return f"{n} {um if n == 1 else varios}"


def _texto_da_entrada(entry: dict) -> str:
    """'nome: 4 clips with warp, 1 clip with transpose' e a causa da track mista."""
    contagem: dict[str, int] = {}
    for item in entry.get("clips") or []:
        for causa in item.get("causes") or []:
            contagem[causa] = contagem.get(causa, 0) + 1
    partes = [f"{_plural(n, 'clip', 'clips')} with {causa}" for causa, n in contagem.items()]
    if entry.get("mixed_channels"):
        partes.append(MISTA)
    return f"{entry.get('track_name', '')}: {', '.join(partes)}"


def montar_render(entradas: list[dict]) -> tuple[str, dict]:
    """Mensagem e contexto de ALS_RENDER_IN_LIVE para as entradas escolhidas."""
    mensagem = "; ".join(_texto_da_entrada(entry) for entry in entradas)
    if any(entry.get("clips") for entry in entradas):
        mensagem = f"{mensagem}. {CONSELHO}"
    return mensagem, {"tracks": list(entradas)}


def filtrar_render_issue(issue, escolhidos: set[str]) -> tuple[str, dict] | None:
    """Filtra ALS_RENDER_IN_LIVE pelas tracks escolhidas; None se nenhuma restar."""
    entradas = [
        entry
        for entry in (issue.context.get("tracks") or [])
        if entry.get("track_id") in escolhidos
    ]
    if not entradas:
        return None
    return montar_render(entradas)


def _emitir_render(log: RenderLog, report: Report) -> None:
    """Um unico ALS_RENDER_IN_LIVE com todas as tracks que param o export."""
    com_render = not log.leave_out
    pendentes = [
        (track_id, t) for track_id, t in log.tracks.items() if t.mista or (com_render and t.clips)
    ]
    if not pendentes:
        return
    entradas = [_contexto_da_track(track_id, t, com_render) for track_id, t in pendentes]
    mensagem, contexto = montar_render(entradas)
    report.error("ALS_RENDER_IN_LIVE", mensagem, contexto)


def _contexto_da_track(track_id: str, anotada: TrackDeRender, com_render: bool) -> dict:
    """A lista estruturada de uma track, da qual o Studio monta a caixa."""
    clips = []
    if com_render:
        clips = [{"clip": nome, "causes": causas} for nome, causas in anotada.clips]
    return {
        "track_id": track_id,
        "track_name": anotada.nome,
        "clips": clips,
        "mixed_channels": anotada.mista,
    }


def _emitir_deixados(log: RenderLog, report: Report, esvaziadas: set[str]) -> None:
    """ALS_CLIPS_LEFT_OUT por track, com os crossfades tirados e a track que saiu."""
    for track_id, anotada in log.tracks.items():
        if not anotada.deixados:
            continue
        mensagem = f"{_plural(anotada.deixados, 'clip', 'clips')} of {anotada.nome!r} left out"
        mensagem += " (render in Live)"
        if anotada.crossfades:
            nomes = ", ".join(repr(nome) for nome in anotada.crossfades)
            mensagem += f"; crossfade removed on {nomes}"
        if track_id in esvaziadas:
            mensagem += "; the track leaves the export with its automation"
        report.warning(
            "ALS_CLIPS_LEFT_OUT",
            mensagem,
            {
                "track": anotada.nome,
                "track_id": track_id,
                "count": anotada.deixados,
                "crossfade_removed": list(anotada.crossfades),
                "track_removed": track_id in esvaziadas,
            },
        )


def _emitir_videos(log: RenderLog, report: Report) -> None:
    for track_id, anotada in log.tracks.items():
        for nome in anotada.videos:
            report.info(
                "ALS_VIDEO_LEFT_OUT",
                f"the video clip {nome!r} of {anotada.nome!r} is not in the AAF",
                {"track": anotada.nome, "track_id": track_id, "clip": nome},
            )


def _emitir_midi(midi: list[str], report: Report) -> None:
    if not midi:
        return
    nomes = ", ".join(repr(nome) for nome in midi)
    report.warning(
        "ALS_MIDI_TRACKS_LEFT_OUT",
        f"MIDI tracks left out: {nomes}; freeze and flatten them in Live",
        {"tracks": list(midi)},
    )


def _esvaziada(log: RenderLog, track) -> bool:
    """A track ficou sem clip de audio porque a leitura deixou os dela de fora."""
    anotada = log.tracks.get(track.id)
    if anotada is None or not anotada.deixados:
        return False
    return not track.clips and not track.is_video


def _automacao_de(issue, nomes: set[str], esvaziadas: set[str]) -> bool:
    """O aviso de automacao de uma track que saiu (nao o de um grupo ou retorno)."""
    if issue.code != "ALS_AUTOMATION_UNSUPPORTED" or "kind" in issue.context:
        return False
    track_id = issue.context.get("track_id")
    if track_id is not None:
        return track_id in esvaziadas
    return issue.context.get("track") in nomes


def _nome_da_track(log: RenderLog, session, track_id: str) -> str | None:
    """Nome da track pelo registro de render ou, para o video, pela sessao."""
    if track_id in log.tracks:
        return log.tracks[track_id].nome
    return next((t.name for t in session.tracks if t.id == track_id), None)


def _automacao_do_video(log: RenderLog, session) -> set[str]:
    """Ids das tracks de video: a automacao delas nao vai ao AAF nem para o export."""
    videos = {track.id for track in session.tracks if track.is_video}
    for track_id in videos:
        mensagem = log.features.get(track_id)
        if mensagem in session.unsupported_export_features:
            session.unsupported_export_features.remove(mensagem)
    return videos


def _tirar_esvaziadas(log: RenderLog, session) -> set[str]:
    """Tira da sessao as tracks que ficaram sem clip, com a automacao delas."""
    esvaziadas = {track.id for track in session.tracks if _esvaziada(log, track)}
    for track_id in esvaziadas:
        mensagem = log.features.get(track_id)
        if mensagem in session.unsupported_export_features:
            session.unsupported_export_features.remove(mensagem)
    session.tracks = [track for track in session.tracks if track.id not in esvaziadas]
    return esvaziadas


def _emitir_nada(log: RenderLog, report: Report, session, midi: list[str]) -> None:
    """Sem clip de audio depois de deixar tudo de fora: o export para."""
    if any(track.clips for track in session.tracks if not track.is_video):
        return
    deixados = sum(t.deixados for t in log.tracks.values())
    videos = sum(len(t.videos) for t in log.tracks.values())
    if not (deixados or videos or midi):
        return
    partes = []
    if deixados:
        partes.append(f"{_plural(deixados, 'clip', 'clips')} left out (render in Live)")
    if midi:
        partes.append(_plural(len(midi), "MIDI track", "MIDI tracks"))
    if videos:
        partes.append(_plural(videos, "video", "videos"))
    report.error(
        "ALS_NOTHING_TO_EXPORT",
        f"nothing left to export: {', '.join(partes)}",
        {"left_out": deixados, "midi_tracks": len(midi), "videos": videos},
    )


def fechar_leitura(log: RenderLog, report: Report, session, midi: list[str]) -> None:
    """Fecha a leitura sem registro: tira as tracks vazias e emite as notas e os erros."""
    esvaziadas = _tirar_esvaziadas(log, session)
    fora = esvaziadas | _automacao_do_video(log, session)
    mantidas = {track.name for track in session.tracks if not track.is_video}
    nomes = {_nome_da_track(log, session, t) for t in fora} - mantidas
    report.issues = [issue for issue in report.issues if not _automacao_de(issue, nomes, fora)]
    _emitir_videos(log, report)
    _emitir_midi(midi, report)
    _emitir_deixados(log, report, esvaziadas)
    _emitir_render(log, report)
    _emitir_nada(log, report, session, midi)


def reemitir(leitura: Report, report: Report) -> None:
    """Passa as issues da leitura para o relatorio de quem chamou.

    Notas e avisos vao primeiro, na ordem; depois os erros, com a lista de
    render na frente: num relatorio estrito o primeiro erro levanta, e o
    usuario ve a lista inteira das tracks, como em ``export_session``.
    """
    erros = [issue for issue in leitura.issues if issue.severity == "error"]
    # A lista de render na frente; o 'nada a exportar' por ultimo, para que uma
    # recusa do set inteiro (tempo que varia) apareca antes dele.
    erros.sort(
        key=lambda issue: (
            2 if issue.code == "ALS_NOTHING_TO_EXPORT" else int(issue.code not in CODIGOS_QUE_PARAM)
        )
    )
    for issue in [i for i in leitura.issues if i.severity != "error"] + erros:
        report.add(issue.severity, issue.code, issue.message, issue.context)


def recusar_se_parado(report: Report) -> None:
    """Num relatorio permissivo, a lista de render e o 'nada a exportar' param o export."""
    for issue in report.issues:
        if issue.severity == "error" and issue.code in CODIGOS_QUE_PARAM:
            raise RenderInLiveError(f"{issue.code}: {issue.message}")
