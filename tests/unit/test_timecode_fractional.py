from fractions import Fraction
from aafton.timecode import (
    TimecodeRate,
    frames_to_samples_exact,
    samples_to_frames_exact,
    samples_per_frame,
    exact_edit_rate,
)
from aafton.aaf_read import _edit_units_to_samples


def test_exact_edit_rate_fractional_constants():
    assert exact_edit_rate(Fraction(24000, 1001)) == Fraction(24000, 1001)
    assert exact_edit_rate(Fraction(30000, 1001)) == Fraction(30000, 1001)
    assert exact_edit_rate(Fraction(60000, 1001)) == Fraction(60000, 1001)


def test_exact_edit_rate_from_float_and_str():
    # 23.976 / 23.98
    assert exact_edit_rate(23.976) == Fraction(24000, 1001)
    assert exact_edit_rate("23.976") == Fraction(24000, 1001)
    assert exact_edit_rate(23.98) == Fraction(24000, 1001)

    # 29.97
    assert exact_edit_rate(29.97) == Fraction(30000, 1001)
    assert exact_edit_rate("29.97") == Fraction(30000, 1001)

    # 59.94
    assert exact_edit_rate(59.94) == Fraction(60000, 1001)
    assert exact_edit_rate("59.94") == Fraction(60000, 1001)


def test_exact_edit_rate_from_timecode_rate():
    tc_24 = TimecodeRate(24000, 1001)
    assert exact_edit_rate(tc_24) == Fraction(24000, 1001)

    tc_30 = TimecodeRate(30000, 1001, drop_frame=True)
    assert exact_edit_rate(tc_30) == Fraction(30000, 1001)


def test_edit_units_to_samples_fractional_exactness():
    sr = 48000
    rate_23976 = Fraction(24000, 1001)
    rate_2997 = Fraction(30000, 1001)
    rate_5994 = Fraction(60000, 1001)

    # For 23.976 at 48000 Hz: 1 edit unit is 48000 * 1001 / 24000 = 2002 samples
    assert _edit_units_to_samples(1, rate_23976, sr) == Fraction(2002)
    assert _edit_units_to_samples(1000, rate_23976, sr) == Fraction(2002000)

    # Passing float 23.976 should also produce exact 2002 samples per unit
    assert _edit_units_to_samples(1, 23.976, sr) == Fraction(2002)
    assert _edit_units_to_samples(1000, 23.976, sr) == Fraction(2002000)

    # For 29.97 at 48000 Hz: 1 edit unit is 48000 * 1001 / 30000 = 8008 / 5 samples
    assert _edit_units_to_samples(1, rate_2997, sr) == Fraction(8008, 5)
    assert _edit_units_to_samples(5, rate_2997, sr) == Fraction(8008)
    assert _edit_units_to_samples(5, 29.97, sr) == Fraction(8008)

    # For 59.94 at 48000 Hz: 1 edit unit is 48000 * 1001 / 60000 = 4004 / 5 samples
    assert _edit_units_to_samples(1, rate_5994, sr) == Fraction(4004, 5)
    assert _edit_units_to_samples(5, rate_5994, sr) == Fraction(4004)
    assert _edit_units_to_samples(5, 59.94, sr) == Fraction(4004)


def test_no_accumulated_drift_over_long_durations():
    sr = 48000
    # 10 hours at 23.976 (approx 864,000 frames)
    frames_10h = 864000
    exact_samples = _edit_units_to_samples(frames_10h, 23.976, sr)
    assert exact_samples == frames_10h * Fraction(2002)
    assert exact_samples == Fraction(1729728000)

    # Linearity check: sum of chunks equals chunk of sums
    chunk1 = 123456
    chunk2 = 654321
    assert _edit_units_to_samples(chunk1 + chunk2, 29.97, sr) == (
        _edit_units_to_samples(chunk1, 29.97, sr) + _edit_units_to_samples(chunk2, 29.97, sr)
    )


def test_timecode_rate_fractional_precision():
    rate_5994 = TimecodeRate(60000, 1001)
    assert rate_5994.nominal_fps == 60
    assert rate_5994.fps == Fraction(60000, 1001)

    spf = samples_per_frame(rate_5994, 48000)
    assert spf == Fraction(4004, 5)

    samples = frames_to_samples_exact(5, rate_5994, 48000)
    assert samples == Fraction(4004)

    assert samples_to_frames_exact(samples, rate_5994, 48000) == 5
