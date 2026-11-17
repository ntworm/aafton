"""Split de N canais: amostras intactas canal a canal e iXML reduzido por mono."""

import random
import struct
from pathlib import Path

import pytest

from aafton.audio_split import _iter_riff_chunks, split_channels, split_stereo_source
from aafton.bwf_meta import IxmlTrack, read_bwf, reduce_track_list
from aafton.media_extract import read_audio_metadata

RATE = 48000
FORMATS = [pytest.param(1, 24, id="pcm24"), pytest.param(3, 32, id="float32")]
NOMES = ("NEUMANN", "CCM8L", "LAPSOLDADO", "LAPJS", "MK41", "BOOM")


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)


def _fmt(tag: int, channels: int, bits: int) -> bytes:
    align = channels * bits // 8
    base = struct.pack("<HHIIHH", tag, channels, RATE, RATE * align, align, bits)
    return base + (struct.pack("<H", 0) if tag == 3 else b"")


def _amostra(rng: random.Random, tag: int) -> bytes:
    if tag == 3:
        return struct.pack("<f", rng.uniform(-1.0, 1.0))
    return rng.randrange(1 << 24).to_bytes(3, "little")


def _canais(channels: int, tag: int, frames: int, semente: int) -> list[bytes]:
    rng = random.Random(semente)
    return [b"".join(_amostra(rng, tag) for _ in range(frames)) for _ in range(channels)]


def _ixml(channels: int, *, interleaves=None) -> bytes:
    """iXML no estilo MixPre: entradas a partir da 3, uma por posicao."""
    interleaves = interleaves or range(1, channels + 1)
    tracks = b"".join(
        b"  <TRACK><CHANNEL_INDEX>%d</CHANNEL_INDEX><INTERLEAVE_INDEX>%d</INTERLEAVE_INDEX>"
        b"<NAME>%s</NAME><FUNCTION>MIC</FUNCTION></TRACK>\n" % (n + 2, n, NOMES[n - 1].encode())
        for n in interleaves
    )
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n<BWFXML>\n'
        b"<SCENE>12A</SCENE>\n<TAKE>005</TAKE>\n"
        b"<TRACK_LIST>\n  <TRACK_COUNT>%d</TRACK_COUNT>\n%s</TRACK_LIST>\n"
        b"<SPEED><TIMECODE_RATE>24/1</TIMECODE_RATE></SPEED>\n</BWFXML>\n"
        % (len(list(interleaves)), tracks)
    )


def _bext() -> bytes:
    return b"descricao".ljust(338, b"\x00") + struct.pack("<QH", 2832278001, 1) + b"\x00" * 254


def _source(path: Path, tag: int, bits: int, canais: list[bytes], before=(), after=()) -> bytes:
    width = bits // 8
    frames = len(canais[0]) // width
    data = b"".join(canal[i * width : (i + 1) * width] for i in range(frames) for canal in canais)
    body = b"WAVE" + _chunk(b"fmt ", _fmt(tag, len(canais), bits))
    body += b"".join(_chunk(cid, payload) for cid, payload in before)
    body += _chunk(b"data", data)
    body += b"".join(_chunk(cid, payload) for cid, payload in after)
    raw = b"RIFF" + struct.pack("<I", len(body)) + body
    path.write_bytes(raw)
    return raw


def _payload(path: Path, chunk_id: bytes) -> bytes | None:
    for cid, offset, size in _iter_riff_chunks(path):
        if cid == chunk_id:
            with path.open("rb") as stream:
                stream.seek(offset)
                return stream.read(size)
    return None


@pytest.mark.parametrize("tag,bits", FORMATS)
@pytest.mark.parametrize("channels", range(1, 7))
def test_split_preserva_cada_amostra(tmp_path, tag, bits, channels):
    """Cada mono e exatamente o canal intercalado correspondente da fonte."""
    source = tmp_path / "poly.wav"
    canais = _canais(channels, tag, 37, semente=channels * 10 + tag)
    original = _source(source, tag, bits, canais)
    outputs = split_channels(source, tmp_path / "media")
    assert len(outputs) == channels
    for numero, (output, esperado) in enumerate(zip(outputs, canais), start=1):
        assert output.name.endswith(f".{numero}.wav")
        assert _payload(output, b"data") == esperado
        meta = read_audio_metadata(output)
        assert (meta["channels"], meta["bits"], meta["sample_rate"]) == (1, bits, RATE)
        assert meta["duration_samples"] == 37
        assert struct.unpack_from("<H", meta["fmt_payload"])[0] == tag
        raw = output.read_bytes()
        assert struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8
    assert source.read_bytes() == original


