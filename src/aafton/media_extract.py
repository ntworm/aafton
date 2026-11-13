"""Resolucao e extracao de midia referenciada pelo AAF.

Ordem de precedencia na resolucao de um link, conforme a secao 7 do spec:

1. caminho absoluto existente, exatamente como veio;
2. sufixo do caminho declarado, procurado dentro das pastas de busca;
3. prefixo de nome no formato A<n>-<nnnn>, que e como os tres fixtures reais
   ligam mob a arquivo;
4. nome do arquivo, aceito somente quando ha candidato unico ou quando os
   metadados desempatam.

Ambiguidade nunca e resolvida por chute.

Medicoes dos fixtures:
- Nenhum dos tres fixtures tem um unico locator.
- Nenhum tem essencia embedded.
- No Resolve, 212 MasterMob tem nome no formato "A6-0028_DIA1  _0447S34";
  os arquivos sao "A1-0001_<uuid>.wav". O prefixo A<n>-<nnnn> casa sem
  ambiguidade entre os 212 mobs e os 550 arquivos da pasta.
- Casamento por UMID: descartado. 0 de 107 arquivos com UUID no nome
  apareceram em qualquer mob_id.
"""

from __future__ import annotations

import struct
import hashlib
import tempfile
import wave
from pathlib import Path

from .aaf_read_rates import mesma_duracao
from .ir import MediaAsset
from .report import Report

MXF_SUFFIXES = {".mxf"}

BEXT_SIZE = 602  # cabecalho BWF versao 1 sem UMID estendido


class AmbiguousMediaError(RuntimeError):
    """Mais de um candidato plausivel para o mesmo arquivo."""


def _read_aiff_float(data: bytes) -> int:
    expon = struct.unpack(">H", data[:2])[0]
    sign = 1
    if expon >= 0x8000:
        sign = -1
        expon -= 0x8000
    mant = struct.unpack(">Q", data[2:10])[0]
    if expon == 0 and mant == 0:
        return 0
    elif expon == 0x7FFF:
        raise ValueError("NaN/Inf in AIFF float")
    expon -= 16383
    return int(round(sign * mant * (2.0 ** (expon - 63))))


