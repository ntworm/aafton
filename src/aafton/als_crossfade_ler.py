"""Leitura do crossfade do Live pelo modelo medido, e a escolha pela versao.

Sem sidecar ou com ``format_version`` >= 3, vale o modelo medido no Live 12 em
15/11/2026 (ver ``als_crossfade``): num par encostado, com o clip que entra em
CrossfadeInState=1 e FadeInLength 0, o crossfade toca L antes da juncao, com
L = min(FadeOutLength do que sai, material do que entra antes do LoopStart).
O clip que sai fica como esta com fade out L; o que entra volta a juncao - L,
com offset LoopStart - L e fade in L. E a sobreposicao de L que o escritor de
AAF transforma em transicao, sem a prova do OutMarker.

Com ``format_version`` <= 2 (sets gravados pelo aafton antes da forma medida)
fica a leitura antiga, ``als_crossfade.normalizar_crossfades``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from typing import TYPE_CHECKING

from . import als_crossfade
from .als_crossfade import _pares_ordenados, _provas_do_estado
from .als_paths import AUDIO_CLIP_FIELDS
from .als_xml import get_value
from .ir import FadeIR

if TYPE_CHECKING:
    from .als_read_clips import ReadContext

# Primeira versao do sidecar gravada na forma medida do crossfade.
VERSAO_DO_MODELO_MEDIDO = 3


def crossfade_do_live(juncao: int, fade_out_a: int, loop_start_b: int) -> tuple[int, int, int]:
    """O crossfade que o Live toca num par encostado, em samples da sessao.

    ``juncao`` e o fim visivel do clip que sai (o inicio visivel do que entra),
    ``fade_out_a`` o FadeOutLength do que sai e ``loop_start_b`` o LoopStart do
    que entra. O Live corta o crossfade no material que o clip que entra tem
    antes do LoopStart (medido: 1,25 beat gravado a 96 BPM, 0,625 s tocado).

    Devolve (L, inicio do clip que entra, offset do clip que entra).
    """
    L = max(0, min(fade_out_a, loop_start_b))
    return L, juncao - L, loop_start_b - L


def normalizar_crossfades(ctx: "ReadContext", pares: list[tuple[object, ET.Element]]) -> None:
    """Reconstroi a geometria logica dos crossfades, pela versao do set."""
    if ctx.anchor.format_version < VERSAO_DO_MODELO_MEDIDO:
        als_crossfade.normalizar_crossfades(ctx, pares)
        return
    ordenados = _pares_ordenados(pares)
    montados: set[int] = set()
    for (clip_a, node_a), (clip_b, node_b) in zip(ordenados, ordenados[1:]):
        if _montar_pelo_modelo(ctx, clip_a, node_a, clip_b, node_b):
            montados.add(id(node_b))
    if ctx.sem_sidecar:
        _acusar_nao_montados(ctx, ordenados, montados)


def _track_do_clip(ctx, clip) -> tuple[str | None, str | None]:
    """Identifica track_id e nome da faixa do clip pela leitura atual."""
    track_id = getattr(clip, "track_id", None)
    if not track_id:
        return None, None
    render = getattr(ctx, "render", None)
    if render is not None:
        anotada = render.tracks.get(track_id)
        nome = render.track_nomes.get(track_id, anotada.nome if anotada is not None else None)
        if nome is not None:
            return track_id, nome
    tracks = getattr(getattr(ctx, "session", None), "tracks", ())
    nome = next((t.name for t in tracks if t.id == track_id), None)
    return track_id, nome


def _acusar_nao_montados(ctx, ordenados, montados: set[int]) -> None:
    """Todo clip com CrossfadeInState=1 cujo par nao foi montado e um erro.

    A regra e por clip, nao pela posicao: o primeiro clip da track tambem
    conta. O clip que entrava num clip deixado de fora ja chega aqui com o
    estado zerado (``als_render.soltar_vizinhos``).
    """
    for clip, node in ordenados:
        if id(node) in montados:
            continue
        if get_value(node, AUDIO_CLIP_FIELDS["crossfade_in_state"]) != "1":
            continue
        track_id, track_nome = _track_do_clip(ctx, clip)
        contexto = {"clip_id": clip.id, "clip": clip.name}
        if track_nome is not None:
            contexto["track"] = track_nome
        if track_id is not None:
            contexto["track_id"] = track_id
        ctx.report.error(
            "ALS_CROSSFADE_NOT_PROVEN",
            f"Live plays a crossfade into the clip {clip.name!r} that the AAF cannot carry "
            "exactly; consolidate the pair in Live (Ctrl+J), save the set and read it again",
            contexto,
        )


def _montar_pelo_modelo(ctx, clip_a, node_a, clip_b, node_b) -> bool:
    """Monta o par pelo modelo medido quando o estado e a juncao provam o crossfade.

    Devolve se o par foi resolvido: montado, ou sem material antes do inicio
    (o Live nao toca crossfade ali, e a nota diz isso).
    """
    if not _provas_do_estado(clip_a, node_a, clip_b, node_b):
        return False
    if clip_a.timeline_end_samples != clip_b.timeline_start_samples:
        return False
    if clip_a.timeline_end_samples <= clip_a.timeline_start_samples:
        return False
    L, inicio_b, offset_b = crossfade_do_live(
        clip_b.timeline_start_samples,
        clip_a.fade_out.duration_samples,
        clip_b.source_offset_samples or 0,
    )
    if L == 0:
        ctx.report.info(
            "ALS_CROSSFADE_WITHOUT_MATERIAL",
            f"the clip {clip_b.name!r} has no material before its start, "
            "so Live plays no crossfade there",
            {"clip_id": clip_b.id},
        )
        return True
    clip_a.fade_out = FadeIR(kind="out", duration_samples=L)
    clip_b.timeline_start_samples = inicio_b
    clip_b.source_offset_samples = offset_b
    clip_b.fade_in = FadeIR(kind="in", duration_samples=L)
    return True


def _chave_clip(clip) -> tuple[int, int, int]:
    """Chave deterministica para ordenar clips dentro do mesmo canal."""
    return (
        clip.timeline_start_samples,
        clip.timeline_end_samples,
        getattr(clip, "event_index", 0),
    )


def _sobrepor_canal(clips: list) -> None:
    """Passa os pares encostados com crossfade para sobreposicao num canal."""
    for clip_a, clip_b in zip(clips, clips[1:]):
        if not clip_b.crossfade_in_state or clip_a.fade_out is None:
            continue
        if clip_b.fade_in is not None and clip_b.fade_in.duration_samples > 0:
            continue
        if clip_a.timeline_end_samples != clip_b.timeline_start_samples:
            continue
        comp, inicio_b, offset_b = crossfade_do_live(
            clip_b.timeline_start_samples,
            clip_a.fade_out.duration_samples,
            clip_b.source_offset_samples or 0,
        )
        if comp == 0:
            continue
        clip_a.fade_out = FadeIR(kind="out", duration_samples=comp)
        clip_b.timeline_start_samples = inicio_b
        clip_b.source_offset_samples = offset_b
        clip_b.fade_in = FadeIR(kind="in", duration_samples=comp)


def crossfades_em_sobreposicao(session) -> None:
    """Passa os pares encostados com crossfade para a forma de sobreposicao.

    O leitor de AAF da dois jeitos ao mesmo crossfade: a transicao vira uma
    sobreposicao, e o fade renderizado do Pro Tools (``--fades absorb``) vira
    um par encostado com o clip que entra em CrossfadeInState. Pelo modelo
    medido os dois tocam igual no Live; o verify compara os dois lados nesta
    forma unica, com o mesmo L = min(fade out do que sai, offset do que entra).
    Agrupa por canal antes de parear para nao misturar canais de track estereo.
    """
    for track in session.tracks:
        por_canal: dict[int, list] = {}
        for clip in track.clips:
            por_canal.setdefault(getattr(clip, "channel_index", 0), []).append(clip)
        for clips_canal in por_canal.values():
            clips_canal.sort(key=_chave_clip)
            _sobrepor_canal(clips_canal)
