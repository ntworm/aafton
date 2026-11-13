"""Ligacao de midia externa no AAF.

Separa de aaf_write a criacao dos mobs por asset, os metadados de midia
e a resolucao do mob por canal.
"""

import fractions
import math
import struct
from pathlib import Path

import aaf2

from .aaf_write_embed import embed_assets, embed_wave
from .ir import SessionIR
from .media_extract import read_audio_metadata
from .report import Report


def min_duration_from_clips(session: SessionIR) -> dict[str, int]:
    """Duracao minima por asset deduzida dos clips (fallback de duracao)."""
    durations: dict[str, int] = {}
    for track in session.tracks:
        for clip in track.clips:
            if clip.media_id:
                asset = session.media_by_id(clip.media_id)
                source_rate = session.sample_rate
                if asset and asset.sample_rate:
                    source_rate = asset.sample_rate
                total_samples = (clip.source_offset_samples or 0) + clip.duration_samples
                exact = fractions.Fraction(total_samples) * source_rate / session.sample_rate
                needed = math.ceil(exact)
                if needed > durations.get(clip.media_id, 0):
                    durations[clip.media_id] = needed
    return durations


def link_media(
    f,
    session: SessionIR,
    report: Report,
    mono_sources,
    min_durations,
    *,
    media: str = "link",
    expected_essence: dict | None = None,
) -> dict:
    """Cria os mobs de cada asset de audio e devolve media_id -> referencias.

    No modo embed a essencia de cada arquivo tambem entra no AAF, e o tamanho
    gravado e registrado em ``expected_essence`` por mob_id do SourceMob.
    """
    mobs_por_media_id = {}
    for asset in _assets_para_ligar(session, media):
        mobs_por_media_id[asset.id] = _link_asset(
            f,
            session,
            report,
            asset,
            mono_sources,
            min_durations,
            media=media,
            expected_essence=expected_essence,
        )
    return mobs_por_media_id


def _assets_para_ligar(session: SessionIR, media: str) -> list:
    """Assets que entram no AAF, na ordem em que os mobs sao criados.

    No embed a selecao canonica e ``embed_assets``: e a mesma lista que o
    preflight e o split stereo ja usam, entao o AAF nao pode ter um asset que
    a essencia nao acompanhou, nem deixar de ter um asset elegivel. No link
    vale a regra de sempre: todo asset de audio, na ordem da sessao, fora os
    que so as tracks de video usam.
    """
    if media == "embed":
        return list(embed_assets(session))
    video_media_ids = _video_media_ids(session)
    return [asset for asset in session.media if asset.id not in video_media_ids]


def _video_media_ids(session: SessionIR) -> set[str]:
    """Ids de midia usados por tracks de video (nao entram no AAF)."""
    return {
        clip.media_id for t in session.tracks if getattr(t, "is_video", False) for clip in t.clips
    }


def _link_asset(
    f,
    session,
    report,
    asset,
    mono_sources,
    min_durations,
    *,
    media: str = "link",
    expected_essence: dict | None = None,
) -> list:
    """Mobs e referencias de canal de um asset de audio."""
    duration_samples = _asset_duration_samples(report, asset, min_durations)
    channels = asset.channels or 1
    sample_rate = asset.sample_rate or session.sample_rate
    resolved, bits, fmt_name = _detect_format(asset)
    meta = _build_meta(resolved, fmt_name, bits, sample_rate, duration_samples, channels)
    return _link_paths(
        f,
        asset,
        resolved,
        meta,
        mono_sources,
        bits,
        channels,
        media=media,
        expected_essence=expected_essence,
    )


def _asset_duration_samples(report, asset, min_durations) -> int:
    """Duracao do asset; quando ausente, deduz dos clips e reporta."""
    duration_samples = asset.duration_samples
    if duration_samples is None or duration_samples == 0:
        duration_samples = min_durations.get(asset.id, 0)
        if duration_samples > 0:
            report.warning(
                "MEDIA_DURATION_DEDUCED",
                f"duration of {asset.filename!r} deduced from the clips ({duration_samples} samples); the file was not read",
                {"filename": asset.filename, "duration_samples": duration_samples},
            )
    return duration_samples