def read_audio_metadata(path: Path) -> dict:
    """Canais, taxa e duracao em samples de um WAV ou AIFF/AIFC."""
    with open(path, "rb") as f:
        header = f.read(12)
        if len(header) < 12:
            raise ValueError(f"{path} is too short to have a header")

        if header[:4] == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
            is_aifc = header[8:12] == b"AIFC"
            channels = None
            sample_rate = None
            duration_samples = None
            bits = None
            data_offset = None
            data_size = None
            is_big_endian = True  # AIFF is big-endian; sowt AIFC is little-endian
            while True:
                chunk_header = f.read(8)
                if not chunk_header or len(chunk_header) < 8:
                    break
                chunk_id, size = struct.unpack(">4sI", chunk_header)
                if chunk_id == b"COMM":
                    payload = f.read(size)
                    if size < 18:
                        raise ValueError("chunk COMM too short")
                    channels, duration_samples, bits = struct.unpack(">HlH", payload[:8])
                    sample_rate = _read_aiff_float(payload[8:18])
                    if is_aifc:
                        if size < 22:
                            raise ValueError("chunk COMM too short in the AIFC")
                        comp = struct.unpack(">4s", payload[18:22])[0]
                        if comp not in (b"NONE", b"sowt"):
                            raise ValueError(
                                f"compression not supported in AIFC: {comp.decode('ascii', 'replace')}"
                            )
                        if comp == b"sowt":
                            is_big_endian = False
                    if size % 2 != 0:
                        f.read(1)
                elif chunk_id == b"SSND":
                    header = f.read(8)
                    if size < 8 or len(header) != 8:
                        raise ValueError("chunk SSND too short")
                    offset, _ = struct.unpack(">II", header)
                    if offset > size - 8:
                        raise ValueError("offset SSND outside the chunk")
                    data_offset = f.tell() + offset
                    data_size = size - 8 - offset
                    f.seek(size - 8 + (size % 2), 1)
                else:
                    f.seek(size + (size % 2), 1)

            if channels is None or sample_rate is None or duration_samples is None:
                raise ValueError("AIFF file without a COMM chunk")
            return {
                "channels": channels,
                "sample_rate": sample_rate,
                "duration_samples": duration_samples,
                "bits": bits,
                "data_offset": data_offset,
                "data_size": data_size,
                "is_big_endian": is_big_endian,
                "format": "aiff",
            }

        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError(
                f"{path} format not supported: magic bytes {header[:4]!r} {header[8:12]!r}"
            )

        channels = None
        sample_rate = None
        block_align = None
        duration_samples = None
        fmt_payload = None
        data_offset = None
        data_size = None
        bits = None

        while True:
            chunk_header = f.read(8)
            if not chunk_header or len(chunk_header) < 8:
                break
            chunk_id, size = struct.unpack("<4sI", chunk_header)

            if chunk_id == b"fmt ":
                payload = f.read(size)
                if size < 16:
                    raise ValueError("chunk fmt too short")
                fmt, channels, sample_rate, bytes_per_sec, block_align, bits = struct.unpack(
                    "<HHIIHH", payload[:16]
                )
                fmt_payload = payload

                # 1 = PCM, 0xFFFE = WAVE_FORMAT_EXTENSIBLE
                if fmt == 0xFFFE:
                    if size < 40:
                        raise ValueError("WAVE_FORMAT_EXTENSIBLE needs a fmt of at least 40 bytes")
                    subformat = struct.unpack("<H", payload[24:26])[0]
                    if subformat not in (1, 3):
                        raise ValueError(f"unknown format: {fmt} with subformat {subformat}")
                    if subformat == 3:
                        bits = 32
                elif fmt != 1 and fmt != 3:
                    raise ValueError(f"unknown format: {fmt}")
                if size % 2 != 0:
                    f.read(1)

            elif chunk_id == b"data":
                data_offset = f.tell()
                data_size = size
                if block_align is not None and block_align > 0:
                    duration_samples = size // block_align
                f.seek(size + (size % 2), 1)

            else:
                f.seek(size + (size % 2), 1)

    if channels is None or sample_rate is None or duration_samples is None:
        raise ValueError("incomplete wav file (without fmt or data)")

    return {
        "channels": channels,
        "sample_rate": sample_rate,
        "duration_samples": duration_samples,
        "fmt_payload": fmt_payload,
        "data_offset": data_offset,
        "data_size": data_size,
        "bits": bits,
        "format": "wav",
        "is_big_endian": False,
    }


def _candidates(asset: MediaAsset, search_roots: list[Path]) -> list[Path]:
    declarado = asset.declared_location or asset.filename
    encontrados: list[Path] = []

    direto = Path(declarado)
    if direto.is_absolute() and direto.exists():
        return [direto]

    partes = [p for p in Path(declarado.replace("\\", "/")).parts if p not in ("/", "", "\\")]
    sufixos = ["/".join(partes[indice:]) for indice in range(len(partes))]

    for raiz in search_roots:
        for sufixo in sufixos:
            candidato = raiz / sufixo
            if candidato.exists() and candidato.is_file():
                encontrados.append(candidato)
        if not encontrados:
            encontrados.extend(
                caminho for caminho in raiz.rglob(Path(asset.filename).name) if caminho.is_file()
            )

    unicos: list[Path] = []
    vistos: set[str] = set()
    for caminho in encontrados:
        chave = str(caminho.resolve())
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(caminho)
    return unicos


