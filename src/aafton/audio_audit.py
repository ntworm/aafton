"""Audita renders sem usar leitores, IR ou alinhamento do conversor.

Alinhamento vem exclusivamente do sidecar/BWF. Correlacao e diagnostico,
nunca uma correcao. Absorb gera relatorio sem aceite; keep compara sem mascara,
exceto a mascara de release (D14), limitada a N amostras depois de cada fim de
clip declarado no sidecar. Track sem audio ativo nos dois lados nao prova nada
e reprova em keep.
"""

from __future__ import annotations

import json
import math
import re
import struct
import unicodedata
from array import array
from dataclasses import dataclass
from pathlib import Path


class AuditError(ValueError):
    """Entrada incompleta ou formato que o oraculo nao pode interpretar."""


@dataclass
class WaveAudio:
    sample_rate: int
    channels: tuple[array, ...]
    time_reference: int | None

    @property
    def frames(self) -> int:
        return len(self.channels[0])


def read_wav(path: str | Path) -> WaveAudio:
    """RIFF proprio: PCM 16/24/32, float 32/64, extensible e BWF."""
    path = Path(path)
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            header = handle.read(12)
            if header[:4] in (b"RF64", b"BW64"):
                raise AuditError(f"{path}: RF64/BW64 not supported")
            if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
                raise AuditError(f"{path}: expected RIFF/WAVE")
            end = struct.unpack_from("<I", header, 4)[0] + 8
            if end != size:
                raise AuditError(
                    f"{path}: the RIFF size differs from the file (truncated or extra data)"
                )
            fmt = payload = None
            time_reference = None
            while handle.tell() < end:
                chunk = handle.read(8)
                if len(chunk) != 8:
                    raise AuditError(f"{path}: truncated chunk header")
                name, length = struct.unpack("<4sI", chunk)
                offset = handle.tell()
                if offset + length + (length % 2) > end:
                    raise AuditError(f"{path}: chunk {name!r} truncated")
                if name == b"fmt ":
                    if fmt is not None or length < 16 or length > 65536:
                        raise AuditError(f"{path}: fmt missing, duplicated or invalid")
                    fmt = handle.read(length)
                elif name == b"data":
                    if payload is not None:
                        raise AuditError(f"{path}: duplicated data")
                    payload = handle.read(length)
                elif name == b"bext":
                    if length < 346 or time_reference is not None:
                        raise AuditError(f"{path}: bext short or duplicated")
                    bext = handle.read(346)
                    time_reference = struct.unpack_from("<Q", bext, 338)[0]
                handle.seek(offset + length + (length % 2))
    except OSError as exc:
        raise AuditError(f"{path}: could not read WAV: {exc}") from exc
    if fmt is None or payload is None:
        raise AuditError(f"{path}: no fmt or data")
    tag, count, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE:
        if len(fmt) < 40 or struct.unpack_from("<H", fmt, 16)[0] < 22:
            raise AuditError(f"{path}: incomplete WAVE_FORMAT_EXTENSIBLE")
        valid_bits = struct.unpack_from("<H", fmt, 18)[0]
        tag = struct.unpack_from("<I", fmt, 24)[0]
        if fmt[28:40] != bytes.fromhex("00001000800000aa00389b71") or valid_bits != bits:
            raise AuditError(f"{path}: GUID or extensible valid bits not supported")
    if not ((tag == 1 and bits in (16, 24, 32)) or (tag == 3 and bits in (32, 64))):
        raise AuditError(f"{path}: WAV format not supported: tag={tag}, bits={bits}")
    width = bits // 8
    if count < 1 or rate < 1 or align != count * width or byte_rate != rate * align:
        raise AuditError(f"{path}: inconsistent fmt")
    if not payload or len(payload) % align:
        raise AuditError(f"{path}: empty data or incomplete frame")
    channels = tuple(array("d") for _ in range(count))
    if tag == 1 and bits == 24:
        values = (
            int.from_bytes(payload[i : i + 3], "little", signed=True) / 8388608
            for i in range(0, len(payload), 3)
        )
    else:
        code = ("h" if bits == 16 else "i") if tag == 1 else ("f" if bits == 32 else "d")
        scale = 1 << (bits - 1) if tag == 1 else 1
        values = (v[0] / scale for v in struct.iter_unpack("<" + code, payload))
    for i, value in enumerate(values):
        if not math.isfinite(value):
            raise AuditError(f"{path}: NaN/Infinity sample")
        channels[i % count].append(value)
    return WaveAudio(rate, channels, time_reference)


