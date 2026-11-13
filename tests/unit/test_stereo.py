from aafton.stereo import _get_base_name
import pytest


@pytest.mark.parametrize(
    "name, expected_base, expected_suffix",
    [
        ("Audio 1_L", "Audio 1", "L"),
        ("audio 1_r", "audio 1", "R"),
        ("REF.L", "REF", "L"),
        ("REF [L]", "REF", "L"),
        ("PIANO_L", "PIANO", "L"),
        ("PIANO_R", "PIANO", "R"),
        ("Track 1", "Track 1", ""),
        ("PIANO_ls", "PIANO_ls", ""),  # We only support _L and _R
        ("REF [C]", "REF", "C"),  # [C] is supported by the bracket fallback
    ],
)
def test_get_base_name(name, expected_base, expected_suffix):
    base, suffix = _get_base_name(name)
    assert base == expected_base
    assert suffix == expected_suffix
