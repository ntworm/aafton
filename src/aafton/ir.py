"""SessionIR: o modelo neutro entre AAF e Ableton Live.

Regras que valem para todo este modulo:

- Todo campo de tempo e um inteiro de samples **absolutos** contados desde
  ``00:00:00:00``, ou ``None``. Nunca float, nunca string, nunca relativo ao
  inicio da sessao.
- Um valor desconhecido e ``None`` e gera issue. Nunca e preenchido com zero.
- Toda dataclass daqui e ``kw_only=True``. O contrato vai crescer (automacao,
  MXF, video), e sem isso um campo novo sem default nao poderia ser acrescentado
  depois de um campo com default sem reordenar a classe inteira e quebrar todo
  mundo que a constroi.
- Este modulo nao importa nada de I/O, XML ou AAF. E o contrato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Literal

from .timecode import TimecodeRate

TrackKind = Literal["audio", "aux", "master", "vca", "folder", "video", "midi"]
FadeKind = Literal["in", "out", "crossfade"]
MediaStatus = Literal["resolved", "missing", "unsupported"]


@dataclass(kw_only=True)
class FadeIR:
    kind: FadeKind
    duration_samples: int
    curve: str | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.duration_samples < 0:
            raise ValueError(f"fade duration cannot be negative: {self.duration_samples}")


@dataclass(kw_only=True)
class MediaAsset:
    id: str
    filename: str
    channels: int | None = None
    sample_rate: int | None = None
    duration_samples: int | None = None
    origin_timecode_samples: int | None = None
    status: MediaStatus = "missing"
    declared_location: str | None = None
    resolved_path: str | None = None
    # mob_id (string UMID) do SourceMob que carrega a essencia embarcada.
    # Preenchido durante read_aaf quando o SourceMob e encontrado na cadeia
    # MasterMob -> SourceClip -> SourceMob. Usado na extracao para localizar
    # o mob sem depender de casamento por nome.
    embedded_mob_id: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class ClipIR:
    id: str
    track_id: str
    channel_index: int
    event_index: int
    name: str
    media_id: str | None
    timeline_start_samples: int
    timeline_end_samples: int
    source_offset_samples: int | None = None
    muted: bool = False
    gain_linear: float = 1.0
    fade_in: FadeIR | None = None
    fade_out: FadeIR | None = None
    crossfade_in_state: bool = False
    anchor_source: str | None = None
    # PhysicalTrackNumber do slot do arquivo que o clip le, quando o AAF o
    # declara: num WAV estereo do Resolve e o canal do arquivo (1 ou 2); no
    # multi-mono do Avid e o canal da origem, com arquivo de um canal so.
    source_channel: int | None = None
    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeline_end_samples < self.timeline_start_samples:
            raise ValueError(
                f"clip {self.id!r} ends before it starts: "
                f"{self.timeline_end_samples} < {self.timeline_start_samples}"
            )
        if self.source_offset_samples is not None and self.source_offset_samples < 0:
            raise ValueError(f"clip {self.id!r} has a negative offset in the source")

    @property
    def duration_samples(self) -> int:
        return self.timeline_end_samples - self.timeline_start_samples


@dataclass(kw_only=True)
class PluginRef:
    name: str
    manufacturer: str | None = None
    version: str | None = None


@dataclass(kw_only=True)
class TrackIR:
    id: str
    source_index: int
    name: str
    kind: TrackKind
    channel_count: int
    channel_layout: str | None = None
    group_path: list[str] = field(default_factory=list)
    original_source_indices: list[int] = field(default_factory=list)
    hidden: bool = False
    muted: bool = False
    gain_linear: float = 1.0
    pan: float = 0.0
    plugins: list[PluginRef] = field(default_factory=list)
    clips: list[ClipIR] = field(default_factory=list)

    def __post_init__(self):
        if not self.original_source_indices:
            self.original_source_indices = [self.source_index]

    annotation: str | None = None
    is_video: bool = False
    # Lista de (sample_absoluto, amplitude_linear) representando a curva de
    # automacao de ganho desta track. Vazia quando nao ha automacao.
    gain_points: list[tuple[int, float]] = field(default_factory=list)
    # Lista de (sample_absoluto, pan) com o mesmo contrato de gain_points. O
    # valor e o pan linear do Live, de 0.0 a 1.0 com 0.5 no centro; a conversao
    # para o Mono Audio Pan do AAF acontece na escrita, nunca aqui.
    pan_points: list[tuple[int, float]] = field(default_factory=list)


@dataclass(kw_only=True)
class MarkerIR:
    id: str
    name: str
    position_samples: int
    comment: str | None = None


@dataclass(kw_only=True)
class SessionIR:
    name: str
    sample_rate: int
    timecode_rate: TimecodeRate
    start_timecode_samples: int
    tracks: list[TrackIR] = field(default_factory=list)
    media: list[MediaAsset] = field(default_factory=list)
    markers: list[MarkerIR] = field(default_factory=list)
    source_path: str | None = None
    source_application: str | None = None
    # Lido para inspeção/verificação geométrica, mas não exportável sem perda.
    unsupported_export_features: list[str] = field(default_factory=list)

    def all_clips(self) -> Iterator[ClipIR]:
        for track in self.tracks:
            yield from track.clips

    def media_by_id(self, media_id: str) -> MediaAsset | None:
        for asset in self.media:
            if asset.id == media_id:
                return asset
        return None
