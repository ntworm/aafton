"""Metadados BWF de um RIFF/WAVE: fmt, bext e iXML, sem ler as amostras.

O walker de chunks valida o cabecalho RIFF/WAVE e os limites de cada chunk com
o seu padding. O iXML e lido com xml.etree; iXML ausente ou quebrado, campo
faltando ou numero invalido nunca derrubam a leitura: o valor fica None e o
resultado leva uma nota curta em ingles.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from .bext_description import campos as _bext_campos

BLOCK = 65536  # teto de bytes lidos por vez de um payload
CHUNK_HEADER = 8  # bytes do cabecalho de um chunk RIFF
RIFF_HEADER = 12  # bytes do cabecalho RIFF/WAVE
BEXT_MIN = 348  # bytes do bext ate o fim do campo Version
FMT_MAX = 64  # bytes do fmt lidos; o resto do fmt nao interessa aqui
METADATA_MAX = 1 << 20  # teto do iXML lido para a memoria

# Campos de texto do iXML: atributo e caminhos aceitos, na ordem de busca.
_TEXTOS = (
    ("project", ("PROJECT",)),
    ("scene", ("SCENE",)),
    ("take", ("TAKE",)),
    ("tape", ("TAPE",)),
    ("note", ("NOTE",)),
    ("file_uid", ("FILE_UID",)),
    ("family_uid", ("FILE_SET/FAMILY_UID", "FAMILY_UID")),
    ("family_name", ("FILE_SET/FAMILY_NAME", "FAMILY_NAME")),
)
_INTEIROS = (
    ("file_sample_rate", "SPEED/FILE_SAMPLE_RATE"),
    ("timestamp_samples_since_midnight_hi", "SPEED/TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_HI"),
    ("timestamp_samples_since_midnight_lo", "SPEED/TIMESTAMP_SAMPLES_SINCE_MIDNIGHT_LO"),
)
_FRACOES = (
    ("master_speed", "SPEED/MASTER_SPEED"),
    ("timecode_rate", "SPEED/TIMECODE_RATE"),
)
_FLAGS = {"NDF": "NDF", "DF": "DF"}
_CIRCLED = {"TRUE": True, "FALSE": False}

_TRACK_LIST_RE = re.compile(rb"(<TRACK_LIST\b[^>/]*>)(.*?)(</TRACK_LIST>)", re.S)
_TRACK_RE = re.compile(rb"[ \t]*<TRACK\b[^>]*>.*?</TRACK>[ \t]*(?:\r?\n)?", re.S)
_INTERLEAVE_RE = re.compile(rb"(<INTERLEAVE_INDEX\b[^>]*>)\s*(\d+)\s*(</INTERLEAVE_INDEX>)")
_COUNT_RE = re.compile(rb"(<TRACK_COUNT\b[^>]*>)[^<]*(</TRACK_COUNT>)")


class IxmlTrack(NamedTuple):
    """Uma entrada do TRACK_LIST: indice do gravador, posicao no arquivo e nome."""

    channel_index: int | None
    interleave_index: int | None
    name: str | None


@dataclass
class Ixml:
    """Campos do iXML usados no som direto; None quando faltam ou sao invalidos."""

    project: str | None = None
    scene: str | None = None
    take: str | None = None
    tape: str | None = None
    circled: bool | None = None
    note: str | None = None
    file_uid: str | None = None
    family_uid: str | None = None
    family_name: str | None = None
    master_speed: Fraction | None = None
    timecode_rate: Fraction | None = None
    timecode_flag: str | None = None
    file_sample_rate: int | None = None
    timestamp_samples_since_midnight_hi: int | None = None
    timestamp_samples_since_midnight_lo: int | None = None
    track_list: list[IxmlTrack] | None = None
    original_filename: str | None = None
    source: str = "ixml"

    @property
    def timestamp_samples(self) -> int | None:
        """Samples desde a meia-noite, juntando HI e LO; None sem o LO."""
        if self.timestamp_samples_since_midnight_lo is None:
            return None
        hi = self.timestamp_samples_since_midnight_hi or 0
        return (hi << 32) | self.timestamp_samples_since_midnight_lo


@dataclass
class BwfInfo:
    """Metadados de um WAV: formato, tamanho em frames, bext, iXML e notas."""

    path: Path
    channels: int
    sample_rate: int
    bits: int
    frames: int | None
    bext: dict | None = None
    ixml: Ixml | None = None
    notes: list[str] = field(default_factory=list)


# --- Chunks RIFF ---


def _riff_end(source: Path, header: bytes, limit: int) -> int:
    """Valida o cabecalho RIFF/WAVE e devolve o fim declarado do RIFF."""
    if len(header) < RIFF_HEADER:
        raise ValueError(f"{source} too short to have a RIFF header")
    if header[:4] == b"RF64":
        raise ValueError(f"RF64 not supported yet: {source}")
    if (header[:4], header[8:12]) != (b"RIFF", b"WAVE"):
        raise ValueError(f"{source} format not supported: {header[:4]!r} {header[8:12]!r}")
    end = CHUNK_HEADER + struct.unpack_from("<I", header, 4)[0]
    if end < RIFF_HEADER:
        raise ValueError(f"{source} RIFF size smaller than the header: {end} < {RIFF_HEADER}")
    if end > limit:
        raise ValueError(f"{source} truncated: RIFF declares {end - CHUNK_HEADER} bytes")
    return end


def iter_riff_chunks(source: Path) -> Iterator[tuple[bytes, int, int]]:
    """Itera os chunks de um RIFF/WAVE como (id, offset do payload, tamanho).

    Valida o cabecalho RIFF/WAVE e os limites de cada chunk com o seu
    padding, sem ler o conteudo dos payloads.
    """
    source = Path(source)
    limit = source.stat().st_size
    with source.open("rb") as stream:
        end = _riff_end(source, stream.read(RIFF_HEADER), limit)
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


def read_payload(stream, offset: int, size: int) -> bytes:
    """Le um payload inteiro em leituras de no maximo BLOCK bytes."""
    stream.seek(offset)
    partes = []
    remaining = size
    while remaining:
        block = stream.read(min(remaining, BLOCK))
        if not block:
            raise ValueError("truncated chunk payload")
        partes.append(block)
        remaining -= len(block)
    return b"".join(partes)


# --- bext ---


def _ascii(raw: bytes) -> str:
    return raw.split(b"\x00")[0].decode("ascii", "replace")


def bext_fields(payload: bytes) -> dict:
    """Campos do bext a partir do payload; exige ao menos BEXT_MIN bytes."""
    if len(payload) < BEXT_MIN:
        raise ValueError("chunk bext truncated")
    return {
        "description": _ascii(payload[:256]),
        "originator": _ascii(payload[256:288]),
        "origination_date": _ascii(payload[320:330]),
        "origination_time": _ascii(payload[330:338]),
        "time_reference": struct.unpack("<Q", payload[338:346])[0],
        "version": struct.unpack("<H", payload[346:348])[0],
    }


def parse_bext(path: str | Path) -> dict | None:
    """Le o chunk bext de um WAV. Devolve None quando nao existe."""
    with Path(path).open("rb") as stream:
        header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None
        while len(chunk := stream.read(8)) == 8:
            chunk_id, size = struct.unpack("<4sI", chunk)
            if chunk_id == b"bext":
                return bext_fields(stream.read(min(size, BEXT_MIN)))
            stream.seek(size + (size % 2), 1)
    return None


# --- iXML ---


def _ixml_root(payload: bytes, notes: list[str]) -> ET.Element | None:
    """Raiz BWFXML do payload; None com nota quando o iXML nao serve."""
    texto = payload.rstrip(b"\x00 \t\r\n")
    if b"<!DOCTYPE" in texto or b"<!ENTITY" in texto:
        notes.append("iXML with a DOCTYPE ignored")
        return None
    try:
        root = ET.fromstring(texto)
    except ET.ParseError as exc:
        notes.append(f"malformed iXML: {exc}")
        return None
    if root.tag != "BWFXML":
        notes.append(f"iXML root is {root.tag!r}, not BWFXML")
        return None
    return root


def _text(element: ET.Element, caminhos: tuple[str, ...], notes: list[str]) -> str | None:
    """Texto do primeiro caminho presente; elemento vazio vale ''."""
    for caminho in caminhos:
        achado = element.find(caminho)
        if achado is not None:
            return (achado.text or "").strip()
    notes.append(f"iXML without {caminhos[0]}")
    return None


def _number(element: ET.Element, caminho: str, kind, notes: list[str]):
    """Numero do caminho convertido por ``kind``; None com nota se invalido."""
    texto = _text(element, (caminho,), notes)
    if texto is None:
        return None
    try:
        return kind(texto)
    except (ValueError, ZeroDivisionError):
        notes.append(f"iXML {caminho} is not a number: {texto!r}")
        return None


def _choice(element: ET.Element, caminho: str, opcoes: dict, notes: list[str]):
    """Valor de um campo de vocabulario fechado, sem diferenciar caixa."""
    texto = _text(element, (caminho,), notes)
    if texto is None:
        return None
    if texto.upper() not in opcoes:
        notes.append(f"iXML {caminho} has an unknown value: {texto!r}")
        return None
    return opcoes[texto.upper()]


def _tracks(root: ET.Element, notes: list[str]) -> list[IxmlTrack] | None:
    """TRACK_LIST como lista de IxmlTrack; None quando nao existe."""
    lista = root.find("TRACK_LIST")
    if lista is None:
        notes.append("iXML without TRACK_LIST")
        return None
    return [
        IxmlTrack(
            _number(track, "CHANNEL_INDEX", int, notes),
            _number(track, "INTERLEAVE_INDEX", int, notes),
            _text(track, ("NAME",), notes),
        )
        for track in lista.findall("TRACK")
    ]


def parse_ixml(payload: bytes | None, notes: list[str]) -> Ixml | None:
    """Campos do iXML do payload; None com nota quando ausente ou quebrado."""
    if payload is None:
        notes.append("no iXML chunk")
        return None
    root = _ixml_root(payload, notes)
    if root is None:
        return None
    valores: dict = {}
    for nome, caminhos in _TEXTOS:
        valores[nome] = _text(root, caminhos, notes)
    for nome, caminho in _INTEIROS:
        valores[nome] = _number(root, caminho, int, notes)
    for nome, caminho in _FRACOES:
        valores[nome] = _number(root, caminho, Fraction, notes)
    valores["circled"] = _choice(root, "CIRCLED", _CIRCLED, notes)
    valores["timecode_flag"] = _choice(root, "SPEED/TIMECODE_FLAG", _FLAGS, notes)
    valores["track_list"] = _tracks(root, notes)
    # Campo opcional que quase nenhum gravador escreve: sem nota quando falta.
    valores["original_filename"] = _text(root, ("HISTORY/ORIGINAL_FILENAME",), [])
    return Ixml(**valores)


def _renumber(track: bytes) -> bytes:
    """TRACK com INTERLEAVE_INDEX 1: a posicao dele num mono."""
    return _INTERLEAVE_RE.sub(rb"\g<1>1\g<3>", track, count=1)


def _interleave_of(track: bytes) -> int | None:
    achado = _INTERLEAVE_RE.search(track)
    return int(achado.group(2)) if achado else None


def reduce_track_list(payload: bytes, interleave_index: int) -> bytes | None:
    """iXML de um mono: TRACK_LIST so com a entrada daquela posicao.

    A entrada guarda CHANNEL_INDEX, NAME e o resto dela; INTERLEAVE_INDEX vira
    1 e TRACK_COUNT vira 1 (0 sem entrada na posicao). O resto do iXML segue
    byte a byte. None quando nao ha TRACK_LIST ou o iXML nao e legivel: o
    chamador copia o payload como esta.
    """
    if _ixml_root(payload, []) is None:
        return None
    lista = _TRACK_LIST_RE.search(payload)
    if lista is None:
        return None
    interno = lista.group(2)
    tracks = list(_TRACK_RE.finditer(interno))
    mantida = next((t for t in tracks if _interleave_of(t.group(0)) == interleave_index), None)
    novo = _TRACK_RE.sub(
        lambda t: _renumber(t.group(0)) if mantida and t.start() == mantida.start() else b"",
        interno,
    )
    contagem = b"1" if mantida else b"0"
    if _COUNT_RE.search(novo):
        novo = _COUNT_RE.sub(rb"\g<1>" + contagem + rb"\g<2>", novo, count=1)
    else:
        novo = b"<TRACK_COUNT>" + contagem + b"</TRACK_COUNT>" + novo
    return payload[: lista.start(2)] + novo + payload[lista.end(2) :]


# --- Leitura do arquivo ---


def _fmt_fields(payload: bytes) -> tuple[int, int, int, int]:
    """Canais, taxa, block align e bits do fmt."""
    if len(payload) < 16:
        raise ValueError("chunk fmt too short")
    _, channels, rate, _, align, bits = struct.unpack("<HHIIHH", payload[:16])
    return channels, rate, align, bits


def _locate(path: Path) -> tuple[dict, int | None]:
    """Offset e tamanho do primeiro fmt, bext e iXML, e o tamanho do data."""
    achados: dict = {}
    data_size = None
    for chunk_id, offset, size in iter_riff_chunks(path):
        if chunk_id == b"data" and data_size is None:
            data_size = size
        elif chunk_id in (b"fmt ", b"bext", b"iXML") and chunk_id not in achados:
            achados[chunk_id] = (offset, size)
    if b"fmt " not in achados or data_size is None:
        raise ValueError(f"{path} without a fmt or data chunk")
    return achados, data_size


def _payloads(path: Path, achados: dict, notes: list[str]) -> dict:
    """Le os payloads de metadata com teto; o data nunca e lido."""
    limites = {b"fmt ": FMT_MAX, b"bext": BEXT_MIN, b"iXML": METADATA_MAX}
    payloads = {}
    with path.open("rb") as stream:
        for chunk_id, (offset, size) in achados.items():
            if chunk_id == b"iXML" and size > METADATA_MAX:
                notes.append(f"iXML larger than {METADATA_MAX} bytes ignored")
                continue
            payloads[chunk_id] = read_payload(stream, offset, min(size, limites[chunk_id]))
    return payloads


def _bext_or_note(payload: bytes | None, notes: list[str]) -> dict | None:
    if payload is None:
        notes.append("no bext chunk")
        return None
    try:
        return bext_fields(payload)
    except ValueError:
        notes.append("bext chunk truncated")
        return None


def com_ixml_do_bext(info: BwfInfo) -> BwfInfo:
    """Sem iXML legivel, um Ixml parcial do bext Description com prefixo z ou s.

    ``read_bwf`` nao chama: o som direto e a expansao chamam depois da leitura.
    """
    if info.ixml is not None or not info.bext:
        return info
    prefixo, mapa = _bext_campos(info.bext.get("description"))
    if prefixo not in ("z", "s"):
        return info
    notes = [*info.notes, "iXML read from the bext Description"]
    if prefixo == "s":
        notes.append("unconfirmed Sound Devices bext keys")
    campos = {k: mapa.get(k.upper()) for k in ("scene", "take", "tape", "note")}
    circled = _CIRCLED.get(mapa.get("CIRCLED", "").upper())
    ixml = Ixml(**campos, circled=circled, source="bext")
    return replace(info, ixml=ixml, notes=notes)


def read_bwf(path: str | Path) -> BwfInfo:
    """Le fmt, bext e iXML de um WAV sem ler as amostras.

    RF64 ou arquivo que nao e RIFF/WAVE levantam ValueError; metadata ausente
    ou quebrada so vira None com nota.
    """
    path = Path(path)
    achados, data_size = _locate(path)
    notes: list[str] = []
    payloads = _payloads(path, achados, notes)
    channels, rate, align, bits = _fmt_fields(payloads[b"fmt "])
    if align == 0:
        notes.append("fmt block align is zero: frames unknown")
    return BwfInfo(
        path=path,
        channels=channels,
        sample_rate=rate,
        bits=bits,
        frames=data_size // align if align else None,
        bext=_bext_or_note(payloads.get(b"bext"), notes),
        ixml=parse_ixml(payloads.get(b"iXML"), notes),
        notes=notes,
    )
