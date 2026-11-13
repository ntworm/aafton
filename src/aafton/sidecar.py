"""Persistencia da ancoragem de timecode ao lado do .als.

O formato .als nao tem campo para timecode de sessao. A ancoragem e gravada em
duas copias, de proposito: um JSON ao lado do arquivo, que e a fonte de verdade,
e o nome de um locator no beat zero, que e o fallback quando o JSON se perde. A
ausencia das duas e erro; exportar ancorado em zero por omissao produziria um
AAF desalinhado em uma hora sem ninguem perceber.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .timecode import TimecodeRate, label_to_samples, samples_to_label

SIDECAR_SUFFIX = ".aafton.json"
_ANCHOR_NAME_RE = re.compile(r"^TC\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})")


class SidecarMissingError(RuntimeError):
    """Nao foi possivel descobrir a ancoragem do arquivo."""


@dataclass(kw_only=True)
class Anchor:
    live_origin_samples: int
    session_start_samples: int
    sample_rate: int
    timecode_rate: TimecodeRate
    bpm: float
    format_version: int = 2
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
    # track_bindings: metadados deterministas por track do Live.
    # Campos base: index, track_name, origin_slot_id, mob_id, channel_count.
    # Campos multicanal (expansao sob GroupTrack): channel_index, multichannel_layout, parent_group.
    track_bindings: list[dict] | None = None
    media_hashes: dict[str, str] | None = None
    origin_slots: list[dict] | dict | None = None


def sidecar_path_for(als_path: str | Path) -> Path:
    path = Path(als_path)
    return path.with_name(path.stem + SIDECAR_SUFFIX)


def write_sidecar(als_path: str | Path, anchor: Anchor) -> Path:
    destino = sidecar_path_for(als_path)
    payload = {
        "format_version": anchor.format_version,
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
    if anchor.track_channels is not None:
        payload["track_channels"] = anchor.track_channels
    if anchor.audio_source_indices is not None:
        payload["audio_source_indices"] = anchor.audio_source_indices
    if anchor.clip_ranges is not None:
        payload["clip_ranges"] = anchor.clip_ranges
    if anchor.track_bindings is not None:
        payload["track_bindings"] = anchor.track_bindings
    if anchor.media_hashes is not None:
        payload["media_hashes"] = anchor.media_hashes
    if anchor.origin_slots is not None:
        payload["origin_slots"] = anchor.origin_slots
    destino.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destino


def parse_anchor_locator_name(name: str, rate: TimecodeRate) -> int | None:
    """Le a posicao absoluta a partir do nome do locator de ancoragem."""
    match = _ANCHOR_NAME_RE.match(name.strip())
    if match is None:
        return None
    sample_rate = 48000
    tail = re.search(r"@\s*(\d+)", name)
    if tail:
        sample_rate = int(tail.group(1))
    return int(label_to_samples(match.group(1), rate, sample_rate))


def read_anchor(als_path: str | Path) -> Anchor:
    """Le a ancoragem do sidecar. Levanta se o arquivo nao existir."""
    origem = sidecar_path_for(als_path)
    if not origem.exists():
        raise SidecarMissingError(
            f"there is no {origem.name} beside {Path(als_path).name} to discover the "
            f"anchor; use the anchor locator of the set or give the start timecode "
            f"on the command line"
        )
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
        track_bindings=dados.get("track_bindings"),
        media_hashes=dados.get("media_hashes"),
        origin_slots=dados.get("origin_slots"),
    )