def checar_formatos_media(session: SessionIR, report: Report) -> bool:
    """Confere que todo asset de audio a ligar esta em WAV ou AIFF.

    O AAF so referencia audio PCM. Um arquivo de outro formato (MP3, por
    exemplo) quebra o pyaaf2 no meio da exportacao, entao o erro entra no
    relatorio antes de qualquer descritor ser criado.

    Devolve False quando algum asset esta fora do formato suportado. O embed
    liga a mesma selecao de assets, entao a conferencia vale para os dois.
    """
    ok = True
    for asset in _assets_para_ligar(session, "link"):
        caminho = _caminho_do_asset(asset)
        if caminho is None or not caminho.is_file():
            continue
        if _container_pcm(caminho) is None:
            report.error(
                "WRITE_MEDIA_FORMAT_UNSUPPORTED",
                f"the file {caminho.name!r} is neither WAV nor AIFF; the AAF only references PCM "
                "audio in WAV or AIFF, and the file needs to be converted",
                {"filename": str(caminho)},
            )
            ok = False
    return ok


def _caminho_do_asset(asset) -> Path | None:
    """Caminho do asset, quando ele tem um."""
    bruto = asset.resolved_path or asset.filename
    return Path(bruto) if bruto else None


def _container_pcm(caminho: Path) -> str | None:
    """Container do audio pelo cabecalho: 'wav', 'aiff' ou None se nao for PCM.

    None quer dizer que o arquivo nao e um container de PCM que o AAF
    referencie, e nao que ele esteja faltando.
    """
    try:
        with caminho.open("rb") as arquivo:
            header = arquivo.read(12)
    except OSError:
        return None
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"FORM" and header[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    return None


def _detect_format(asset) -> tuple[str, int, str]:
    """Caminho resolvido, bits e formato detectados pelos magic bytes.

    O container vem do cabecalho do arquivo: e ele que diz se o link aponta
    para WAV ou AIFF quando os metadados do disco nao podem ser lidos.
    """
    resolved = asset.resolved_path or asset.filename
    bits = 16  # default
    fmt_name = "wav"
    caminho = _caminho_do_asset(asset)
    if caminho is not None:
        fmt_name = _container_pcm(caminho) or fmt_name
        try:
            meta_disk = read_audio_metadata(caminho)
            bits = meta_disk.get("bits") or 16
            fmt_name = meta_disk.get("format", fmt_name)
        except Exception:
            pass
    return resolved, bits, fmt_name


def _build_meta(resolved, fmt_name, bits, sample_rate, duration_samples, channels) -> dict:
    """Metadados de midia no formato esperado pelo linkador externo."""
    codec_name = f"pcm_s{bits}le" if fmt_name == "wav" else f"pcm_s{bits}be"
    return {
        "format": {"filename": resolved, "format_name": fmt_name},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": codec_name,
                "sample_rate": sample_rate,
                "duration_ts": duration_samples,
                "channels": channels,
            }
        ],
    }


def _link_paths(
    f,
    asset,
    resolved,
    meta,
    mono_sources,
    bits,
    channels,
    *,
    media: str = "link",
    expected_essence: dict | None = None,
) -> list:
    """Liga cada caminho do asset e devolve as referencias por canal."""
    media_paths = mono_sources.get(asset.id, (resolved,))
    is_mono_split = asset.id in mono_sources
    references = []
    for media_path in media_paths:
        if media == "embed":
            link_meta = _embed_meta_for_path(media_path)
        else:
            link_meta = _link_meta_for_path(meta, media_path, is_mono_split, bits)
        master_mob, source_mob, _ = f.content.link_external_wav(link_meta)
        _use_direct_source_clip(master_mob, source_mob, channels)
        if media == "embed":
            _embarcar(f, source_mob, Path(media_path), expected_essence)
        references.extend(
            (master_mob, source_mob, n + 1, is_mono_split)
            for n in range(link_meta["streams"][0]["channels"])
        )
    return references


def _embarcar(f, source_mob, media_path: Path, expected_essence: dict | None) -> None:
    """Grava a essencia RIFF no SourceMob do arquivo e registra o tamanho."""
    size = embed_wave(f, source_mob, media_path)
    if expected_essence is not None:
        expected_essence[str(source_mob.mob_id)] = size


def _embed_meta_for_path(media_path) -> dict:
    """Metadados de linkagem do arquivo real que sera embarcado no AAF."""
    codec, channels, sample_rate, frames = _wav_facts(Path(media_path))
    return {
        "format": {"filename": str(media_path), "format_name": "wav"},
        "streams": [
            {
                "codec_type": "audio",
                "codec_name": codec,
                "sample_rate": sample_rate,
                "duration_ts": frames,
                "channels": channels,
            }
        ],
    }


