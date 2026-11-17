"""Conferencia da posicao das pecas removidas (saiu) na copia aplicada.

O oraculo manda para a track [saiu] o material que o corte novo nao cobre.
A peca tem de ficar na emenda do trecho removido, mais o deslocamento que
ela tinha dentro desse trecho, dentro de uma amostra.
"""

from __future__ import annotations

from .reconform_verify import (
    _Clip,
    _Ctx,
    _base_saiu,
    _emenda,
    _evento_em,
    _frame_guardado,
    _frames,
    _para_samples,
)

# Uma amostra: a tolerancia da posicao conferida.
_TOLERANCIA = 1
# Duas amostras: a tolerancia do material conferido na fonte.
_TOLERANCIA_FONTE = 2


def _clips_saiu(aplicado: dict[str, list[_Clip]], nome: str) -> list[_Clip]:
    """Os clips da copia que estao nas tracks [saiu] da track de origem."""
    saida: list[_Clip] = []
    for track, clips in aplicado.items():
        if _base_saiu(track) == nome:
            saida.extend(clips)
    return saida


def _cobre(clip: _Clip, loop: int, duracao: int) -> bool:
    """O clip da track [saiu] tem o material da peca na fonte.

    O deslocamento na fonte mede a distancia entre o inicio da peca e o inicio
    do clip, que em clip sem warp e a mesma distancia na timeline.
    """
    deslocamento = loop - clip.loop_start
    if deslocamento < -_TOLERANCIA_FONTE:
        return False
    return deslocamento + duracao <= (clip.end - clip.start) + _TOLERANCIA_FONTE


def _esperado(ctx: _Ctx, inicio: int) -> int | None:
    """Posicao esperada da peca removida, em samples absolutos.

    A emenda e o ponto onde o material removido entra no corte novo; a peca
    fica na emenda mais o deslocamento que tinha dentro do trecho removido.
    """
    frame = _frames(ctx, inicio)
    evento = _evento_em(ctx.old.events, frame)
    if evento is None or evento.source_in is None:
        return None
    tc = evento.source_in + (frame - evento.record_in)
    guardado = _frame_guardado(ctx, evento, tc)
    if guardado is None:
        return None
    emenda = _emenda(ctx, frame)
    if emenda is None:
        return None
    # O trecho removido comeca no frame seguinte ao ultimo guardado, mas nunca
    # antes da sequencia de planos encostados que contem a peca: uma lacuna de
    # V1 entre os dois corta o trecho.
    comeco = max(guardado + 1, _inicio_da_sequencia(ctx, evento))
    return emenda + (inicio - _para_samples(ctx, comeco))


def _inicio_da_sequencia(ctx: _Ctx, evento) -> int:
    """Primeiro frame da sequencia de planos encostados que termina no evento."""
    inicio = evento.record_in
    for outro in sorted(ctx.old.events, key=lambda ev: ev.record_in, reverse=True):
        if outro.record_out == inicio and outro.kind == "clip":
            inicio = outro.record_in
    return inicio


def conferir_saiu(
    ctx: _Ctx,
    nome: str,
    pedaco: tuple[int, int, int],
    aplicado: dict[str, list[_Clip]],
) -> None:
    """Confere a posicao da peca removida na track [saiu] da copia aplicada.

    O apply pode juntar numa track [saiu] varias pecas do trecho removido: a
    peca e conferida pela posicao que ela ocupa dentro do clip da copia.
    """
    inicio, fim, loop = pedaco
    esperado = _esperado(ctx, inicio)
    if esperado is None:
        return
    duracao = fim - inicio
    achou = any(
        abs(clip.start + (loop - clip.loop_start) - esperado) <= _TOLERANCIA
        and _cobre(clip, loop, duracao)
        for clip in _clips_saiu(aplicado, nome)
    )
    if not achou:
        ctx.problema(
            "RECONFORM_VERIFY_POSITION",
            nome,
            f"removed piece at {inicio} expected at {esperado}",
        )
