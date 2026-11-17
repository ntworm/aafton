"""Leitura de fmt, bext e iXML (bwf_meta) sobre WAVs sinteticos.

Nenhuma gravacao real entra no repositorio: os arquivos imitam a estrutura
medida no MixPre-6 II e no Wave Agent, com bytes montados aqui.
"""

import random
import struct
from fractions import Fraction
from pathlib import Path

import pytest

from aafton import bwf_meta, media_extract
from aafton.bwf_meta import IxmlTrack, iter_riff_chunks, parse_bext, read_bwf

RATE = 48000
TIME_REFERENCE = 2832278001

MIXPRE_IXML = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b"<BWFXML>\n"
    b"<IXML_VERSION>1.5</IXML_VERSION>\n"
    b"<PROJECT>05-06-25</PROJECT>\n"
    b"<SCENE>21CENA12APL01TK05</SCENE>\n"
    b"<TAKE>007</TAKE>\n"
    b"<TAPE>250506</TAPE>\n"
    b"<CIRCLED>TRUE</CIRCLED>\n"
    b"<FILE_UID>USSDVTH1322152381250506GND000401</FILE_UID>\n"
    b"<NOTE>boom fora</NOTE>\n"
    b"<SPEED>\n"
    b"<NOTE></NOTE>\n"
    b"<MASTER_SPEED>24/1</MASTER_SPEED>\n"
    b"<CURRENT_SPEED>24/1</CURRENT_SPEED>\n"
    b"<TIMECODE_RATE>24000/1001</TIMECODE_RATE>\n"
    b"<TIMECODE_FLAG>NDF</TIMECODE_FLAG>\n"
    b"<FILE_SAMPLE_RATE>48000</FILE_SAMPLE_RATE>\n"
    b"<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>0</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI>\n"
    b"<TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>2832278001</TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO>\n"
    b"</SPEED>\n"
    b"<TRACK_LIST>\n"
    b"<TRACK_COUNT>4</TRACK_COUNT>\n"
    b"<TRACK><CHANNEL_INDEX>3</CHANNEL_INDEX><INTERLEAVE_INDEX>1</INTERLEAVE_INDEX>"
    b"<NAME>NEUMANN</NAME></TRACK>\n"
    b"<TRACK><CHANNEL_INDEX>4</CHANNEL_INDEX><INTERLEAVE_INDEX>2</INTERLEAVE_INDEX>"
    b"<NAME>CCM8L</NAME></TRACK>\n"
    b"<TRACK><CHANNEL_INDEX>5</CHANNEL_INDEX><INTERLEAVE_INDEX>3</INTERLEAVE_INDEX>"
    b"<NAME>LAPSOLDADO</NAME></TRACK>\n"
    b"<TRACK><CHANNEL_INDEX>6</CHANNEL_INDEX><INTERLEAVE_INDEX>4</INTERLEAVE_INDEX>"
    b"<NAME>LAPJS</NAME></TRACK>\n"
    b"</TRACK_LIST>\n"
    b"<FILE_SET><TOTAL_FILES>1</TOTAL_FILES>"
    b"<FAMILY_UID>USSDVTH1322152381250506GND000400</FAMILY_UID>"
    b"<FAMILY_NAME>21CENA12APL01TK05-007</FAMILY_NAME></FILE_SET>\n"
    b"</BWFXML>\n"
)


def _chunk(chunk_id: bytes, payload: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)