def _wav_codec(fmt_payload: bytes, bits: int) -> str:
    """Nome do codec de acordo com o chunk fmt: IEEE float vira pcm_f32le."""
    if len(fmt_payload) >= 2:
        tag = struct.unpack_from("<H", fmt_payload, 0)[0]
        if tag == 3:
            return "pcm_f32le"
        if tag == 0xFFFE and len(fmt_payload) >= 26:
            if struct.unpack_from("<H", fmt_payload, 24)[0] == 3:
                return "pcm_f32le"
    return f"pcm_s{bits}le"


def _wav_facts(path: Path) -> tuple[str, int, int, int]:
    """Codec, canais, taxa e frames do WAV real que sera embarcado.

    O codec segue o arquivo, e nao a deteccao do asset: um WAV IEEE float
    (fmt 3) ou extensivel com subformato float vira pcm_f32le, e nao um
    PCM de 32 bits que mentiria sobre os bytes embarcados.
    """
    meta = read_audio_metadata(path)
    codec = _wav_codec(meta.get("fmt_payload") or b"", meta.get("bits") or 16)
    return codec, meta["channels"], meta["sample_rate"], meta["duration_samples"]


def _split_stereo_embed(session: SessionIR, directory: Path, report: Report) -> dict | None:
    """Separa em mono os assets estereo elegiveis, dentro do temporario do embed.

    O split cobre todos os assets do embed, inclusive os declarados e nao
    usados: eles tambem entram no AAF, entao tambem precisam de essencia.
    """
    from .audio_split import split_stereo_source

    mono_sources = {}
    for asset in embed_assets(session):
        caminho = Path(asset.resolved_path or asset.filename)
        channels = None
        if caminho.is_file():
            try:
                channels = read_audio_metadata(caminho).get("channels")
            except Exception:
                pass
        if channels is None:
            channels = asset.channels
        if channels != 2:
            continue
        try:
            mono_sources[asset.id] = split_stereo_source(caminho, directory, preserve_metadata=True)
        except (OSError, ValueError) as exc:
            report.error("WRITE_STEREO_SOURCE", str(exc), {"media_id": asset.id})
            return None
    return mono_sources


def _link_meta_for_path(meta, media_path, is_mono_split, bits) -> dict:
    """Metadados de linkagem de um caminho, forcado a mono quando dividido."""
    link_meta = {
        "format": dict(meta["format"]),
        "streams": [dict(meta["streams"][0])],
    }
    link_meta["format"]["filename"] = str(media_path)
    if is_mono_split:
        link_meta["format"]["format_name"] = "wav"
        link_meta["streams"][0]["channels"] = 1
        link_meta["streams"][0]["codec_name"] = f"pcm_s{bits}le"
    return link_meta


def _use_direct_source_clip(master_mob, source_mob, channels) -> None:
    """Perfil externo: SourceClip direto em cada slot de midia."""
    for mob in (master_mob, source_mob) if channels == 2 else ():
        for media_slot in mob.slots:
            segment = media_slot.segment
            if isinstance(segment, aaf2.components.Sequence):
                components = list(segment.components)
                if len(components) == 1 and isinstance(components[0], aaf2.components.SourceClip):
                    media_slot.segment = components[0]


def _clonar_master_mob(f, master_mob, source_mob, nome: str):
    """Cria novo MasterMob apontando para o mesmo source_mob, com outro nome."""
    novo = f.create.MasterMob()
    novo.name = nome
    for slot in master_mob.slots:
        if slot.media_kind in ("Picture", "Sound"):
            comps = getattr(slot.segment, "components", None)
            orig_sc = (
                slot.segment
                if isinstance(slot.segment, aaf2.components.SourceClip)
                else (comps[0] if comps else None)
            )
            source_slot_id = getattr(orig_sc, "slot_id", slot.slot_id)
            from_clip = source_mob.create_source_clip(
                slot_id=source_slot_id, media_kind=slot.media_kind
            )
            if isinstance(slot.segment, aaf2.components.SourceClip):
                to_slot = novo.create_timeline_slot(edit_rate=slot.edit_rate, slot_id=slot.slot_id)
                to_slot.segment = from_clip
            else:
                to_slot = novo.create_empty_sequence_slot(
                    edit_rate=slot.edit_rate, slot_id=slot.slot_id, media_kind=slot.media_kind
                )
                to_slot.segment.components.append(from_clip)
            if slot.name:
                to_slot.name = slot.name
            if "PhysicalTrackNumber" in slot:
                to_slot["PhysicalTrackNumber"].value = slot["PhysicalTrackNumber"].value
    f.content.mobs.append(novo)
    return novo


