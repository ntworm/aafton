"""Leitura das faixas de audio de um AAF.

O modulo concentra o que o loop de `read_aaf` compartilha entre as faixas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .aaf_read_components import (
    _contar_source_clips,
    _declared_source_samples,
    _descer_ate_sequence,
    _edit_units_to_samples,
    _fonte_cobre_o_fade,
    _is_audio_channel_combiner,
    _is_fade_source_clip,
    _source_mob_ref,
    _source_offset_samples,
    _type_name,
    _unwrap,
    _valor_de_propriedade,
)
from .aaf_read_canal_fonte import canal_da_fonte
from .aaf_read_fades import absorver_fades
from .aaf_read_grupo_sequencia import (
    componente_grupo_sequencia,
    conferir_ganho_de_grupo_com_envelope,
)
from .aaf_read_session import _read_static_track_pan
from .aaf_read_timewarp import _descricao_do_time_warp, _is_time_warp
from .aaf_read_transicao import componente_transition
from .ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from .report import Report
from .timecode import quantize_samples


@dataclass
class AssetIndex:
    """Indice de midia do AAF, com uma entrada por arquivo."""

    session: SessionIR
    sample_rate: int
    assets: dict[str, MediaAsset] = field(default_factory=dict)

    def asset_for(
        self, nome_arquivo: str | None, source_mob_id: str | None = None
    ) -> MediaAsset | None:
        """Devolve o asset do arquivo, criando-o na primeira vez."""
        if not nome_arquivo:
            return None
        chave = f"{source_mob_id}::{nome_arquivo}" if source_mob_id else nome_arquivo
        existente = self.assets.get(chave)
        if existente is not None:
            return existente
        novo = MediaAsset(
            id=f"m{len(self.assets)}",
            filename=Path(nome_arquivo).name,
            declared_location=nome_arquivo,
            sample_rate=self.sample_rate,
            embedded_mob_id=source_mob_id,
        )
        self.assets[chave] = novo
        self.session.media.append(novo)
        return novo


@dataclass
class TrackContext:
    """O que o loop de faixas compartilha entre as tracks do AAF."""

    report: Report
    session: SessionIR
    sample_rate: int
    inicio_absoluto: int
    fades: str
    assets: AssetIndex
    constant_gain: Callable[..., float]
    has_varying_gain: Callable[..., bool]
    extract_gain_curve: Callable[[list[dict], int], list[tuple[int, float]]]
    total_absorbed_fades: int = 0
    fades_mantidos_por_falta_de_fonte: int = 0
    static_pan_count: int = 0
    next_audio_channel_index: int = 0


@dataclass
class EditUnitConverter:
    """Converte as unidades de edicao de um slot em samples."""

    taxa: int
    sample_rate: int
    track: TrackIR
    report: Report

    def em_samples(self, unidades: int) -> int:
        """Converte unidades de edicao do slot em samples."""
        exato = _edit_units_to_samples(unidades, self.taxa, self.sample_rate)
        valor, res = quantize_samples(exato)
        if res:
            self.report.info(
                "EDIT_UNIT_ROUNDING",
                f"the conversion of {unidades} edit units left a residual of "
                f"{float(res):+.3f} sample",
                {"track": self.track.name, "edit_rate": str(self.taxa)},
            )
        return valor


@dataclass
class TrackState:
    """Estado mutavel da construcao de uma faixa."""

    ctx: TrackContext
    track: TrackIR
    conv: EditUnitConverter
    cursor: int = 0
    evento: int = 0
    transicao_pendente: int = 0
    raw_items: list[dict] = field(default_factory=list)
    muted: bool = False


def read_track(
    ctx: TrackContext, indice_track: int, orig_idx: int, slot, slot_names: dict[int, str]
) -> TrackIR | None:
    """Le uma faixa de audio do AAF e devolve a TrackIR montada.

    Devolve None quando o slot nao tem Sequence nem SourceClip alcancavel.
    """
    segmento = getattr(slot, "segment", None)
    track_name = _nome_da_track(ctx, indice_track, orig_idx, slot, slot_names)
    track = TrackIR(
        id=f"t{indice_track}",
        source_index=indice_track,
        original_source_indices=[indice_track],
        name=track_name,
        kind="audio",
        channel_count=1,
    )
    static_pan = _read_static_track_pan(segmento, ctx.report, track.name)
    componentes = _componentes_do_slot(ctx, segmento, track)
    if componentes is None:
        return None
    if static_pan is not None:
        track.pan = static_pan
        ctx.static_pan_count += 1
    taxa_do_slot = getattr(slot, "edit_rate", ctx.sample_rate) or ctx.sample_rate
    conv = EditUnitConverter(taxa_do_slot, ctx.sample_rate, track, ctx.report)
    estado = TrackState(ctx=ctx, track=track, conv=conv)
    for componente in componentes:
        _processar_componente(estado, componente)
    if ctx.fades == "absorb":
        absorver_fades(ctx, track, estado.raw_items)
    _aplicar_curva_de_ganho(ctx, track, estado.raw_items)
    conferir_ganho_de_grupo_com_envelope(ctx, track, estado.raw_items)
    for it in estado.raw_items:
        for c, _ in it.get("clip_assets", []):
            track.clips.append(c)
    canais = max(1, track.channel_count)
    primeiro = ctx.next_audio_channel_index
    track.original_source_indices = list(range(primeiro, primeiro + canais))
    ctx.next_audio_channel_index += canais
    ctx.session.tracks.append(track)
    return track


def _nome_da_track(ctx, indice_track, orig_idx, slot, slot_names) -> str:
    """Nome da trilha, com provisorio quando o slot do AAF nao tem nome."""
    if slot_names.get(orig_idx):
        return slot_names[orig_idx]
    if slot.name:
        return slot.name
    track_name = f"Track {indice_track + 1}"
    ctx.report.warning(
        "TRACK_NAME_MISSING",
        f"The slot at position {indice_track + 1} came without a name in the AAF and received the provisional name {track_name!r}.",
        {"track": track_name},
    )
    return track_name


def _componentes_do_slot(ctx, segmento, track) -> list | None:
    """Componentes do slot, ou None quando a trilha deve ser ignorada."""
    sequencia = _descer_ate_sequence(segmento)
    if sequencia is not None:
        return list(sequencia.components)
    internos, _ = _unwrap(segmento)
    if any(_type_name(c) == "SourceClip" for c in internos):
        return [segmento]
    ctx.report.warning(
        "AAF_NO_SEQUENCE",
        f"the slot {track.name!r} has no reachable Sequence or SourceClip; track ignored",
        {"track": track.name},
    )
    return None


def _processar_componente(estado, componente) -> None:
    """Le um componente da Sequence e registra o item bruto correspondente."""
    tipo = _type_name(componente)
    comprimento = int(getattr(componente, "length", 0) or 0)
    dur_samples = estado.conv.em_samples(comprimento)
    if tipo == "Transition":
        _componente_transition(estado, dur_samples, comprimento)
        return
    if tipo == "Filler":
        _componente_filler(estado, dur_samples, comprimento)
        return
    if tipo == "Selector":
        _componente_selector(estado, componente, comprimento, dur_samples)
        return
    if tipo == "Sequence":
        _componente_sequence(estado, componente, comprimento)
        return
    _componente_source(estado, componente, tipo, comprimento, dur_samples)


def _componente_selector(estado, componente, comprimento, dur_samples) -> None:
    """Selector: escolhe entre o material selecionado e os alternativos.

    O Avid grava o clip mudo como um Selector cujo Selected e um Filler e
    cujo material esta nos Alternates. O clip continua na timeline, so que
    sem audio, e por isso ele entra como clip mudo em vez de lacuna.
    """
    selected = _valor_de_propriedade(componente, "Selected")
    alternates = list(_valor_de_propriedade(componente, "Alternates") or [])
    if _type_name(selected) != "Filler":
        alvo = selected if selected is not None else componente
        _componente_source(estado, alvo, _type_name(alvo), comprimento, dur_samples)
        return
    for alternate in alternates:
        internos, _ = _unwrap(alternate)
        if not any(_type_name(interno) == "SourceClip" for interno in internos):
            continue
        estado.muted = True
        try:
            _componente_source(estado, alternate, "Selector", comprimento, dur_samples)
        finally:
            estado.muted = False
        return
    _componente_filler(estado, dur_samples, comprimento)


def _componente_sequence(estado, componente, comprimento) -> None:
    """Sequence aninhada: os componentes dela entram no mesmo estado da de fora.

    Uma Sequence dentro de outra Sequence e o jeito que o OTIO agrupa o
    audio. Os componentes sao lidos como se estivessem na Sequence de fora:
    o cursor, as transicoes e os eventos continuam os mesmos, e o audio de
    dentro do agrupamento nao se perde.
    """
    cursor_inicial = estado.cursor
    for interno in list(getattr(componente, "components", None) or []):
        _processar_componente(estado, interno)
    avancado = estado.cursor - cursor_inicial
    # Sem comprimento declarado nao ha com o que comparar.
    if comprimento > 0 and avancado != comprimento:
        estado.ctx.report.error(
            "AAF_NESTED_SEQUENCE_LENGTH_MISMATCH",
            f"the nested Sequence of the track {estado.track.name!r} declares "
            f"{comprimento} edit units, but the cursor advanced {avancado}",
            {
                "track": estado.track.name,
                "declared_length": comprimento,
                "advanced": avancado,
            },
        )


def _componente_transition(estado, dur_samples, comprimento) -> None:
    """Transition: a sobreposicao entre dois clips, que recua o cursor."""
    componente_transition(estado, dur_samples, comprimento)


def _componente_filler(estado, dur_samples, comprimento) -> None:
    """Filler: um trecho sem audio, que so avanca o cursor."""
    # Um fade-in pendente de uma Transition antes do Filler era um fade para o
    # silencio; ele nao pode passar por cima do Filler para o clip seguinte.
    estado.transicao_pendente = 0
    estado.raw_items.append(
        {
            "kind": "FILLER",
            "length_samples": dur_samples,
            "cursor_samples": estado.conv.em_samples(estado.cursor),
        }
    )
    estado.cursor += comprimento


def _componente_source(estado, componente, tipo, comprimento, dur_samples) -> None:
    """SourceClip (ou agrupamento deles): vira clip, fade ou lacuna."""
    ctx = estado.ctx
    track = estado.track
    if _is_time_warp(componente):
        _componente_time_warp(estado, componente, comprimento, dur_samples)
        return
    if tipo == "Sequence" or _type_name(componente) == "Sequence":
        _componente_sequence(estado, componente, comprimento)
        return
    if componente_grupo_sequencia(estado, componente, comprimento, _componente_sequence):
        return
    internos, parametros = _unwrap(componente, ctx.report)
    if _is_audio_channel_combiner(componente) and len(internos) > 1:
        track.channel_count = max(track.channel_count, len(internos))
    clips, is_fade = _clips_do_componente(estado, internos, parametros, comprimento, dur_samples)
    if is_fade:
        _append_item(estado, "FADE", componente, parametros, clips, dur_samples)
    elif clips:
        _append_item(estado, "CLIP", componente, parametros, clips, dur_samples)
    else:
        _append_other_item(estado, componente, tipo, comprimento, dur_samples)
    estado.transicao_pendente = 0
    estado.cursor += comprimento


def _componente_time_warp(estado, componente, comprimento, dur_samples) -> None:
    """Efeito de velocidade: o leitor nao sabe reescrever o tempo do audio.

    O Media Composer marca essas operacoes com IsTimeWarp na OperationDef.
    Deixar o efeito passar seria entregar um audio com o tempo trocado,
    entao o leitor falha em vez de fingir que leu o trecho.
    """
    ctx = estado.ctx
    track = estado.track
    mensagem, contexto = _descricao_do_time_warp(componente, track.name)
    ctx.report.error("AAF_TIME_WARP_UNSUPPORTED", mensagem, contexto)
    _registrar_outro_item(estado, comprimento, dur_samples)
    estado.transicao_pendente = 0
    estado.cursor += comprimento


def _clips_do_componente(
    estado, internos, parametros, comprimento, dur_samples
) -> tuple[list, bool]:
    """Clips de um componente e a marca de que ele e um fade."""
    ctx = estado.ctx
    track = estado.track
    clips: list[tuple[ClipIR, MediaAsset | None]] = []
    for ch_index, interno in enumerate(internos):
        if _type_name(interno) != "SourceClip":
            continue
        if ctx.fades == "absorb" and _is_fade_source_clip(interno):
            if _fonte_cobre_o_fade(estado.raw_items, dur_samples):
                return clips, True
            _reportar_fade_mantido(ctx, track, dur_samples)
        clip, asset = _criar_clip(estado, interno, ch_index, parametros, comprimento)
        clips.append((clip, asset))
    return clips, False


def _reportar_fade_mantido(ctx, track, dur_samples) -> None:
    """Avisa que um fade ficou como clip proprio, por falta de material."""
    ctx.report.warning(
        "FADE_KEPT_SOURCE_TOO_SHORT",
        f"in the track {track.name!r}, a fade of {dur_samples} samples does not fit "
        f"in the material left in the file of the previous clip; it was kept "
        f"as its own clip instead of absorbed, so the audio does not become silence",
        {"track": track.name, "fade_samples": dur_samples},
    )
    ctx.fades_mantidos_por_falta_de_fonte += 1


def _criar_clip(
    estado, interno, ch_index, parametros, comprimento
) -> tuple[ClipIR, MediaAsset | None]:
    """Cria o ClipIR de um SourceClip e devolve o asset do arquivo."""
    ctx = estado.ctx
    track = estado.track
    conv = estado.conv
    nome_arquivo, source_mob_id = _source_mob_ref(interno)
    asset = _asset_do_clip(ctx, interno, nome_arquivo, source_mob_id)
    start_samples = ctx.inicio_absoluto + conv.em_samples(estado.cursor)
    end_samples = ctx.inicio_absoluto + conv.em_samples(estado.cursor + comprimento)
    nome_clip = getattr(interno.mob, "name", None) if getattr(interno, "mob", None) else None
    if not nome_clip:
        nome_clip = Path(nome_arquivo).name if nome_arquivo else f"clip {estado.evento + 1}"
    clip = ClipIR(
        id=f"{track.id}c{estado.evento}_ch{ch_index}",
        track_id=track.id,
        channel_index=ch_index,
        event_index=estado.evento,
        name=nome_clip,
        media_id=asset.id if asset else None,
        timeline_start_samples=start_samples,
        timeline_end_samples=end_samples,
        source_offset_samples=_source_offset_samples(
            interno, ctx.sample_rate, conv.taxa, ctx.report
        ),
        source_channel=canal_da_fonte(interno),
    )
    clip.gain_linear = ctx.constant_gain(parametros, ctx.report, track.name, clip.name)
    if estado.muted:
        clip.muted = True
        ctx.report.info(
            "AAF_CLIP_MUTED",
            f"the clip {clip.name!r} of the track {track.name!r} is muted in the AAF and becomes a clip without audio",
            {"track": track.name, "clip": clip.name},
        )
    if estado.transicao_pendente:
        clip.fade_in = FadeIR(kind="in", duration_samples=estado.transicao_pendente)
    return clip, asset


def _asset_do_clip(ctx, interno, nome_arquivo, source_mob_id) -> MediaAsset | None:
    """Asset do clip, com o mob_id e a duracao declarada pelo AAF."""
    asset = ctx.assets.asset_for(nome_arquivo, source_mob_id)
    if asset is not None and source_mob_id is not None:
        # Guarda o mob_id do SourceMob para que a extracao
        # possa localiza-lo por referencia em vez de por nome.
        if asset.embedded_mob_id is None:
            asset.embedded_mob_id = source_mob_id
    if asset is not None and asset.duration_samples is None:
        # O comprimento declarado pelo AAF, disponivel aqui e
        # nao so depois da resolucao em disco. E dele que a
        # decisao de absorver o fade depende.
        asset.duration_samples = _declared_source_samples(interno, ctx.sample_rate)
    return asset


def _append_item(estado, tipo_item, componente, parametros, clips, dur_samples) -> None:
    """Guarda o item bruto de um clip ou de um fade na timeline."""
    if tipo_item == "FADE":
        extra = {"component": componente, "parameters": parametros, "clip_assets": clips}
    else:
        extra = {"clip_assets": clips, "parameters": parametros}
    item = {
        "kind": tipo_item,
        "length_samples": dur_samples,
        "cursor_samples": estado.conv.em_samples(estado.cursor),
    }
    item.update(extra)
    estado.raw_items.append(item)
    estado.evento += 1


def _append_other_item(estado, componente, tipo, comprimento, dur_samples) -> None:
    """Guarda o item bruto de um componente nao reconhecido.

    Quando o componente esconde SourceClips, deixar passar em silencio
    perderia audio do arquivo: isso e erro, e nao aviso.
    """
    ctx = estado.ctx
    track = estado.track
    escondidos = _contar_source_clips(componente)
    if escondidos:
        ctx.report.error(
            "AAF_UNKNOWN_COMPONENT",
            f"component {tipo} not recognized in the track {track.name!r} hides "
            f"{escondidos} SourceClip(s) that were left out of the conversion",
            {"track": track.name, "component": tipo, "source_clips": escondidos},
        )
    else:
        ctx.report.warning(
            "AAF_UNKNOWN_COMPONENT",
            f"component {tipo} not recognized in the track {track.name!r}; "
            f"treated as a gap of {comprimento} samples",
            {"track": track.name, "component": tipo},
        )
    _registrar_outro_item(estado, comprimento, dur_samples)


def _registrar_outro_item(estado, comprimento, dur_samples) -> None:
    """Item bruto de um componente que entrou na timeline como lacuna."""
    estado.raw_items.append(
        {
            "kind": "OTHER",
            "length_samples": dur_samples,
            "cursor_samples": estado.conv.em_samples(estado.cursor),
        }
    )


def _aplicar_curva_de_ganho(ctx, track, raw_items) -> None:
    """Converte a automacao de ganho dos segmentos em envelope da track."""
    # Detectar se ha automacao de ganho na track (qualquer segmento varying)
    track_has_gain_automation = any(
        ctx.has_varying_gain(it.get("parameters", []))
        for it in raw_items
        if it["kind"] in ("CLIP", "FADE") and it.get("parameters")
    )
    if not track_has_gain_automation:
        return
    # Extrair curva de ganho continua de todos os segmentos
    gain_curve = ctx.extract_gain_curve(raw_items, ctx.inicio_absoluto)
    track.gain_points = gain_curve
    # Zerar ganho de todos os clips (o envelope carrega o ganho)
    for it in raw_items:
        for c, _ in it.get("clip_assets", []):
            c.gain_linear = 1.0