def _json(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        data = json.loads(Path(value).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise AuditError(f"invalid JSON: {value}: {exc}") from exc
    if not isinstance(data, dict):
        raise AuditError(f"JSON must be an object: {value}")
    return data


def _integer(value, name: str, *, minimum=0) -> int:
    if type(value) is not int or value < minimum:
        raise AuditError(f"{name}: expected integer >= {minimum}")
    return value


def _reference_start(label: str | int, sidecar: dict) -> int:
    if type(label) is int:
        return _integer(label, "reference-start")
    rate = sidecar.get("timecode_rate", {})
    fps = rate.get("num")
    if (
        type(fps) is not int
        or fps <= 0
        or rate.get("den") != 1
        or rate.get("drop_frame") is not False
    ):
        raise AuditError("reference-start by label requires a non-drop integer rate in the sidecar")
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}):(\d{2})", str(label))
    if not match:
        raise AuditError("reference-start: use HH:MM:SS:FF non-drop")
    hour, minute, second, frame = map(int, match.groups())
    if hour > 23 or minute > 59 or second > 59 or frame >= fps:
        raise AuditError("reference-start: label out of range")
    numerator = ((hour * 3600 + minute * 60 + second) * fps + frame) * sidecar["sample_rate"]
    if numerator % fps:
        raise AuditError("reference-start does not fall on a whole sample")
    return numerator // fps


def normalize_name(name: str) -> str:
    # Nomes de track como '5.1 [L]' nao tem extensao; Path.stem perderia o canal.
    basename = Path(name).name
    if basename.lower().endswith(".wav"):
        basename = basename[:-4]
    text = unicodedata.normalize("NFKD", basename).casefold()
    return "".join(c for c in text if c.isalnum() and not unicodedata.combining(c))


def _files(folder: Path) -> dict[str, Path]:
    if not folder.is_dir():
        raise AuditError(f"renders folder missing: {folder}")
    return {
        p.name: p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() == ".wav"
    }


def _names(value) -> list[str]:
    names = [value] if isinstance(value, str) else value
    if (
        not isinstance(names, list)
        or len(names) not in (1, 2)
        or not all(isinstance(n, str) for n in names)
    ):
        raise AuditError("map: each side must be a WAV name or list [L.wav, R.wav]")
    return names


def _group(files: dict[str, Path], names: list[str]) -> WaveAudio:
    audio = [read_wav(files[n]) for n in names]
    if len(audio) == 1:
        return audio[0]
    if any(len(w.channels) != 1 for w in audio):
        raise AuditError("map split: both files must be mono")
    if len({(w.sample_rate, w.frames, w.time_reference) for w in audio}) != 1:
        raise AuditError("map split: L/R differ in rate, length or TimeReference")
    return WaveAudio(
        audio[0].sample_rate, tuple(w.channels[0] for w in audio), audio[0].time_reference
    )


