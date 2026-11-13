"""Testes unitarios para a conversao de automacao de ganho em envelope de volume."""

import math
import pytest
import xml.etree.ElementTree as ET
import gzip

from aafton.aaf_read import _read_point_list, _has_varying_gain, _extract_gain_curve
from aafton.als_write import (
    build_gain_envelope,
    VOLUME_GAIN_MAX,
    SENTINEL_TIME,
    WriteOptions,
)
from aafton.als_xml import IdAllocator, classify_id_tags, unique_ids, validate_document
from aafton.report import Report
from aafton.timecode import TimecodeRate


# --- Helpers ---


class FakeParam:
    """Simula um VaryingValue do AAF2 com PointList como lista de tuplas."""

    def __init__(self, points: list[tuple[float, float]]):
        self.name = "amplitude"
        self._points = points
        self.point_list = points

    def __contains__(self, item):
        return item == "PointList"

    def get(self, key, default=None):
        if key == "PointList":
            return self._points
        return default


class ConstantParam:
    """Simula um ConstantValue."""

    def __init__(self, val: float):
        self.name = "amplitude"
        self.value = val


def make_raw_items(segments: list[tuple[str, int, int, list[tuple[float, float]]]]) -> list[dict]:
    """Cria raw_items sinteticos. segments: [(kind, cursor, length, points)]."""
    items = []
    for kind, cursor, length, pts in segments:
        params = [FakeParam(pts)] if pts else []
        items.append(
            {
                "kind": kind,
                "cursor_samples": cursor,
                "length_samples": length,
                "parameters": params,
                "clip_assets": [],
            }
        )
    return items


# --- Testes de _read_point_list ---


def test_read_point_list_constant():
    param = ConstantParam(0.9333)
    pts = _read_point_list([param])
    assert len(pts) == 2
    assert all(math.isclose(v, 0.9333) for _, v in pts)


def test_read_point_list_varying():
    pts_in = [(0.0, 0.195), (0.7, 0.195), (1.0, 0.9333)]
    param = FakeParam(pts_in)
    pts = _read_point_list([param])
    assert len(pts) == 3
    assert math.isclose(pts[0][1], 0.195)
    assert math.isclose(pts[2][1], 0.9333)


def test_has_varying_gain_false_for_constant():
    param = ConstantParam(0.5)
    assert not _has_varying_gain([param])


def test_has_varying_gain_true_for_ramp():
    pts_in = [(0.0, 0.195), (0.7, 0.195), (1.0, 0.9333)]
    param = FakeParam(pts_in)
    assert _has_varying_gain([param])


# --- Testes de _extract_gain_curve ---


def test_extract_gain_curve_synthetic():
    """Tres segmentos encadeados. Verifica pontos em samples absolutos."""
    # Segmento 1: FADE, cursor=0, len=1000, rampa 0.2->0.9
    # Segmento 2: CLIP, cursor=1000, len=500, const 0.9
    # Segmento 3: FADE, cursor=1500, len=800, rampa 0.9->0.3
    inicio_absoluto = 10000
    segments = [
        ("FADE", 0, 1000, [(0.0, 0.2), (1.0, 0.9)]),
        ("CLIP", 1000, 500, [(0.0, 0.9), (1.0, 0.9)]),
        ("FADE", 1500, 800, [(0.0, 0.9), (1.0, 0.3)]),
    ]
    raw_items = make_raw_items(segments)
    curve = _extract_gain_curve(raw_items, inicio_absoluto)

    # Pontos esperados em samples absolutos:
    # seg1: 10000+0=10000 (v=0.2), 10000+1000=11000 (v=0.9)
    # seg2: 11000+0=11000 (v=0.9) -> duplicado de 11000, seg2 end: 11000+500=11500 (v=0.9) -> igual
    # seg3: 11500+0=11500 (v=0.9) -> duplicado, 11500+800=12300 (v=0.3)
    # Depois de deduplicar (mesmo sample ou mesmo valor):
    # 10000->0.2, 11000->0.9, 12300->0.3
    assert any(s == 10000 and math.isclose(v, 0.2) for s, v in curve)
    assert any(s == 11000 and math.isclose(v, 0.9) for s, v in curve)
    assert any(s == 12300 and math.isclose(v, 0.3) for s, v in curve)

    # Nao deve ter o ponto duplicado na mesma amostra (11000 repetido)
    samples = [s for s, _ in curve]
    assert len(samples) == len(set(samples))


