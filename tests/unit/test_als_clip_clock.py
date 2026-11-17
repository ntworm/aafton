"""D12a: valores salvos pelo Live calibram o relogio, sem usar o leitor ALS."""

import json
import math
import random
from pathlib import Path

import pytest

from aafton.als_paths import find_prototype
from aafton.als_write import WriteOptions, build_audio_clip
from aafton.als_xml import IdAllocator, classify_id_tags, load_als
from aafton.ir import ClipIR, MediaAsset
from aafton.report import Report


PROFILE = json.loads(
    Path("tests/fixtures/als/live_clip_clock_profile.json").read_text(encoding="utf-8")
)
TICKS = PROFILE["inferred_ticks_per_beat"]
SR = PROFILE["sample_rate"]


def _saved_times(start, end):
    """Modelo independente: inicio e comprimento arredondados pelo Live."""
    saved_start = round(start * TICKS) / TICKS
    return saved_start, saved_start + round((end - start) * TICKS) / TICKS


@pytest.mark.parametrize("row", PROFILE["records"])
def test_modelo_confere_com_saveas_humano_em_dois_tempos(row):
    start, end = _saved_times(float(row["input_start"]), float(row["input_end"]))
    assert start == float(row["saved_start"])
    assert abs(end - float(row["saved_end"])) < 2e-15


def _node(start, duration, bpm, sr=SR, *, video=False):
    root = load_als("templates/live12.als")
    positional, unique = classify_id_tags(root)
    clip = ClipIR(
        id="c",
        track_id="t",
        channel_index=0,
        event_index=0,
        name="mono",
        media_id="m",
        timeline_start_samples=start,
        timeline_end_samples=start + duration,
        source_offset_samples=617,
    )
    asset = MediaAsset(
        id="m",
        filename="mono.wav",
        channels=1,
        sample_rate=sr,
        duration_samples=2**31,
        status="resolved",
        resolved_path="mono.wav",
    )
    return build_audio_clip(
        clip,
        asset,
        find_prototype(root, "AudioClip"),
        IdAllocator.from_root(root),
        positional,
        unique,
        WriteOptions(sample_rate=sr, bpm=bpm),
        Report(),
        is_video=video,
    )


def _assert_playback(node, start, duration, bpm, sr):
    written_start = float(node.find("CurrentStart").get("Value"))
    written_end = float(node.find("CurrentEnd").get("Value"))
    assert float(node.get("Time")) == written_start
    saved_start, saved_end = _saved_times(written_start, written_end)
    for value, expected in [(saved_start, start), (saved_end, start + duration)]:
        samples = value * 60.0 / bpm * sr
        assert math.ceil(samples) == expected
        assert round(samples) == expected
    assert round((saved_end - saved_start) * 60.0 / bpm * sr) == duration
    # LoopStart de clip sem warp e segundo do arquivo, em qualquer tempo.
    assert round(float(node.find("Loop/LoopStart").get("Value")) * sr) == 617


@pytest.mark.parametrize("bpm", (60.0, 97.0, 120.0))
@pytest.mark.parametrize(
    "start,duration",
    [(48007, 48000), (48000, 101), (96013, 24000), (0, 1), (32005, 101), (162012, 101)],
)
def test_clip_chega_na_amostra_certa_apos_relogio_do_live(start, duration, bpm):
    _assert_playback(_node(start, duration, bpm), start, duration, bpm, SR)


def test_limites_longos_e_tempos_distintos_preservam_inicio_fim_e_comprimento():
    rng = random.Random(20261014)
    root = load_als("templates/live12.als")
    positional, unique = classify_id_tags(root)
    prototype = find_prototype(root, "AudioClip")
    for bpm in (20.0, 60.0, 97.0, 120.0, 133.7, 999.0):
        for sr in (44100, 48000, 96000, 192000):
            for _ in range(20):
                start = rng.randrange(0, 2**31 - sr)
                duration = rng.randrange(1, sr)
                clip = ClipIR(
                    id="c",
                    track_id="t",
                    channel_index=0,
                    event_index=0,
                    name="mono",
                    media_id="m",
                    timeline_start_samples=start,
                    timeline_end_samples=start + duration,
                    source_offset_samples=617,
                )
                asset = MediaAsset(
                    id="m",
                    filename="mono.wav",
                    channels=1,
                    sample_rate=sr,
                    duration_samples=2**31,
                    status="resolved",
                    resolved_path="mono.wav",
                )
                node = build_audio_clip(
                    clip,
                    asset,
                    prototype,
                    IdAllocator.from_root(root),
                    positional,
                    unique,
                    WriteOptions(sample_rate=sr, bpm=bpm),
                    Report(),
                )
                _assert_playback(node, start, duration, bpm, sr)


def test_video_mantem_tempos_em_precisao_double():
    node = _node(48007, 48000, 60.0, video=True)
    assert node.get("Time") == "1.0001458333333333"
    assert float(node.find("Loop/LoopStart").get("Value")) * SR == 617.0
