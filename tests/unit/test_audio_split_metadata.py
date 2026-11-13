"""Preservacao opt-in de bext/iXML no split estereo (fase 3 estreita).

O padrao antigo (sem a flag) precisa continuar byte a byte igual; a flag
preserva apenas bext e iXML, com payload, ordem e padding intactos.
"""

import struct
import wave
from pathlib import Path

import pytest

from aafton.audio_split import _iter_riff_chunks, split_stereo_source
from aafton.media_extract import read_audio_metadata

GUID_PCM = bytes.fromhex("0100000000001000800000aa00389b71")
GUID_FLOAT = bytes.fromhex("0300000000001000800000aa00389b71")
RATE = 48000

FORMATS = [
    pytest.param(1, None, 16, id="pcm16"),
    pytest.param(1, None, 24, id="pcm24"),
    pytest.param(3, None, 32, id="float32"),
    pytest.param(0xFFFE, 1, 24, id="pcm24_extensivel"),
    pytest.param(0xFFFE, 3, 32, id="float32_extensivel"),
]


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)


def _chunked(size: int) -> int:
    return 8 + size + (size % 2)


def _fmt(tag: int, subformat: int | None, channels: int, bits: int) -> bytes:
    width = bits // 8
    align = channels * width
    base = struct.pack("<HHIIHH", tag, channels, RATE, RATE * align, align, bits)
    if tag != 0xFFFE:
        return base
    guid = GUID_FLOAT if subformat == 3 else GUID_PCM
    return base + struct.pack("<HHI", 22, bits, 0b11) + guid


def _mono_fmt(tag: int, subformat: int | None, bits: int) -> bytes:
    """fmt mono esperado: canal unico, mask zerado no extensivel."""
    fmt = bytearray(_fmt(tag, subformat, 2, bits))
    width = bits // 8
    struct.pack_into("<HIIH", fmt, 2, 1, RATE, RATE * width, width)
    if tag == 0xFFFE:
        struct.pack_into("<I", fmt, 20, 0)
    return bytes(fmt)


def _channels(frames: int, width: int) -> tuple[list[bytes], list[bytes]]:
    left = [bytes((i * 37 + b) % 256 for b in range(width)) for i in range(frames)]
    right = [bytes((255 - i * 11 - b) % 256 for b in range(width)) for i in range(frames)]
    return left, right


def _source(
    path: Path,
    *,
    tag: int = 1,
    subformat: int | None = None,
    bits: int = 16,
    frames: int = 17,
    before: tuple = (),
    after: tuple = (),
) -> tuple[bytes, bytes]:
    """WAV stereo com chunks extras antes e depois do chunk data."""
    width = bits // 8
    left, right = _channels(frames, width)
    fmt = _fmt(tag, subformat, 2, bits)
    body = b"WAVE" + _chunk(b"fmt ", fmt)
    for chunk_id, payload in before:
        body += _chunk(chunk_id, payload)
    body += _chunk(b"data", b"".join(esq + dir_ for esq, dir_ in zip(left, right)))
    for chunk_id, payload in after:
        body += _chunk(chunk_id, payload)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return b"".join(left), b"".join(right)


def _bext_payload(time_reference: int = 123456789) -> bytes:
    """Chunk bext BWF versao 1 (602 bytes) com time reference."""
    payload = (
        b"descricao de origem".ljust(256, b"\x00")
        + b"origem".ljust(32, b"\x00")
        + b"ref-0001".ljust(32, b"\x00")
        + b"2026-01-01".ljust(10, b"\x00")
        + b"12:00:00".ljust(8, b"\x00")
        + struct.pack("<Q", time_reference)
        + struct.pack("<H", 1)
        + b"\x00" * 64
        + b"\x00" * 10
        + b"\x00" * 180
    )
    assert len(payload) == 602
    return payload


def _ixml_payload() -> bytes:
    """Payload iXML de tamanho impar: o chunk exige byte de padding."""
    payload = b"<BWFXML><PROJECT>Nome do projeto</PROJECT></BWFXML>"
    if len(payload) % 2 == 0:
        payload += b" "
    assert len(payload) % 2 == 1
    return payload


def _payload(path: Path, chunk_id: bytes) -> bytes | None:
    for cid, offset, size in _iter_riff_chunks(path):
        if cid == chunk_id:
            with path.open("rb") as stream:
                stream.seek(offset)
                return stream.read(size)
    return None