def test_extract_gain_curve_deduplication():
    """Pontos duplicados no mesmo sample sao eliminados, mas plateaus sao preservados."""
    inicio_absoluto = 0
    segments = [
        ("CLIP", 0, 100, [(0.0, 0.5), (1.0, 0.5)]),
        ("CLIP", 100, 100, [(0.0, 0.5), (1.0, 0.7)]),
    ]
    raw_items = make_raw_items(segments)
    curve = _extract_gain_curve(raw_items, inicio_absoluto)

    # Valores: 0->0.5, 100->0.5 (dup of previous 100->0.5 entry mas mesma amostra, descartado), 200->0.7
    # Com a preservacao de plateau, a curva deve ser:
    # 0 -> 0.5
    # 100 -> 0.5 (seg1 final e seg2 inicio caem aqui. O primeiro 100 fica, o segundo 100 atualiza)
    # 200 -> 0.7
    expected = [(0, 0.5), (100, 0.5), (200, 0.7)]
    assert len(curve) == len(expected)
    for (s, v), (exp_s, exp_v) in zip(curve, expected):
        assert s == exp_s
        assert math.isclose(v, exp_v)


def test_extract_gain_curve_first_event_sentinel():
    """O primeiro evento usa Time = SENTINEL_TIME."""
    # (Testado via build_gain_envelope)
    gain_points = [(1000, 0.5), (2000, 0.8)]
    options = WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=0)
    report = Report(strict=False)

    with gzip.open("templates/live12.als", "rb") as f:
        root = ET.fromstring(f.read())
    allocator = IdAllocator.from_root(root)
    posicionais, _ = classify_id_tags(root)
    unicos = unique_ids(root)

    # Encontrar prototipo FloatEvent
    prototype = None
    for ae in root.iter("AutomationEnvelope"):
        events = ae.find(".//Events")
        if events is not None and any(ev.tag == "FloatEvent" for ev in events):
            prototype = ae
            break
    assert prototype is not None

    env = build_gain_envelope(
        gain_points,
        "99999",
        prototype,
        allocator,
        posicionais,
        unicos,
        options,
        "TestTrack",
        report,
    )
    events = env.findall(".//FloatEvent")
    assert len(events) >= 1
    assert events[0].get("Time") == str(SENTINEL_TIME)


def test_gain_envelope_sample_volume_1():
    """Numa track com rampa, todos os SampleVolume dos clips saem em 1.0."""
    import gzip
    from aafton.als_write import write_session, WriteOptions
    from aafton.ir import SessionIR, TrackIR, ClipIR, MediaAsset

    with gzip.open("templates/live12.als", "rb") as f:
        ET.fromstring(f.read())

    session = SessionIR(
        name="test",
        sample_rate=48000,
        start_timecode_samples=0,
        timecode_rate=TimecodeRate(25, 1),
    )
    media = MediaAsset(
        id="m1",
        filename="test.wav",
        status="resolved",
        resolved_path="/fake/test.wav",
        sample_rate=48000,
        duration_samples=100000,
    )
    session.media.append(media)
    track = TrackIR(
        id="t1",
        source_index=1,
        name="RampTrack",
        kind="audio",
        channel_count=1,
        # Simular gain_points para a track
        gain_points=[(1000, 0.5), (5000, 0.9)],
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip1",
        media_id="m1",
        timeline_start_samples=1000,
        timeline_end_samples=5000,
        gain_linear=1.0,  # ja resetado pelo aaf_read
    )
    track.clips.append(clip)
    session.tracks.append(track)

    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as f:
        tmp = f.name
    try:
        report = Report(strict=False)
        write_session(
            session,
            "templates/live12.als",
            tmp,
            WriteOptions(sample_rate=48000, live_origin_samples=0),
            report,
        )
        with gzip.open(tmp, "rb") as f:
            out = ET.fromstring(f.read())
        for sv in out.iter("SampleVolume"):
            assert float(sv.get("Value", "1")) == pytest.approx(1.0), (
                f"SampleVolume should be 1.0 but is {sv.get('Value')}"
            )
    finally:
        os.unlink(tmp)


