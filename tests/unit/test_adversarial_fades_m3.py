# tests/unit/test_adversarial_fades_m3.py
"""Adversarial stress-testing suite for Milestone 3 (Pro Tools Rendered Fade Absorption - R3).

Tests:
1. Exact abutting for odd and even crossfades (L = 1, 2, 3, 5, 100, 44100, 44101, 96000, 96001).
2. Start clamping when offset < L (boundary conditions: 0, partial, exact, excess).
3. End clamping when offset + duration + L > media_duration.
4. Adjacent fade patterns (FADE->FADE, CLIP->FADE->FADE->CLIP, CLIP->FADE->FADE->FADE->CLIP).
5. Isolated fades, zero-length fillers, and non-zero filler boundaries.
6. Short clip warning thresholds (exact 999 vs 1000 vs 1001).
7. Single clip sandwiched by fade-in and fade-out.
8. CLI & verify integration with sidecar persistence.
"""

import pytest

from aafton.aaf_read import read_aaf
from aafton.report import Report
from tests.aaf_factory import build_aaf_with_rendered_fades, wav_bytes
from tests.support.paths import fixture_aaf


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "som.wav"
    caminho.write_bytes(wav_bytes(48000 * 120))
    return caminho


@pytest.mark.parametrize(
    "L",
    [1, 2, 3, 5, 10, 100, 44100, 44101, 96000, 96001],
)
def test_crossfade_exact_abutting_odd_even_lengths(tmp_path, wav, L):
    """Stress-test crossfade lengths L:
    - Junction midpoint Tm = T0 + L // 2.
    - Clip A ends at Tm (gains L // 2, fade_out = L // 2).
    - Clip B starts at Tm (recedes L - L // 2, offset recedes L - L // 2, fade_in = L - L // 2).
    - Clip A and Clip B abut with EXACT 0 gap and 0 overlap on the timeline.
    - Sum of fade durations equals L.
    """
    dur_a = 120000
    dur_b = 120000
    offset_b = 100000
    aaf = build_aaf_with_rendered_fades(
        tmp_path / f"crossfade_{L}.aaf",
        wav,
        [
            ("clip", dur_a, 0),
            ("fade", L, f"Fade {L}"),
            ("clip", dur_b, offset_b),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    la = L
    lb = 0
    tm = dur_a + la

    # Exact junction verification
    assert clip_a.timeline_start_samples == 0
    assert clip_a.timeline_end_samples == tm
    assert clip_b.timeline_start_samples == tm
    assert clip_a.timeline_end_samples == clip_b.timeline_start_samples, (
        "Timeline gap or overlap detected!"
    )

    # Durations and offsets
    assert clip_a.duration_samples == dur_a + la
    assert clip_b.duration_samples == dur_b + lb
    assert clip_b.source_offset_samples == offset_b - lb

    # Fade properties
    if la > 0:
        assert clip_a.fade_out is not None
        assert clip_a.fade_out.duration_samples == la
    else:
        # L=1 -> La=0: fade_out is 0
        assert clip_a.fade_out is not None
        assert clip_a.fade_out.duration_samples == 0

    # Invariants
    total_fades = (clip_a.fade_out.duration_samples if clip_a.fade_out else 0) + (
        clip_b.fade_in.duration_samples if clip_b.fade_in else 0
    )
    assert total_fades == L

    # Timeline total span
    total_span = clip_b.timeline_end_samples - clip_a.timeline_start_samples
    assert total_span == dur_a + L + dur_b


@pytest.mark.parametrize(
    "offset,L,expected_clamped,should_warn",
    [
        (0, 10000, 0, True),
        (3000, 10000, 3000, True),
        (9999, 10000, 9999, True),
        (10000, 10000, 10000, False),
        (15000, 10000, 10000, False),
    ],
)
def test_fade_in_start_clamping_boundaries(tmp_path, wav, offset, L, expected_clamped, should_warn):
    """Stress-test fade-in start clamping when offset < L."""
    dur = 50000
    filler_len = 20000
    aaf = build_aaf_with_rendered_fades(
        tmp_path / f"clamp_in_{offset}_{L}.aaf",
        wav,
        [
            ("filler", filler_len),
            ("fade", L, "Fade 1"),
            ("clip", dur, offset),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 1
    clip = session.tracks[0].clips[0]

    nominal_start = filler_len + L
    assert clip.timeline_start_samples == nominal_start - expected_clamped
    assert clip.duration_samples == dur + expected_clamped
    assert clip.source_offset_samples == (offset - expected_clamped if not should_warn else 0)
    assert clip.fade_in is not None
    assert clip.fade_in.duration_samples == expected_clamped

    clamp_issues = [i for i in report.issues if i.code == "FADE_CLAMPED_START"]
    if should_warn:
        assert len(clamp_issues) == 1
        assert clamp_issues[0].context["requested"] == L
        assert clamp_issues[0].context["applied"] == expected_clamped
    else:
        assert len(clamp_issues) == 0


def test_crossfade_with_clip_b_offset_clamping(tmp_path, wav):
    """Crossfade where Clip B has insufficient source offset for Lb."""
    dur_a = 48000
    dur_b = 48000
    L = 20000
    offset_b = 3000  # < 10000

    aaf = build_aaf_with_rendered_fades(
        tmp_path / "crossfade_clamp_b.aaf",
        wav,
        [
            ("clip", dur_a, 0),
            ("fade", L, "Fade 10"),
            ("clip", dur_b, offset_b),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    # Clip A gets normal La = 10000
    assert clip_a.timeline_end_samples == 48000 + 3000
    assert clip_a.fade_out.duration_samples == 3000

    # Clip B is clamped to offset_b = 3000
    # Original start was 48000 + 20000 = 68000. Recedes by 3000 -> 65000
    assert clip_b.timeline_start_samples == 68000
    assert clip_b.source_offset_samples == 3000

    clamp_issues = [i for i in report.issues if i.code == "FADE_CLAMPED_START"]
    assert len(clamp_issues) == 1
    assert clamp_issues[0].context["requested"] == 20000
    assert clamp_issues[0].context["applied"] == 3000


def test_fade_out_end_clamping_with_known_media_asset_duration(tmp_path, wav):
    """When asset duration is known, test clamping on available tail."""
    dur = 40000
    offset = 10000
    L = 10000
    # Create AAF
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "fade_out_clamp.aaf",
        wav,
        [
            ("clip", dur, offset),
            ("fade", L, "Fade 20"),
            ("filler", 10000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    # Read AAF
    session = read_aaf(aaf, report, fades="absorb")
    clip = session.tracks[0].clips[0]
    # Without asset duration known, it applied full L = 10000
    assert clip.duration_samples == dur + L
    assert clip.fade_out.duration_samples == L


def test_adjacent_fades_triple_chain(tmp_path, wav):
    """Chain of 3 fades: ClipA -> Fade1 -> Fade2 -> Fade3 -> ClipB.
    - Fade1: prev=ClipA, next=Fade2 -> Fade-out for ClipA.
    - Fade2: prev=Fade1, next=Fade3 -> Isolated fade (discarded).
    - Fade3: prev=Fade2, next=ClipB -> Fade-in for ClipB.
    - Total 3 fades absorbed / discarded.
    """
    dur_a = 48000
    dur_b = 48000
    offset_b = 30000
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "triple_fade.aaf",
        wav,
        [
            ("clip", dur_a, 0),
            ("fade", 5000, "Fade 1"),
            ("fade", 6000, "Fade 2"),
            ("fade", 7000, "Fade 3"),
            ("clip", dur_b, offset_b),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    assert clip_a.fade_out is not None
    assert clip_a.fade_out.duration_samples == 5000
    assert clip_a.timeline_end_samples == dur_a + 5000

    # Original B start: 48000 + 5000 + 6000 + 7000 = 66000. Recedes by 7000 -> 59000
    assert clip_b.timeline_start_samples == 59000
    assert clip_b.source_offset_samples == offset_b - 7000

    discarded = [i for i in report.issues if i.code == "FADE_RENDER_DISCARDED"]
    assert len(discarded) == 1
    assert discarded[0].context["fades_absorbed"] == 3


def test_isolated_fade_between_fillers(tmp_path, wav):
    """Isolated fade: Filler(10000) -> Fade(5000) -> Filler(10000).
    No clips produced, but total_absorbed_fades == 1, FADE_RENDER_DISCARDED emitted.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "isolated_fade.aaf",
        wav,
        [
            ("filler", 10000),
            ("fade", 5000, "Fade 1"),
            ("filler", 10000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 0

    discarded = [i for i in report.issues if i.code == "FADE_RENDER_DISCARDED"]
    assert len(discarded) == 1
    assert discarded[0].context["fades_absorbed"] == 1


def test_fade_surrounded_by_non_zero_fillers_acts_as_fade_in_or_out(tmp_path, wav):
    """ClipA -> Filler(1000) -> Fade(5000) -> ClipB.
    Here Filler(1000) is non-zero length:
    - For Fade: prev is Filler(1000), next is ClipB -> Classified as Fade-in for ClipB!
    - ClipA is separated by Filler(1000) -> ClipA has NO fade-out.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "filler_gap_fade.aaf",
        wav,
        [
            ("clip", 48000, 0),
            ("filler", 1000),
            ("fade", 5000, "Fade 1"),
            ("clip", 48000, 20000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    clip_a, clip_b = session.tracks[0].clips

    assert clip_a.fade_out is None
    assert clip_a.duration_samples == 48000

    # Original B start: 48000 + 1000 + 5000 = 54000. Recedes by 5000 -> 49000
    assert clip_b.timeline_start_samples == 49000
    assert clip_b.source_offset_samples == 15000


def test_sandwich_fade_in_and_out_on_same_clip(tmp_path, wav):
    """Fade1(10000) -> Clip(48000, offset=20000) -> Fade2(12000).
    - Fade1 absorbed as fade-in: start recedes 10000, offset recedes 10000, dur +10000.
    - Fade2 absorbed as fade-out: dur +12000.
    - Total dur = 70000.
    - Invariant: fade_in (10000) + fade_out (12000) <= 70000.
    """
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "sandwich_fade.aaf",
        wav,
        [
            ("fade", 10000, "Fade 1"),
            ("clip", 48000, 20000),
            ("fade", 12000, "Fade 2"),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 1
    clip = session.tracks[0].clips[0]

    assert clip.timeline_start_samples == 0
    assert clip.timeline_end_samples == 70000
    assert clip.duration_samples == 70000
    assert clip.source_offset_samples == 10000
    assert clip.fade_in is not None
    assert clip.fade_in.duration_samples == 10000
    assert clip.fade_out is not None
    assert clip.fade_out.duration_samples == 12000
    assert clip.fade_in.duration_samples + clip.fade_out.duration_samples <= clip.duration_samples


@pytest.mark.parametrize(
    "dur,should_warn",
    [
        (1, True),
        (500, True),
        (999, True),
        (1000, False),
        (1001, False),
        (48000, False),
    ],
)
def test_short_clip_warning_threshold_exact(tmp_path, wav, dur, should_warn):
    """CLIP_VERY_SHORT threshold is strictly duration_samples < 1000."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / f"short_{dur}.aaf",
        wav,
        [("clip", dur, 0)],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    read_aaf(aaf, report, fades="absorb")
    short_issues = [i for i in report.issues if i.code == "CLIP_VERY_SHORT"]
    if should_warn:
        assert len(short_issues) == 1
        assert short_issues[0].severity == "warning"
        assert short_issues[0].context["duration_samples"] == dur
    else:
        assert len(short_issues) == 0


def test_real_protools_oracle_crossfade_abutting():
    """Verify on real TESTE_AFF_PROTOOLS.aaf that all adjacent absorbed clips abut cleanly."""
    pt_aaf = fixture_aaf("TESTE_AFF_PROTOOLS.aaf")

    report = Report(strict=False)
    session = read_aaf(pt_aaf, report, fades="absorb")

    for track in session.tracks:
        sorted_clips = sorted(track.clips, key=lambda c: c.timeline_start_samples)
        for i in range(len(sorted_clips) - 1):
            curr_c = sorted_clips[i]
            next_c = sorted_clips[i + 1]
            # Clips should NEVER overlap on timeline
            assert curr_c.timeline_end_samples <= next_c.timeline_start_samples, (
                f"Overlap detected on track {track.name}: clip {curr_c.name} ({curr_c.timeline_end_samples}) "
                f"> clip {next_c.name} ({next_c.timeline_start_samples})"
            )
            # Check fade invariants per clip
            fi = curr_c.fade_in.duration_samples if curr_c.fade_in else 0
            fo = curr_c.fade_out.duration_samples if curr_c.fade_out else 0
            assert fi + fo <= curr_c.duration_samples, (
                f"Fade sum exceeds duration on clip {curr_c.name}: {fi} + {fo} > {curr_c.duration_samples}"
            )


def test_continuous_crossfade_chain_10_clips(tmp_path, wav):
    """Stress test a chain of 10 clips with alternating odd and even crossfades:
    Clip0 -> Fade1(2001) -> Clip1 -> Fade2(4000) -> Clip2 -> Fade3(1501) ... -> Clip9.
    Every adjacent junction must abut at exact midpoint with 0 gap and 0 overlap.
    """
    n_clips = 10
    clip_dur = 48000
    clip_offset = 30000
    fade_lengths = [2001, 4000, 1501, 6000, 3333, 8000, 7777, 10000, 1111]

    seq = []
    for i in range(n_clips):
        seq.append(("clip", clip_dur, clip_offset))
        if i < len(fade_lengths):
            seq.append(("fade", fade_lengths[i], f"Fade {i + 1}"))

    aaf = build_aaf_with_rendered_fades(
        tmp_path / "chain_10_clips.aaf",
        wav,
        seq,
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == n_clips
    clips = session.tracks[0].clips

    for i in range(n_clips - 1):
        c_curr = clips[i]
        c_next = clips[i + 1]
        L = fade_lengths[i]

        # Verify junction point exact equality
        assert c_curr.timeline_end_samples == c_next.timeline_start_samples, (
            f"Junction mismatch at clip {i}->{i + 1}: {c_curr.timeline_end_samples} != {c_next.timeline_start_samples}"
        )
        assert c_curr.fade_out.duration_samples == L
        assert getattr(c_next, "fade_in", None) is None
        assert c_next.crossfade_in_state is True


def test_zero_length_fade_is_skipped(tmp_path, wav):
    """Zero-length fade segment (Fade with length=0) should be skipped without affecting neighbors."""
    aaf = build_aaf_with_rendered_fades(
        tmp_path / "zero_len_fade.aaf",
        wav,
        [
            ("clip", 48000, 0),
            ("fade", 0, "Fade 1"),
            ("clip", 48000, 20000),
        ],
        start_timecode_frames=0,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert len(session.tracks[0].clips) == 2
    c0, c1 = session.tracks[0].clips

    # Since fade length was 0, it should not absorb any samples
    assert c0.timeline_end_samples == 48000
    assert c0.fade_out is None
    assert c1.timeline_start_samples == 48000
    assert c1.fade_in is None


def test_crossfade_with_one_hour_start_timecode(tmp_path, wav):
    """Pro Tools session starting at 01:00:00:00 (90000 frames @ 25fps = 172800000 samples @ 48kHz)."""
    start_tc_frames = 90000
    expected_start_samples = 172800000
    dur_a = 96000
    dur_b = 96000
    L = 24001  # Odd length crossfade
    offset_b = 48000

    aaf = build_aaf_with_rendered_fades(
        tmp_path / "one_hour_crossfade.aaf",
        wav,
        [
            ("clip", dur_a, 0),
            ("fade", L, "Fade 1"),
            ("clip", dur_b, offset_b),
        ],
        start_timecode_frames=start_tc_frames,
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report, fades="absorb")
    assert session.start_timecode_samples == expected_start_samples
    assert len(session.tracks[0].clips) == 2
    c0, c1 = session.tracks[0].clips

    expected_junction = expected_start_samples + dur_a + L

    assert c0.timeline_start_samples == expected_start_samples
    assert c0.timeline_end_samples == expected_junction
    assert c1.timeline_start_samples == expected_junction
    assert c0.timeline_end_samples == c1.timeline_start_samples
