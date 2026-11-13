"""Fontes mono para AAF: separa bytes de amostras, sem ganho ou resampling."""

import hashlib
import struct
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from .media_extract import read_audio_metadata

BLOCK = 65536  # teto de bytes lidos por vez na copia de metadata
CHUNK_HEADER = 8  # bytes do cabecalho de um chunk RIFF
RIFF_HEADER = 12  # bytes do cabecalho RIFF/WAVE
METADATA_IDS = (b"bext", b"iXML")


class _Layout(NamedTuple):
    """Chunks de metadata preservados, antes e depois do chunk data."""

    antes: list[tuple[bytes, int, int]]
    depois: list[tuple[bytes, int, int]]


class _Plan(NamedTuple):
    """Plano de escrita dos monos: fmt, metadata preservada e tamanho do data."""

    fmt: bytes
    antes: list[tuple[bytes, int, int]]
    depois: list[tuple[bytes, int, int]]
    riff_size: int
    data_size: int


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_riff_chunks(source: Path) -> Iterator[tuple[bytes, int, int]]:
    """Itera os chunks de um RIFF/WAVE como (id, offset do payload, tamanho).

    Valida o cabecalho RIFF/WAVE e os limites de cada chunk com o seu
    padding, sem ler o conteudo dos payloads.
    """
    source = Path(source)
    limit = source.stat().st_size
    with source.open("rb") as stream:
        header = stream.read(RIFF_HEADER)
        if len(header) < RIFF_HEADER:
            raise ValueError(f"{source} too short to have a RIFF header")
        if (header[:4], header[8:12]) != (b"RIFF", b"WAVE"):
            raise ValueError(f"{source} format not supported: {header[:4]!r} {header[8:12]!r}")
        end = CHUNK_HEADER + struct.unpack_from("<I", header, 4)[0]
        if end < RIFF_HEADER:
            raise ValueError(f"{source} RIFF size smaller than the header: {end} < {RIFF_HEADER}")
        if end > limit:
            raise ValueError(f"{source} truncated: RIFF declares {end - CHUNK_HEADER} bytes")
        offset = RIFF_HEADER
        while offset + CHUNK_HEADER <= end:
            stream.seek(offset)
            raw = stream.read(CHUNK_HEADER)
            if len(raw) < CHUNK_HEADER:
                raise ValueError(f"{source} truncated before the end of the RIFF")
            chunk_id, payload_size = struct.unpack("<4sI", raw)
            payload_offset = offset + CHUNK_HEADER
            if payload_offset + payload_size + (payload_size % 2) > end:
                raise ValueError(f"{source} chunk {chunk_id!r} outside the RIFF bounds")
            yield chunk_id, payload_offset, payload_size
            offset = payload_offset + payload_size + (payload_size % 2)
        if offset != end:
            raise ValueError(f"{source} truncated: partial tail before the end of the RIFF")


def _layout(source: Path) -> _Layout:
    """Localiza bext/iXML na fonte, mantendo a ordem relativa ao chunk data."""
    antes: list[tuple[bytes, int, int]] = []
    depois: list[tuple[bytes, int, int]] = []
    viu_data = False
    for chunk_id, offset, size in _iter_riff_chunks(source):
        if chunk_id == b"data":
            viu_data = True
        elif chunk_id in METADATA_IDS:
            if viu_data:
                depois.append((chunk_id, offset, size))
            else:
                antes.append((chunk_id, offset, size))
    if not viu_data:
        raise ValueError(f"{source} without a data chunk")
    return _Layout(antes, depois)


def _mono_fmt(meta: dict) -> bytes:
    """fmt mono derivado da fonte stereo: canal unico, mask de canal zerada."""
    bits, frames = meta["bits"], meta["duration_samples"]
    if meta["channels"] != 2 or bits not in (8, 16, 24, 32):
        raise ValueError("splitting requires stereo audio of 8, 16, 24 or 32 bits")
    width, rate = bits // 8, meta["sample_rate"]
    if meta["data_offset"] is None or meta["data_size"] != frames * width * 2:
        raise ValueError("incomplete or misaligned stereo data")
    if meta["format"] == "wav":
        fmt = bytearray(meta["fmt_payload"])
        struct.pack_into("<HIIH", fmt, 2, 1, rate, rate * width, width)
        if struct.unpack_from("<H", fmt)[0] == 0xFFFE:
            struct.pack_into("<I", fmt, 20, 0)  # mono sem máscara de speaker
        return bytes(fmt)
    if bits == 8:
        raise ValueError("AIFF 8 bits requires a signed/unsigned conversion that is not supported")
    return struct.pack("<HHIIHH", 1, 1, rate, rate * width, width, bits)


def _chunked(payload_size: int) -> int:
    """Bytes de um chunk completo: cabecalho, payload e padding."""
    return CHUNK_HEADER + payload_size + (payload_size % 2)


