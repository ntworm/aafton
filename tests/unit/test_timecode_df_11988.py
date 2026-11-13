"""Timecode drop frame em 119,88 (120000/1001), como o Media Composer grava.

A taxa de 120 frames nominais (119,88 reais) e aceita em drop frame pelo
SMPTE ST 12-3: 8 rotulos sao pulados no começo de cada minuto, exceto a cada
10 minutos.
"""

import pytest

from aafton.timecode import TimecodeRate, frames_to_label, label_to_frames


@pytest.fixture
def rate() -> TimecodeRate:
    """Taxa drop frame de 119,88 frames por segundo."""
    return TimecodeRate(120000, 1001, drop_frame=True)


def test_drop_frame_11988_is_accepted(rate):
    """A taxa de 120000/1001 pode ser drop frame."""
    assert rate.nominal_fps == 120
    assert rate.dropped_per_minute == 8


@pytest.mark.parametrize(
    "label",
    [
        "00:00:00;00",
        "00:00:01;00",
        "00:01:00;08",
        "00:09:59;119",
        "00:10:00;00",
        "01:00:00;00",
    ],
)
def test_label_and_frames_round_trip(rate, label):
    """Ida e volta entre rotulo e indice de frames."""
    frames = label_to_frames(label, rate)
    assert frames_to_label(frames, rate) == label


@pytest.mark.parametrize("frame", [0, 1, 2, 3, 4, 5, 6, 7])
def test_dropped_labels_do_not_exist_in_the_first_minute(rate, frame):
    """No minuto 1 os rotulos de ;00 a ;07 nao existem."""
    with pytest.raises(ValueError, match="drop frame"):
        label_to_frames(f"00:01:00;{frame:02d}", rate)


def test_dropped_labels_exist_again_in_the_tenth_minute(rate):
    """No minuto 10 os rotulos de ;00 a ;07 voltam a existir."""
    for frame in range(8):
        frames = label_to_frames(f"00:10:00;{frame:02d}", rate)
        assert frames_to_label(frames, rate) == f"00:10:00;{frame:02d}"


def test_drop_frame_rejects_rates_outside_the_standard():
    """Fora das tres taxas do padrao, drop frame continua sendo recusado."""
    with pytest.raises(ValueError, match="120000/1001"):
        TimecodeRate(25, 1, drop_frame=True)
