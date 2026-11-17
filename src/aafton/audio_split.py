"""Fontes mono para AAF e som direto: separa bytes de amostras, sem ganho ou resampling."""

import hashlib
import struct
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import NamedTuple

from .bwf_meta import BLOCK, CHUNK_HEADER, METADATA_MAX, read_payload, reduce_track_list
from .bwf_meta import iter_riff_chunks as _iter_riff_chunks
from .media_extract import read_audio_metadata

METADATA_IDS = (b"bext", b"iXML")
FRAMES_PER_READ = 65536  # frames lidos por vez do chunk data


class _Chunk(NamedTuple):
    """Chunk de metadata da fonte; ``payload`` pronto ou None para copiar em blocos."""

    chunk_id: bytes
    offset: int
    size: int
    payload: bytes | None = None


class _Layout(NamedTuple):
    """Chunks de metadata preservados, antes e depois do chunk data."""

    antes: list[_Chunk]
    depois: list[_Chunk]


class _Plan(NamedTuple):
    """Plano de escrita de um mono: fmt, metadata preservada e tamanho do data."""

    fmt: bytes
    antes: list[_Chunk]
    depois: list[_Chunk]
    riff_size: int
    data_size: int


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _layout(source: Path) -> _Layout:
    """Localiza bext/iXML na fonte, mantendo a ordem relativa ao chunk data."""
    antes: list[_Chunk] = []
    depois: list[_Chunk] = []
    viu_data = False
    for chunk_id, offset, size in _iter_riff_chunks(source):
        if chunk_id == b"data":
            viu_data = True
        elif chunk_id in METADATA_IDS:
            (depois if viu_data else antes).append(_Chunk(chunk_id, offset, size))
    if not viu_data:
        raise ValueError(f"{source} without a data chunk")
    return _Layout(antes, depois)


def _mono_fmt(meta: dict, stereo: bool) -> bytes:
    """fmt mono derivado da fonte: canal unico, mask de canal zerada."""
    bits, frames, channels = meta["bits"], meta["duration_samples"], meta["channels"]
    if bits not in (8, 16, 24, 32) or (channels != 2 if stereo else channels < 1):
        if stereo:
            raise ValueError("splitting requires stereo audio of 8, 16, 24 or 32 bits")
        raise ValueError("splitting requires audio of 8, 16, 24 or 32 bits")
    width, rate = bits // 8, meta["sample_rate"]
    if meta["data_offset"] is None or meta["data_size"] != frames * width * channels:
        raise ValueError("incomplete or misaligned audio data")
    if meta["format"] == "wav":
        fmt = bytearray(meta["fmt_payload"])
        struct.pack_into("<HIIH", fmt, 2, 1, rate, rate * width, width)
        if struct.unpack_from("<H", fmt)[0] == 0xFFFE:
            struct.pack_into("<I", fmt, 20, 0)  # mono sem mascara de speaker
        return bytes(fmt)
    if bits == 8:
        raise ValueError("AIFF 8 bits requires a signed/unsigned conversion that is not supported")
    return struct.pack("<HHIIHH", 1, 1, rate, rate * width, width, bits)


def _chunked(payload_size: int) -> int:
    """Bytes de um chunk completo: cabecalho, payload e padding."""
    return CHUNK_HEADER + payload_size + (payload_size % 2)


def _riff_size(fmt: bytes, data_size: int, extras: list[_Chunk]) -> int:
    """Tamanho do RIFF: todos os cabecalhos, payloads e padding."""
    total = 4 + _chunked(len(fmt)) + _chunked(data_size)
    for chunk in extras:
        total += _chunked(chunk.size)
    return total


def _mono_chunk(chunk: _Chunk, ixml: dict, index: int) -> _Chunk:
    """Chunk do mono ``index``: iXML com TRACK_LIST reduzido, o resto intacto."""
    payload = ixml.get(chunk.offset) if chunk.chunk_id == b"iXML" else None
    if payload is None:
        return chunk
    reduzido = reduce_track_list(payload, index + 1)
    if reduzido is None:
        return chunk
    return chunk._replace(size=len(reduzido), payload=reduzido)


def _ixml_da_fonte(source: Path, layout: _Layout, reduzir: bool) -> dict:
    """iXML da fonte por offset, lido uma vez; vazio quando o iXML segue como esta."""
    if not reduzir:
        return {}
    with source.open("rb") as original:
        return {
            chunk.offset: read_payload(original, chunk.offset, chunk.size)
            for chunk in layout.antes + layout.depois
            if chunk.chunk_id == b"iXML" and chunk.size <= METADATA_MAX
        }


def _plans(
    source: Path, fmt: bytes, data_size: int, layout: _Layout, count: int, reduzir: bool
) -> list[_Plan]:
    """Um plano por mono; com ``reduzir`` o iXML de cada mono leva so a entrada dele."""
    ixml = _ixml_da_fonte(source, layout, reduzir)
    plans = []
    for index in range(count):
        antes = [_mono_chunk(chunk, ixml, index) for chunk in layout.antes]
        depois = [_mono_chunk(chunk, ixml, index) for chunk in layout.depois]
        riff_size = _riff_size(fmt, data_size, antes + depois)
        if riff_size > 0xFFFFFFFF:
            raise ValueError("source exceeds the supported RIFF size")
        plans.append(_Plan(fmt, antes, depois, riff_size, data_size))
    return plans


