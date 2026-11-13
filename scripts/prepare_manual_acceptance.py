"""Gera um pacote pequeno para os portoes manuais finais da v1."""

from __future__ import annotations

import argparse
import copy
import json
import math
import struct
import wave
from pathlib import Path

from aafton.aaf_write import write_aaf
from aafton.als_write import WriteOptions, write_session
from aafton.ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.sidecar import sidecar_path_for
from aafton.stereo import process_stereo_pairing
from aafton.timecode import TimecodeRate

SAMPLE_RATE = 48000
DURATION_SAMPLES = 2 * SAMPLE_RATE
SESSION_START = 3600 * SAMPLE_RATE
CLIP_START = SESSION_START + SAMPLE_RATE
LEFT_HZ = 440.0
RIGHT_HZ = 660.0


def _write_tone(path: Path, frequency: float) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for frame in range(DURATION_SAMPLES):
            sample = int(0.2 * 32767 * math.sin(2 * math.pi * frequency * frame / SAMPLE_RATE))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)


def _make_session(left_path: Path, right_path: Path) -> SessionIR:
    left_asset = MediaAsset(
        id="qa-left",
        filename=left_path.name,
        channels=1,
        sample_rate=SAMPLE_RATE,
        duration_samples=DURATION_SAMPLES,
        status="resolved",
        resolved_path=str(left_path.resolve()),
    )
    right_asset = MediaAsset(
        id="qa-right",
        filename=right_path.name,
        channels=1,
        sample_rate=SAMPLE_RATE,
        duration_samples=DURATION_SAMPLES,
        status="resolved",
        resolved_path=str(right_path.resolve()),
    )
    left_clip = ClipIR(
        id="qa-clip-left",
        track_id="qa-track-left",
        channel_index=0,
        event_index=0,
        name="QA_PAN_L.wav",
        media_id=left_asset.id,
        timeline_start_samples=CLIP_START,
        timeline_end_samples=CLIP_START + DURATION_SAMPLES,
        source_offset_samples=0,
    )
    right_clip = ClipIR(
        id="qa-clip-right",
        track_id="qa-track-right",
        channel_index=0,
        event_index=0,
        name="QA_PAN_R.wav",
        media_id=right_asset.id,
        timeline_start_samples=CLIP_START,
        timeline_end_samples=CLIP_START + DURATION_SAMPLES,
        source_offset_samples=0,
    )
    return SessionIR(
        name="aafton v1 manual acceptance",
        sample_rate=SAMPLE_RATE,
        timecode_rate=TimecodeRate(25, 1, False),
        start_timecode_samples=SESSION_START,
        tracks=[
            TrackIR(
                id="qa-track-left",
                source_index=0,
                name="QA_PAN_L",
                kind="audio",
                channel_count=1,
                pan=-1.0,
                clips=[left_clip],
            ),
            TrackIR(
                id="qa-track-right",
                source_index=1,
                name="QA_PAN_R",
                kind="audio",
                channel_count=1,
                pan=1.0,
                clips=[right_clip],
            ),
        ],
        media=[left_asset, right_asset],
        markers=[
            MarkerIR(
                id="qa-marker",
                name="QA AUDIO START 01:00:01:00",
                position_samples=CLIP_START,
                comment="440 Hz esquerda; 660 Hz direita",
            )
        ],
        source_application="aafton manual acceptance generator",
    )


def _report_counts(report: Report) -> dict[str, int]:
    return {
        "errors": report.count("error"),
        "warnings": report.count("warning"),
        "info": report.count("info"),
    }


def _require_complete(label: str, report: Report) -> None:
    if report.incomplete:
        raise RuntimeError(f"{label} gerou erros:\n{report.to_json()}")