def test_gain_envelope_no_envelope_without_ramp():
    """Numa track sem rampa, nenhum envelope e escrito e SampleVolume fica com o valor original."""
    from aafton.als_write import write_session, WriteOptions
    from aafton.ir import SessionIR, TrackIR, ClipIR, MediaAsset

    session = SessionIR(
        name="test",
        sample_rate=48000,
        start_timecode_samples=0,
        timecode_rate=TimecodeRate(25, 1),
    )
    media = MediaAsset(
        id="m1",
        filename="test.wav",
        status="resolved",
        resolved_path="/fake/test.wav",
        sample_rate=48000,
        duration_samples=100000,
    )
    session.media.append(media)
    track = TrackIR(
        id="t1",
        source_index=1,
        name="ConstantTrack",
        kind="audio",
        channel_count=1,
        gain_points=[],  # Sem curva
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip1",
        media_id="m1",
        timeline_start_samples=1000,
        timeline_end_samples=5000,
        gain_linear=0.7244,  # Ganho constante do clip
    )
    track.clips.append(clip)
    session.tracks.append(track)

    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as f:
        tmp = f.name
    try:
        report = Report(strict=False)
        write_session(
            session,
            "templates/live12.als",
            tmp,
            WriteOptions(sample_rate=48000, live_origin_samples=0),
            report,
        )
        with gzip.open(tmp, "rb") as f:
            out = ET.fromstring(f.read())
        # Nenhum envelope nos AudioTracks
        for track_el in out.iter("AudioTrack"):
            env = track_el.find(".//AutomationEnvelopes/Envelopes")
            assert env is None or len(env) == 0, "Track sem ramp nao deve ter envelope"
        # SampleVolume deve ser o valor original
        for sv in out.iter("SampleVolume"):
            assert float(sv.get("Value", "0")) == pytest.approx(0.7244, abs=0.0001)
    finally:
        os.unlink(tmp)


def test_gain_envelope_clamped():
    """Pontos acima de VOLUME_GAIN_MAX sao cortados e emitem GAIN_ENVELOPE_CLAMPED."""
    gain_points = [(1000, 0.5), (2000, 2.5)]  # 2.5 > 1.99526238
    options = WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=0)
    report = Report(strict=False)

    with gzip.open("templates/live12.als", "rb") as f:
        root = ET.fromstring(f.read())
    allocator = IdAllocator.from_root(root)
    posicionais, _ = classify_id_tags(root)
    unicos = unique_ids(root)

    prototype = None
    for ae in root.iter("AutomationEnvelope"):
        events = ae.find(".//Events")
        if events is not None and any(ev.tag == "FloatEvent" for ev in events):
            prototype = ae
            break

    env = build_gain_envelope(
        gain_points,
        "99999",
        prototype,
        allocator,
        posicionais,
        unicos,
        options,
        "TestTrack",
        report,
    )
    events = env.findall(".//FloatEvent")
    # O ponto de 2.5 deve ter sido cortado para VOLUME_GAIN_MAX
    values = [float(ev.get("Value", "0")) for ev in events]
    assert all(v <= VOLUME_GAIN_MAX + 1e-9 for v in values), f"Clamping failed: {values}"
    # Deve ter emitido GAIN_ENVELOPE_CLAMPED
    clamped = [i for i in report.issues if i.code == "GAIN_ENVELOPE_CLAMPED"]
    assert len(clamped) == 1