def _riff(chunks) -> bytes:
    body = b"WAVE" + b"".join(_chunk(chunk_id, payload) for chunk_id, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _fmt(tag: int, channels: int, bits: int) -> bytes:
    align = channels * bits // 8
    base = struct.pack("<HHIIHH", tag, channels, RATE, RATE * align, align, bits)
    return base + (struct.pack("<H", 0) if tag == 3 else b"")


def _bext(time_reference: int = TIME_REFERENCE) -> bytes:
    """bext versao 1 de 602 bytes, como o do MixPre."""
    return (
        b"sSCENE=21CENA12APL01TK05\r\nsTAKE=007\r\n".ljust(256, b"\x00")
        + b"SoundDev: MixPre-6 II".ljust(32, b"\x00")
        + b"USSDVTH1322152381".ljust(32, b"\x00")
        + b"2025-05-06"
        + b"15:44:03"
        + struct.pack("<QH", time_reference, 1)
        + b"\x00" * 254
    )


def _write(path: Path, chunks) -> Path:
    path.write_bytes(_riff(chunks))
    return path


def test_mixpre_polywav_le_todos_os_campos(tmp_path):
    """Polywav de 4 canais float 32 com bext e iXML 1.5, entradas 3 a 6."""
    frames = 10
    wav = _write(
        tmp_path / "21CENA12APL01TK05-007.WAV",
        [
            (b"bext", _bext()),
            (b"fmt ", _fmt(3, 4, 32)),
            (b"iXML", MIXPRE_IXML),
            (b"data", b"\x01" * (frames * 16)),
        ],
    )
    info = read_bwf(wav)
    assert (info.channels, info.sample_rate, info.bits, info.frames) == (4, RATE, 32, frames)
    assert info.bext == {
        "description": "sSCENE=21CENA12APL01TK05\r\nsTAKE=007\r\n",
        "originator": "SoundDev: MixPre-6 II",
        "origination_date": "2025-05-06",
        "origination_time": "15:44:03",
        "time_reference": TIME_REFERENCE,
        "version": 1,
    }
    ixml = info.ixml
    assert (ixml.project, ixml.scene, ixml.take, ixml.tape) == (
        "05-06-25",
        "21CENA12APL01TK05",
        "007",
        "250506",
    )
    assert ixml.circled is True
    assert ixml.note == "boom fora"
    assert ixml.file_uid == "USSDVTH1322152381250506GND000401"
    assert ixml.family_uid == "USSDVTH1322152381250506GND000400"
    assert ixml.family_name == "21CENA12APL01TK05-007"
    assert ixml.master_speed == Fraction(24)
    assert ixml.timecode_rate == Fraction(24000, 1001)
    assert ixml.timecode_flag == "NDF"
    assert ixml.file_sample_rate == RATE
    assert ixml.timestamp_samples_since_midnight_hi == 0
    assert ixml.timestamp_samples_since_midnight_lo == TIME_REFERENCE
    assert ixml.timestamp_samples == TIME_REFERENCE
    assert ixml.track_list == [
        IxmlTrack(3, 1, "NEUMANN"),
        IxmlTrack(4, 2, "CCM8L"),
        IxmlTrack(5, 3, "LAPSOLDADO"),
        IxmlTrack(6, 4, "LAPJS"),
    ]
    assert info.notes == []


def test_wave_agent_com_chunks_extras(tmp_path):
    """DGDA, minf, regn e umid em volta da metadata nao atrapalham a leitura."""
    ixml = MIXPRE_IXML.replace(b"<CIRCLED>TRUE", b"<CIRCLED>false")
    wav = _write(
        tmp_path / "AMBCATEDRAL-003.WAV",
        [
            (b"DGDA", b"\x07" * 33),
            (b"bext", _bext(1827062001)),
            (b"fmt ", _fmt(3, 4, 32)),
            (b"iXML", ixml + b"\x00" * 7),
            (b"minf", b"\x02" * 17),
            (b"regn", b"\x03" * 92),
            (b"umid", b"\x04" * 24),
            (b"data", b"\x00" * (3 * 16)),
        ],
    )
    info = read_bwf(wav)
    assert (info.channels, info.frames) == (4, 3)
    assert info.bext["time_reference"] == 1827062001
    assert info.ixml.circled is False
    assert info.ixml.scene == "21CENA12APL01TK05"
    assert [track.channel_index for track in info.ixml.track_list] == [3, 4, 5, 6]


def test_ixml_quebrado_vira_none_com_nota(tmp_path):
    wav = _write(
        tmp_path / "quebrado.wav",
        [
            (b"fmt ", _fmt(1, 2, 24)),
            (b"bext", _bext()),
            (b"iXML", b"<BWFXML><SCENE>12A</SCE"),
            (b"data", b"\x00" * 12),
        ],
    )
    info = read_bwf(wav)
    assert info.ixml is None
    assert info.bext["time_reference"] == TIME_REFERENCE
    assert any(note.startswith("malformed iXML") for note in info.notes)


def test_ixml_com_doctype_e_raiz_errada_viram_none(tmp_path):
    for nome, payload in (
        ("doctype.wav", b'<!DOCTYPE x [<!ENTITY a "b">]><BWFXML/>'),
        ("raiz.wav", b"<OUTRO><SCENE>1</SCENE></OUTRO>"),
    ):
        wav = _write(
            tmp_path / nome,
            [(b"fmt ", _fmt(1, 1, 16)), (b"iXML", payload), (b"data", b"\x00\x00")],
        )
        info = read_bwf(wav)
        assert info.ixml is None
        assert len(info.notes) == 2  # sem bext e iXML recusado


def test_campo_faltando_e_numero_invalido_nao_levantam(tmp_path):
    ixml = (
        MIXPRE_IXML.replace(b"<TAKE>007</TAKE>\n", b"")
        .replace(b"24000/1001", b"abc")
        .replace(b"<FILE_SAMPLE_RATE>48000", b"<FILE_SAMPLE_RATE>48k")
        .replace(b"<CIRCLED>TRUE", b"<CIRCLED>MAYBE")
        .replace(b"<TIMECODE_FLAG>NDF", b"<TIMECODE_FLAG>XX")
        .replace(b"<MASTER_SPEED>24/1", b"<MASTER_SPEED>24/0")
        .replace(b"<CHANNEL_INDEX>5<", b"<CHANNEL_INDEX>cinco<")
    )
    wav = _write(
        tmp_path / "campos.wav",
        [(b"fmt ", _fmt(3, 4, 32)), (b"iXML", ixml), (b"data", b"\x00" * 16)],
    )
    info = read_bwf(wav)
    ixml_lido = info.ixml
    assert ixml_lido.take is None
    assert ixml_lido.timecode_rate is None
    assert ixml_lido.master_speed is None
    assert ixml_lido.file_sample_rate is None
    assert ixml_lido.circled is None
    assert ixml_lido.timecode_flag is None
    assert ixml_lido.track_list[2] == IxmlTrack(None, 3, "LAPSOLDADO")
    assert ixml_lido.scene == "21CENA12APL01TK05"
    for trecho in (
        "iXML without TAKE",
        "SPEED/TIMECODE_RATE is not a number",
        "SPEED/MASTER_SPEED is not a number",
        "SPEED/FILE_SAMPLE_RATE is not a number",
        "CIRCLED has an unknown value",
        "SPEED/TIMECODE_FLAG has an unknown value",
        "CHANNEL_INDEX is not a number",
        "no bext chunk",
    ):
        assert any(trecho in note for note in info.notes), trecho


def test_sem_metadados_estereo_24_bits(tmp_path):
    frames = 7
    wav = _write(
        tmp_path / "250923_0001.wav",
        [(b"fmt ", _fmt(1, 2, 24)), (b"data", b"\x05" * (frames * 6 + 1))],
    )
    info = read_bwf(wav)
    assert (info.channels, info.sample_rate, info.bits, info.frames) == (2, RATE, 24, frames)
    assert info.bext is None
    assert info.ixml is None
    assert info.notes == ["no bext chunk", "no iXML chunk"]


def test_bext_truncado_vira_nota(tmp_path):
    wav = _write(
        tmp_path / "bext_curto.wav",
        [(b"fmt ", _fmt(1, 1, 16)), (b"bext", b"\x00" * 100), (b"data", b"\x00\x00")],
    )
    info = read_bwf(wav)
    assert info.bext is None
    assert "bext chunk truncated" in info.notes


def test_rf64_e_nao_wave_sao_recusados(tmp_path):
    rf64 = tmp_path / "longo.wav"
    rf64.write_bytes(b"RF64" + b"\xff\xff\xff\xff" + b"WAVE" + _chunk(b"ds64", b"\x00" * 28))
    with pytest.raises(ValueError, match="^RF64 not supported yet"):
        read_bwf(rf64)
    aiff = tmp_path / "som.aiff"
    aiff.write_bytes(b"FORM" + struct.pack(">I", 4) + b"AIFF")
    with pytest.raises(ValueError, match="format not supported"):
        read_bwf(aiff)
    sem_data = _write(tmp_path / "sem_data.wav", [(b"fmt ", _fmt(1, 1, 16))])
    with pytest.raises(ValueError, match="without a fmt or data chunk"):
        read_bwf(sem_data)


def test_parse_bext_continua_em_media_extract(tmp_path):
    """O nome antigo segue importavel e ganha data e hora de origem."""
    assert media_extract.parse_bext is bwf_meta.parse_bext
    wav = _write(
        tmp_path / "bext.wav",
        [(b"fmt ", _fmt(1, 1, 16)), (b"bext", _bext()), (b"data", b"\x00\x00")],
    )
    bext = media_extract.parse_bext(wav)
    assert (bext["origination_date"], bext["origination_time"]) == ("2025-05-06", "15:44:03")
    assert bext["time_reference"] == TIME_REFERENCE
    assert parse_bext(_write(tmp_path / "nada.wav", [(b"data", b"")])) is None


def _layout_aleatorio(rng: random.Random) -> list[tuple[bytes, bytes]]:
    """Chunks com ids e tamanhos aleatorios, impares inclusos."""
    chunks = []
    for _ in range(rng.randint(0, 7)):
        chunk_id = bytes(rng.choice(b"abcdefghijklmnopqrstuvwxyz ") for _ in range(4))
        chunks.append((chunk_id, bytes(rng.randrange(256) for _ in range(rng.randint(0, 41)))))
    return chunks


@pytest.mark.parametrize("caso", range(60))
def test_propriedade_walker_em_layouts_gerados(tmp_path, caso):
    """O walker devolve ids, offsets e tamanhos exatos; corte sempre falha."""
    rng = random.Random(20261114 + caso)
    chunks = _layout_aleatorio(rng)
    raw = _riff(chunks)
    wav = tmp_path / "gerado.wav"
    wav.write_bytes(raw)
    lidos = list(iter_riff_chunks(wav))
    assert [(chunk_id, size) for chunk_id, _, size in lidos] == [
        (chunk_id, len(payload)) for chunk_id, payload in chunks
    ]
    for (_, offset, size), (_, payload) in zip(lidos, chunks):
        assert offset % 2 == 0
        assert raw[offset : offset + size] == payload
    if len(raw) > 12:
        wav.write_bytes(raw[: rng.randrange(12, len(raw))])
        with pytest.raises(ValueError, match="truncated"):
            list(iter_riff_chunks(wav))
