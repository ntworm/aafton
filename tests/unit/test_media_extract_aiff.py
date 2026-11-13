import pytest
from aafton.media_extract import read_audio_metadata
import wave
import aifc
import struct


def create_aiff(path, channels, framerate, frames, compname=b"NONE"):
    # using aifc
    with aifc.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(framerate)
        f.setnframes(frames)
        f.setcomptype(compname, b"")
        f.writeframes(b"\x00\x00" * channels * frames)


def test_aiff_mono(tmp_path):
    p = tmp_path / "mono.aif"
    create_aiff(p, 1, 48000, 100)
    meta = read_audio_metadata(p)
    assert meta["channels"] == 1
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == 100


def test_aiff_stereo(tmp_path):
    p = tmp_path / "stereo.aif"
    create_aiff(p, 2, 44100, 200)
    meta = read_audio_metadata(p)
    assert meta["channels"] == 2
    assert meta["sample_rate"] == 44100
    assert meta["duration_samples"] == 200


def test_aiff_sowt(tmp_path):
    p = tmp_path / "sowt.aifc"
    # Write manually to bypass aifc's check
    with open(p, "wb") as f:
        f.write(b"FORM" + struct.pack(">I", 46) + b"AIFC")
        f.write(b"COMM" + struct.pack(">I", 22))  # length
        # channels=1, frames=50, sampwidth=16, framerate=48000
        # float 48000 = 40 0D BB 80 00 00 00 00 00 00
        f.write(struct.pack(">HlH", 1, 50, 16) + b"\x40\x0e\xbb\x80\x00\x00\x00\x00\x00\x00")
        f.write(b"sowt")
    meta = read_audio_metadata(p)
    assert meta["channels"] == 1
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == 50


def test_aiff_reject_compression(tmp_path):
    p = tmp_path / "comp.aifc"
    create_aiff(p, 1, 48000, 50, b"ulaw")
    with pytest.raises(ValueError, match="compression not supported in AIFC"):
        read_audio_metadata(p)


def test_read_riff_despite_aif_extension(tmp_path):
    p = tmp_path / "fake.aif"
    with wave.open(str(p), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes(b"\x00\x00" * 10)

    meta = read_audio_metadata(p)
    assert meta["channels"] == 1
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == 10