def test_gain_envelope_pointee_ids_differ():
    """O PointeeId do envelope de cada track deve ser diferente."""
    from aafton.als_write import write_session, WriteOptions
    from aafton.ir import SessionIR, TrackIR, ClipIR, MediaAsset

    session = SessionIR(
        name="test",
        sample_rate=48000,
        start_timecode_samples=0,
        timecode_rate=TimecodeRate(25, 1),
    )
    media = MediaAsset(
        id="m1",
        filename="test.wav",
        status="resolved",
        resolved_path="/fake/test.wav",
        sample_rate=48000,
        duration_samples=100000,
    )
    session.media.append(media)

    for i, track_name in enumerate(["TrackA", "TrackB"]):
        track = TrackIR(
            id=f"t{i}",
            source_index=i + 1,
            name=track_name,
            kind="audio",
            channel_count=1,
            gain_points=[(1000, 0.5), (2000, 0.8)],
        )
        clip = ClipIR(
            id=f"c{i}",
            track_id=f"t{i}",
            channel_index=0,
            event_index=0,
            name="clip",
            media_id="m1",
            timeline_start_samples=1000,
            timeline_end_samples=5000,
        )
        track.clips.append(clip)
        session.tracks.append(track)

    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as f:
        tmp = f.name
    try:
        report = Report(strict=False)
        write_session(
            session,
            "templates/live12.als",
            tmp,
            WriteOptions(sample_rate=48000, live_origin_samples=0),
            report,
        )
        with gzip.open(tmp, "rb") as f:
            out = ET.fromstring(f.read())

        pids = []
        for track_el in out.iter("AudioTrack"):
            env = track_el.find(".//AutomationEnvelopes/Envelopes")
            if env is not None and len(env):
                for ae in env:
                    pid = ae.find("EnvelopeTarget/PointeeId")
                    if pid is not None:
                        pids.append(pid.get("Value"))

        assert len(pids) == 2, f"Esperava 2 envelopes, achei {len(pids)}"
        assert len(set(pids)) == 2, f"PointeeIds nao sao unicos: {pids}"
    finally:
        os.unlink(tmp)


def test_gain_envelope_validate_document():
    """validate_document aprova o documento gerado sem Id repetido."""
    from aafton.als_write import write_session, WriteOptions
    from aafton.als_xml import classify_id_tags
    from aafton.ir import SessionIR, TrackIR, ClipIR, MediaAsset

    session = SessionIR(
        name="test", sample_rate=48000, start_timecode_samples=0, timecode_rate=TimecodeRate(25, 1)
    )
    media = MediaAsset(
        id="m1",
        filename="test.wav",
        status="resolved",
        resolved_path="/fake/test.wav",
        sample_rate=48000,
        duration_samples=100000,
    )
    session.media.append(media)
    track = TrackIR(
        id="t1",
        source_index=1,
        name="ValidTrack",
        kind="audio",
        channel_count=1,
        gain_points=[(1000, 0.5), (2000, 0.8)],
    )
    clip = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="clip",
        media_id="m1",
        timeline_start_samples=1000,
        timeline_end_samples=5000,
    )
    track.clips.append(clip)
    session.tracks.append(track)

    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".als", delete=False) as f:
        tmp = f.name
    try:
        report = Report(strict=False)
        # Should not raise
        write_session(
            session,
            "templates/live12.als",
            tmp,
            WriteOptions(sample_rate=48000, live_origin_samples=0),
            report,
        )
        with gzip.open(tmp, "rb") as f:
            out = ET.fromstring(f.read())
        posicionais, _ = classify_id_tags(out)
        validate_document(out, posicionais)  # Must not raise
    finally:
        os.unlink(tmp)


def test_gain_automation_integration():
    """Integracao: Audio 3 do SPOT_AAF_TESTE sai com envelope, outras tracks sem."""
    from pathlib import Path
    from aafton.aaf_read import read_aaf
    from aafton.report import Report
    import pytest

    aaf_path = Path("tests/fixtures/aaf/SPOT_AAF_TESTE.aaf")
    if not aaf_path.exists():
        pytest.skip("Fixture SPOT_AAF_TESTE.aaf not found")

    report = Report(strict=False)
    session = read_aaf(aaf_path, report)

    # Tracks Audio 3 [L] e Audio 3 [R] (ou Audio 3 se pareado) devem ter gain_points
    tracks_with_env = [t for t in session.tracks if t.gain_points]
    assert len(tracks_with_env) > 0

    # Cada track com envelope deve ter pontos
    for t in tracks_with_env:
        assert len(t.gain_points) > 0
        assert "Audio 3" in t.name or "audio 3" in t.name.lower()

    # Tracks sem ramp (ex: Footsteps 1) nao devem ter gain_points
    footsteps = [t for t in session.tracks if "Footsteps" in t.name]
    for t in footsteps:
        assert not t.gain_points, f"{t.name} nao deveria ter gain_points"
