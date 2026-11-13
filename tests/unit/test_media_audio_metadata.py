import struct
from aafton.media_extract import read_audio_metadata
from tests.support.paths import corpus_file


def criar_wav(fmt_code, channels, rate, bits, size_data, subformat=None):
    block_align = channels * (bits // 8)
    bytes_per_sec = rate * block_align

    if fmt_code == 0xFFFE:
        # EXTENSIBLE requires at least 40 bytes.
        fmt_payload = struct.pack(
            "<HHIIHH", fmt_code, channels, rate, bytes_per_sec, block_align, bits
        )
        # cbSize = 22
        fmt_payload += struct.pack("<H", 22)
        # ValidBitsPerSample = bits
        fmt_payload += struct.pack("<H", bits)
        # dwChannelMask = 0
        fmt_payload += struct.pack("<I", 0)
        # SubFormat GUID (first 2 bytes is the subformat, e.g. 1 for PCM)
        fmt_payload += struct.pack("<H", subformat) + b"\x00" * 14
    else:
        fmt_payload = struct.pack(
            "<HHIIHH", fmt_code, channels, rate, bytes_per_sec, block_align, bits
        )

    data_payload = b"\x00" * size_data

    chunks = b""
    chunks += b"fmt " + struct.pack("<I", len(fmt_payload)) + fmt_payload
    chunks += b"data" + struct.pack("<I", len(data_payload)) + data_payload

    riff = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
    return riff


def test_read_audio_metadata_pcm(tmp_path):
    path = tmp_path / "pcm.wav"
    # 2 channels, 48000 rate, 16 bits, data size 48000 * 2 * 2 = 192000 (1 second)
    path.write_bytes(criar_wav(1, 2, 48000, 16, 192000))
    meta = read_audio_metadata(path)
    assert meta["channels"] == 2
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == 48000


def test_read_audio_metadata_extensible(tmp_path):
    path = tmp_path / "extensible.wav"
    # 1 channel, 48000 rate, 24 bits, data size 48000 * 1 * 3 = 144000 (1 second)
    path.write_bytes(criar_wav(0xFFFE, 1, 48000, 24, 144000, subformat=1))
    meta = read_audio_metadata(path)
    assert meta["channels"] == 1
    assert meta["sample_rate"] == 48000
    assert meta["duration_samples"] == 48000


def test_read_audio_metadata_mundo_real():
    """WAV mono 48 kHz de silêncio gerado, usado apenas em teste estrutural.

    O manifesto foi gerado por scripts/corpus_manifest.py, que le o cabecalho
    por conta propria; aqui o leitor do aafton tem de chegar aos mesmos numeros.
    """
    path, esperado = corpus_file("TESTE_AFF_PqczzFCHRbdxwVBBB.wav")
    assert (esperado["channels"], esperado["sample_rate"]) == (1, 48000)

    meta = read_audio_metadata(path)
    assert meta["channels"] == esperado["channels"]
    assert meta["sample_rate"] == esperado["sample_rate"]
    assert meta["duration_samples"] == esperado["frames"]
