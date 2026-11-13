"""Verificacao independente entre o AAF de origem e o .als gerado."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .als_read import read_session
from .formats.origem import ler_origem_verify, read_aaf
from .ir import ClipIR, SessionIR, TrackIR
from .report import Report
from .timecode import samples_to_label
from .verify_coverage import formatar_cobertura, mapeamento_de_slots, slots_por_track

import aaf2


@dataclass
class VerifyRow:
    track: str
    clip: str
    aaf_in: str
    aaf_out: str
    als_in: str
    als_out: str
    delta_samples: int
    delta_frames: float
    status: str
    anchor_source: str | None = None
    delta_end_samples: int | None = None
    is_audio: bool = True


@dataclass
class VerifyResult:
    rows: list[VerifyRow] = field(default_factory=list)
    tolerance_samples: int = 0
    regime_name: str = ""
    coverage_msg: str = ""
    audio_expected: int = 0
    audio_compared: int = 0
    audio_missing: int = 0
    audio_extra: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        if self.audio_expected <= 0:
            return False
        if self.audio_compared != self.audio_expected:
            return False
        if self.audio_missing != 0 or self.audio_extra != 0:
            return False
        if len(self.errors) > 0:
            return False
        for linha in self.rows:
            if linha.is_audio:
                if linha.status != "ok":
                    return False
                if linha.delta_end_samples is None:
                    return False
                if abs(linha.delta_samples) > self.tolerance_samples:
                    return False
                if abs(linha.delta_end_samples) > self.tolerance_samples:
                    return False
            else:
                if linha.status not in ("ok", "presumed"):
                    return False
        return True

    @property
    def max_delta_samples(self) -> int:
        deltas = []
        for linha in self.rows:
            if linha.status in ("ok", "out of tolerance"):
                deltas.append(abs(linha.delta_samples))
                if linha.delta_end_samples is not None:
                    deltas.append(abs(linha.delta_end_samples))
        return max(deltas, default=0)

    def to_table(self) -> str:
        linhas = [
            f"Regime: {self.regime_name}",
            f"Result: {'passed' if self.ok else 'failed'}",
            f"Audio events: {self.audio_compared} compared of {self.audio_expected} expected "
            f"({self.audio_missing} missing, {self.audio_extra} extra)",
        ]
        if self.errors:
            linhas.append(f"Errors: {'; '.join(self.errors)}")
        if self.coverage_msg:
            linhas.append(self.coverage_msg)
        linhas.append(
            "Fields out of scope: media offset, audio content, clip gain/fader/pan and envelopes "
            "were not verified."
        )
        cabecalho = (
            f"{'TRACK':<16}{'CLIP':<20}{'TC AAF (in-out)':<24}"
            f"{'TC ALS (in-out)':<24}{'D START':>8}{'D END':>8}{'D FRAMES':>9}  STATUS"
        )
        linhas.append(cabecalho)
        has_presumida = False
        anchor_used = None
        for linha in self.rows:
            if linha.status == "presumed" and linha.anchor_source != "VIDEO_ANCHOR_FROM_AAF":
                has_presumida = True
                anchor_used = linha.anchor_source
                aaf_col = "---"
                als_col = linha.als_in + "-" + linha.als_out
                d_start = "---"
                d_end = "---"
                delta_f = "---"
            elif linha.status == "missing in the ALS":
                aaf_col = linha.aaf_in + "-" + linha.aaf_out
                als_col = "---"
                d_start = "---"
                d_end = "---"
                delta_f = "---"
            elif linha.status == "extra in the ALS":
                aaf_col = "---"
                als_col = linha.als_in + "-" + linha.als_out
                d_start = "---"
                d_end = "---"
                delta_f = "---"
            else:
                aaf_col = linha.aaf_in + "-" + linha.aaf_out
                als_col = linha.als_in + "-" + linha.als_out
                d_start = str(linha.delta_samples)
                d_end = (
                    str(linha.delta_end_samples)
                    if linha.delta_end_samples is not None
                    else str(linha.delta_samples)
                )
                delta_f = f"{linha.delta_frames:.2f}"

            linhas.append(
                f"{linha.track[:15]:<16}{linha.clip[:19]:<20}"
                f"{aaf_col:<24}"
                f"{als_col:<24}"
                f"{d_start:>8}{d_end:>8}{delta_f:>9}  {linha.status}"
            )

        if has_presumida:
            linhas.append("")
            linhas.append(
                "Note: The position of the video cannot be checked because the AAF has no picture slot."
            )
            linhas.append(f"      (Source used to anchor: {anchor_used})")
        return "\n".join(linhas) + "\n"

    def to_markdown(self) -> str:
        def cell(value: object) -> str:
            return str(value).replace("|", "\\|").replace("\n", " ")

        lines = [
            "# Verification AAF -> ALS",
            "",
            f"- **Result**: {'passed' if self.ok else 'failed'}",
            f"- **Regime**: {self.regime_name}",
            f"- **Tolerance**: {self.tolerance_samples} samples",
            f"- **Largest deviation**: {self.max_delta_samples} samples",
            f"- **Audio events**: {self.audio_compared} compared of {self.audio_expected} expected "
            f"({self.audio_missing} missing, {self.audio_extra} extra)",
            "- **Out of scope**: media offset, audio content, clip gain/fader/pan and envelopes "
            "were not verified",
        ]
        if self.errors:
            lines.append(f"- **Errors**: {', '.join(self.errors)}")
        if self.coverage_msg:
            lines.append(f"- **Coverage**: {self.coverage_msg.removeprefix('Coverage: ').strip()}")
        lines.extend(
            [
                "",
                "| Track | Clip | TC AAF | TC ALS | Start deviation (samples) | End deviation (samples) | Status |",
                "| --- | --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for row in self.rows:
            aaf_range = f"{row.aaf_in}-{row.aaf_out}" if row.aaf_in != "-" else "---"
            als_range = f"{row.als_in}-{row.als_out}" if row.als_in != "-" else "---"
            d_ini = str(row.delta_samples) if row.status in ("ok", "out of tolerance") else "---"
            d_fim = (
                str(row.delta_end_samples)
                if row.delta_end_samples is not None and row.status in ("ok", "out of tolerance")
                else "---"
            )
            lines.append(
                f"| {cell(row.track)} | {cell(row.clip)} | {cell(aaf_range)} | "
                f"{cell(als_range)} | {d_ini} | {d_fim} | {cell(row.status)} |"
            )
        return "\n".join(lines) + "\n"


def _index(
    session: SessionIR,
    source_indices: list[list[int]] | None = None,
    fallback_posicional: bool = False,
    channel_aware: bool = False,
) -> dict[tuple[int, int], ClipIR]:
    """Indexa clips por (indice da track, ordem do evento na track)."""
    if source_indices is not None and len(source_indices) != len(session.tracks):
        raise ValueError(
            "Size of the audio_source_indices map does not match the number of tracks in the session"
        )

    resultado: dict[tuple[int, int], ClipIR] = {}
    for i, track in enumerate(session.tracks):
        if fallback_posicional:
            indices = [i]
        elif source_indices is not None:
            indices = source_indices[i]
            if not indices or all(idx < 0 for idx in indices):
                indices = [-1 - i]
        else:
            indices = [track.source_index]
        if channel_aware and len(indices) > 1:
            for channel_index, track_idx in enumerate(indices):
                ordenados = sorted(
                    (clip for clip in track.clips if clip.channel_index == channel_index),
                    key=lambda c: (c.timeline_start_samples, c.timeline_end_samples, c.name, c.id),
                )
                for posicao, clip in enumerate(ordenados):
                    chave = (track_idx, posicao)
                    if chave in resultado:
                        raise ValueError(f"Collision in the event index for key {chave}")
                    resultado[chave] = clip
            continue

        ordenados = sorted(
            track.clips,
            key=lambda c: (c.timeline_start_samples, c.timeline_end_samples, c.name, c.id),
        )
        for track_idx in indices:
            for posicao, clip in enumerate(ordenados):
                chave = (track_idx, posicao)
                if chave in resultado:
                    raise ValueError(f"Collision in the event index for key {chave}")
                resultado[chave] = clip
    return resultado


def verify_conversion(
    aaf_path: str | Path,
    als_path: str | Path,
    override_tolerance_samples: int | None = None,
) -> VerifyResult:
    """Compara clip a clip o AAF de origem e o .als gerado."""
    if override_tolerance_samples is not None and override_tolerance_samples < 0:
        raise ValueError("Tolerance cannot be negative")

    from .sidecar import SidecarMissingError, read_anchor

    fades_mode = "absorb"
    stereo_mode = "pair"
    try:
        anchor = read_anchor(als_path)
        if getattr(anchor, "fades", None):
            fades_mode = anchor.fades
        if getattr(anchor, "stereo", None):
            stereo_mode = anchor.stereo
    except (SidecarMissingError, Exception):
        pass

    aaf_report = Report(strict=False)
    origem = ler_origem_verify(aaf_path, aaf_report, fades_mode, als_path, read_aaf)

    from .video import process_video

    video_path = getattr(anchor, "video_path", None) if "anchor" in locals() else None
    video_report = Report(strict=False)
    process_video(origem, Path(aaf_path), video_path, None, video_report)

    als_report = Report(strict=False)
    destino = read_session(als_path, als_report)

    collected_errors: list[str] = []
    for rep in (aaf_report, video_report, als_report):
        for issue in rep.issues:
            if issue.severity == "error" or issue.code in {
                "AAF_UNKNOWN_COMPONENT",
                "AAF_NO_SEQUENCE",
                "ALS_CLIP_UNREADABLE",
            }:
                collected_errors.append(f"{issue.code}: {issue.message}")
        if rep.incomplete and not any(i.severity == "error" for i in rep.issues):
            collected_errors.append("Incomplete read")

    import xml.etree.ElementTree as ET
    import gzip

    root = ET.parse(gzip.open(Path(als_path), "rb")).getroot()
    has_group = len(root.findall(".//LiveSet/Tracks/GroupTrack")) > 0

    source_indices = getattr(anchor, "audio_source_indices", None) if "anchor" in locals() else None

    origem_indices_por_track = slots_por_track(origem)
    if source_indices is not None:
        normalized_indices = []
        for x in source_indices:
            if isinstance(x, int) and not isinstance(x, bool):
                normalized_indices.append([x])
            elif isinstance(x, list) and all(
                isinstance(y, int) and not isinstance(y, bool) for y in x
            ):
                normalized_indices.append(x)
            else:
                raise ValueError("Invalid audio_source_indices map")
        source_indices = normalized_indices

        if len(source_indices) != len(destino.tracks):
            raise ValueError(
                "Size of the audio_source_indices map differs from the number of AudioTracks in the ALS"
            )

        flat_indices = [idx for sublist in source_indices for idx in sublist]
        flat_valid = [idx for idx in flat_indices if idx >= 0]
        if len(flat_valid) != len(set(flat_valid)):
            raise ValueError("The audio_source_indices map contains duplicates")

        origem_primary_indices = [track.source_index for track in origem.tracks]
        if len(origem_primary_indices) != len(set(origem_primary_indices)):
            raise ValueError("There are duplicate source_index values in the source")

        origem_source_indices_list = [
            index for indices in origem_indices_por_track for index in indices
        ]
        if len(origem_source_indices_list) != len(set(origem_source_indices_list)):
            raise ValueError("There are duplicate source_index values in the source")
        origem_source_indices = set(origem_source_indices_list)

        for idx in flat_indices:
            if idx < 0:
                continue
            if idx not in origem_source_indices:
                raise ValueError(
                    f"The set of the audio_source_indices map differs from the source_index values of the source (index {idx} not found)"
                )

        # A origem fica intacta! Nao removemos tracks irmaos para que sejam testadas de verdade.

    else:
        if has_group:
            raise ValueError(
                "audio_source_indices map missing in a grouped session; identity unavailable"
            )

    fallback = source_indices is None and not has_group

    tabela_origem: dict[int, TrackIR] = {}
    for i, t in enumerate(origem.tracks):
        indices = [i] if fallback else origem_indices_por_track[i]
        for idx in indices:
            if idx in tabela_origem:
                raise RuntimeError(f"duplicate source_index {idx} in the source")
            tabela_origem[idx] = t

    tabela_destino: dict[int, TrackIR] = {}
    if fallback:
        for i, t in enumerate(destino.tracks):
            tabela_destino[i] = t
    elif source_indices is not None:
        for i, indices in enumerate(source_indices):
            valid_indices = [idx for idx in indices if idx >= 0]
            if not valid_indices:
                t_dest = destino.tracks[i]
                tabela_destino[-1 - i] = t_dest
                if not t_dest.clips:
                    collected_errors.append(
                        f"Destination track with no source mapping in the AAF: {t_dest.name}"
                    )
            else:
                for idx in valid_indices:
                    tabela_destino[idx] = destino.tracks[i]

    rate = origem.timecode_rate
    sample_rate = origem.sample_rate

    # Determine tolerance based on edit_rate of the AAF
    edit_rates = []
    try:
        with aaf2.open(str(aaf_path), "r") as f:
            comp = next((m for m in f.content.mobs if type(m).__name__ == "CompositionMob"), None)
            if comp:
                for slot in comp.slots:
                    if getattr(getattr(slot, "segment", None), "media_kind", None) == "Sound":
                        er = getattr(slot, "edit_rate", None)
                        if er:
                            if hasattr(er, "num"):
                                edit_rates.append(er.num / er.den)
                            else:
                                edit_rates.append(float(er))
    except Exception:
        pass

    if override_tolerance_samples is not None:
        tolerance = override_tolerance_samples
        regime = "Forced Tolerance"
    else:
        if not edit_rates or sample_rate in edit_rates:
            tolerance = 0
            regime = "Sample-exact (edit_rate == sample_rate)"
        else:
            # Tolerancia de um frame de offset
            tolerance = min(int(sample_rate / er) for er in edit_rates)
            regime = f"Frame-exact (edit_rates = {sorted(set(edit_rates))})"

    def rotulo(samples: int) -> str:
        return samples_to_label(samples, rate, sample_rate)

    samples_por_frame = sample_rate * rate.den / rate.num

    indice_origem = _index(
        origem,
        None if fallback else origem_indices_por_track,
        fallback_posicional=fallback,
        channel_aware=not fallback,
    )
    indice_destino = _index(destino, source_indices, fallback_posicional=fallback)

    resultado = VerifyResult(
        tolerance_samples=tolerance,
        regime_name=regime,
        errors=collected_errors,
    )

    audio_expected = 0
    for chave, clip in indice_origem.items():
        t_orig = tabela_origem.get(chave[0])
        is_audio = not getattr(t_orig, "is_video", False) if t_orig else True
        if is_audio:
            audio_expected += 1
    resultado.audio_expected = audio_expected

    chaves_origem = set(indice_origem.keys())
    chaves_destino = set(indice_destino.keys())
    todas_as_chaves = sorted(chaves_origem | chaves_destino, key=lambda k: (k[0], k[1]))

    for chave in todas_as_chaves:
        track_idx, posicao = chave
        clip_origem = indice_origem.get(chave)
        clip_destino = indice_destino.get(chave)

        if clip_origem is not None and clip_destino is not None:
            track_origem = tabela_origem.get(track_idx)
            is_vid = getattr(track_origem, "is_video", False) if track_origem else False
            is_audio = not is_vid
            track_name = track_origem.name if track_origem else f"Track {track_idx + 1}"

            delta_start = clip_destino.timeline_start_samples - clip_origem.timeline_start_samples
            delta_end = clip_destino.timeline_end_samples - clip_origem.timeline_end_samples

            if is_audio:
                resultado.audio_compared += 1
                status = (
                    "ok"
                    if (abs(delta_start) <= tolerance and abs(delta_end) <= tolerance)
                    else "out of tolerance"
                )
                resultado.rows.append(
                    VerifyRow(
                        track=track_name,
                        clip=clip_origem.name,
                        aaf_in=rotulo(clip_origem.timeline_start_samples),
                        aaf_out=rotulo(clip_origem.timeline_end_samples),
                        als_in=rotulo(clip_destino.timeline_start_samples),
                        als_out=rotulo(clip_destino.timeline_end_samples),
                        delta_samples=delta_start,
                        delta_frames=delta_start / samples_por_frame,
                        delta_end_samples=delta_end,
                        status=status,
                        anchor_source=clip_origem.anchor_source,
                        is_audio=True,
                    )
                )
            else:
                status = (
                    "ok"
                    if (abs(delta_start) <= tolerance and abs(delta_end) <= tolerance)
                    else "out of tolerance"
                )
                if clip_origem.anchor_source != "VIDEO_ANCHOR_FROM_AAF":
                    status = "presumed"
                resultado.rows.append(
                    VerifyRow(
                        track=track_name,
                        clip=clip_origem.name,
                        aaf_in=rotulo(clip_origem.timeline_start_samples),
                        aaf_out=rotulo(clip_origem.timeline_end_samples),
                        als_in=rotulo(clip_destino.timeline_start_samples),
                        als_out=rotulo(clip_destino.timeline_end_samples),
                        delta_samples=delta_start,
                        delta_frames=delta_start / samples_por_frame,
                        delta_end_samples=delta_end if status != "presumed" else None,
                        status=status,
                        anchor_source=clip_origem.anchor_source,
                        is_audio=False,
                    )
                )

        elif clip_origem is not None and clip_destino is None:
            track_origem = tabela_origem.get(track_idx)
            is_vid = getattr(track_origem, "is_video", False) if track_origem else False
            is_audio = not is_vid
            track_name = track_origem.name if track_origem else f"Track {track_idx + 1}"
            if is_audio:
                resultado.audio_missing += 1
                status_val = "missing in the ALS"
            else:
                status_val = (
                    "presumed"
                    if clip_origem.anchor_source != "VIDEO_ANCHOR_FROM_AAF"
                    else "missing in the ALS"
                )

            resultado.rows.append(
                VerifyRow(
                    track=track_name,
                    clip=clip_origem.name,
                    aaf_in=rotulo(clip_origem.timeline_start_samples),
                    aaf_out=rotulo(clip_origem.timeline_end_samples),
                    als_in="-",
                    als_out="-",
                    delta_samples=clip_origem.duration_samples,
                    delta_frames=clip_origem.duration_samples / samples_por_frame,
                    delta_end_samples=None,
                    status=status_val,
                    anchor_source=clip_origem.anchor_source,
                    is_audio=is_audio,
                )
            )

        elif clip_origem is None and clip_destino is not None:
            track_dest = tabela_destino.get(track_idx) or next(
                (t for t in destino.tracks if t.id == clip_destino.track_id), None
            )
            is_vid = getattr(track_dest, "is_video", False) if track_dest else False
            is_audio = not is_vid
            track_name = track_dest.name if track_dest else f"Track {track_idx + 1}"
            if is_audio:
                resultado.audio_extra += 1

            resultado.rows.append(
                VerifyRow(
                    track=track_name,
                    clip=clip_destino.name,
                    aaf_in="-",
                    aaf_out="-",
                    als_in=rotulo(clip_destino.timeline_start_samples),
                    als_out=rotulo(clip_destino.timeline_end_samples),
                    delta_samples=clip_destino.duration_samples,
                    delta_frames=clip_destino.duration_samples / samples_por_frame,
                    delta_end_samples=None,
                    status="extra in the ALS",
                    is_audio=is_audio,
                )
            )

    # A cobertura sai depois da comparacao: evento comparado e evento que
    # existe no .als, nao slot mapeado. Slot mapeado e evidencia separada.
    resultado.coverage_msg = formatar_cobertura(
        resultado, mapeamento_de_slots(origem, source_indices)
    )

    return resultado