def _riff_size(fmt: bytes, data_size: int, extras: list[tuple[bytes, int, int]]) -> int:
    """Tamanho do RIFF: todos os cabecalhos, payloads e padding."""
    total = 4 + _chunked(len(fmt)) + _chunked(data_size)
    for _, _, size in extras:
        total += _chunked(size)
    return total


def _copy_chunk(original, stream, chunk_id: bytes, payload_offset: int, payload_size: int) -> None:
    """Copia um chunk da fonte para o stream, sem ler o payload inteiro."""
    stream.write(struct.pack("<4sI", chunk_id, payload_size))
    original.seek(payload_offset)
    remaining = payload_size
    while remaining:
        block = original.read(min(remaining, BLOCK))
        if not block:
            raise ValueError("truncated stereo source")
        stream.write(block)
        remaining -= len(block)
    if payload_size % 2:
        original.seek(payload_offset + payload_size)
        stream.write(original.read(1) or b"\0")


def _write_head(stream, original, plan: _Plan) -> None:
    """Escreve RIFF, fmt, os chunks de metadata antes do data e o header data."""
    stream.write(struct.pack("<4sI4s", b"RIFF", plan.riff_size, b"WAVE"))
    stream.write(struct.pack("<4sI", b"fmt ", len(plan.fmt)))
    stream.write(plan.fmt)
    stream.write(b"\0" * (len(plan.fmt) % 2))
    for chunk_id, offset, size in plan.antes:
        _copy_chunk(original, stream, chunk_id, offset, size)
    stream.write(struct.pack("<4sI", b"data", plan.data_size))


def _write_tail(stream, original, plan: _Plan) -> None:
    """Fecha o chunk data com o padding e copia os chunks de metadata finais."""
    stream.write(b"\0" * (plan.data_size % 2))
    for chunk_id, offset, size in plan.depois:
        _copy_chunk(original, stream, chunk_id, offset, size)


def _write_frames(original, left, right, meta: dict, width: int) -> None:
    """Separa os canais stereo em dois fluxos mono, sem alterar os bytes."""
    original.seek(meta["data_offset"])
    remaining = meta["duration_samples"]
    while remaining:
        count = min(remaining, 65536)
        raw = original.read(count * width * 2)
        if len(raw) != count * width * 2:
            raise ValueError("truncated stereo source")
        for channel, stream in enumerate((left, right)):
            mono = bytearray(count * width)
            for byte in range(width):
                source_byte = width - 1 - byte if meta["is_big_endian"] else byte
                mono[byte::width] = raw[channel * width + source_byte :: width * 2]
            stream.write(mono)
        remaining -= count


def _publish(staging: list[Path], directory: Path) -> list[Path]:
    """Move os monos estagiados para o cache, verificando os bytes existentes."""
    outputs = []
    for channel, temporary_path in zip(("L", "R"), staging):
        digest = _digest(temporary_path)
        output = directory / f"{digest}.{channel}.wav"
        if output.exists() or output.is_symlink():
            if output.is_symlink() or not output.is_file() or _digest(output) != digest:
                raise ValueError(f"existing mono source differs: {output}")
        else:
            temporary_path.rename(output)
        outputs.append(output.resolve())
    return outputs


def split_stereo_source(
    source: Path, directory: Path, *, preserve_metadata: bool = False
) -> tuple[Path, Path]:
    """WAV/AIFF estéreo vira dois WAVs mono com todas as amostras intactas.

    Nomes incluem hash do WAV resultante: exports distintos e fontes homônimas
    não compartilham um cache por nome. Arquivos existentes só são reutilizados
    quando seus bytes coincidem; a fonte original nunca é escrita.

    Com ``preserve_metadata`` os chunks bext e iXML de uma fonte WAV são
    copiados para os dois monos, com payload, ordem relativa ao chunk data e
    padding intactos; a flag só vale para WAV.
    """
    meta = read_audio_metadata(source)
    if preserve_metadata and meta["format"] != "wav":
        raise ValueError("metadata preservation requires a WAV source")
    fmt = _mono_fmt(meta)
    width = meta["bits"] // 8
    data_size = meta["duration_samples"] * width
    layout = _layout(source) if preserve_metadata else _Layout([], [])
    riff_size = _riff_size(fmt, data_size, layout.antes + layout.depois)
    if riff_size > 0xFFFFFFFF:
        raise ValueError("source exceeds the supported RIFF size")
    plan = _Plan(fmt, layout.antes, layout.depois, riff_size, data_size)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="split-", dir=directory) as temporary:
        staging = [Path(temporary) / f"{channel}.wav" for channel in range(2)]
        with (
            source.open("rb") as original,
            staging[0].open("wb") as left,
            staging[1].open("wb") as right,
        ):
            for stream in (left, right):
                _write_head(stream, original, plan)
            _write_frames(original, left, right, meta, width)
            for stream in (left, right):
                _write_tail(stream, original, plan)
        return tuple(_publish(staging, directory))
