"""Ajuda comum aos escritores de EDL e de XML do FCP.

Os dois escritores conferem as mesmas coisas antes de gravar: o que o
formato nao carrega, a origem do arquivo lida do bext, o arredondamento das
posicoes para o frame e os canais das tracks de audio. O que muda entre eles
e o codigo do erro e o nome do formato nas mensagens.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from ..ir import ClipIR, MediaAsset, SessionIR, TrackIR
from ..media_extract import parse_bext
from ..report import Report
from ..timecode import TimecodeRate, frames_to_samples_exact, samples_to_frames_exact

# Midia com mais de um canal nao cabe na EDL nem no XML do FCP.
CODIGO_MULTICANAL = "FORMAT_MULTICHANNEL_UNSUPPORTED"


@dataclass
class Contexto:
    """Estado da escrita: sessao, relatorio e o que ja foi conferido."""

    session: SessionIR
    report: Report
    arredondados: int = 0
    maior_erro: Fraction = Fraction(0)
    origens: dict[str, int] = field(default_factory=dict)


@dataclass
class Canal:
    """Um canal de audio da sessao, com os clips que ele carrega."""

    track: TrackIR
    numero: int
    indice: int
    clips: list[ClipIR] = field(default_factory=list)


def conferir_recursos(
    session: SessionIR,
    report: Report,
    codigo: str,
    formato: str,
    codigo_marcadores: str | None = None,
) -> None:
    """Recusa o que o formato nao carrega e avisa o que ele descarta.

    O codigo do erro e o nome do formato sao parametros: a EDL recusa com
    FORMAT_EDL_FEATURE_UNSUPPORTED e avisa os marcadores descartados; um
    formato que carrega marcadores nao passa o codigo do aviso.
    """
    for mensagem in session.unsupported_export_features:
        report.error("WRITE_SESSION_FEATURE_UNSUPPORTED", mensagem)
    for track in session.tracks:
        if not track.is_video:
            conferir_track(track, report, codigo, formato)
    if codigo_marcadores and session.markers:
        report.warning(
            codigo_marcadores,
            f"the session has {len(session.markers)} markers, which {formato} does not carry",
        )


def conferir_track(track: TrackIR, report: Report, codigo: str, formato: str) -> None:
    """Recusa a automacao, o mudo, o volume e o pan da track."""
    if track.gain_points or track.pan_points:
        report.error(
            codigo,
            f"the track {track.name!r} has gain or pan automation, which {formato} does not carry",
            {"track": track.name},
        )
    for recusa in _recusas_da_track(track):
        report.error(codigo, f"{recusa}, which {formato} does not carry", {"track": track.name})
    for clip in track.clips:
        conferir_clip(clip, report, codigo, formato)


def _recusas_da_track(track: TrackIR) -> list[str]:
    """O que a track tem e o formato nao carrega: mudo, volume e pan."""
    recusas = []
    if track.muted:
        recusas.append(f"the track {track.name!r} is muted")
    if track.gain_linear != 1.0:
        recusas.append(f"the track {track.name!r} has volume {track.gain_linear}")
    if track.pan != 0.0:
        recusas.append(f"the track {track.name!r} has pan {track.pan}")
    return recusas


def conferir_multicanal(session: SessionIR, report: Report, formato: str) -> None:
    """Recusa clip de midia com mais de um canal, que o formato nao carrega."""
    for track in session.tracks:
        if track.is_video:
            continue
        for clip in track.clips:
            asset = session.media_by_id(clip.media_id) if clip.media_id else None
            if asset is None or not asset.channels or asset.channels <= 1:
                continue
            report.error(
                CODIGO_MULTICANAL,
                f"the clip {clip.name!r} uses the file {asset.filename!r}, which has "
                f"{asset.channels} channels, which {formato} does not carry",
                {"clip": clip.name, "filename": asset.filename},
            )


def conferir_clip(clip: ClipIR, report: Report, codigo: str, formato: str) -> None:
    """Recusa fade, ganho e mudo de um clip, que o formato nao carrega."""
    recusas = []
    if clip.fade_in or clip.fade_out:
        recusas.append(f"the clip {clip.name!r} has a fade")
    if clip.gain_linear != 1.0:
        recusas.append(f"the clip {clip.name!r} has gain {clip.gain_linear}")
    if clip.muted:
        recusas.append(f"the clip {clip.name!r} is muted")
    for recusa in recusas:
        report.error(codigo, f"{recusa}, which {formato} does not carry")


def origem(contexto: Contexto, asset: MediaAsset) -> int:
    """Samples do inicio do arquivo, lidos do bext; zero quando nao ha bext."""
    if asset.id not in contexto.origens:
        contexto.origens[asset.id] = _origem_do_arquivo(contexto, asset)
    return contexto.origens[asset.id]


def _origem_do_arquivo(contexto: Contexto, asset: MediaAsset) -> int:
    """Origem do timecode de um arquivo, conferida uma vez por arquivo."""
    sessao = contexto.session
    if asset.sample_rate is not None and asset.sample_rate != sessao.sample_rate:
        contexto.report.error(
            "FORMAT_SOURCE_TC_RATE_MISMATCH",
            f"the file {asset.filename!r} is at {asset.sample_rate} Hz "
            f"and the session at {sessao.sample_rate} Hz",
            {"media_id": asset.id},
        )
        return 0
    bext = _bext(asset)
    if bext is None:
        contexto.report.warning(
            "FORMAT_SOURCE_TC_FROM_ZERO",
            f"the file {asset.filename!r} has no bext; the origin timecode starts at zero",
            {"media_id": asset.id},
        )
        return 0
    return int(bext["time_reference"])


def _bext(asset: MediaAsset) -> dict | None:
    """Chunk bext do arquivo do asset; None quando nao ha ou nao da para ler."""
    if not asset.resolved_path:
        return None
    try:
        return parse_bext(asset.resolved_path)
    except (OSError, ValueError):
        return None


def frame(contexto: Contexto, samples: int, rate: TimecodeRate) -> int:
    """Frame da posicao em samples, arredondado, contando o arredondamento."""
    sample_rate = contexto.session.sample_rate
    exato = Fraction(samples) * Fraction(rate.num, sample_rate * rate.den)
    frame = samples_to_frames_exact(samples, rate=rate, sample_rate=sample_rate)
    if exato != frame:
        frame = round(exato)
        erro = abs(Fraction(samples) - frames_to_samples_exact(frame, rate, sample_rate))
        contexto.arredondados += 1
        if erro > contexto.maior_erro:
            contexto.maior_erro = erro
    return frame


def avisar_arredondamento(contexto: Contexto) -> None:
    """Avisa, uma vez, quantas posicoes foram arredondadas e o maior erro."""
    if not contexto.arredondados:
        return
    contexto.report.warning(
        "FORMAT_POSITION_ROUNDED_TO_FRAME",
        f"{contexto.arredondados} positions were rounded to the nearest frame; "
        f"the largest error was {int(contexto.maior_erro)} samples",
    )


def alocar_canais(tracks: list[TrackIR]) -> list[Canal]:
    """Divide as tracks de audio em canais, na ordem global dos canais.

    O numero do canal e global: a primeira track de audio comeca no canal 1
    e cada canal a seguir soma o tanto de canais que a track anterior tinha.
    """
    canais: list[Canal] = []
    numero = 1
    for track in tracks:
        if track.is_video:
            continue
        quantidade = max(1, track.channel_count)
        for indice in range(quantidade):
            canais.append(
                Canal(
                    track=track,
                    numero=numero + indice,
                    indice=indice + 1,
                    clips=_clips_do_canal(track, indice + 1, quantidade),
                )
            )
        numero += quantidade
    return canais


def _clips_do_canal(track: TrackIR, canal: int, quantidade: int) -> list[ClipIR]:
    """Clips da track que ficam no canal dado."""
    clips: list[ClipIR] = []
    for grupo in _grupos(track.clips):
        if len(grupo) >= quantidade:
            clips.extend(_clips_do_grupo(grupo, canal, quantidade))
        else:
            clips.extend(grupo)
    return clips


def _clips_do_grupo(grupo: list[ClipIR], canal: int, quantidade: int) -> list[ClipIR]:
    """Clips do grupo que ficam no canal dado.

    Com um clip por canal, cada clip fica no seu canal; com menos clips que
    canais, cada clip fica em todos os canais.
    """
    return [
        clip
        for clip in sorted(grupo, key=lambda item: item.channel_index)
        if min(clip.channel_index, quantidade - 1) + 1 == canal
    ]


def _grupos(clips: list[ClipIR]) -> list[list[ClipIR]]:
    """Clips agrupados pelo intervalo de tempo, na ordem em que aparecem."""
    grupos: list[list[ClipIR]] = []
    por_chave: dict[tuple[int, int], list[ClipIR]] = {}
    for clip in clips:
        chave = (clip.timeline_start_samples, clip.timeline_end_samples)
        grupo = por_chave.get(chave)
        if grupo is None:
            grupo = []
            por_chave[chave] = grupo
            grupos.append(grupo)
        grupo.append(clip)
    return grupos


def gravar(destino: Path, texto: str) -> None:
    """Grava o texto por arquivo temporario e troca pelo destino, sem arquivo pela metade."""
    temporario = destino.with_name(f"{destino.name}.tmp")
    try:
        temporario.write_bytes(texto.encode("utf-8"))
        os.replace(temporario, destino)
    finally:
        if temporario.exists():
            temporario.unlink()
