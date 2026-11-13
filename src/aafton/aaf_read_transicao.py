"""Leitura de uma Transition do AAF: o fade que ela representa.

Uma Transition sobrepoe o segmento que vem antes dela ao que vem depois. So
o vizinho imediato conta: entre dois clips ela e um crossfade; depois de um
Filler e o fade-in do clip seguinte a partir do silencio; antes de um Filler
e o fade-out do clip anterior para o silencio. O Resolve e o Premiere usam
os tres casos.

Entre dois clips, a duracao do fade vem das posicoes ja quantizadas dos dois
clips, e nao do comprimento da Transition quantizado sozinho: em taxa que
nao fecha em sample inteiro as duas contas diferem em 1 sample, e o export
deixaria de reconhecer o crossfade simetrico.
"""

from __future__ import annotations

from .ir import FadeIR


def componente_transition(estado, dur_samples: int, comprimento: int) -> None:
    """Recua o cursor, aplica o fade-out do clip anterior e deixa o fade-in pendente."""
    estado.cursor -= comprimento
    cursor_samples = estado.conv.em_samples(estado.cursor)
    anterior = estado.raw_items[-1] if estado.raw_items else None
    dur_fade = dur_samples
    if anterior is not None and anterior.get("kind") == "CLIP" and anterior.get("clip_assets"):
        inicio_proximo = estado.ctx.inicio_absoluto + cursor_samples
        dur_fade = _duracao_pelas_posicoes(estado, anterior, inicio_proximo, dur_samples)
        for clip, _ in anterior["clip_assets"]:
            clip.fade_out = FadeIR(kind="out", duration_samples=dur_fade)
    estado.transicao_pendente = dur_fade
    estado.raw_items.append(
        {"kind": "TRANSITION", "length_samples": dur_fade, "cursor_samples": cursor_samples}
    )


def _duracao_pelas_posicoes(estado, anterior: dict, inicio_proximo: int, dur_samples: int) -> int:
    """Sobreposicao medida pelas posicoes, ou a da Transition quando ela nao vale.

    A sobreposicao tem de ser positiva e caber no clip anterior; fora disso a
    duracao da propria Transition fica, com aviso.
    """
    clip = anterior["clip_assets"][0][0]
    pelas_posicoes = clip.timeline_end_samples - inicio_proximo
    duracao_do_clip = clip.timeline_end_samples - clip.timeline_start_samples
    report = estado.ctx.report
    contexto = {
        "track": estado.track.name,
        "transition_samples": dur_samples,
        "overlap": pelas_posicoes,
    }
    if pelas_posicoes <= 0 or pelas_posicoes > duracao_do_clip:
        report.warning(
            "TRANSITION_OVERLAP_INVALID",
            f"the Transition of the track {estado.track.name!r} asks for {pelas_posicoes} samples of "
            f"overlap with a clip of {duracao_do_clip}; the duration of the Transition stands, "
            f"{dur_samples} samples",
            contexto,
        )
        return dur_samples
    if pelas_posicoes != dur_samples:
        report.info(
            "TRANSITION_LENGTH_FROM_POSITIONS",
            f"the Transition of the track {estado.track.name!r} measures {dur_samples} samples, but the "
            f"overlap of the clips is {pelas_posicoes} samples",
            contexto,
        )
    return pelas_posicoes