def _copy_chunk(original, stream, chunk: _Chunk) -> None:
    """Escreve um chunk: payload pronto ou copia da fonte, sem ler tudo de uma vez."""
    stream.write(struct.pack("<4sI", chunk.chunk_id, chunk.size))
    if chunk.payload is not None:
        stream.write(chunk.payload + b"\0" * (chunk.size % 2))
        return
    original.seek(chunk.offset)
    remaining = chunk.size
    while remaining:
        block = original.read(min(remaining, BLOCK))
        if not block:
            raise ValueError("truncated source")
        stream.write(block)
        remaining -= len(block)
    if chunk.size % 2:
        original.seek(chunk.offset + chunk.size)
        stream.write(original.read(1) or b"\0")


def _write_head(stream, original, plan: _Plan) -> None:
    """Escreve RIFF, fmt, os chunks de metadata antes do data e o header data."""
    stream.write(struct.pack("<4sI4s", b"RIFF", plan.riff_size, b"WAVE"))
    stream.write(struct.pack("<4sI", b"fmt ", len(plan.fmt)))
    stream.write(plan.fmt)
    stream.write(b"\0" * (len(plan.fmt) % 2))
    for chunk in plan.antes:
        _copy_chunk(original, stream, chunk)
    stream.write(struct.pack("<4sI", b"data", plan.data_size))


def _write_tail(stream, original, plan: _Plan) -> None:
    """Fecha o chunk data com o padding e copia os chunks de metadata finais."""
    stream.write(b"\0" * (plan.data_size % 2))
    for chunk in plan.depois:
        _copy_chunk(original, stream, chunk)


def _write_frames(original, streams: list, meta: dict, width: int) -> None:
    """Separa os N canais intercalados em N fluxos mono, sem alterar os bytes."""
    frame = width * len(streams)
    ordem = [width - 1 - byte if meta["is_big_endian"] else byte for byte in range(width)]
    original.seek(meta["data_offset"])
    remaining = meta["duration_samples"]
    while remaining:
        count = min(remaining, FRAMES_PER_READ)
        raw = original.read(count * frame)
        if len(raw) != count * frame:
            raise ValueError("truncated source")
        for channel, stream in enumerate(streams):
            mono = bytearray(count * width)
            for byte, source_byte in enumerate(ordem):
                mono[byte::width] = raw[channel * width + source_byte :: frame]
            stream.write(mono)
        remaining -= count


def _publish(staging: list[Path], directory: Path, labels: list[str]) -> list[Path]:
    """Move os monos estagiados para o cache, verificando os bytes existentes."""
    outputs = []
    for label, temporary_path in zip(labels, staging):
        digest = _digest(temporary_path)
        output = directory / f"{digest}.{label}.wav"
        if output.exists() or output.is_symlink():
            if output.is_symlink() or not output.is_file() or _digest(output) != digest:
                raise ValueError(f"existing mono source differs: {output}")
        else:
            temporary_path.rename(output)
        outputs.append(output.resolve())
    return outputs


def _split(
    source: Path, directory: Path, labels: list[str], preserve_metadata: bool, stereo: bool
) -> tuple[Path, ...]:
    """Escreve um mono por canal da fonte, com os nomes ``<hash>.<label>.wav``."""
    meta = read_audio_metadata(source)
    if preserve_metadata and meta["format"] != "wav":
        raise ValueError("metadata preservation requires a WAV source")
    fmt = _mono_fmt(meta, stereo)
    width = meta["bits"] // 8
    labels = labels or [str(n) for n in range(1, meta["channels"] + 1)]
    data_size = meta["duration_samples"] * width
    layout = _layout(source) if preserve_metadata else _Layout([], [])
    # O estereo do export copia o iXML byte a byte; so o split de N canais reduz.
    plans = _plans(source, fmt, data_size, layout, len(labels), reduzir=not stereo)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="split-", dir=directory) as temporary:
        staging = [Path(temporary) / f"{channel}.wav" for channel in range(len(labels))]
        with source.open("rb") as original, ExitStack() as stack:
            streams = [stack.enter_context(path.open("wb")) for path in staging]
            for stream, plan in zip(streams, plans):
                _write_head(stream, original, plan)
            _write_frames(original, streams, meta, width)
            for stream, plan in zip(streams, plans):
                _write_tail(stream, original, plan)
        return tuple(_publish(staging, directory, labels))


def split_channels(
    source: Path, directory: Path, *, preserve_metadata: bool = False
) -> tuple[Path, ...]:
    """WAV/AIFF de N canais vira N WAVs mono com todas as amostras intactas.

    O mono do canal na posicao n (a partir de 1) se chama ``<hash>.<n>.wav``;
    nomes, cache e fonte seguem ``split_stereo_source``. Com
    ``preserve_metadata`` (so WAV) o bext e copiado sem mudanca e cada mono
    recebe o iXML com o TRACK_LIST reduzido a entrada dele (CHANNEL_INDEX e
    NAME mantidos, INTERLEAVE_INDEX 1, TRACK_COUNT 1); o resto do iXML segue
    intacto, e um iXML sem TRACK_LIST ou ilegivel e copiado como esta.
    """
    return _split(Path(source), Path(directory), [], preserve_metadata, stereo=False)


def split_stereo_source(
    source: Path, directory: Path, *, preserve_metadata: bool = False
) -> tuple[Path, Path]:
    """WAV/AIFF estereo vira dois WAVs mono com todas as amostras intactas.

    Nomes incluem hash do WAV resultante: exports distintos e fontes homonimas
    nao compartilham um cache por nome. Arquivos existentes so sao reutilizados
    quando seus bytes coincidem; a fonte original nunca e escrita.

    Com ``preserve_metadata`` os chunks bext e iXML de uma fonte WAV vao para
    os dois monos byte a byte, com ordem relativa ao chunk data e padding
    intactos; o TRACK_LIST nao e reduzido. A flag so vale para WAV.
    """
    left, right = _split(source, directory, ["L", "R"], preserve_metadata, stereo=True)
    return left, right