def test_cache_por_hash_e_reuso_so_com_bytes_iguais(tmp_path):
    source = tmp_path / "poly.wav"
    _source(source, 1, 24, _canais(3, 1, 11, semente=3))
    media = tmp_path / "media"
    outputs = split_channels(source, media)
    antes = [path.read_bytes() for path in outputs]
    assert split_channels(source, media) == outputs
    outputs[1].write_bytes(b"divergente")
    with pytest.raises(ValueError, match="existing mono source differs"):
        split_channels(source, media)
    assert outputs[1].read_bytes() == b"divergente"
    assert [outputs[0].read_bytes(), outputs[2].read_bytes()] == [antes[0], antes[2]]


@pytest.mark.parametrize("channels", (1, 4))
def test_metadata_bext_intacto_e_track_list_reduzido(tmp_path, channels):
    """Cada mono declara so a propria entrada; o resto do iXML segue igual."""
    source = tmp_path / "poly.wav"
    bext, ixml = _bext(), _ixml(channels)
    _source(
        source,
        3,
        32,
        _canais(channels, 3, 9, semente=4),
        before=((b"bext", bext),),
        after=((b"iXML", ixml),),
    )
    outputs = split_channels(source, tmp_path / "media", preserve_metadata=True)
    inicio, fim = ixml.index(b"<TRACK_LIST>"), ixml.index(b"</TRACK_LIST>")
    for numero, output in enumerate(outputs, start=1):
        assert [cid for cid, _, _ in _iter_riff_chunks(output)] == [
            b"fmt ",
            b"bext",
            b"data",
            b"iXML",
        ]
        assert _payload(output, b"bext") == bext
        reduzido = _payload(output, b"iXML")
        assert reduzido[:inicio] == ixml[:inicio]
        assert reduzido.endswith(ixml[fim:])
        assert b"<TRACK_COUNT>1</TRACK_COUNT>" in reduzido
        assert b"<FUNCTION>MIC</FUNCTION>" in reduzido
        info = read_bwf(output)
        assert info.ixml.track_list == [IxmlTrack(numero + 2, 1, NOMES[numero - 1])]
        assert (info.ixml.scene, info.ixml.take) == ("12A", "005")
        assert info.bext["time_reference"] == 2832278001
        raw = output.read_bytes()
        assert struct.unpack_from("<I", raw, 4)[0] == len(raw) - 8
        assert len(raw) % 2 == 0


def test_stereo_copia_o_ixml_byte_a_byte_sem_reduzir(tmp_path):
    """split_stereo_source (export) nao reduz o TRACK_LIST: L e R levam o iXML da fonte."""
    source = tmp_path / "stereo.wav"
    canais = _canais(2, 1, 5, semente=8)
    ixml = _ixml(2)
    _source(source, 1, 24, canais, before=((b"iXML", ixml),))
    left, right = split_stereo_source(source, tmp_path / "media", preserve_metadata=True)
    assert left.name.endswith(".L.wav") and right.name.endswith(".R.wav")
    assert _payload(left, b"iXML") == _payload(right, b"iXML") == ixml
    assert len(read_bwf(left).ixml.track_list) == 2
    assert [_payload(left, b"data"), _payload(right, b"data")] == canais


def test_ixml_sem_track_list_ou_quebrado_e_copiado_como_esta(tmp_path):
    for nome, ixml in (
        ("sem_lista.wav", b"<BWFXML><SCENE>1</SCENE></BWFXML>"),
        ("quebrado.wav", b"<BWFXML><TRACK_LIST><TRACK>"),
    ):
        source = tmp_path / nome
        _source(source, 1, 24, _canais(3, 1, 4, semente=1), after=((b"iXML", ixml),))
        for output in split_channels(source, tmp_path / "media", preserve_metadata=True):
            assert _payload(output, b"iXML") == ixml


def test_posicao_sem_entrada_fica_com_track_list_vazio(tmp_path):
    ixml = _ixml(2, interleaves=[1])
    reduzido = reduce_track_list(ixml, 2)
    assert b"<TRACK_COUNT>0</TRACK_COUNT>" in reduzido
    assert b"<TRACK>" not in reduzido
    assert reduce_track_list(ixml, 1) == ixml  # unica entrada na posicao: nada muda


def test_stereo_recusa_outra_contagem_e_split_recusa_aiff_com_flag(tmp_path):
    source = tmp_path / "tres.wav"
    _source(source, 1, 24, _canais(3, 1, 4, semente=2))
    with pytest.raises(ValueError, match="requires stereo audio"):
        split_stereo_source(source, tmp_path / "media")
    aiff = tmp_path / "som.aiff"
    comm = struct.pack(">HIH", 3, 0, 16) + bytes.fromhex("400ebb80000000000000")
    ssnd = b"SSND" + struct.pack(">III", 8, 0, 0)
    body = b"AIFFCOMM" + struct.pack(">I", len(comm)) + comm + ssnd
    aiff.write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    with pytest.raises(ValueError, match="requires a WAV source"):
        split_channels(aiff, tmp_path / "media", preserve_metadata=True)