def _target_clip_name(clip_name: str, channel_index: int, is_mono_split: bool) -> str:
    """Nome do MasterMob para o clipe, acrescentando sufixo .L/.R quando split."""
    if not is_mono_split:
        return clip_name
    suffix = ".L" if channel_index == 0 else (".R" if channel_index == 1 else "")
    if suffix and not clip_name.endswith(suffix):
        return f"{clip_name}{suffix}"
    return clip_name


class MobResolver:
    """Resolve o mob e o slot de cada media_id, com descritor de fallback."""

    def __init__(self, f, session, report, mobs_por_media_id, min_durations):
        self._f = f
        self._session = session
        self._report = report
        self._mobs_por_media_id = mobs_por_media_id
        self._min_durations = min_durations
        self._named_mobs = {}
        self._mob_assigned_names = {}
        self._unnamed_assets = self._detect_unnamed_assets(session)

    def _detect_unnamed_assets(self, session) -> set[str]:
        """Assets com ao menos um clip sem nome customizado (preservam nome raiz)."""
        unnamed = set()
        for track in getattr(session, "tracks", ()):
            for clip in getattr(track, "clips", ()):
                if clip.media_id and not getattr(clip, "name", None):
                    unnamed.add(clip.media_id)
        return unnamed

    def get_mob(self, media_id: str, channel_index: int = 0, clip_name: str | None = None):
        """Mob e slot do canal pedido; assets nao declarados usam fallback."""
        if media_id not in self._mobs_por_media_id:
            return self._fallback_mob(media_id, clip_name)
        references = self._mobs_por_media_id[media_id]
        ref = references[min(channel_index, len(references) - 1)]
        master_mob, source_mob, slot_id, is_mono_split = ref
        if not clip_name:
            return master_mob, slot_id
        target_name = _target_clip_name(clip_name, channel_index, is_mono_split)
        return self._resolve_named_mob(
            media_id, channel_index, is_mono_split, target_name, master_mob, source_mob, slot_id
        )

    def _resolve_named_mob(
        self, media_id, channel_index, is_mono_split, target_name, master_mob, source_mob, slot_id
    ):
        """Devolve o MasterMob com target_name, reutilizando ou clonando."""
        key_channel = channel_index if is_mono_split else 0
        key = (media_id, key_channel, target_name)
        if key in self._named_mobs:
            return self._named_mobs[key], slot_id
        assigned = self._mob_assigned_names.get(id(master_mob))
        if master_mob.name == target_name:
            self._mob_assigned_names[id(master_mob)] = target_name
            self._named_mobs[key] = master_mob
            return master_mob, slot_id
        if media_id not in self._unnamed_assets and (assigned is None or assigned == target_name):
            master_mob.name = target_name
            self._mob_assigned_names[id(master_mob)] = target_name
            self._named_mobs[key] = master_mob
            return master_mob, slot_id
        novo = _clonar_master_mob(self._f, master_mob, source_mob, target_name)
        self._mob_assigned_names[id(novo)] = target_name
        self._named_mobs[key] = novo
        return novo, slot_id

    def _fallback_mob(self, media_id: str, clip_name: str | None = None):
        """Cria o descritor de fallback de clips que apontam assets nao declarados."""
        self._report.warning(
            "MEDIA_FALLBACK_MOB",
            f"asset {media_id!r} not declared in the session; creating a fallback descriptor",
            {"media_id": media_id},
        )
        duration_samples = self._min_durations.get(media_id, self._session.sample_rate * 60)
        meta = {
            "format": {"filename": str(media_id), "format_name": "wav"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": "pcm_s16le",
                    "sample_rate": self._session.sample_rate,
                    "duration_ts": duration_samples,
                    "channels": 1,
                }
            ],
        }
        master_mob, source_mob, _t = self._f.content.link_external_wav(meta)
        if clip_name:
            master_mob.name = clip_name
            self._mob_assigned_names[id(master_mob)] = clip_name
        self._mobs_por_media_id[media_id] = [(master_mob, source_mob, 1, False)]
        return master_mob, 1


def source_start(session, report, clip, mob, slot_id, offset: int = 0):
    """Inicio da fonte em samples do arquivo, avisando desvios de arredondamento."""
    source_rate = fractions.Fraction(str(mob.slot_at(slot_id).edit_rate))
    total_samples = (clip.source_offset_samples or 0) + offset
    exact = fractions.Fraction(total_samples) * source_rate / session.sample_rate
    value = round(exact)
    if value != exact:
        report.warning(
            "WRITE_SOURCE_OFFSET_ROUNDING",
            "source offset between samples; rounded",
            {"clip": clip.name, "residual_source_samples": float(exact - value)},
        )
    return value
