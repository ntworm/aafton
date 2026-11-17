"""Crossfade simetrico do Live: a forma nativa no .als e as regras comuns.

O Live nao aceita dois clips sobrepostos na mesma track do Arrangement: quando
ele salva o arquivo, o clip que sai e truncado no ponto da juncao. O modelo
nativo do Live e outro, medido no Live 12 em 15/11/2026 (oraculo de audio em
``.local/artifacts/aafton-live-aaf-qualquer-set-20261115/xfade-live/``): os
dois clips ficam encostados, o clip que entra tem CrossfadeInState=1 e
FadeInLength=0, e o crossfade inteiro toca **antes** da juncao. O clip que sai
desce dentro do proprio trecho visivel; o que entra comeca a tocar L antes do
seu inicio visivel, com o material do arquivo anterior ao seu LoopStart.

Este modulo tem a metade do escritor (``canonicalizar_crossfades``) e a
leitura dos sets de ``format_version`` <= 2 (``normalizar_crossfades``), que
estende o clip que sai so quando o XML traz todas as provas da forma antiga.
A leitura pelo modelo medido, e a escolha pela versao, moram em
``als_crossfade_ler``.

Forma escrita, a do modelo medido: a juncao e o fim do clip que sai no AAF.
O clip que sai fica com o fim e o FadeOutLength = L; o que entra ganha
CurrentStart (e o atributo Time) na juncao, LoopStart avancado L segundos,
FadeInLength 0 e CrossfadeInState 1. Assim o Live toca o crossfade em cima da
sobreposicao do AAF.

Os marcadores de loop de um clip de audio sem warp (LoopStart, LoopEnd,
HiddenLoopEnd) sao segundos do arquivo; posicao e fades sao beats do arranjo.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from decimal import Decimal

from typing import TYPE_CHECKING, Callable

from .als_paths import AUDIO_CLIP_FIELDS
from .als_xml import get_value, set_value
from .als_write_base import TIMELINE, WriteOptions, samples_to_beats, source_seconds
from .ir import FadeIR

if TYPE_CHECKING:
    from .als_read_clips import ReadContext


def clip_em_modo_especial(clip_node: ET.Element) -> bool:
    """Warp, loop ativo ou StartRelative diferente de zero mudam o alinhamento."""
    return (
        get_value(clip_node, AUDIO_CLIP_FIELDS["is_warped"]) == "true"
        or get_value(clip_node, AUDIO_CLIP_FIELDS["loop_on"]) == "true"
        or float(get_value(clip_node, AUDIO_CLIP_FIELDS["start_relative"]) or 0) != 0
    )


def material_cobre(
    duracao: int | None,
    taxa: int | None,
    taxa_da_sessao: int,
    usado: int,
) -> bool:
    """O material do arquivo cobre o trecho que o clip usa.

    Sem duracao conhecida, ou com taxa diferente da sessao, nao ha prova
    contra o trecho; com as duas coisas conhecidas o trecho e coberto quando o
    uso cabe na duracao do arquivo.
    """
    if duracao is None:
        return True
    if taxa != taxa_da_sessao:
        return True
    return usado <= duracao


# --- Escrita: a forma nativa do Live ---------------------------------------


def canonicalizar_crossfades(
    pares: list[tuple[object, ET.Element]],
    options: WriteOptions,
    is_video: bool = False,
    *,
    material: Callable[[object], tuple[int, int] | None] | None = None,
) -> None:
    """Passa para a forma nativa medida os pares que se sobrepoem.

    ``pares`` traz o clip do IR e o no do XML de cada clip da track. A lista
    auxiliar e ordenada pelo inicio antes de procurar o par vizinho: nem o IR
    nem a ordem gravada no XML mudam, so a busca usa a ordem do tempo.

    ``material`` fica na assinatura de quem chama, mas a forma medida nao
    precisa mais dele: o clip que sai usa so o trecho do AAF, e o que entra
    toca o crossfade com o material antes do LoopStart novo, que e o offset do
    AAF (``_material_sustenta_o_crossfade``).

    So o XML muda: o IR e o sidecar continuam com a geometria logica, que e a
    do AAF de origem.
    """
    if is_video:
        return
    ordenados = _pares_ordenados(pares)
    for (clip_a, node_a), (clip_b, node_b) in zip(ordenados, ordenados[1:]):
        L = _sobreposicao_de_crossfade(clip_a, clip_b)
        if L is None:
            continue
        if clip_em_modo_especial(node_a) or clip_em_modo_especial(node_b):
            continue
        if not _fades_sustentam_o_crossfade(clip_a, node_a, clip_b, node_b, L, options):
            continue
        if not _material_sustenta_o_crossfade(clip_b):
            continue
        _escrever_canonica(node_a, node_b, clip_b, L, options)


def _material_sustenta_o_crossfade(clip_b) -> bool:
    """O clip que entra tem L de material antes do LoopStart novo.

    O LoopStart novo e o offset do AAF mais L, entao o material antes dele e o
    offset do AAF mais L: basta o offset do AAF nao ser negativo.
    """
    return (clip_b.source_offset_samples or 0) >= 0


def _pares_ordenados(pares: list[tuple[object, ET.Element]]) -> list[tuple[object, ET.Element]]:
    """Os pares em ordem cronologica; a lista guardada nao muda.

    O inicio manda, e o fim e o indice do evento desempatam: dois clips que
    comecam no mesmo sample ficam em ordem determinista.
    """
    return sorted(
        pares,
        key=lambda par: (
            par[0].timeline_start_samples,
            par[0].timeline_end_samples,
            par[0].event_index,
        ),
    )


def _sobreposicao_de_crossfade(clip_a, clip_b) -> int | None:
    """Samples de sobreposicao que valem como crossfade, ou None se nao valem.

    O par tem de ser de verdade: o clip que entra comeca depois do que sai e a
    sobreposicao e positiva. Ela tem de ser menor que o clip que entra: na
    forma nativa ele comeca na juncao, e com a duracao no AAF <= L ficaria com
    comprimento zero, e o Live apaga o clip.
    """
    if clip_b.timeline_start_samples <= clip_a.timeline_start_samples:
        return None
    sobreposicao = clip_a.timeline_end_samples - clip_b.timeline_start_samples
    if sobreposicao <= 0:
        return None
    if sobreposicao >= clip_b.timeline_end_samples - clip_b.timeline_start_samples:
        return None
    return sobreposicao


def _fade_em_samples(node: ET.Element, campo: str, options: WriteOptions, minimo: int):
    """Samples do fade que esta escrito no XML, ou None quando o valor nao serve.

    O valor lido e o que o Live vai ver: quando o escritor escalou o fade para
    caber no clip, vale o valor escalado, e nao o do IR. ``minimo`` e o menor
    valor aceito: 1 para um fade que precisa existir, 0 para um fade que pode
    ser zero.
    """
    texto = get_value(node, AUDIO_CLIP_FIELDS[campo])
    if texto is None:
        return None if minimo > 0 else 0
    try:
        beats = float(texto)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(beats) or beats < 0:
        return None
    samples = round(beats * 60.0 / options.bpm * options.sample_rate)
    if samples < minimo:
        return None
    return samples


def _fades_sustentam_o_crossfade(clip_a, node_a, clip_b, node_b, L, options) -> bool:
    """Os fades do IR e os escritos no XML sustentam o mesmo comprimento.

    O fade out do que sai e o fade in do que entra valem L, e o fade in do que
    sai ainda cabe junto do fade out no clip que sai, que fica com o fim do AAF.

    O IR tambem tem de valer L. Um par que so ficou simetrico depois do
    clamping do escritor -- o fade in e o fade out do clip que entra
    disputando o mesmo espaco, e o escritor repartindo os dois -- nao e o
    crossfade de L: canonizar ali trocaria um fade de 2L por um de L.
    """
    fade_out_a = _fade_em_samples(node_a, "fade_out_length", options, 1)
    fade_in_b = _fade_em_samples(node_b, "fade_in_length", options, 1)
    fade_in_a = _fade_em_samples(node_a, "fade_in_length", options, 0)
    if fade_out_a != L or fade_in_b != L or fade_in_a is None:
        return False
    if not _fade_do_ir_equivale(clip_a, "out", L):
        return False
    if not _fade_do_ir_equivale(clip_b, "in", L):
        return False
    return fade_in_a + L <= clip_a.duration_samples


def _fade_do_ir_equivale(clip, kind: str, L: int) -> bool:
    """O fade do IR, quando existe, tem de valer exatamente L."""
    fade = clip.fade_out if kind == "out" else clip.fade_in
    return fade is not None and fade.duration_samples == L


def _escrever_canonica(node_a: ET.Element, node_b: ET.Element, clip_b, L: int, options) -> None:
    """Escreve a forma medida: B comeca no fim de A, L segundos adiante na fonte."""
    fim_a = node_a.find(AUDIO_CLIP_FIELDS["current_end"])
    inicio_b = node_b.find(AUDIO_CLIP_FIELDS["current_start"])
    if inicio_b is None or fim_a is None:
        return
    juncao = fim_a.get("Value")
    if juncao is None:
        return
    # O texto do fim de A vai inteiro para o inicio de B, e o atributo Time
    # acompanha o CurrentStart: os dois clips ficam encostados no mesmo ponto
    # da grade, sem arredondar nada pelo caminho.
    inicio_b.set("Value", juncao)
    node_b.set("Time", juncao)
    loop_start_b = source_seconds((clip_b.source_offset_samples or 0) + L, options.sample_rate)
    set_value(node_b, AUDIO_CLIP_FIELDS["loop_start"], loop_start_b)
    set_value(node_a, AUDIO_CLIP_FIELDS["fade_out_length"], _beats(L, options))
    set_value(node_b, AUDIO_CLIP_FIELDS["crossfade_in_state"], 1)
    set_value(node_b, AUDIO_CLIP_FIELDS["fade_in_length"], 0.0)
    _zerar_curvas_de_entrada(node_b)


def _beats(samples: int, options: WriteOptions) -> float:
    """Samples na unidade de tempo do arranjo: beats."""
    return samples_to_beats(samples, options.sample_rate, options.bpm, TIMELINE)


def _zerar_curvas_de_entrada(node: ET.Element) -> None:
    """O fade in do clip que entra vale zero; as curvas dele tambem."""
    for chave in ("fade_in_curve_skew", "fade_in_curve_slope"):
        try:
            set_value(node, AUDIO_CLIP_FIELDS[chave], 0.0)
        except KeyError:
            pass


# --- Leitura dos sets de format_version <= 2 --------------------------------


def normalizar_crossfades(ctx: "ReadContext", pares: list[tuple[object, ET.Element]]) -> None:
    """Reconstroi a geometria logica dos crossfades que o Live representa nativamente.

    ``pares`` traz o clip ja lido e o no do XML de cada clip da track. A lista
    auxiliar e ordenada pelo inicio antes de procurar o par vizinho: nem o IR
    nem a ordem gravada no XML mudam.

    So quando o conjunto inteiro de provas esta no XML a geometria e
    estendida; sem ele, nada muda.
    """
    ordenados = _pares_ordenados(pares)
    for (clip_a, node_a), (clip_b, node_b) in zip(ordenados, ordenados[1:]):
        fim_visual = clip_a.timeline_end_samples
        L = _comprimento_provado(ctx, clip_a, node_a, clip_b, node_b)
        if L is None:
            continue
        clip_a.timeline_end_samples = fim_visual + L
        clip_b.fade_in = FadeIR(kind="in", duration_samples=L)


def _comprimento_provado(ctx, clip_a, node_a, clip_b, node_b) -> int | None:
    """O comprimento do crossfade quando o XML traz todas as provas; None caso contrario."""
    if not _provas_do_estado(clip_a, node_a, clip_b, node_b):
        return None
    L = clip_a.fade_out.duration_samples
    if not _provas_da_geometria(ctx, clip_a, node_a, clip_b, L):
        return None
    return L


def _provas_do_estado(clip_a, node_a, clip_b, node_b) -> bool:
    """O estado de crossfade, o fade in zerado e o fade out vivo do vizinho."""
    if get_value(node_b, AUDIO_CLIP_FIELDS["crossfade_in_state"]) != "1":
        return False
    if not _fade_in_zerado(node_b):
        return False
    if not _fades_ativos(node_b):
        return False
    if clip_a.fade_out is None or clip_a.fade_out.duration_samples <= 0:
        return False
    if clip_em_modo_especial(node_a) or clip_em_modo_especial(node_b):
        return False
    return True


def _fade_in_zerado(node: ET.Element) -> bool:
    """O fade in do clip que entra tem de estar presente e valer zero.

    "Zero" e o numero do XML, antes de qualquer arredondamento: um valor
    positivo que arredonda para zero nao e um fade in zerado, e um valor
    ausente ou invalido tambem nao e.

    A comparacao e feita com ``Decimal``, antes do ``float``: um valor positivo
    tao pequeno que vira zero no ``float`` (1e-1000) continua positivo em
    decimal, e um valor lido como zero ali seria um fade in que nao existe.
    """
    texto = get_value(node, AUDIO_CLIP_FIELDS["fade_in_length"])
    if texto is None:
        return False
    try:
        exato = Decimal(texto)
    except (ArithmeticError, TypeError, ValueError):
        return False
    if not exato.is_finite():
        return False
    return exato == 0


def _fades_ativos(node: ET.Element) -> bool:
    """Os fades do clip que entra: sem elemento Fade valem, Fade=false desliga.

    A regra e a do leitor de clips, importada aqui para nao duplicar o
    contrato. O import fica dentro da funcao porque os dois modulos se
    referenciam.
    """
    from .als_read_clips import _fades_enabled

    return _fades_enabled(node)


def _provas_da_geometria(ctx, clip_a, node_a, clip_b, L) -> bool:
    """A juncao encostada, o material da fonte e o limite do clip que entra.

    A duracao visual do clip que sai tem de ser positiva: um clip que so aparece
    quando o leitor o estende nao estava na cena, e estender um clip de duracao
    visual zero inventaria um clip que nao existe.
    """
    if clip_a.timeline_end_samples != clip_b.timeline_start_samples:
        return False
    if clip_a.timeline_end_samples <= clip_a.timeline_start_samples:
        return False
    if L > clip_b.timeline_end_samples - clip_b.timeline_start_samples:
        return False
    if not _excesso_de_fonte(ctx, node_a, clip_a, L):
        return False
    return _material_cobre(ctx, clip_a, L)


def _excesso_de_fonte(ctx, node, clip, L) -> bool:
    """O material da fonte passa da duracao visual pelo comprimento do crossfade.

    E o excesso que distingue o crossfade da forma antiga de um fade de render
    legado: o clip que sai guarda L samples de material a mais do que a duracao
    visivel. O OutMarker e beats e o LoopStart e segundos: o material e
    OutMarker x 60 / tempo - LoopStart, em segundos, depois em amostras da
    sessao, com tolerancia de meia amostra.

    Um OutMarker invalido, infinito ou tao grande que estoura a conversao nao
    prova nada: a leitura recusa o par, sem abortar e sem inventar geometria.
    """
    out_marker = get_value(node, AUDIO_CLIP_FIELDS["out_marker"])
    loop_start = get_value(node, AUDIO_CLIP_FIELDS["loop_start"])
    if out_marker is None or loop_start is None:
        return False
    try:
        segundos = float(out_marker) * 60.0 / ctx.anchor.bpm - float(loop_start)
    except (TypeError, ValueError):
        return False
    material = segundos * ctx.anchor.sample_rate
    if not math.isfinite(material):
        return False
    return abs(material - (clip.duration_samples + L)) <= 0.5


def _material_cobre(ctx, clip, L) -> bool:
    """O material real do clip que sai cobre o trecho que o crossfade devolve."""
    asset = ctx.session.media_by_id(clip.media_id) if clip.media_id else None
    if asset is None or asset.duration_samples is None:
        return True
    usado = (clip.source_offset_samples or 0) + clip.duration_samples + L
    return material_cobre(
        asset.duration_samples,
        asset.sample_rate,
        ctx.anchor.sample_rate,
        usado,
    )