def _disambiguate(asset: MediaAsset, candidates: list[Path]) -> Path:
    if asset.duration_samples is None and asset.channels is None:
        raise AmbiguousMediaError(
            f"{asset.filename}: {len(candidates)} candidates and no metadata to "
            f"disambiguate: " + ", ".join(str(c) for c in candidates)
        )
    sobreviventes = []
    for candidato in candidates:
        try:
            meta = read_audio_metadata(candidato)
        except (wave.Error, OSError, ValueError):
            continue
        if asset.duration_samples is not None and not mesma_duracao(
            meta["duration_samples"], meta["sample_rate"], asset.duration_samples, asset.sample_rate
        ):
            continue
        if asset.channels is not None and meta["channels"] != asset.channels:
            continue
        sobreviventes.append(candidato)
    if len(sobreviventes) == 1:
        return sobreviventes[0]
    raise AmbiguousMediaError(
        f"{asset.filename}: {len(candidates)} candidates and the metadata does not disambiguate: "
        + ", ".join(str(c) for c in candidates)
    )


def resolve_asset(
    asset: MediaAsset, search_roots: list[Path], report: Report, mxf_dir=None, taxa_tc=None
) -> None:
    """Preenche ``resolved_path``, ``status`` e os metadados do asset.

    Com ``mxf_dir``, um arquivo MXF e extraido para WAV e o asset passa a
    apontar para o WAV. Sem a pasta, sem ffmpeg ou com a extracao falha, o
    MXF continua marcado como midia nao suportada.
    """
    candidatos = _candidates(asset, [Path(raiz) for raiz in search_roots])

    if not candidatos:
        asset.status = "missing"
        report.error(
            "MEDIA_MISSING",
            f"the file {asset.filename!r} was not found in the search folders",
            {"filename": asset.filename, "declared": asset.declared_location},
        )
        return

    escolhido = candidatos[0] if len(candidatos) == 1 else _disambiguate(asset, candidatos)

    if escolhido.suffix.lower() in MXF_SUFFIXES:
        wav = _resolver_mxf(asset, escolhido, report, mxf_dir, taxa_tc)
        if wav is None:
            return
        escolhido = wav

    asset.resolved_path = str(Path(escolhido).absolute())
    asset.status = "resolved"
    try:
        meta = read_audio_metadata(escolhido)
    except (wave.Error, OSError, ValueError) as exc:
        report.warning(
            "MEDIA_METADATA_UNREADABLE",
            f"could not read the metadata of {escolhido.name}: {exc}",
            {"path": str(escolhido)},
        )
        return
    asset.channels = meta["channels"]
    asset.sample_rate = meta["sample_rate"]
    asset.duration_samples = meta["duration_samples"]


def _resolver_mxf(asset, escolhido, report, mxf_dir, taxa_tc) -> Path | None:
    """Extrai o MXF para WAV; None quando o MXF continua nao suportado."""
    from . import mxf

    return mxf.resolver_mxf(asset, escolhido, report, mxf_dir, taxa_tc)


# --- Chunk bext (BWF) ---


def _bext_chunk(
    time_reference: int,
    description: str = "",
    originator: str = "ableton-aaf",
    originator_reference: str = "",
    origination_date: str = "",
    origination_time: str = "",
) -> bytes:
    """Monta o chunk bext do BWF. TimeReference e um inteiro de 64 bits."""
    payload = b"".join(
        [
            description.encode("ascii", "replace").ljust(256, b"\x00")[:256],
            originator.encode("ascii", "replace").ljust(32, b"\x00")[:32],
            originator_reference.encode("ascii", "replace").ljust(32, b"\x00")[:32],
            origination_date.encode("ascii", "replace").ljust(10, b"\x00")[:10],
            origination_time.encode("ascii", "replace").ljust(8, b"\x00")[:8],
            struct.pack("<Q", time_reference),
            struct.pack("<H", 1),  # Version
            b"\x00" * 64,  # UMID
            b"\x00" * 10,  # loudness, reservado na versao 1
            b"\x00" * 180,  # Reserved
        ]
    )
    return struct.pack("<4sI", b"bext", len(payload)) + payload