def _db(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else -math.inf


def _included(start: int, count: int, regions: list[tuple[int, int]]) -> bytearray:
    flags = bytearray(b"\x01") * count
    for a, b in regions:
        lo, hi = max(0, a - start), min(count, b - start)
        if hi > lo:
            flags[lo:hi] = b"\x00" * (hi - lo)
    if not any(flags):
        raise AuditError("mask leaves the window without samples")
    return flags


def _lag(
    reference, candidate, ri: int, ci: int, flags: bytearray, eligible: array, max_lag: int
) -> tuple[int, float | None, int, int]:
    """Correlacao normalizada em ate 256 probes distribuidos no audio ativo.

    Todas as defasagens inteiras sao avaliadas. A amostragem limita custo em
    renders longos; pico/RMS e aceite sempre usam TODAS as amostras da janela.
    Uma defasagem so concorre se pelo menos metade dos probes cair na janela:
    com um probe so, ``dot/sqrt(xx*yy)`` vale 1.0 e uma borda vencia o lag 0
    real. Empates favorecem zero e depois menor modulo. Sinal silencioso e
    inconclusivo. Devolve lag, correlacao, probes e probes usados no lag.
    """
    if not eligible:
        return 0, None, 0, 0
    probes = [
        eligible[i * len(eligible) // min(256, len(eligible))]
        for i in range(min(256, len(eligible)))
    ]
    minimum = (len(probes) + 1) // 2

    def score(lag):
        dot = xx = yy = 0.0
        used = 0
        for p in probes:
            q = p + lag
            if q < 0 or q >= len(flags) or not flags[q]:
                continue
            x, y = reference[ri + p], candidate[ci + q]
            dot += x * y
            xx += x * x
            yy += y * y
            used += 1
        if used < minimum:
            return None, used
        return (dot / math.sqrt(xx * yy) if xx > 0 and yy > 0 else -1.0), used

    best, best_used = score(0)
    best_lag = 0
    if best < 1 - 1e-12:
        for lag in range(-min(max_lag, len(flags) - 1), min(max_lag, len(flags) - 1) + 1):
            value, used = score(lag)
            if value is None:
                continue
            if value > best + 1e-12 or (
                abs(value - best) <= 1e-12 and (abs(lag), lag) < (abs(best_lag), best_lag)
            ):
                best_lag, best, best_used = lag, value, used
    return best_lag, best, len(probes), best_used


def _metrics(reference, candidate, ri, ci, flags, max_lag) -> dict:
    peak = ss = xx = dot = 0.0
    reference_peak = candidate_peak = 0.0
    eligible = array("I")
    count = 0
    for p, enabled in enumerate(flags):
        if not enabled:
            continue
        x, y = reference[ri + p], candidate[ci + p]
        diff = y - x
        peak = max(peak, abs(diff))
        reference_peak = max(reference_peak, abs(x))
        candidate_peak = max(candidate_peak, abs(y))
        ss += diff * diff
        xx += x * x
        dot += x * y
        count += 1
        if abs(x) > 1e-12:
            eligible.append(p)
    gain = dot / xx if xx else None
    lag, correlation, probes, used = _lag(reference, candidate, ri, ci, flags, eligible, max_lag)
    return {
        "samples": count,
        "residual_peak_dbfs": _db(peak),
        "residual_rms_dbfs": _db(math.sqrt(ss / count)),
        "reference_rms_dbfs": _db(math.sqrt(xx / count)),
        "reference_peak_dbfs": _db(reference_peak),
        "candidate_peak_dbfs": _db(candidate_peak),
        "gain_linear": gain,
        "gain_db": _db(gain) if gain is not None and gain > 0 else None,
        "lag_samples": lag,
        "correlation": correlation,
        "correlation_probes": probes,
        "correlation_probes_used": used,
    }


def _clip_ranges(anchor: dict) -> dict[str, list[tuple[int, int]]]:
    """Intervalos absolutos de clip por track, gravados pelo conversor no sidecar."""
    entries = anchor.get("clip_ranges")
    if not isinstance(entries, list):
        raise AuditError("release-mask requires clip_ranges in the sidecar; regenerate the .als")
    found: dict[str, list[tuple[int, int]]] = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("track"), str)
            or not isinstance(entry.get("ranges"), list)
        ):
            raise AuditError("sidecar: clip_ranges expects {track, ranges}")
        key = normalize_name(entry["track"])
        if not key or key in found:
            raise AuditError(f"sidecar: empty or repeated track in clip_ranges: {entry['track']!r}")
        ranges = []
        for interval in entry["ranges"]:
            if not isinstance(interval, list) or len(interval) != 2:
                raise AuditError("sidecar: clip_ranges uses [start, end] in absolute samples")
            a, b = (_integer(n, "clip_ranges sample") for n in interval)
            if b <= a:
                raise AuditError("sidecar: clip_ranges with end before start")
            ranges.append((a, b))
        found[key] = sorted(ranges)
    return found


