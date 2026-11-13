"""Gera calibracao AAF e renders esperados pela especificacao, sem IR."""

from __future__ import annotations

import argparse
import json
import math
import random
import struct
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from tests.aaf_factory import build_calibration_aaf  # noqa: E402

RATE = 48000
ORIGIN = 3600 * RATE
FRAMES = 4 * RATE
SEED = 20261013


def _source(path: Path, signals: list[list[float]]) -> list[list[float]]:
    """Escreve PCM16 e devolve a quantizacao calculada, sem reler o WAV."""
    quantized = [[round(x * 32768) / 32768 for x in channel] for channel in signals]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(len(signals))
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(
            b"".join(
                struct.pack("<h", round(x * 32768)) for frame in zip(*quantized) for x in frame
            )
        )
    return quantized


def write_float_bwf(path: Path, signals: list[list[float]], start: int) -> None:
    """Render esperado float32 com TimeReference conhecido da especificacao."""
    count = len(signals)
    fmt = struct.pack("<HHIIHH", 3, count, RATE, RATE * count * 4, count * 4, 32)
    bext = bytearray(602)
    struct.pack_into("<Q", bext, 338, start)
    payload = b"".join(struct.pack("<f", x) for frame in zip(*signals) for x in frame)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"bext" + struct.pack("<I", len(bext)) + bext
    body += b"data" + struct.pack("<I", len(payload)) + payload
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body)


def make_calibration_session(output_dir: Path) -> dict:
    output_dir = output_dir.resolve()
    samples, expected = output_dir / "Samples", output_dir / "expected"
    samples.mkdir(parents=True, exist_ok=True)
    expected.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    mono = [
        0.15 * math.sin(2 * math.pi * 437 * i / RATE) + rng.uniform(-0.04, 0.04)
        for i in range(3 * RATE)
    ]
    mono[17] = 0.75  # transiente diagnostico
    right = [
        0.13 * math.sin(2 * math.pi * 683 * i / RATE) + rng.uniform(-0.03, 0.03)
        for i in range(3 * RATE)
    ]
    mono_path, stereo_path = samples / "mono.wav", samples / "interleaved.wav"
    mono = _source(mono_path, [mono])[0]
    stereo = _source(stereo_path, [mono, right])
    left_path, right_path = samples / "split_L.wav", samples / "split_R.wav"
    split = [_source(left_path, [stereo[0]])[0], _source(right_path, [stereo[1]])[0]]

    def clip(path, start=RATE, duration=RATE, offset=0, gain=None, **extra):
        return {
            "path": str(path),
            "start": start,
            "duration": duration,
            "offset": offset,
            "gain": gain or [1, 1],
            **extra,
        }

    specs = [
        {"name": "CAL_MONO", "channels": 1, "clips": [clip(mono_path, start=RATE + 7)]},
        {"name": "CAL_OFFSET", "channels": 1, "clips": [clip(mono_path, offset=617)]},
        {"name": "CAL_GAIN", "channels": 1, "clips": [clip(mono_path, gain=[501187, 1000000])]},
        {"name": "CAL_INTERLEAVED", "channels": 2, "clips": [clip(stereo_path)]},
        {
            "name": "CAL_SPLIT",
            "channels": 2,
            "clips": [clip(left_path), clip(right_path)],
            "split": True,
        },
        {
            "name": "CAL_FADES",
            "channels": 1,
            "clips": [clip(mono_path)],
            "fade_in": 4800,
            "fade_out": 4800,
        },
        {
            "name": "CAL_TWO_CLIPS",
            "channels": 1,
            "clips": [
                clip(mono_path, duration=12000),
                clip(mono_path, start=2 * RATE + 13, duration=24000, offset=RATE + 211),
            ],
        },
    ]
    sources = {
        str(mono_path): [mono],
        str(stereo_path): stereo,
        str(left_path): [split[0]],
        str(right_path): [split[1]],
    }
    tracks_for_aaf, masks = [], {}
    for spec in specs:
        render = [[0.0] * FRAMES for _ in range(spec["channels"])]
        for clip_index, item in enumerate(spec["clips"]):
            data = sources[item["path"]]
            gain = item["gain"][0] / item["gain"][1]
            for channel, values in enumerate(data):
                target = clip_index if spec.get("split") else channel
                for i in range(item["duration"]):
                    envelope = 1.0
                    if spec.get("fade_in") and i < spec["fade_in"]:
                        envelope = i / spec["fade_in"]
                    if spec.get("fade_out") and i >= item["duration"] - spec["fade_out"]:
                        envelope = (item["duration"] - 1 - i) / spec["fade_out"]
                    render[target][item["start"] + i] = values[item["offset"] + i] * gain * envelope
        write_float_bwf(expected / f"{spec['name']}.wav", render, ORIGIN)
        if spec.get("split"):
            for index, item in enumerate(spec["clips"]):
                tracks_for_aaf.append(
                    {"name": spec["name"] + ("_L" if index == 0 else "_R"), "clips": [item]}
                )
        elif spec.get("fade_in"):
            start, duration = RATE, RATE
            fi, fo = spec["fade_in"], spec["fade_out"]
            fade_in, fade_out = samples / "Fade 1.wav", samples / "Fade 2.wav"
            _source(fade_in, [render[0][start : start + fi]])
            _source(fade_out, [render[0][start + duration - fo : start + duration]])
            tracks_for_aaf.append(
                {
                    "name": spec["name"],
                    "clips": [
                        clip(fade_in, start=start, duration=fi, mob_name="Fade 1"),
                        clip(mono_path, start=start + fi, duration=duration - fi - fo, offset=fi),
                        clip(fade_out, start=start + duration - fo, duration=fo, mob_name="Fade 2"),
                    ],
                }
            )
            masks[spec["name"]] = [
                [ORIGIN + start, ORIGIN + start + fi],
                [ORIGIN + start + duration - fo, ORIGIN + start + duration],
            ]
        else:
            tracks_for_aaf.append(spec)
    aaf = build_calibration_aaf(output_dir / "calibracao.aaf", tracks_for_aaf, frames=FRAMES)
    specification = {
        "seed": SEED,
        "sample_rate": RATE,
        "origin_samples": ORIGIN,
        "frames": FRAMES,
        "source_format": "PCM16; render esperado calculado da quantizacao da spec",
        "fade_curve": "rampa linear; forma do Live sera medida no P03",
        "tracks": specs,
    }
    spec_path = output_dir / "spec.json"
    spec_path.write_text(
        json.dumps(specification, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    mask_path = output_dir / "mask.json"
    mask_path.write_text(json.dumps({"tracks": masks}, indent=2) + "\n", encoding="utf-8")
    return {
        "aaf": str(aaf),
        "expected": str(expected),
        "samples": str(samples),
        "spec": str(spec_path),
        "mask": str(mask_path),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(make_calibration_session(args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
