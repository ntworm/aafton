"""Parte de aaf_write: os fades de um clip que nao fazem par com um vizinho.

O fade que cabe no clip e nao tem ponto de automacao dentro dele vira uma
Transition ao lado de um Filler: e a forma que o leitor ja le e que o Resolve e
o Premiere gravam. O fade que nao cabe no clip, ou que tem automacao dentro
dele, continua como envelope de ganho e vira erro no relatorio.
"""

from .report import Report
from .aaf_write_gain import _fades_independentes


def _duracao(fade) -> int:
    """Duracao do fade em samples; zero quando o clip nao tem esse fade."""
    return fade.duration_samples if fade else 0


def _automacao_dentro(gain_points, inicio: int, fim: int) -> bool:
    """Diz se algum ponto de automacao cai dentro do trecho, sem contar as pontas."""
    return any(inicio < t < fim for t, _ in gain_points)


def fades_para_silencio(clip, consumed_in, consumed_out, gain_points) -> tuple[int, int]:
    """Duracao dos fades de entrada e de saida que viram Transition.

    O fade vira Transition quando cabe no clip e nao tem ponto de automacao
    dentro dele; nos outros casos ele fica como envelope de ganho.
    """
    tem_in, tem_out = _fades_independentes(clip, consumed_in, consumed_out)
    inicio, fim = clip.timeline_start_samples, clip.timeline_end_samples
    li, lo = _duracao(clip.fade_in), _duracao(clip.fade_out)
    if li + lo > fim - inicio:
        return 0, 0
    entrada = li if tem_in and not _automacao_dentro(gain_points, inicio, inicio + li) else 0
    saida = lo if tem_out and not _automacao_dentro(gain_points, fim - lo, fim) else 0
    return entrada, saida


def fades_em_envelope(clip, consumed_in, consumed_out, gain_points) -> tuple[bool, bool]:
    """Fades independentes que ficam como envelope de ganho, entrada e saida.

    Sao os fades que fades_para_silencio recusa: os que nao cabem no clip ou
    os que tem automacao dentro deles.
    """
    tem_in, tem_out = _fades_independentes(clip, consumed_in, consumed_out)
    entrada, saida = fades_para_silencio(clip, consumed_in, consumed_out, gain_points)
    return (tem_in and not entrada, tem_out and not saida)


def escrever_silencio(ctx, sequence, saida: int, lacuna: int, entrada: int) -> None:
    """Escreve o silencio entre dois clips: fade de saida, lacuna e fade de entrada.

    A Transition do fade de saida vem antes do Filler e a do fade de entrada vem
    depois dele: e a ordem que o leitor reconhece como fade para o silencio e
    fade a partir do silencio.
    """
    if saida:
        ctx.builder.append_audio_dissolve(sequence, saida)
    comprimento = saida + lacuna + entrada
    if comprimento:
        sequence.components.append(ctx.f.create.Filler("sound", length=comprimento))
    if entrada:
        ctx.builder.append_audio_dissolve(sequence, entrada)


def _fades_como_transition(track, consumed_in, consumed_out, pulados) -> set:
    """Fades que viram Transition, um por trecho e direcao da timeline.

    Os clips dos canais de uma track stereo ou multicanal ocupam os mesmos
    samples: o mesmo fade conta uma vez so.
    """
    achados = set()
    for clip in track.clips:
        if id(clip) in pulados:
            continue
        entrada, saida = fades_para_silencio(clip, consumed_in, consumed_out, track.gain_points)
        if entrada:
            achados.add((clip.timeline_start_samples, clip.timeline_end_samples, "in"))
        if saida:
            achados.add((clip.timeline_start_samples, clip.timeline_end_samples, "out"))
    return achados


def reportar_fades(track, consumed_in, consumed_out, pulados, report: Report) -> None:
    """Reporta os fades que ficam como envelope e os que viram Transition.

    O fade que nao cabe no clip, ou que tem automacao dentro dele, continua
    como envelope de ganho: e um erro, porque o AAF nao o representa.
    """
    for clip in track.clips:
        if id(clip) in pulados:
            continue
        if any(fades_em_envelope(clip, consumed_in, consumed_out, track.gain_points)):
            report.error(
                "WRITE_FADE_UNSUPPORTED",
                f"independent fade of {clip.name!r} cannot be written as a fade in the AAF; "
                "use rendered audio",
                {"clip": clip.name},
            )
    fades = _fades_como_transition(track, consumed_in, consumed_out, pulados)
    if fades:
        report.info(
            "FADE_WRITTEN_AS_TRANSITION",
            f"{len(fades)} fades of the track {track.name!r} written as a Transition "
            "next to a Filler",
            {"track": track.name, "fades": len(fades)},
        )