def _ids(path: Path) -> list[bytes]:
    return [chunk_id for chunk_id, _, _ in _iter_riff_chunks(path)]


def _expected_mono(fmt: bytes, pcm: bytes) -> bytes:
    """WAV mono no layout antigo: fmt e data, sem metadata."""
    riff_size = 4 + _chunked(len(fmt)) + _chunked(len(pcm))
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"\x00" * (len(fmt) % 2)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
        + b"\x00" * (len(pcm) % 2)
    )


def test_default_mantem_bytes_antigos_e_exclui_metadata(tmp_path):
    """Sem a flag o resultado e o WAV de antes: fmt, data e nada mais."""
    source = tmp_path / "stereo.wav"
    left, right = _source(
        source,
        before=((b"bext", _bext_payload()),),
        after=((b"iXML", _ixml_payload()),),
    )
    outputs = split_stereo_source(source, tmp_path / "media")
    fmt = _mono_fmt(1, None, 16)
    for output, expected in zip(outputs, (left, right)):
        assert output.read_bytes() == _expected_mono(fmt, expected)
        assert _ids(output) == [b"fmt ", b"data"]
        assert _payload(output, b"bext") is None
        assert _payload(output, b"iXML") is None
    with wave.open(str(outputs[0]), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        assert w.readframes(w.getnframes()) == left


@pytest.mark.parametrize("tag,subformat,bits", FORMATS)
def test_flag_preserva_bext_e_ixml(tmp_path, tag, subformat, bits):
    """Com a flag, bext e iXML viaem inteiros, na ordem e com o padding."""
    source = tmp_path / "stereo.wav"
    bext, ixml = _bext_payload(), _ixml_payload()
    left, right = _source(
        source,
        tag=tag,
        subformat=subformat,
        bits=bits,
        before=((b"bext", bext),),
        after=((b"iXML", ixml), (b"LIST", b"INFOIART" + b"\x00" * 8)),
    )
    original = source.read_bytes()
    outputs = split_stereo_source(source, tmp_path / "media", preserve_metadata=True)
    width = bits // 8
    for output, expected in zip(outputs, (left, right)):
        raw = output.read_bytes()
        assert _ids(output) == [b"fmt ", b"bext", b"data", b"iXML"]
        assert _payload(output, b"bext") == bext
        assert _payload(output, b"iXML") == ixml
        assert _payload(output, b"data") == expected
        assert struct.unpack_from("<Q", _payload(output, b"bext"), 338)[0] == 123456789
        assert struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8
        assert len(raw) % 2 == 0
        meta = read_audio_metadata(output)
        assert meta["channels"] == 1
        assert meta["bits"] == bits
        assert meta["sample_rate"] == RATE
        assert meta["duration_samples"] == len(expected) // width
        fmt = meta["fmt_payload"]
        assert struct.unpack_from("<H", fmt, 0)[0] == tag
        assert struct.unpack_from("<H", fmt, 2)[0] == 1
        assert struct.unpack_from("<I", fmt, 8)[0] == RATE * width
        assert struct.unpack_from("<H", fmt, 12)[0] == width
        if tag == 0xFFFE:
            assert struct.unpack_from("<I", fmt, 20)[0] == 0
    assert source.read_bytes() == original


def test_flag_preserva_ordem_relativa_ao_data(tmp_path):
    """bext depois do data continua depois; iXML antes continua antes."""
    source = tmp_path / "stereo.wav"
    bext, ixml = _bext_payload(987654321), _ixml_payload()
    _source(
        source,
        before=((b"iXML", ixml),),
        after=((b"bext", bext),),
    )
    outputs = split_stereo_source(source, tmp_path / "media", preserve_metadata=True)
    for output in outputs:
        assert _ids(output) == [b"fmt ", b"iXML", b"data", b"bext"]
        assert _payload(output, b"bext") == bext
        assert _payload(output, b"iXML") == ixml
        raw = output.read_bytes()
        assert struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8


def test_flag_rejeita_aiff(tmp_path):
    """A preservacao de metadata e exclusiva de WAV."""
    width, frames = 2, 4
    left, right = _channels(frames, width)
    pcm = b"".join(esq + dir_ for esq, dir_ in zip(left, right))
    comm = struct.pack(">HIH", 2, frames, 16) + bytes.fromhex("400ebb80000000000000")
    body = (
        b"AIFFCOMM"
        + struct.pack(">I", len(comm))
        + comm
        + b"SSND"
        + struct.pack(">III", len(pcm) + 8, 0, 0)
        + pcm
    )
    source = tmp_path / "stereo.aiff"
    source.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    with pytest.raises(ValueError, match="requires a WAV source"):
        split_stereo_source(source, tmp_path / "media", preserve_metadata=True)
    outputs = split_stereo_source(source, tmp_path / "media")
    assert len(outputs) == 2


def test_fonte_truncada_detectada(tmp_path):
    """Cortar a fonte falha na preservacao de metadata e nao deixa temporarios."""
    source = tmp_path / "stereo.wav"
    _source(
        source,
        before=((b"bext", _bext_payload()),),
        after=((b"iXML", _ixml_payload()),),
    )
    media = tmp_path / "media"
    raw = source.read_bytes()
    source.write_bytes(raw[: len(raw) - 10])
    with pytest.raises(ValueError, match="truncated"):
        split_stereo_source(source, media, preserve_metadata=True)
    assert not media.exists() or not list(media.iterdir())


def test_truncamento_no_data_descarta_temporarios(tmp_path):
    """Cortar o payload do data falha no meio da escrita e limpa o cache."""
    source = tmp_path / "stereo.wav"
    _source(source)
    media = tmp_path / "media"
    raw = source.read_bytes()
    source.write_bytes(raw[:-10])
    with pytest.raises(ValueError, match="truncated"):
        split_stereo_source(source, media)
    with pytest.raises(ValueError, match="truncated"):
        split_stereo_source(source, media, preserve_metadata=True)
    assert media.exists()
    assert not list(media.iterdir())


def test_cache_verificado_e_arquivo_anterior_preservado(tmp_path):
    """Reuso so com bytes iguais; arquivos anteriores nunca sao apagados."""
    source = tmp_path / "stereo.wav"
    _source(source)
    media = tmp_path / "media"
    outputs = split_stereo_source(source, media)
    antes = [p.read_bytes() for p in outputs]
    assert split_stereo_source(source, media) == outputs
    assert [p.read_bytes() for p in outputs] == antes
    outra = tmp_path / "outra.wav"
    _source(outra, before=((b"bext", _bext_payload()),))
    novos = split_stereo_source(outra, media, preserve_metadata=True)
    assert novos != outputs
    assert all(p.exists() for p in outputs)
    assert [p.read_bytes() for p in outputs] == antes
    outputs[0].write_bytes(b"arquivo existente divergente")
    with pytest.raises(ValueError, match="existing mono source differs"):
        split_stereo_source(source, media)
    assert outputs[0].read_bytes() == b"arquivo existente divergente"


def test_iter_riff_chunks_expoe_interface(tmp_path):
    """O walker entrega (id, offset do payload, tamanho) de cada chunk."""
    source = tmp_path / "stereo.wav"
    bext, ixml = _bext_payload(), _ixml_payload()
    _source(source, before=((b"bext", bext),), after=((b"iXML", ixml),))
    raw = source.read_bytes()
    chunks = list(_iter_riff_chunks(source))
    assert [chunk_id for chunk_id, _, _ in chunks] == [b"fmt ", b"bext", b"data", b"iXML"]
    for chunk_id, offset, size in chunks:
        assert offset % 2 == 0
        assert raw[offset : offset + size]
        if chunk_id == b"bext":
            assert raw[offset : offset + size] == bext
        if chunk_id == b"iXML":
            assert raw[offset : offset + size] == ixml


def test_iter_riff_chunks_valida_limites(tmp_path):
    """RIFF invalido, chunk fora do limite e arquivo truncado sao recusados."""
    source = tmp_path / "stereo.wav"
    _source(source)
    raw = bytearray(source.read_bytes())
    furado = bytearray(raw)
    struct.pack_into("<I", furado, 16, 0xFFFF)  # tamanho do fmt
    source.write_bytes(bytes(furado))
    with pytest.raises(ValueError, match="outside the RIFF bounds"):
        list(_iter_riff_chunks(source))
    truncado = tmp_path / "truncado.wav"
    truncado.write_bytes(bytes(raw)[: len(raw) - 10])
    with pytest.raises(ValueError, match="truncated"):
        list(_iter_riff_chunks(truncado))
    nao_wav = tmp_path / "nao.wav"
    nao_wav.write_bytes(b"FORM" + struct.pack(">I", 4) + b"AIFF")
    with pytest.raises(ValueError, match="format not supported"):
        list(_iter_riff_chunks(nao_wav))
