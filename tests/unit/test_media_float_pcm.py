from pathlib import Path
import struct
import pytest
from aafton.media_extract import read_audio_metadata


def build_wav(
    fmt_code: int, channels: int, rate: int, bits: int, frames: int, subformat: int | None = None
) -> bytes:
    width = bits // 8
    block_align = channels * width
    bytes_per_sec = rate * block_align
    data_size = frames * block_align

    if fmt_code == 0xFFFE:
        # WAVE_FORMAT_EXTENSIBLE: at least 40 bytes fmt
        fmt_payload = struct.pack(
            "<HHIIHH", fmt_code, channels, rate, bytes_per_sec, block_align, bits
        )
        # cbSize = 22
        fmt_payload += struct.pack("<H", 22)
        # wValidBitsPerSample = bits
        fmt_payload += struct.pack("<H", bits)
        # dwChannelMask = 0
        fmt_payload += struct.pack("<I", 0)
        # SubFormat GUID (first 2 bytes is subformat tag, rest standard GUID)
        guid_tail = bytes.fromhex("000000001000800000aa00389b71")
        fmt_payload += struct.pack("<H", subformat if subformat is not None else 1) + guid_tail
    elif fmt_code == 3:
        # Standard IEEE float fmt chunk (usually 16 or 18 bytes with cbSize=0)
        fmt_payload = struct.pack(
            "<HHIIHH", fmt_code, channels, rate, bytes_per_sec, block_align, bits
        )
    else:
        fmt_payload = struct.pack(
            "<HHIIHH", fmt_code, channels, rate, bytes_per_sec, block_align, bits
        )

    # Simple sample data
    if fmt_code == 3 or (fmt_code == 0xFFFE and subformat == 3):
        # 32-bit float samples
        sample_bytes = struct.pack("<f", 0.125) * channels
        data_payload = sample_bytes * frames
    else:
        data_payload = b"\x00" * data_size

    chunks = b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    chunks += b"data" + struct.pack("<I", len(data_payload)) + data_payload
    riff = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
    return riff


def test_wav_ieee_float_standard_32bit(tmp_path: Path):
    path = tmp_path / "float32_standard.wav"
    frames = 960
    path.write_bytes(build_wav(fmt_code=3, channels=2, rate=48000, bits=32, frames=frames))

    meta = read_audio_metadata(path)
    assert meta["format"] == "wav"
    assert meta["channels"] == 2
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == frames
    assert meta["bits"] == 32
    assert meta["fmt_payload"] is not None
    assert meta["is_big_endian"] is False


def test_wav_ieee_float_extensible_32bit(tmp_path: Path):
    path = tmp_path / "float32_extensible.wav"
    frames = 480
    path.write_bytes(
        build_wav(fmt_code=0xFFFE, channels=1, rate=48000, bits=32, frames=frames, subformat=3)
    )

    meta = read_audio_metadata(path)
    assert meta["format"] == "wav"
    assert meta["channels"] == 1
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == frames
    assert meta["bits"] == 32
    assert meta["fmt_payload"] is not None
    assert len(meta["fmt_payload"]) >= 40


def test_wav_pcm_extensible_and_pcm_standard(tmp_path: Path):
    # Standard PCM 16-bit
    path_pcm16 = tmp_path / "pcm16.wav"
    path_pcm16.write_bytes(build_wav(fmt_code=1, channels=2, rate=44100, bits=16, frames=1000))
    meta_16 = read_audio_metadata(path_pcm16)
    assert meta_16["bits"] == 16
    assert meta_16["duration_samples"] == 1000

    # Extensible PCM 24-bit
    path_ext24 = tmp_path / "pcm24_ext.wav"
    path_ext24.write_bytes(
        build_wav(fmt_code=0xFFFE, channels=2, rate=48000, bits=24, frames=2000, subformat=1)
    )
    meta_24 = read_audio_metadata(path_ext24)
    assert meta_24["bits"] == 24
    assert meta_24["duration_samples"] == 2000


def test_wav_extensible_unsupported_subformat_raises(tmp_path: Path):
    path_invalid = tmp_path / "invalid_subformat.wav"
    path_invalid.write_bytes(
        build_wav(fmt_code=0xFFFE, channels=1, rate=48000, bits=16, frames=100, subformat=7)
    )
    with pytest.raises(ValueError, match="with subformat 7"):
        read_audio_metadata(path_invalid)