def _release_regions(ranges: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    """Ate ``length`` amostras depois de cada fim de clip, sem tocar dentro de clip algum.

    O Live 12.4 continua a fonte por cerca de 46 amostras depois do fim do clip,
    numa rampa linear (declick, D14). A regiao parte do fim declarado e perde
    qualquer trecho que caia dentro de outro clip da mesma track.
    """
    pieces = []
    for _, end in ranges:
        current = [(end, end + length)]
        for a, b in ranges:
            current = [
                part
                for lo, hi in current
                for part in ((lo, min(hi, a)), (max(lo, b), hi))
                if part[1] > part[0]
            ]
        pieces.extend(current)
    merged: list[tuple[int, int]] = []
    for a, b in sorted(pieces):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _outside_peak(audio: WaveAudio, start: int, lo: int, hi: int, regions) -> float:
    peak = 0.0
    for a, b in ((0, max(0, lo - start)), (max(0, hi - start), audio.frames)):
        for i in range(a, min(b, audio.frames)):
            if any(x <= start + i < y for x, y in regions):
                continue
            peak = max(peak, *(abs(ch[i]) for ch in audio.channels))
    return _db(peak)


def audit_audio(
    reference,
    candidate,
    sidecar,
    *,
    mapping=None,
    mask=None,
    tolerance_db=-90.0,
    max_lag=4800,
    reference_start=None,
    mode=None,
    release_mask=None,
) -> dict:
    """Compara pastas completas de WAV. Nao usa correlacao para alinhar.

    ``release_mask`` (D14) e a unica mascara aceita em keep: ate N amostras logo
    depois de cada fim de clip declarado no sidecar, nunca dentro de clip.
    """
    anchor = _json(sidecar)
    origin = _integer(anchor.get("live_origin_samples"), "live_origin_samples")
    rate = _integer(anchor.get("sample_rate"), "sample_rate", minimum=1)
    if anchor.get("bpm") != 60:
        raise AuditError("sidecar: the audit requires 60 BPM (beat zero = live_origin_samples)")
    mode = mode or anchor.get("fades")
    if mode not in ("keep", "absorb"):
        raise AuditError("mode must be keep or absorb; specify --mode or fades in the sidecar")
    if mask is not None and mode == "keep":
        raise AuditError("keep does not allow a mask")
    _integer(max_lag, "max-lag")
    clip_ranges = None
    if release_mask is not None:
        _integer(release_mask, "release-mask")
        clip_ranges = _clip_ranges(anchor)
    if not math.isfinite(tolerance_db) or tolerance_db > 0:
        raise AuditError("tolerance-db must be finite and <= 0")
    ref_files, cand_files = _files(Path(reference)), _files(Path(candidate))
    errors, pairs = [], []
    if mapping is None:
        indexes = []
        for files in (ref_files, cand_files):
            index = {}
            for name in files:
                index.setdefault(normalize_name(name), []).append(name)
            indexes.append(index)
        for key in sorted(set(indexes[0]) | set(indexes[1])):
            r, c = indexes[0].get(key, []), indexes[1].get(key, [])
            if len(r) != 1 or len(c) != 1:
                errors.append(
                    f"no unique pair for {key!r}: reference={r}, candidate={c}; use --map"
                )
            else:
                pairs.append({"track": key, "reference": r, "candidate": c})
    else:
        entries = _json(mapping).get("pairs")
        if not isinstance(entries, list):
            raise AuditError("map: expected object with a pairs list")
        for pair in entries:
            if (
                not isinstance(pair, dict)
                or not isinstance(pair.get("track"), str)
                or not pair["track"]
            ):
                raise AuditError("map: each pair requires track, reference and candidate")
            pairs.append(
                {
                    "track": pair["track"],
                    "reference": _names(pair.get("reference")),
                    "candidate": _names(pair.get("candidate")),
                }
            )
    used = [set(), set()]
    tracks_seen = set()
    valid_pairs = []
    for pair in pairs:
        key = normalize_name(pair["track"])
        valid = bool(key) and key not in tracks_seen
        if not valid:
            errors.append(f"empty or repeated track in the map: {pair['track']}")
        tracks_seen.add(key)
        for side, files in enumerate((ref_files, cand_files)):
            for name in pair["reference" if side == 0 else "candidate"]:
                if name not in files or name in used[side]:
                    errors.append(f"missing or repeated file in the map: {name}")
                    valid = False
                used[side].add(name)
        if valid:
            valid_pairs.append(pair)
    for files, chosen, label in (
        (ref_files, used[0], "reference"),
        (cand_files, used[1], "candidate"),
    ):
        extra = sorted(set(files) - chosen)
        if extra:
            errors.append(f"files without coverage ({label}): {extra}")
    if not valid_pairs:
        errors.append("no paired track")
    masks = {}
    if mask is not None:
        raw = _json(mask).get("tracks")
        if not isinstance(raw, dict):
            raise AuditError("mask: expected tracks object with absolute intervals in samples")
        for key, regions in raw.items():
            key = normalize_name(key)
            if key not in tracks_seen or key in masks or not isinstance(regions, list):
                raise AuditError(f"mask: unknown, duplicated or invalid track: {key}")
            checked = []
            for interval in regions:
                if not isinstance(interval, list) or len(interval) != 2:
                    raise AuditError("mask: use [start, end] in absolute samples")
                a, b = (_integer(n, "mask sample") for n in interval)
                if b <= a:
                    raise AuditError("mask: end must be greater than start")
                checked.append((a, b))
            masks[key] = sorted(checked)
    override = _reference_start(reference_start, anchor) if reference_start is not None else None
    tracks = []
    for pair in valid_pairs:
        r, c = _group(ref_files, pair["reference"]), _group(cand_files, pair["candidate"])
        if r.sample_rate != rate or c.sample_rate != rate:
            raise AuditError(f"{pair['track']}: WAV/sidecar rates diverge; there is no resample")
        start = override if override is not None else r.time_reference
        if start is None:
            raise AuditError(
                f"{pair['track']}: reference without BWF TimeReference; use --reference-start"
            )
        if len(r.channels) == len(c.channels) and len(r.channels) in (1, 2):
            channel_pairs = list(zip(r.channels, c.channels))
            policy = "matching channels"
        elif len(r.channels) == 1 and len(c.channels) == 2:
            channel_pairs = [(r.channels[0], ch) for ch in c.channels]
            policy = "mono->stereo: each channel against mono"
        else:
            raise AuditError(
                f"{pair['track']}: incompatible channels {len(r.channels)}->{len(c.channels)}"
            )
        lo, hi = max(start, origin), min(start + r.frames, origin + c.frames)
        if hi <= lo:
            raise AuditError(f"{pair['track']}: no common window")
        key = normalize_name(pair["track"])
        release = []
        if clip_ranges is not None:
            if key not in clip_ranges:
                raise AuditError(f"{pair['track']}: sidecar without clip_ranges for the track")
            release = _release_regions(clip_ranges[key], release_mask) if release_mask else []
        regions = sorted(masks.get(key, []) + release)
        flags = _included(lo, hi - lo, regions)
        release_flags = _included(lo, hi - lo, release) if release else None
        release_masked = (hi - lo) - sum(release_flags) if release_flags is not None else 0
        metrics = [
            _metrics(x, y, lo - start, lo - origin, flags, max_lag) for x, y in channel_pairs
        ]
        outside = max(
            _outside_peak(r, start, lo, hi, regions), _outside_peak(c, origin, lo, hi, regions)
        )
        reasons = []
        reference_peak = max(m["reference_peak_dbfs"] for m in metrics)
        candidate_peak = max(m["candidate_peak_dbfs"] for m in metrics)
        # Sem audio ativo nao ha prova: silencio contra silencio dava residuo -inf e ok.
        if reference_peak <= tolerance_db:
            reasons.append(
                f"reference without active audio: peak {reference_peak:.6f} dBFS <= {tolerance_db} dBFS"
            )
        if candidate_peak <= tolerance_db:
            reasons.append(
                f"candidate without active audio: peak {candidate_peak:.6f} dBFS <= {tolerance_db} dBFS"
            )
        for i, m in enumerate(metrics):
            if m["lag_samples"] != 0:
                reasons.append(f"channel {i + 1}: lag {m['lag_samples']} samples")
            if m["residual_peak_dbfs"] > tolerance_db:
                reasons.append(
                    f"channel {i + 1}: residual peak {m['residual_peak_dbfs']:.6f} > {tolerance_db} dBFS"
                )
        if outside > tolerance_db:
            reasons.append(f"audio outside the common window: peak {outside:.6f} dBFS")
        lr = -math.inf
        if len(c.channels) == 2:
            lr = _db(
                max(
                    abs(c.channels[0][i + lo - origin] - c.channels[1][i + lo - origin])
                    for i, enabled in enumerate(flags)
                    if enabled
                )
            )
        tracks.append(
            {
                **pair,
                "reference_channels": len(r.channels),
                "candidate_channels": len(c.channels),
                "reference_start_samples": start,
                "candidate_start_samples": origin,
                "reference_frames": r.frames,
                "candidate_frames": c.frames,
                "common_start_samples": lo,
                "common_end_samples": hi,
                "masked_frames": len(flags) - sum(flags),
                "release_masked_frames": release_masked,
                "release_regions": [list(x) for x in release],
                "reference_peak_dbfs": reference_peak,
                "candidate_peak_dbfs": candidate_peak,
                "channel_policy": policy,
                "channels": metrics,
                "lr_peak_dbfs": lr,
                "outside_peak_dbfs": outside,
                "reasons": reasons,
                "verdict": "report" if mode == "absorb" else ("fail" if reasons else "ok"),
            }
        )
    complete = not errors
    return {
        "format_version": 1,
        "ok": mode == "keep"
        and complete
        and bool(tracks)
        and all(t["verdict"] == "ok" for t in tracks),
        "mode": mode,
        "report_only": mode == "absorb",
        "sample_rate": rate,
        "tolerance_db": tolerance_db,
        "max_lag": max_lag,
        "release_mask": release_mask,
        "alignment": "sidecar/BWF; diagnostic correlation, no realignment",
        "correlation_method": "up to 256 probes of active audio; search of all integer lags",
        "coverage": {
            "complete": complete,
            "reference_files": len(ref_files),
            "candidate_files": len(cand_files),
            "paired_tracks": len(tracks),
        },
        "errors": errors,
        "tracks": tracks,
    }


def json_safe(value):
    """JSON estrito: null representa -inf dBFS ou ganho/correlacao indefinidos."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    return value


def to_markdown(result: dict) -> str:
    def db(v):
        return "undefined" if v is None else ("-inf" if v == -math.inf else f"{v:.6f}")

    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Audio audit",
        "",
        f"Result: {'OK' if result['ok'] else 'NOT ACCEPTED'}; mode {result['mode']}",
        "Absorb is report only."
        if result["report_only"]
        else f"Provisional acceptance: lag 0, peak <= {result['tolerance_db']} dBFS and active audio above that limit on both sides.",
        (
            "Release mask: none."
            if result.get("release_mask") is None
            else f"Release mask: up to {result['release_mask']} samples after each clip end of the sidecar, never inside a clip."
        ),
        "Alignment by sidecar/BWF, no correction by correlation.",
        "Diagnostic correlation: up to 256 distributed probes; peak/RMS use all unmasked samples.",
        "JSON: null in dBFS means silence (-inf); gain/correlation may be undefined.",
        "",
        "| Track/channel | Verdict | Residual peak dBFS | Residual RMS dBFS | Reference RMS dBFS | Reference peak dBFS | Candidate peak dBFS | Gain dB | Lag samples | Probes in the lag |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for t in result["tracks"]:
        for i, m in enumerate(t["channels"]):
            lines.append(
                f"| {cell(t['track'])}/{i + 1} | {t['verdict']} | {db(m['residual_peak_dbfs'])} | "
                f"{db(m['residual_rms_dbfs'])} | {db(m['reference_rms_dbfs'])} | "
                f"{db(m['reference_peak_dbfs'])} | {db(m['candidate_peak_dbfs'])} | {db(m['gain_db'])} | "
                f"{m['lag_samples']} | {m['correlation_probes_used']}/{m['correlation_probes']} |"
            )
    lines += [""]
    for t in result["tracks"]:
        lines += [
            "",
            f"{cell(t['track'])}: {t['channel_policy']}; window [{t['common_start_samples']}, {t['common_end_samples']}); "
            f"{t['masked_frames']} masked frames, {t['release_masked_frames']} of them by the release mask; "
            f"L/R difference {db(t['lr_peak_dbfs'])} dBFS; "
            f"outside the window {db(t['outside_peak_dbfs'])} dBFS.",
        ]
        lines += [f"- {cell(reason)}" for reason in t["reasons"]]
    lines += ["", f"Coverage: {result['coverage']}"]
    lines += [f"- {cell(e)}" for e in result["errors"]]
    return "\n".join(lines) + "\n"