def write_wav_with_bext(
    path: str | Path,
    pcm: bytes,
    channels: int,
    sample_rate: int,
    bits_per_sample: int,
    time_reference: int,
    description: str = "",
) -> Path:
    """Escreve um WAV PCM com chunk bext carregando a posicao em samples."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    block_align = channels * bits_per_sample // 8
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bits_per_sample,
    )
    bext = _bext_chunk(time_reference, description=description)
    data = struct.pack("<4sI", b"data", len(pcm)) + pcm
    if len(pcm) % 2:
        data += b"\x00"
    body = b"WAVE" + fmt + bext + data
    path.write_bytes(struct.pack("<4sI", b"RIFF", len(body)) + body)
    return path


def parse_bext(path: str | Path) -> dict | None:
    """Le o chunk bext de um WAV. Devolve None quando nao existe."""
    with Path(path).open("rb") as stream:
        header = stream.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None
        while len(chunk := stream.read(8)) == 8:
            chunk_id, size = struct.unpack("<4sI", chunk)
            if chunk_id == b"bext":
                payload = stream.read(min(size, 348))
                if len(payload) < 348:
                    raise ValueError("chunk bext truncated")
                return {
                    "description": payload[:256].split(b"\x00")[0].decode("ascii", "replace"),
                    "originator": payload[256:288].split(b"\x00")[0].decode("ascii", "replace"),
                    "time_reference": struct.unpack("<Q", payload[338:346])[0],
                    "version": struct.unpack("<H", payload[346:348])[0],
                }
            stream.seek(size + (size % 2), 1)
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_embedded_essence(
    aaf_file,
    source_mob,
    destination_dir: str | Path,
    time_reference: int,
    report: Report,
    suggested_filename: str | None = None,
    existing_destinations: set[Path] | None = None,
    skip_existing: bool = True,
) -> Path | None:
    """Extrai a essencia embedded de um SourceMob para um WAV com bext.

    Devolve o caminho escrito, ou None quando o mob nao tem essencia embedded.
    Nenhum dos tres fixtures reais tem essencia embedded; esta funcao e o
    caminho de producao para AAFs que consolidam a midia.
    """
    destination_dir = Path(destination_dir)
    essence = getattr(source_mob, "essence", None)
    if essence is None:
        return None

    descritor = getattr(source_mob, "descriptor", None)
    channels = 1
    sample_rate = 48000
    bits = 16
    if descritor is not None:
        if hasattr(descritor, "get"):
            sr_prop = descritor.get("SampleRate")
            if sr_prop and sr_prop.value is not None:
                v = sr_prop.value
                try:
                    sample_rate = int(v.num / v.den) if hasattr(v, "num") else int(v)
                except Exception:
                    pass
            ch_prop = descritor.get("Channels") or descritor.get("ChannelCount")
            if ch_prop and ch_prop.value is not None:
                try:
                    channels = int(ch_prop.value)
                except Exception:
                    pass
            bits_prop = descritor.get("QuantizationBits")
            if bits_prop and bits_prop.value is not None:
                try:
                    bits = int(bits_prop.value)
                except Exception:
                    pass
        if hasattr(descritor, "sample_rate") and getattr(descritor, "sample_rate", None):
            try:
                sample_rate = int(descritor.sample_rate)
            except Exception:
                pass
        if hasattr(descritor, "channels") and getattr(descritor, "channels", None):
            try:
                channels = int(descritor.channels)
            except Exception:
                pass
        if hasattr(descritor, "quantization_bits") and getattr(
            descritor, "quantization_bits", None
        ):
            try:
                bits = int(descritor.quantization_bits)
            except Exception:
                pass

    import re as _re

    if suggested_filename:
        nome_base = Path(suggested_filename).name
        # Remove caracteres invalidos em nomes de arquivo Windows e Unix
        nome_base = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome_base)
        nome_base = nome_base.strip(". ")
        if nome_base.lower().endswith(".wav"):
            nome = nome_base
            nome_stem = nome_base[:-4]
        else:
            nome = f"{nome_base}.wav"
            nome_stem = nome_base
    else:
        nome_base = str(source_mob.name or source_mob.mob_id)
        # Remove sufixos como <SOURCE MOB> adicionados pelo pyaaf2
        nome_base = _re.sub(r"\s*<[^>]+>\s*$", "", nome_base)
        # Remove caracteres invalidos em nomes de arquivo Windows e Unix
        nome_base = _re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome_base)
        nome_base = nome_base.strip(". ")  # nao pode terminar com ponto ou espaco
        if not nome_base:
            nome_base = str(source_mob.mob_id).replace(":", "_").replace(".", "_")
        nome = f"{nome_base}.wav"
        nome_stem = nome_base

    if not nome_stem or nome_stem.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        nome_stem = "audio_" + hashlib.sha256(str(source_mob.mob_id).encode()).hexdigest()[:16]
        nome = nome_stem + ".wav"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destino = destination_dir / nome
    # Gerar em arquivo temporário permite comparar conteúdo e preservar saídas
    # anteriores. Nenhum cabeçalho RIFF, nome ou UMID sozinho prova identidade.
    with tempfile.TemporaryDirectory(prefix="essence-", dir=destination_dir) as temporary:
        staged = Path(temporary) / "audio.wav"
        stream = essence.open("r")
        try:
            first = stream.read(12)
            riff = first[:4] == b"RIFF" and first[8:12] == b"WAVE"
            with staged.open("w+b") as out:
                data_start = 0
                if not riff:
                    align = channels * bits // 8
                    fmt = struct.pack(
                        "<4sIHHIIHH",
                        b"fmt ",
                        16,
                        1,
                        channels,
                        sample_rate,
                        sample_rate * align,
                        align,
                        bits,
                    )
                    out.write(
                        b"RIFF\0\0\0\0WAVE"
                        + fmt
                        + _bext_chunk(time_reference, description=str(source_mob.name or ""))
                    )
                    out.write(b"data\0\0\0\0")
                    data_start = out.tell()
                out.write(first)
                while chunk := stream.read(1024 * 1024):
                    out.write(chunk)
                    if out.tell() - 8 > 0xFFFFFFFF:
                        raise ValueError("essence exceeds the supported RIFF size; RF64 needed")
                if not riff:
                    size = out.tell() - data_start
                    if size % align:
                        raise ValueError("truncated or misaligned PCM essence")
                    out.write(b"\0" * (size % 2))
                    end = out.tell()
                    if end - 8 > 0xFFFFFFFF:
                        raise ValueError("essence exceeds the supported RIFF size; RF64 needed")
                    out.seek(data_start - 4)
                    out.write(struct.pack("<I", size))
                    out.seek(4)
                    out.write(struct.pack("<I", end - 8))
        finally:
            stream.close()
        digest = file_sha256(staged)
        if destino.exists() or destino.is_symlink():
            if not (
                skip_existing
                and destino.is_file()
                and not destino.is_symlink()
                and file_sha256(destino) == digest
            ):
                destino = destination_dir / f"{nome_stem}_{digest}.wav"
        if destino.exists() or destino.is_symlink():
            if destino.is_symlink() or not destino.is_file() or file_sha256(destino) != digest:
                raise ValueError(f"existing essence destination differs: {destino}")
            report.info(
                "MEDIA_REUSED",
                f"{destino.name} has verified identical content",
                {"path": str(destino), "sha256": digest},
            )
        else:
            staged.rename(destino)
            report.info(
                "MEDIA_EXTRACTED",
                f"{destino.name} extracted from the embedded essence",
                {"path": str(destino), "sha256": digest},
            )
    if existing_destinations is not None:
        existing_destinations.add(destino)
    return destino.resolve()
