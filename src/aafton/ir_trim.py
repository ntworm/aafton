"""Corte no fim da midia: clip que pede menos de um frame alem da propria midia.

Editores arredondam o fim do clip para o frame seguinte, e a midia acaba antes.
Um excesso menor que um frame e cortado no fim da midia, com uma nota; um
excesso de um frame ou mais fica como esta e continua erro no export.
"""

from __future__ import annotations

from fractions import Fraction

from .ir import ClipIR, SessionIR, TrackIR
from .report import Report

TRIMMED_CODE = "CLIP_TRIMMED_TO_MEDIA_END"
TRIMMED_STATUS = "trimmed to media end"
# status de linha do verify que trazem desvio medido
MEASURED_STATUSES = ("ok", "out of tolerance", TRIMMED_STATUS)


def samples_per_frame(session: SessionIR) -> Fraction:
    """Samples de um frame na taxa de timecode da sessao, como fracao exata."""
    fps = session.timecode_rate.fps
    return Fraction(session.sample_rate * fps.denominator, fps.numerator)


def media_excess(clip: ClipIR, session: SessionIR) -> int | None:
    """Samples que o clip pede alem do fim da midia; None quando nao da para saber."""
    if clip.source_offset_samples is None or clip.media_id is None:
        return None
    asset = session.media_by_id(clip.media_id)
    if asset is None or asset.duration_samples is None:
        return None
    if asset.sample_rate is not None and asset.sample_rate != session.sample_rate:
        return None
    return clip.source_offset_samples + clip.duration_samples - asset.duration_samples


def _fades_em_par(clip: ClipIR, seguinte: ClipIR) -> bool:
    """O fade de saida do clip faz par com o fade de entrada do seguinte."""
    if clip.fade_out is None or seguinte.fade_in is None:
        return False
    return clip.fade_out.duration_samples == seguinte.fade_in.duration_samples


def _termina_em_transicao(clip: ClipIR, track: TrackIR) -> bool:
    """O fim do clip esta numa transicao com o proximo clip do mesmo canal."""
    canal = sorted(
        (c for c in track.clips if c.channel_index == clip.channel_index),
        key=lambda c: c.timeline_start_samples,
    )
    posicao = next(i for i, c in enumerate(canal) if c is clip)
    if posicao + 1 >= len(canal):
        return False
    seguinte = canal[posicao + 1]
    if seguinte.timeline_start_samples < clip.timeline_end_samples:
        return True
    return seguinte.crossfade_in_state or _fades_em_par(clip, seguinte)


def _excesso_cortavel(clip: ClipIR, track: TrackIR, session: SessionIR, limite) -> int | None:
    """Excesso do clip quando ele pode ser cortado; None quando fica como esta."""
    excesso = media_excess(clip, session)
    if excesso is None or not 0 < excesso < limite:
        return None
    # um clip que ficaria com menos de 1 frame nao e o arredondamento do editor
    if clip.duration_samples - excesso < limite:
        return None
    if _termina_em_transicao(clip, track):
        return None
    return excesso


def _eventos(track: TrackIR) -> list[list[ClipIR]]:
    """Clips da track agrupados por inicio e fim: os canais de um mesmo evento."""
    grupos: dict[tuple[int, int], list[ClipIR]] = {}
    for clip in track.clips:
        chave = (clip.timeline_start_samples, clip.timeline_end_samples)
        grupos.setdefault(chave, []).append(clip)
    return list(grupos.values())


def _cortar(clip: ClipIR, excesso: int) -> None:
    """Recua o fim do clip e encurta o fade de saida que nao cabe mais."""
    clip.timeline_end_samples -= excesso
    # o fade de saida cabe no que sobra depois do fade de entrada
    sobra = clip.duration_samples - (clip.fade_in.duration_samples if clip.fade_in else 0)
    if clip.fade_out is not None and clip.fade_out.duration_samples > sobra:
        clip.fade_out.duration_samples = max(sobra, 0)


def _cortar_evento(evento: list[ClipIR], track: TrackIR, session: SessionIR, limite) -> list[int]:
    """Corta o evento so quando todos os canais tem o mesmo excesso cortavel."""
    excessos = [_excesso_cortavel(clip, track, session, limite) for clip in evento]
    if None in excessos or len(set(excessos)) != 1:
        return []
    for clip in evento:
        _cortar(clip, excessos[0])
    return excessos


def trim_to_media_end(session: SessionIR, report: Report) -> None:
    """Corta no fim da midia o clip que pede menos de um frame alem dela.

    Age no proprio ``session``. Emite uma nota agregada quando algo foi cortado.
    Sem taxa de timecode o frame e desconhecido, e nada e tocado.
    """
    if session.timecode_rate is None:
        return
    limite = samples_per_frame(session)
    cortados: list[int] = []
    # video nao tem fim de midia em samples de audio; fica de fora
    for track in (t for t in session.tracks if not t.is_video):
        for evento in _eventos(track):
            cortados.extend(_cortar_evento(evento, track, session, limite))
    if not cortados:
        return
    report.info(
        TRIMMED_CODE,
        f"{len(cortados)} clips ended up to {max(cortados)} samples past the end of their "
        f"media (less than one frame); trimmed to the media end",
        {"clips": len(cortados), "max_excess_samples": max(cortados)},
    )


def _cortado_no_fim(aaf_clip: ClipIR, set_clip: ClipIR, set_session: SessionIR, quadro) -> bool:
    """O clip do set e o do AAF cortado no fim da midia do set, por menos de um frame."""
    offset = set_clip.source_offset_samples
    if offset is None or offset != aaf_clip.source_offset_samples:
        return False
    asset = set_session.media_by_id(set_clip.media_id) if set_clip.media_id else None
    if asset is None or asset.duration_samples is None:
        return False
    if offset + set_clip.duration_samples != asset.duration_samples:
        return False
    encurtado = aaf_clip.timeline_end_samples - set_clip.timeline_end_samples
    excesso_aaf = offset + aaf_clip.duration_samples - asset.duration_samples
    return encurtado == excesso_aaf and 0 < encurtado < quadro


def media_end_status(
    aaf_clip: ClipIR, set_clip: ClipIR, set_session: SessionIR, tolerance: int, quadro
) -> str:
    """Estado da linha de audio do verify: ok, cortado no fim da midia ou fora da tolerancia."""
    delta_start = set_clip.timeline_start_samples - aaf_clip.timeline_start_samples
    delta_end = set_clip.timeline_end_samples - aaf_clip.timeline_end_samples
    if abs(delta_start) > tolerance:
        return "out of tolerance"
    if abs(delta_end) <= tolerance:
        return "ok"
    if _cortado_no_fim(aaf_clip, set_clip, set_session, quadro):
        return TRIMMED_STATUS
    return "out of tolerance"