def prepare_bundle(output_dir: Path, template_path: Path) -> dict:
    output_dir = output_dir.resolve()
    template_path = template_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_dir = output_dir / "Samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    left_path = samples_dir / "QA_PAN_L.wav"
    right_path = samples_dir / "QA_PAN_R.wav"
    _write_tone(left_path, LEFT_HZ)
    _write_tone(right_path, RIGHT_HZ)

    split_session = _make_session(left_path, right_path)
    split_als = output_dir / "pan-split.als"
    split_report = Report(strict=False)
    write_session(
        split_session,
        template_path,
        split_als,
        WriteOptions(
            sample_rate=SAMPLE_RATE,
            bpm=60.0,
            live_origin_samples=SESSION_START,
            stereo="split",
        ),
        split_report,
    )
    _require_complete("pan-split.als", split_report)

    pair_session = copy.deepcopy(split_session)
    pair_report = Report(strict=False)
    process_stereo_pairing(
        pair_session,
        "pair",
        pair_report,
        samples_dir,
        dry_run=False,
    )
    if (
        len(pair_session.tracks) != 1
        or pair_session.tracks[0].channel_count != 2
        or pair_session.tracks[0].pan != 0.0
    ):
        raise RuntimeError("o pacote de QA nao formou uma track estereo central")
    _require_complete("pareamento", pair_report)

    baseline_aaf = output_dir / "pan-pair-baseline.aaf"
    aaf_report = Report(strict=False)
    write_aaf(pair_session, baseline_aaf, aaf_report)
    _require_complete("pan-pair-baseline.aaf", aaf_report)

    pair_session.source_path = str(baseline_aaf.resolve())
    pair_als = output_dir / "pan-pair.als"
    write_session(
        pair_session,
        template_path,
        pair_als,
        WriteOptions(
            sample_rate=SAMPLE_RATE,
            bpm=60.0,
            live_origin_samples=SESSION_START,
            stereo="pair",
        ),
        pair_report,
    )
    _require_complete("pan-pair.als", pair_report)

    split_report_path = output_dir / "pan-split-report.json"
    pair_report_path = output_dir / "pan-pair-report.json"
    aaf_report_path = output_dir / "pan-pair-baseline-report.json"
    split_report_path.write_text(split_report.to_json(), encoding="utf-8")
    pair_report_path.write_text(pair_report.to_json(), encoding="utf-8")
    aaf_report_path.write_text(aaf_report.to_json(), encoding="utf-8")

    manifest_path = output_dir / "manifest.json"
    manifest = {
        "schema_version": 1,
        "files": {
            "split_als": str(split_als.resolve()),
            "split_sidecar": str(sidecar_path_for(split_als).resolve()),
            "pair_als": str(pair_als.resolve()),
            "pair_sidecar": str(sidecar_path_for(pair_als).resolve()),
            "baseline_aaf": str(baseline_aaf.resolve()),
            "left_wav": str(left_path.resolve()),
            "right_wav": str(right_path.resolve()),
            "stereo_wav": str(
                Path(
                    pair_session.media_by_id(pair_session.tracks[0].clips[0].media_id).resolved_path
                ).resolve()
            ),
            "split_report": str(split_report_path.resolve()),
            "pair_report": str(pair_report_path.resolve()),
            "aaf_report": str(aaf_report_path.resolve()),
            "manifest": str(manifest_path.resolve()),
        },
        "expected": {
            "sample_rate": SAMPLE_RATE,
            "timecode_rate": "25 fps non-drop",
            "session_start_timecode": "01:00:00:00",
            "clip_start_timecode": "01:00:01:00",
            "duration_samples": DURATION_SAMPLES,
            "duration_seconds": 2.0,
            "split_pan": [-1.0, 1.0],
            "pair_pan": 0.0,
            "left_frequency_hz": LEFT_HZ,
            "right_frequency_hz": RIGHT_HZ,
            "warped": False,
        },
        "reports": {
            "split": _report_counts(split_report),
            "pair": _report_counts(pair_report),
            "aaf": _report_counts(aaf_report),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepara os arquivos pequenos dos portoes manuais da v1."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("out/manual-acceptance-v1"),
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("templates/live12.als"),
    )
    args = parser.parse_args(argv)
    manifest = prepare_bundle(args.output_dir, args.template)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
