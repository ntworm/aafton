"""Persistencia da ancoragem de timecode ao lado do .als.

O formato .als nao tem campo para timecode de sessao. A ancoragem e gravada em
duas copias, de proposito: um JSON ao lado do arquivo, que e a fonte de verdade,
e o nome de um locator no beat zero, que e o fallback quando o JSON se perde. A
ausencia das duas e erro; exportar ancorado em zero por omissao produziria um
AAF desalinhado em uma hora sem ninguem perceber.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .timecode import (
    TimecodeRate,
    frames_to_samples_exact,
    label_to_frames,
    label_to_samples,
    samples_to_label,
)

SIDECAR_SUFFIX = ".aafton.json"
# Versao que este codigo grava. A 3 marca a forma medida do crossfade no Live
# (o clip que entra comeca no fim do que sai); a leitura das versoes <= 2 fica
# como era.
SIDECAR_FORMAT_VERSION = 3
_ANCHOR_NAME_RE = re.compile(r"^TC\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})")


class SidecarMissingError(RuntimeError, ValueError):
    """Nao foi possivel descobrir a ancoragem do arquivo.

    Tambem e ``ValueError``: a linha de comando mostra a mensagem, sem traceback.
    """


# Mensagem da recusa de volta de um set de som direto.
PRODUCTION_SET_MESSAGE = "Sets built from production audio cannot be exported back yet"


class ProductionSetError(ValueError):
    """O set foi montado de som direto: a volta para AAF ainda nao existe."""

    code = "production_set"

    def __init__(self, message: str = PRODUCTION_SET_MESSAGE) -> None:
        super().__init__(message)


@dataclass(kw_only=True)
class Anchor:
    live_origin_samples: int
    session_start_samples: int
    sample_rate: int
    timecode_rate: TimecodeRate
    bpm: float
    # Versao do set lido; um set sem sidecar vale como a atual.
    format_version: int = SIDECAR_FORMAT_VERSION
    source_path: str | None = None
    fades: str = "absorb"
    stereo: str = "pair"
    video_path: str | None = None
    video_anchor_source: str | None = None
    track_channels: dict[str, int] | None = None
    audio_source_indices: list[int] | list[list[int]] | None = None
    # Intervalos absolutos [inicio, fim) de cada clip escrito, por track do Live,
    # na ordem das tracks. O auditor de audio usa so os fins (--release-mask, D14).
    clip_ranges: list[dict] | None = None
    # Chaves de origem [source_index, ordem] de cada clip escrito nas sessoes
    # organizadas por tipo, na ordem das tracks do .als.
    clip_origins: list[dict] | None = None
    # track_bindings: metadados deterministas por track do Live.
    # Campos base: index, track_name, origin_slot_id, mob_id, channel_count.
    # Campos multicanal (expansao sob GroupTrack): channel_index, multichannel_layout, parent_group.
    track_bindings: list[dict] | None = None
    media_hashes: dict[str, str] | None = None
    origin_slots: list[dict] | dict | None = None
    # Som direto: layout da sessao e os blocos das diarias; None num set de AAF.
    layout: str | None = None
    blocks: list[dict] | None = None
    # Expansao de canais: pasta do gravador, AAF do editor e, por irma (pelo
    # nome da track), a marca e o original e a entrada de cada clip.
    expanded: dict | None = None


def sidecar_path_for(als_path: str | Path) -> Path:
    path = Path(als_path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


# Campos gravados so quando existem, na ordem em que saem no arquivo.
_CAMPOS_OPCIONAIS = (
    "track_channels",
    "audio_source_indices",
    "clip_ranges",
    "clip_origins",
    "track_bindings",
    "media_hashes",
    "origin_slots",
)


def resolve_video_path(anchor_video_path: str, set_path: Path) -> Path:
    """Resolve o video do sidecar: absoluto fica; relativo resolve a partir do set."""
    caminho = Path(anchor_video_path)
    if caminho.is_absolute():
        return caminho
    return (Path(set_path).resolve().parent / caminho).resolve()


def write_sidecar(als_path: str | Path, anchor: Anchor) -> Path:
    destino = sidecar_path_for(als_path)
    # O arquivo sai sempre na versao que este codigo grava, qualquer que seja a
    # versao lida guardada na ancora.
    payload = {
        "format_version": SIDECAR_FORMAT_VERSION,
        "sample_rate": anchor.sample_rate,
        "bpm": anchor.bpm,
        "timecode_rate": {
            "num": anchor.timecode_rate.num,
            "den": anchor.timecode_rate.den,
            "drop_frame": anchor.timecode_rate.drop_frame,
        },
        "live_origin_samples": anchor.live_origin_samples,
        "live_origin_timecode": samples_to_label(
            anchor.live_origin_samples, anchor.timecode_rate, anchor.sample_rate
        ),
        "session_start_samples": anchor.session_start_samples,
        "session_start_timecode": samples_to_label(
            anchor.session_start_samples, anchor.timecode_rate, anchor.sample_rate
        ),
        "source_path": anchor.source_path,
        "fades": anchor.fades,
        "stereo": anchor.stereo,
        "video_path": anchor.video_path,
        "video_anchor_source": anchor.video_anchor_source,
    }
    for campo in _CAMPOS_OPCIONAIS:
        valor = getattr(anchor, campo)
        if valor is not None:
            payload[campo] = valor
    if anchor.layout is not None:
        payload["layout"] = anchor.layout
        payload["blocks"] = anchor.blocks or []
    if anchor.expanded is not None:
        payload["expanded"] = anchor.expanded
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def anchor_locator_label(name: str) -> str | None:
    """O timecode escrito no nome do locator de ancoragem; None quando nao e um."""
    match = _ANCHOR_NAME_RE.match(name.strip())
    return match.group(1) if match else None


def sidecar_missing(als_path: str | Path) -> SidecarMissingError:
    """A recusa de um set sem sidecar e sem outra fonte de ancoragem."""
    origem = sidecar_path_for(als_path)
    return SidecarMissingError(
        f"there is no {origem.name} beside {Path(als_path).name} to discover the "
        f"anchor; use the anchor locator of the set or give the start timecode "
        f"with --start-tc and --frame-rate"
    )


def frames_per_day(rate: TimecodeRate) -> int:
    """Quantos frames de rotulo tem um dia de timecode na taxa dada.

    Um dia sao 24 h de frames de rotulo, nao 24 h de samples: o ultimo frame
    do dia e o do rotulo `23:59:59:FF` e o dia inteiro tem mais um frame. A
    conta passa pelo proprio rotulo para nao reimplementar o drop frame.
    """
    ultimo = rate.nominal_fps - 1
    return label_to_frames(f"23:59:59:{ultimo:02d}", rate) + 1


def preroll_frames(rate: TimecodeRate) -> int:
    """A janela do leitor em frames: o pre-roll de dez segundos, para cima."""
    # O pipeline importa o sidecar: a constante entra aqui, na hora, para nao
    # fechar o ciclo de importacao.
    from .pipeline import PRE_ROLL_SEGUNDOS

    return math.ceil(PRE_ROLL_SEGUNDOS * rate.fps)


def na_janela_preroll(frame_negativo: int, rate: TimecodeRate) -> bool:
    """Verdadeiro quando o deslocamento negativo cabe na janela de volta 24 h."""
    return -preroll_frames(rate) <= frame_negativo < 0


def anchor_label_samples(label: str, rate: TimecodeRate, sample_rate: int) -> int | Fraction:
    """Posicao em samples do rotulo do locator, desembrulhando 24 h na pre-roll."""
    frame = label_to_frames(label, rate)
    dia = frames_per_day(rate)
    if na_janela_preroll(frame - dia, rate):
        posicao = frames_to_samples_exact(frame - dia, rate, sample_rate)
    else:
        posicao = label_to_samples(label, rate, sample_rate)
    return int(posicao) if posicao.denominator == 1 else posicao


def parse_anchor_locator_name(name: str, rate: TimecodeRate) -> int | Fraction | None:
    """Le a posicao absoluta a partir do nome do locator de ancoragem.

    Um locator escrito com a origem embrulhada em 24 h (a pre-roll negativa
    antes do primeiro clip) volta para a origem negativa. A volta e contada em
    frames de rotulo: 24 h de samples nao sao 24 h de timecode.
    """
    match = _ANCHOR_NAME_RE.match(name.strip())
    if match is None:
        return None
    sample_rate = 48000
    tail = re.search(r"@\s*(\d+)", name)
    if tail:
        sample_rate = int(tail.group(1))
    posicao = anchor_label_samples(match.group(1), rate, sample_rate)
    # Fora da janela a posicao fica como sempre foi lida (int).
    return int(posicao) if posicao >= 0 else posicao


def read_anchor(als_path: str | Path) -> Anchor:
    """Le a ancoragem do sidecar. Levanta se o arquivo nao existir."""
    origem = sidecar_path_for(als_path)
    if not origem.exists():
        raise sidecar_missing(als_path)
    dados = json.loads(origem.read_text(encoding="utf-8"))
    rate = dados["timecode_rate"]
    return Anchor(
        live_origin_samples=int(dados["live_origin_samples"]),
        session_start_samples=int(dados["session_start_samples"]),
        sample_rate=int(dados["sample_rate"]),
        timecode_rate=TimecodeRate(int(rate["num"]), int(rate["den"]), bool(rate["drop_frame"])),
        bpm=float(dados["bpm"]),
        format_version=int(dados.get("format_version", 1)),
        source_path=dados.get("source_path"),
        fades=str(dados.get("fades", "absorb")),
        stereo=str(dados.get("stereo", "pair")),
        video_path=dados.get("video_path"),
        video_anchor_source=dados.get("video_anchor_source"),
        track_channels=dados.get("track_channels"),
        audio_source_indices=dados.get("audio_source_indices"),
        clip_ranges=dados.get("clip_ranges"),
        clip_origins=dados.get("clip_origins"),
        track_bindings=dados.get("track_bindings"),
        media_hashes=dados.get("media_hashes"),
        origin_slots=dados.get("origin_slots"),
        layout=dados.get("layout"),
        blocks=dados.get("blocks"),
        expanded=dados.get("expanded"),
    )
