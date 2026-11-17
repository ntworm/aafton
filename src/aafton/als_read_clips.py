"""Leitura dos AudioClip de uma track do Live e helpers de caminho de midia.

O modulo divide com a leitura da sessao o que as duas precisam: o contexto de
leitura (o ``ReadContext``) e o nome da track no Live.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .als_crossfade import clip_em_modo_especial
from .als_crossfade_ler import normalizar_crossfades
from .als_loop import (
    cortar_caudas_de_loop,
    expandir_clip_em_passadas,
    loop_expansivel,
    passadas,
)
from .als_leitura import offset_do_warp
from .als_paths import AUDIO_CLIP_FIELDS
from .als_render import (
    RenderLog,
    e_video,
    marcar_video,
    precisa_render,
    soltar_vizinhos,
)

from .als_xml import get_value
from .ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from .report import Report
from .sidecar import Anchor, resolve_video_path


def track_name(node: ET.Element) -> str:
    """Nome da track no Live: EffectiveName tem precedencia sobre UserName."""
    for caminho in ("Name/EffectiveName", "Name/UserName"):
        alvo = node.find(caminho)
        if alvo is not None and alvo.get("Value"):
            return alvo.get("Value", "")
    return ""


def _beats_to_samples(beats: float, sample_rate: int, bpm: float) -> int:
    return round(beats * 60.0 / bpm * sample_rate)


def _same_media_path(left: str, right: str) -> bool:
    try:
        return Path(left).resolve(strict=False) == Path(right).resolve(strict=False)
    except (OSError, ValueError):
        return False


def _media_path(clip: ET.Element, set_path: Path) -> str:
    """Resolve referências de Save As/Collect All a partir do set, nunca do cwd."""
    absolute = get_value(clip, AUDIO_CLIP_FIELDS["sample_path"]) or ""
    relative = get_value(clip, AUDIO_CLIP_FIELDS["sample_relative_path"]) or ""
    if relative:
        rel = Path(relative.replace("\\", "/"))
        if not rel.is_absolute():
            candidate = set_path.resolve().parent / rel
            if candidate.is_file():
                return str(candidate.resolve())
    if absolute:
        candidate = Path(absolute)
        if not candidate.is_absolute():
            candidate = set_path.resolve().parent / candidate
        return str(candidate)
    if relative:
        return str(set_path.resolve().parent / relative.replace("\\", "/"))
    raise ValueError("media reference without Path and RelativePath")


@dataclass
class ReadContext:
    """Estado que a leitura dos clips divide com a leitura da session."""

    path: Path
    anchor: Anchor
    report: Report
    session: SessionIR
    media_por_caminho: dict[str, MediaAsset] = field(default_factory=dict)
    metadata_cache: dict[str, dict | None] = field(default_factory=dict)
    # Set sem o registro do aafton: video por extensao, lista de render e
    # crossfade nao provado viram regra (ver als_render).
    sem_sidecar: bool = False
    render: RenderLog = field(default_factory=RenderLog)

    @property
    def tempo(self) -> Fraction:
        """O tempo do set, exato a partir do texto do numero."""
        return Fraction(str(self.anchor.bpm))

    def absolute(self, beats: float) -> int:
        """Posicao na timeline, em samples, somando a origem do Live."""
        return self.anchor.live_origin_samples + self.samples(beats)

    def samples(self, beats: float) -> int:
        """Beats convertidos em samples, sem a origem do Live."""
        return _beats_to_samples(beats, self.anchor.sample_rate, self.anchor.bpm)

    def source_samples(self, seconds: float) -> int:
        """Segundos do arquivo (marcadores de loop sem warp) em samples da sessao."""
        return _beats_to_samples(seconds, self.anchor.sample_rate, 60.0)


def _clip_causas_modo(clip_node: ET.Element) -> list[str]:
    # Nomeia cada causa em vez de dizer so "warp, loop ou StartRelative".
    # Um envelope de warp ou um loop ativo mudam o alinhamento entre o
    # tempo do Live e o tempo linear do AAF; sem dizer qual deles
    # apareceu, o unico conserto aceitavel parece ser sempre render.
    causas: list[str] = []
    if get_value(clip_node, "IsWarped") == "true":
        causas.append("warp_active")
    if get_value(clip_node, "Loop/LoopOn") == "true":
        causas.append("loop_active")
    inicio_relativo = float(get_value(clip_node, "Loop/StartRelative") or 0)
    if inicio_relativo != 0:
        causas.append(f"StartRelative={inicio_relativo:g}")
    return causas


def _report_clip_mode(
    ctx: ReadContext,
    track: TrackIR,
    clip_node: ET.Element,
) -> None:
    """Avisa que warp, loop ou StartRelative exigem render antes do AAF."""
    if not clip_em_modo_especial(clip_node):
        return
    ctx.report.error(
        "ALS_CLIP_MODE_UNSUPPORTED",
        "warp, loop or a non-zero StartRelative require a render before the AAF",
        {
            "track": track.name,
            "clip_id": clip_node.get("Id"),
            "causes": _clip_causas_modo(clip_node),
            "warp_mode": get_value(clip_node, "WarpMode"),
            "loop_start_beats": get_value(clip_node, "Loop/LoopStart"),
            "loop_length_beats": get_value(clip_node, "Loop/LoopLength"),
        },
    )


@dataclass
class _ClipFields:
    """Campos basicos de um clip do arrangement."""

    caminho: str
    inicio: float
    fim: float
    loop_start: float
    nome: str


def _read_clip_fields(
    ctx: ReadContext,
    track: TrackIR,
    clip_node: ET.Element,
) -> _ClipFields | None:
    """Le os campos do clip; devolve None quando falta algum campo esperado."""
    try:
        caminho = _media_path(clip_node, ctx.path)
        inicio = float(get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"]))
        fim = float(get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"]))
        loop_start = float(get_value(clip_node, AUDIO_CLIP_FIELDS["loop_start"]))
        nome = get_value(clip_node, AUDIO_CLIP_FIELDS["name"])

        if ctx.anchor.video_path:
            resolved_video = str(resolve_video_path(ctx.anchor.video_path, ctx.path))
            if _same_media_path(caminho, resolved_video):
                track.is_video = True

    except (KeyError, TypeError, ValueError) as exc:
        ctx.report.error(
            "ALS_CLIP_UNREADABLE",
            f"clip in track {track.name!r} without the expected field: {exc}",
            {"track": track.name},
        )
        return None

    return _ClipFields(
        caminho=caminho,
        inicio=inicio,
        fim=fim,
        loop_start=loop_start,
        nome=nome,
    )


def _media_metadata(
    ctx: ReadContext,
    caminho: str,
    p_caminho: Path,
) -> dict | None:
    """Metadados do arquivo, com cache por caminho para nao ler duas vezes."""
    from .media_extract import read_audio_metadata

    if caminho not in ctx.metadata_cache:
        try:
            ctx.metadata_cache[caminho] = read_audio_metadata(p_caminho)
        except Exception as e:
            ctx.metadata_cache[caminho] = None
            ctx.report.warning(
                "MEDIA_METADATA_UNREADABLE",
                f"error reading the metadata of {caminho!r}: {e}",
                {"path": caminho},
            )
    return ctx.metadata_cache[caminho]


def _create_media_asset(ctx: ReadContext, caminho: str) -> MediaAsset:
    """Cria o MediaAsset do arquivo e o registra na session."""
    p_caminho = Path(caminho)
    exists = p_caminho.is_file()
    channels = None
    sample_rate = None
    duration_samples = None

    if exists:
        meta = _media_metadata(ctx, caminho, p_caminho)
        if meta:
            channels = meta["channels"]
            sample_rate = meta["sample_rate"]
            duration_samples = meta["duration_samples"]
    else:
        ctx.report.warning(
            "MEDIA_METADATA_UNREADABLE",
            f"file {caminho!r} not found for reading the metadata",
            {"path": caminho},
        )

    asset = MediaAsset(
        id=f"m{len(ctx.media_por_caminho)}",
        filename=p_caminho.name,
        channels=channels,
        sample_rate=sample_rate,
        duration_samples=duration_samples,
        status="resolved" if exists else "missing",
        declared_location=caminho,
        resolved_path=caminho,
    )
    ctx.media_por_caminho[caminho] = asset
    ctx.session.media.append(asset)

    if asset.status == "missing":
        ctx.report.warning(
            "MEDIA_MISSING",
            f"the file {caminho!r} referenced by the set does not exist on disk",
            {"path": caminho},
        )
    return asset


def _media_asset(ctx: ReadContext, caminho: str) -> MediaAsset:
    """Asset do caminho, reaproveitando o que ja foi criado antes."""
    asset = ctx.media_por_caminho.get(caminho)
    if asset is not None:
        return asset
    return _create_media_asset(ctx, caminho)


def _clip_gain(clip_node: ET.Element) -> float:
    """Ganho do clip; em qualquer duvida fica o neutro 1.0."""
    gain_linear = 1.0
    try:
        gain_val = None
        if "clip_gain" in AUDIO_CLIP_FIELDS:
            gain_val = get_value(clip_node, AUDIO_CLIP_FIELDS["clip_gain"])
        if gain_val is None:
            sv = clip_node.find("SampleVolume")
            if sv is not None:
                gain_val = sv.get("Value")
        if gain_val is not None:
            gain_linear = float(gain_val)
    except Exception:
        gain_linear = 1.0
    return gain_linear


def _fade_length_str(
    clip_node: ET.Element,
    field_name: str,
    element_path: str,
) -> str | None:
    """Comprimento bruto do fade, pelo campo ou pelo elemento de fallback."""
    valor = None
    if field_name in AUDIO_CLIP_FIELDS:
        valor = get_value(clip_node, AUDIO_CLIP_FIELDS[field_name])
    if valor is None:
        elem = clip_node.find(element_path)
        if elem is not None:
            valor = elem.get("Value")
    return valor


def _fade(
    ctx: ReadContext, clip_node: ET.Element, kind: str, campo: str, elemento: str
) -> FadeIR | None:
    """Fade de entrada (kind 'in') ou de saida (kind 'out') do clip.

    Um valor que nao vira numero de samples (infinito, por exemplo) nao e um
    fade: o clip fica sem fade, e a leitura inteira nao cai por causa dele.
    """
    valor = _fade_length_str(clip_node, campo, elemento)
    if valor is None:
        return None
    try:
        beats = float(valor)
        if beats > 0:
            samples = ctx.samples(beats)
            if samples > 0:
                return FadeIR(kind=kind, duration_samples=samples)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _fades_enabled(clip_node: ET.Element) -> bool:
    """O elemento Fade do clip liga e desliga os dois fades."""
    fade_elem = clip_node.find("Fade")
    if fade_elem is None:
        return True
    return fade_elem.get("Value", "true").strip().lower() == "true"


def _clip_fades(
    ctx: ReadContext,
    clip_node: ET.Element,
) -> tuple[FadeIR | None, FadeIR | None]:
    """Fades de entrada e de saida do clip."""
    if not _fades_enabled(clip_node):
        return (None, None)
    fade_in = _fade(ctx, clip_node, "in", "fade_in_length", "Fades/FadeInLength")
    fade_out = _fade(ctx, clip_node, "out", "fade_out_length", "Fades/FadeOutLength")
    return (fade_in, fade_out)


def _clip_ir_do_no(
    ctx: ReadContext,
    track: TrackIR,
    clip_node: ET.Element,
    fields: _ClipFields,
    indice: int,
    evento: int,
    fade_in: FadeIR | None,
    fade_out: FadeIR | None,
) -> ClipIR:
    """Constroi o ClipIR basico a partir do no e dos campos lidos."""
    asset = _media_asset(ctx, fields.caminho)
    return ClipIR(
        id=f"t{indice}c{evento}",
        track_id=track.id,
        channel_index=0,
        event_index=evento,
        name=fields.nome,
        media_id=asset.id,
        timeline_start_samples=ctx.absolute(fields.inicio),
        timeline_end_samples=ctx.absolute(fields.fim),
        source_offset_samples=_offset(ctx, clip_node, fields.loop_start),
        gain_linear=_clip_gain(clip_node),
        muted=get_value(clip_node, "Disabled") == "true",
        fade_in=fade_in,
        fade_out=fade_out,
        crossfade_in_state=(get_value(clip_node, AUDIO_CLIP_FIELDS["crossfade_in_state"]) == "1"),
    )


def _read_clip(
    ctx: ReadContext,
    track: TrackIR,
    clip_node: ET.Element,
    indice: int,
    evento: int,
    proximo_evento: list[int] | None = None,
) -> ClipIR | None:
    """Le um clip do arrangement; clip ilegivel e pulado mas conta o indice."""
    if not ctx.sem_sidecar:
        _report_clip_mode(ctx, track, clip_node)
    elif not _triagem(ctx, track, clip_node):
        return None

    fields = _read_clip_fields(ctx, track, clip_node)
    if fields is None:
        return None

    fade_in, fade_out = _clip_fades(ctx, clip_node)
    clip = _clip_ir_do_no(ctx, track, clip_node, fields, indice, evento, fade_in, fade_out)
    origem = ctx.anchor.live_origin_samples
    inicio_abs = clip.timeline_start_samples
    fim_abs = clip.timeline_end_samples
    if ctx.sem_sidecar and loop_expansivel(
        clip_node, ctx.tempo, ctx.anchor.sample_rate, origem, inicio_abs, fim_abs
    ):
        contador = proximo_evento if proximo_evento is not None else [evento + 1]
        lista = passadas(clip_node, ctx.tempo, ctx.anchor.sample_rate, origem, inicio_abs, fim_abs)
        expandidos = expandir_clip_em_passadas(lista, clip, fade_in, fade_out, contador)
        track.clips.extend(expandidos)
        return expandidos[0]
    track.clips.append(clip)
    return clip


def _limites_em_amostras(
    ctx: ReadContext, clip_node: ET.Element
) -> tuple[int | None, int | None, int | None]:
    """Origem e inicio/fim em amostras quando CurrentStart e CurrentEnd sao finitos."""
    try:
        inicio = float(get_value(clip_node, AUDIO_CLIP_FIELDS["current_start"]))
        fim = float(get_value(clip_node, AUDIO_CLIP_FIELDS["current_end"]))
    except (TypeError, ValueError):
        return None, None, None
    if not (math.isfinite(inicio) and math.isfinite(fim)):
        return None, None, None
    return ctx.anchor.live_origin_samples, ctx.absolute(inicio), ctx.absolute(fim)


def _triagem(ctx: ReadContext, track: TrackIR, clip_node: ET.Element) -> bool:
    """Sem sidecar: o video sai, e o clip de render entra na lista ou fica de fora.

    Devolve se o clip segue para a leitura. Um clip sem caminho de midia segue:
    a leitura normal reporta o clip ilegivel.
    """
    try:
        caminho = _media_path(clip_node, ctx.path)
    except ValueError:
        return True
    nome = get_value(clip_node, AUDIO_CLIP_FIELDS["name"]) or ""
    if e_video(caminho):
        ctx.render.video(track, nome)
        return False
    origem, inicio_abs, fim_abs = _limites_em_amostras(ctx, clip_node)
    causas = precisa_render(
        clip_node,
        ctx.tempo,
        ctx.anchor.sample_rate,
        origem,
        inicio_abs,
        fim_abs,
    )
    if causas:
        ctx.render.clip(track, nome, causas, clip_node)
    return not ctx.render.deixado(clip_node)


def _offset(ctx: ReadContext, clip_node: ET.Element, loop_start: float) -> int:
    """Offset na midia: segundos do arquivo, ou o SecTime de um clip com warp 1:1."""
    if ctx.sem_sidecar and get_value(clip_node, AUDIO_CLIP_FIELDS["is_warped"]) == "true":
        offset = offset_do_warp(clip_node, ctx.anchor.sample_rate)
        if offset is not None:
            return offset
    return ctx.source_samples(loop_start)


def read_track_clips(
    ctx: ReadContext,
    track: TrackIR,
    track_node: ET.Element,
    indice: int,
) -> None:
    """Le os clips do arrangement da track e anexa em track.clips."""
    arrangement = track_node.findall(
        "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
    )
    proximo_evento = [len(arrangement)]
    pares: list[tuple[ClipIR, ET.Element]] = []
    for evento, clip_node in enumerate(arrangement):
        clip = _read_clip(ctx, track, clip_node, indice, evento, proximo_evento)
        if clip is not None:
            pares.append((clip, clip_node))
    if ctx.sem_sidecar:
        cortar_caudas_de_loop(track, ctx.report)
        marcar_video(ctx.render, track, arrangement)
        soltar_vizinhos(ctx.render, track, arrangement, pares)
    if not track.is_video:
        normalizar_crossfades(ctx, pares)
