"""Suporte a midia embedded no AAF exportado.

Gerencia o teto de tamanho do AAF (2 GiB), o streaming de essencia RIFF/WAVE
para EssenceData sem alocacao de buffers gigantes, o preflight de elegibilidade
de midia e a verificacao estrutural do AAF gerado.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import NamedTuple

import aaf2

from .audio_split import _iter_riff_chunks
from .ir import MediaAsset, SessionIR
from .media_extract import read_audio_metadata
from .report import Report

EMBED_LIMIT_BYTES = 2 * 1024**3
COPY_CHUNK_SIZE = 65536
SUPPORTED_BITS = (8, 16, 24, 32)
EXTENSIBLE = 0xFFFE
IEEE_FLOAT = 3


class _Fmt(NamedTuple):
    """Campos geometricos do chunk fmt de um WAV, lidos do payload."""

    tag: int
    subformat: int | None
    channels: int
    sample_rate: int
    byte_rate: int
    block_align: int
    bits: int

    @property
    def align(self) -> int:
        """Bytes por frame: canais vezes a largura da amostra."""
        return self.channels * (self.bits // 8)


def embed_assets(session: SessionIR) -> list[MediaAsset]:
    """Retorna os assets nao-video da sessao, preservando ordem e multiplicidade."""
    video_media_ids = {
        clip.media_id
        for track in getattr(session, "tracks", ())
        if getattr(track, "is_video", False)
        for clip in getattr(track, "clips", ())
        if clip.media_id
    }
    return [asset for asset in getattr(session, "media", ()) if asset.id not in video_media_ids]


def check_embed_size(size: int, report: Report, *, stage: str = "payload") -> bool:
    """Verifica se o tamanho do embed excede o teto de 2 GiB."""
    if size > EMBED_LIMIT_BYTES:
        context = {
            "stage": stage,
            "size": size,
            "limit": EMBED_LIMIT_BYTES,
        }
        report.error(
            "EXPORT_EMBED_EXCEEDS_2GB",
            f"media ({size} bytes) at the {stage!r} stage exceeds the limit of {EMBED_LIMIT_BYTES}",
            context,
        )
        return False
    return True


def embed_payload_size(
    session: SessionIR, mono_sources: dict[str, tuple[Path, ...]] | None = None
) -> int:
    """Soma o stat dos arquivos que serao gravados na essencia."""
    total = 0
    sources = mono_sources or {}
    for asset in embed_assets(session):
        if asset.id in sources:
            for piece in sources[asset.id]:
                total += Path(piece).stat().st_size
        else:
            target = asset.resolved_path or asset.filename
            if target:
                total += Path(target).stat().st_size
    return total


def embed_wave(f, source_mob, path: Path) -> int:
    """Embarca um arquivo WAV como EssenceData do SourceMob."""
    path = Path(path)
    expected_size = path.stat().st_size

    desc = getattr(source_mob, "descriptor", None)
    if desc is not None:
        try:
            del desc["Locator"]
        except (KeyError, AttributeError):
            pass

    ed = f.create.EssenceData()
    ed.mob = source_mob
    f.content.essencedata.append(ed)

    stream = ed.open("w")
    total_written = 0
    try:
        with path.open("rb") as src:
            while True:
                block = src.read(COPY_CHUNK_SIZE)
                if not block:
                    break
                stream.write(block)
                total_written += len(block)
    finally:
        stream.close()

    if total_written != expected_size:
        raise ValueError(
            f"{path}: incomplete write ({total_written} bytes written != {expected_size} expected)"
        )
    return total_written


def _classify_format(path: Path) -> str:
    """Classifica o arquivo pelo cabecalho: wav, aiff ou desconhecido.

    A classificacao vem antes do passeio de chunks RIFF: um AIFF nao e um
    RIFF corrompido, e sim um formato fora do contrato do embed.
    """
    with path.open("rb") as stream:
        magic = stream.read(4)
    if magic == b"RIFF":
        return "wav"
    if magic == b"FORM":
        return "aiff"
    return f"unknown ({magic!r})"


def _scan_riff_chunks(chunks) -> tuple[int, int, str | None]:
    """Conta chunks fmt e data e recusa fmt com tamanho fora do razoavel."""
    fmt_count = 0
    data_count = 0
    for chunk_id, _offset, size in chunks:
        if chunk_id == b"fmt ":
            fmt_count += 1
            if size < 16 or size > 4096:
                return fmt_count, data_count, f"suspicious fmt size ({size})"
        elif chunk_id == b"data":
            data_count += 1
    return fmt_count, data_count, None


def _validate_asset_riff(path: Path) -> tuple[str | None, str | None]:
    """Valida chunks RIFF e bounds sem carregar payloads em memoria."""
    try:
        fmt_count, data_count, suspicion = _scan_riff_chunks(_iter_riff_chunks(path))
    except (ValueError, OSError) as exc:
        return "EMBED_RIFF_INVALID", f"invalid RIFF in {path.name}: {exc}"
    if suspicion:
        return "EMBED_FMT_INVALID", f"{suspicion} in {path.name}"
    if fmt_count > 1:
        return "EMBED_FMT_INVALID", f"duplicate fmt chunks ({fmt_count}) in {path.name}"
    if data_count > 1:
        return "EMBED_DATA_INVALID", f"duplicate data chunks ({data_count}) in {path.name}"
    if fmt_count == 0:
        return "EMBED_FMT_MISSING", f"file {path.name} has no fmt chunk"
    if data_count == 0:
        return "EMBED_DATA_MISSING", f"file {path.name} has no data chunk"
    return None, None


def _read_fmt(payload: bytes) -> _Fmt:
    """Le os campos geometricos do payload do chunk fmt."""
    tag, channels, rate, byte_rate, block_align, bits = struct.unpack("<HHIIHH", payload[:16])
    subformat = None
    if tag == EXTENSIBLE and len(payload) >= 26:
        subformat = struct.unpack("<H", payload[24:26])[0]
    return _Fmt(tag, subformat, channels, rate, byte_rate, block_align, bits)


def _validate_bits(fmt: _Fmt, path: Path) -> tuple[str | None, str | None]:
    """Bits suportados: 8, 16, 24 e 32; IEEE float somente em 32."""
    if fmt.tag == IEEE_FLOAT or fmt.subformat == IEEE_FLOAT:
        if fmt.bits != 32:
            return (
                "EMBED_BITS_UNSUPPORTED",
                f"IEEE float requires 32 bits ({fmt.bits}) in {path.name}",
            )
        return None, None
    if fmt.bits not in SUPPORTED_BITS:
        return (
            "EMBED_BITS_UNSUPPORTED",
            f"bits ({fmt.bits}) outside {SUPPORTED_BITS} in {path.name}",
        )
    return None, None


def _validate_header(fmt: _Fmt, path: Path) -> tuple[str | None, str | None]:
    """Canais, taxa, block_align e byte_rate do cabecalho."""
    if fmt.channels not in (1, 2):
        return (
            "EMBED_CHANNELS_UNSUPPORTED",
            f"channels ({fmt.channels}) outside 1 or 2 in {path.name}",
        )
    if fmt.sample_rate <= 0:
        return (
            "EMBED_FMT_INVALID",
            f"invalid sample rate ({fmt.sample_rate}) in {path.name}",
        )
    if fmt.block_align != fmt.align:
        return (
            "EMBED_FMT_INVALID",
            f"block_align ({fmt.block_align}) differs from the expected ({fmt.align}) in {path.name}",
        )
    expected_rate = fmt.sample_rate * fmt.align
    if fmt.byte_rate != expected_rate:
        return (
            "EMBED_FMT_INVALID",
            f"byte_rate ({fmt.byte_rate}) differs from the expected ({expected_rate}) in {path.name}",
        )
    return None, None


def _validate_data(meta: dict, fmt: _Fmt, path: Path) -> tuple[str | None, str | None]:
    """O chunk data precisa estar alinhado ao cabecalho que o descreve."""
    data_size = int(meta.get("data_size") or 0)
    if data_size % fmt.align:
        return (
            "EMBED_DATA_INVALID",
            f"data ({data_size}) is not a multiple of align ({fmt.align}) in {path.name}",
        )
    frames = int(meta.get("duration_samples") or 0)
    if frames * fmt.align != data_size:
        return "EMBED_DATA_INVALID", f"data frames diverge from the header in {path.name}"
    return None, None


def _validate_wav_geometry(meta: dict, path: Path) -> tuple[str | None, str | None]:
    """Confere bits, canais, taxa e alinhamento do WAV contra o chunk fmt.

    O payload do fmt e a unica fonte: o que o cabecalho descreve precisa
    casar com o chunk data que sera copiado para a essencia.
    """
    fmt = _read_fmt(meta.get("fmt_payload") or b"")
    code, message = _validate_bits(fmt, path)
    if code:
        return code, message
    code, message = _validate_header(fmt, path)
    if code:
        return code, message
    return _validate_data(meta, fmt, path)


def _inspect_asset(asset: MediaAsset) -> tuple[str | None, str | None, dict | None, int]:
    """Valida um asset e devolve os bytes de DATA comprovados pelo cabecalho."""
    target = asset.resolved_path or asset.filename
    if not target:
        return (
            "EMBED_MEDIA_MISSING",
            f"asset {asset.id!r} with no declared path",
            {"asset_id": asset.id},
            0,
        )
    path = Path(target)
    ctx = {"asset_id": asset.id, "path": str(path)}
    if not path.is_file():
        return (
            "EMBED_MEDIA_MISSING",
            f"file not found: {path}",
            ctx,
            0,
        )
    try:
        kind = _classify_format(path)
    except (OSError, struct.error) as exc:
        return (
            "EMBED_METADATA_UNREADABLE",
            str(exc),
            ctx,
            0,
        )
    if kind != "wav":
        return (
            "EMBED_FORMAT_UNSUPPORTED",
            f"format not supported ({kind}): {path.name}",
            ctx,
            0,
        )
    riff_err, riff_msg = _validate_asset_riff(path)
    if riff_err:
        return riff_err, riff_msg, ctx, 0
    try:
        meta = read_audio_metadata(path)
        geometry_err, geometry_msg = _validate_wav_geometry(meta, path)
    except (ValueError, OSError, struct.error) as exc:
        return (
            "EMBED_METADATA_UNREADABLE",
            str(exc),
            ctx,
            0,
        )
    if geometry_err:
        return geometry_err, geometry_msg, ctx, 0
    return None, None, None, int(meta["data_size"])


def _validate_clips(
    session: SessionIR, eligible_ids: set[str]
) -> tuple[str | None, str | None, dict | None]:
    """Verifica se todos os clips de audio apontam para assets validos."""
    for track in getattr(session, "tracks", ()):
        if getattr(track, "is_video", False):
            continue
        for clip in getattr(track, "clips", ()):
            if not clip.media_id or clip.media_id not in eligible_ids:
                return (
                    "EMBED_CLIP_NO_ELIGIBLE_ASSET",
                    f"clip {clip.name!r} without an audio asset eligible for embed",
                    {"clip": clip.name, "media_id": clip.media_id},
                )
    return None, None, None


def preflight_embed(session: SessionIR, report: Report) -> bool:
    """Valida assets, clips e o teto de 2 GiB antes de qualquer gravacao."""
    assets = embed_assets(session)
    eligible_asset_ids = set()
    total = 0
    has_error = False

    for asset in assets:
        code, message, context, asset_data = _inspect_asset(asset)
        if code:
            report.error(code, message, context or {})
            has_error = True
        else:
            eligible_asset_ids.add(asset.id)
            total += asset_data

    clip_code, clip_message, clip_context = _validate_clips(session, eligible_asset_ids)
    if clip_code:
        report.error(clip_code, clip_message, clip_context or {})
        has_error = True

    # O limite e do payload gravado, nunca do arquivo stereo original: um
    # split stereo devolve os mesmos bytes de audio em dois monos.
    if not check_embed_size(total, report, stage="preflight"):
        has_error = True

    return not has_error


def _find_file_source_mobs(f) -> dict[str, object]:
    """Localiza todos os SourceMobs com WAVEDescriptor pelo mob_id."""
    mobs = {}
    for mob in getattr(f.content, "mobs", ()):
        if type(mob).__name__ != "SourceMob":
            continue
        desc = getattr(mob, "descriptor", None)
        if desc is not None and type(desc).__name__ in ("WAVEDescriptor", "PCMDescriptor"):
            mobs[str(mob.mob_id)] = mob
    return mobs


def _read_and_check_essence(
    essence, expected_bytes: int, mob_id: str
) -> tuple[str | None, str | None, dict | None]:
    """Le o stream de essencia em blocos <= 64 KiB e valida RIFF e tamanho."""
    try:
        stream = essence.open()
    except Exception as exc:
        return "VERIFY_EMBED_STREAM_FAILED", f"failed to open stream: {exc}", {"mob_id": mob_id}
    try:
        header = stream.read(12)
        if (header[:4], header[8:12]) != (b"RIFF", b"WAVE"):
            return "VERIFY_EMBED_NOT_RIFF", "the essence is not RIFF/WAVE", {"mob_id": mob_id}
        total = len(header)
        while True:
            block = stream.read(COPY_CHUNK_SIZE)
            if not block:
                break
            total += len(block)
    finally:
        stream.close()

    if total != expected_bytes:
        return (
            "VERIFY_EMBED_SIZE_MISMATCH",
            f"size differs: {total} != {expected_bytes}",
            {"mob_id": mob_id, "actual": total, "expected": expected_bytes},
        )
    return None, None, None


def _verify_source_mob(mob, expected_bytes: int) -> tuple[str | None, str | None, dict | None]:
    """Valida essencia, ausencia de Locator e conteudo do mob de arquivo."""
    mob_id = str(mob.mob_id)
    essence = getattr(mob, "essence", None)
    if essence is None:
        return "VERIFY_EMBED_NO_ESSENCE", f"mob {mob_id} without essence", {"mob_id": mob_id}

    desc = getattr(mob, "descriptor", None)
    if desc is not None:
        for prop in desc.properties():
            if getattr(prop, "name", None) == "Locator":
                return (
                    "VERIFY_EMBED_LOCATOR_PRESENT",
                    f"mob {mob_id} contains Locator in properties()",
                    {"mob_id": mob_id},
                )

    return _read_and_check_essence(essence, expected_bytes, mob_id)


def verify_embedded_aaf(path: Path, expected: dict[str, int], report: Report) -> bool:
    """Reabre o AAF e valida WAVEDescriptors, essencias e ausencia de Locator."""
    path = Path(path)
    if not path.is_file():
        report.error(
            "VERIFY_EMBED_FILE_MISSING",
            f"file not found: {path}",
            {"path": str(path)},
        )
        return False

    errors: list[tuple[str, str, dict]] = []
    try:
        with aaf2.open(str(path), "r") as f:
            file_mobs = _find_file_source_mobs(f)
            if set(file_mobs.keys()) != set(expected.keys()):
                errors.append(
                    (
                        "VERIFY_EMBED_MOBS_MISMATCH",
                        (
                            "SourceMobs differ from the expected ones: "
                            f"{sorted(file_mobs.keys())} != {sorted(expected.keys())}"
                        ),
                        {
                            "found": sorted(file_mobs.keys()),
                            "expected": sorted(expected.keys()),
                        },
                    )
                )
            for mob_id, mob in file_mobs.items():
                if mob_id in expected:
                    err, msg, ctx = _verify_source_mob(mob, expected[mob_id])
                    if err:
                        errors.append((err, msg, ctx or {}))
    except Exception as exc:
        errors.append(("VERIFY_EMBED_FAILED", f"failed to open AAF: {exc}", {"path": str(path)}))

    # Emissao fora do try para que StrictModeError nunca seja engolido
    for code, msg, ctx in errors:
        report.error(code, msg, ctx)

    return len(errors) == 0
