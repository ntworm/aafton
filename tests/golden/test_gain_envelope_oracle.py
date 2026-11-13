"""Golden Oracle Integration Test for Gain Automation Envelope.

Verifies that the points written to the ALS match the exact points
present in the AAF's OperationGroups (MonoAudioGain), by computing them
independently.
"""

import gzip
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.als_write import write_session, WriteOptions, to_beats
from aafton.report import Report

AAF_PATH = Path("tests/fixtures/aaf/SPOT_AAF_TESTE.aaf")


def extract_oracle_gain_points(
    aaf_path: Path, track_name: str, sample_rate: int = 48000
) -> list[tuple[int, float]]:
    """Extracts absolute sample positions and gain values directly from aaf2.

    Computes: start_absolute + time_norm * length_samples.
    Deduplicates only if BOTH absolute sample and value are exactly identical.
    """
    points = []
    with aaf2.open(str(aaf_path)) as f:
        comp = next(
            (mob for mob in f.content.mobs if isinstance(mob, aaf2.mobs.CompositionMob)), None
        )
        assert comp is not None, "CompositionMob not found"

        track = None
        for slot in comp.slots:
            if slot.segment.media_kind != "Sound":
                continue
            if track_name in (slot.name, slot.segment.name):
                track = slot
                break
        assert track is not None, f"Track {track_name} not found"

        inicio_absoluto = 0
        sequence = track.segment

        def walk(segment, cursor_samples):
            if isinstance(segment, aaf2.components.Sequence):
                for c in segment.components:
                    cursor_samples = walk(c, cursor_samples)
                return cursor_samples

            edit_rate = (
                float(track.edit_rate.numerator) / float(track.edit_rate.denominator)
                if hasattr(track, "edit_rate") and track.edit_rate
                else 25.0
            )
            length_samples = int(
                Fraction(segment.length) * Fraction(sample_rate) / Fraction(edit_rate)
            )

            if isinstance(segment, aaf2.components.OperationGroup):
                # Get the gain param
                params = segment.parameters
                for p in params:
                    nome = str(getattr(p, "name", "") or "").lower()
                    if not nome or nome in (
                        "amplitude",
                        "amplitude multiplier",
                        "level",
                        "gain",
                        "audiogain",
                    ):
                        point_list = getattr(p, "point_list", getattr(p, "pointlist", None))
                        if point_list is None and hasattr(p, "get"):
                            prop = p.get("PointList")
                            if prop:
                                point_list = prop.value if hasattr(prop, "value") else prop
                        if point_list is None and hasattr(p, "__getitem__"):
                            try:
                                prop = p["PointList"]
                                point_list = prop.value if hasattr(prop, "value") else prop
                            except Exception:
                                pass

                        if point_list:
                            pts = []
                            for cp in point_list:
                                t = getattr(cp, "time", getattr(cp, "Time", None))
                                if t is None and hasattr(cp, "__getitem__"):
                                    try:
                                        t = cp["Time"]
                                    except Exception:
                                        pass
                                    if t is None:
                                        try:
                                            t = cp["time"]
                                        except Exception:
                                            pass

                                v = getattr(cp, "value", getattr(cp, "Value", None))
                                if v is None and hasattr(cp, "__getitem__"):
                                    try:
                                        v = cp["Value"]
                                    except Exception:
                                        pass
                                    if v is None:
                                        try:
                                            v = cp["value"]
                                        except Exception:
                                            pass

                                if hasattr(v, "value"):
                                    v = v.value

                                if t is not None and v is not None:
                                    pts.append((float(t), float(v)))

                            if not pts:
                                pts = [(0.0, 1.0), (1.0, 1.0)]

                            seg_start = inicio_absoluto + cursor_samples
                            for t_norm, val in pts:
                                abs_s = seg_start + int(round(t_norm * length_samples))
                                points.append((abs_s, val))
            return cursor_samples + length_samples

        walk(sequence, 0)

    # Deduplicate only exact matches (time and value)
    if not points:
        return []

    deduped = [points[0]]
    for abs_s, val in points[1:]:
        prev_s, prev_v = deduped[-1]
        if abs_s == prev_s and math.isclose(val, prev_v, rel_tol=1e-9):
            continue  # Exact duplicate
        if abs_s == prev_s:
            deduped[-1] = (abs_s, val)  # Vertical jump, keep the latter
        else:
            deduped.append((abs_s, val))

    return deduped


def test_gain_envelope_oracle_spot_audio_3():
    """Verifies Audio 3 gain envelope points against the AAF oracle."""
    if not AAF_PATH.exists():
        pytest.skip(f"Fixture {AAF_PATH} not found")

    # 1. Oracle: extract directly from AAF
    oracle_points = extract_oracle_gain_points(AAF_PATH, "Audio 3", sample_rate=48000)
    assert len(oracle_points) == 16

    expected_starts = [172819200, 172832640, 172838400, 172846080]
    actual_starts = [p[0] for p in oracle_points[:4]]
    assert actual_starts == expected_starts, f"Expected {expected_starts}, got {actual_starts}"

    # 2. Conversion: run read_aaf and write_session
    report = Report(strict=False)
    session = read_aaf(AAF_PATH, report)

    with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as f:
        tmp = f.name

    try:
        options = WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=0)
        write_session(session, "templates/live12.als", tmp, options, report)

        # 3. Parse ALS and extract events
        with gzip.open(tmp, "rb") as f:
            root = ET.fromstring(f.read())

        als_events = []
        for track in root.iter("AudioTrack"):
            name_el = track.find(".//Name/EffectiveName")
            if name_el is not None and "Audio 3" in name_el.get("Value", ""):
                # Get FloatEvents
                for env in track.iter("AutomationEnvelope"):
                    events = env.find(".//Events")
                    if events is not None:
                        for ev in events.iter("FloatEvent"):
                            als_events.append((float(ev.get("Time")), float(ev.get("Value"))))
                break  # Only check one side of the stereo pair

        # 4. Compare
        # The first event is the sentinel, discard it for the value comparison, or just check its value
        assert als_events[0][0] == -63072000
        assert math.isclose(als_events[0][1], oracle_points[0][1], rel_tol=1e-5)

        als_real_events = als_events[1:]
        assert len(als_real_events) == len(oracle_points)

        for (als_t_beats, als_v), (orcl_s, orcl_v) in zip(als_real_events, oracle_points):
            # Convert oracle sample to beats to compare
            # timecode_rate is passed as sample_rate=48000, bpm=60.0 -> 1 beat = 48000 samples
            orcl_beats = to_beats(orcl_s, 0, 48000, 60.0)

            assert math.isclose(als_t_beats, orcl_beats, rel_tol=1e-5), (
                f"Time mismatch: ALS {als_t_beats} != Oracle {orcl_beats}"
            )
            assert math.isclose(als_v, orcl_v, rel_tol=1e-5), (
                f"Value mismatch at {orcl_s}: ALS {als_v} != Oracle {orcl_v}"
            )

    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
