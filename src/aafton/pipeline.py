"""Import and export pipeline of the aafton, shared by the CLI and the app."""

from __future__ import annotations

import wave
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .aaf_read import check_frame_alignment, check_pulldown
from .als_read import read_session
from .als_write import WriteOptions, write_session
from .bundled import default_template
from .formats.destino import write_destination
from .formats.origem import conferir_canais, read_origin
from .ir import MediaAsset, SessionIR
from .ltc_track import add_ltc_track
from .media_extract import AmbiguousMediaError, read_audio_metadata, resolve_asset
from .report import Report
from .timecode import TimecodeRate, label_to_samples, samples_to_label

PRE_ROLL_SEGUNDOS = 10


class NoTracksError(ValueError):
    """The selection named no track that exists in the set."""


@dataclass(kw_only=True)
class ImportOptions:
    """Options of one import, mirroring the arguments of the import command."""

    input: str
    output: str
    template: str | None = None
    live_origin: str | None = None
    bpm: float = 60.0
    media: str = "extract"
    stereo: str = "pair"
    fades: str = "absorb"
    groups: str = "none"
    audio_dir: str | None = None
    video: str | None = None
    video_start: str | None = None
    video_no_burn: bool = False
    session_text: str | None = None
    permissive: bool = False
    ltc: bool = False
    edl_rate: TimecodeRate | None = None


def resolve_origin(valor: str | None, session: SessionIR, report: Report) -> int:
    """Absolute position that lands on the beat zero of the Live set.

    Without an explicit value the origin is the start of the first clip minus
    ten seconds of room, never below zero.
    """
    if valor is not None:
        return int(label_to_samples(valor, session.timecode_rate, session.sample_rate))

    inicios = [c.timeline_start_samples for c in session.all_clips()]
    inicios.extend([m.position_samples for m in session.markers])

    if not inicios:
        return session.start_timecode_samples

    origem = min(inicios) - PRE_ROLL_SEGUNDOS * session.sample_rate
    report.info(
        "ANCHOR_AUTO",
        f"origin chosen automatically at "
        f"{samples_to_label(origem, session.timecode_rate, session.sample_rate)}, "
        f"ensuring {PRE_ROLL_SEGUNDOS}s of pre-roll before the first clip",
        {"origin_samples": origem},
    )
    return origem


def media_roots(input_path: str, audio_dir: str | None) -> list[Path]:
    """Folders where the media is searched: the audio folder and the file folder."""
    raizes = [Path(input_path).parent]
    if audio_dir:
        raizes.insert(0, Path(audio_dir))
    return raizes


def media_dir_for(output: str) -> Path:
    """Folder of the imported media, inside the project folder of the Live set."""
    output_path = Path(output).resolve()
    parent = output_path.parent
    if parent.name.endswith("Project"):
        project_dir = parent
    else:
        project_dir = parent / f"{output_path.stem} Project"
    return (project_dir / "Samples" / "Imported").resolve()


def _preencher_metadados_extraidos(asset: MediaAsset, report: Report) -> None:
    """Fills channels and metadata of the already extracted WAV when they are missing."""
    if not asset.resolved_path:
        return
    if (
        asset.channels is not None
        and asset.sample_rate is not None
        and asset.duration_samples is not None
    ):
        return
    caminho = Path(asset.resolved_path)
    try:
        meta = read_audio_metadata(caminho)
    except (wave.Error, OSError, ValueError) as exc:
        report.warning(
            "MEDIA_METADATA_UNREADABLE",
            f"could not read the metadata of {caminho.name}: {exc}",
            {"path": str(caminho)},
        )
        return
    asset.channels = meta["channels"]
    asset.sample_rate = meta["sample_rate"]
    asset.duration_samples = meta["duration_samples"]


def resolve_media(opts: ImportOptions, session: SessionIR, report: Report) -> None:
    """Looks on disk for the media that the embedded extraction did not resolve."""
    raizes = media_roots(opts.input, opts.audio_dir)
    mxf_dir = media_dir_for(opts.output)
    for asset in session.media:
        if asset.status == "resolved":
            # Already resolved by the embedded essence extraction; no disk search.
            _preencher_metadados_extraidos(asset, report)
            continue
        try:
            resolve_asset(asset, raizes, report, mxf_dir=mxf_dir, taxa_tc=session.timecode_rate)
        except AmbiguousMediaError as exc:
            report.error("MEDIA_AMBIGUOUS", str(exc), {"filename": asset.filename})
        except ValueError as exc:
            report.error("MEDIA_FORMAT_UNSUPPORTED", str(exc), {"filename": asset.filename})
        except Exception as exc:  # noqa: BLE001
            report.error("MEDIA_RESOLVE_ERROR", str(exc), {"filename": asset.filename})
    # Now that the media is resolved, the channels of the file are known.
    conferir_canais(session, report)


def import_session(opts: ImportOptions, report: Report) -> SessionIR:
    """Runs the whole import: read, media, text, video and the Live set."""
    stereo_dir = media_dir_for(opts.output)
    destino_midia = stereo_dir if opts.media == "extract" else None
    session = read_origin(
        opts.input,
        report,
        extract_dir=destino_midia,
        fades=opts.fades,
        edl_rate=opts.edl_rate,
        media_roots=media_roots(opts.input, opts.audio_dir),
        mxf_dir=stereo_dir,
    )

    resolve_media(opts, session, report)

    from .session_text import process_session_text
    from .stereo import process_stereo_pairing
    from .video import process_video

    process_session_text(session, Path(opts.input), opts.session_text, report)
    check_frame_alignment(session, report)
    process_stereo_pairing(session, opts.stereo, report, stereo_dir)
    burn = bool(opts.video) and not opts.video_no_burn
    process_video(
        session,
        Path(opts.input),
        opts.video,
        opts.video_start,
        report,
        burn_timecode=burn,
    )

    check_pulldown(session, report)

    origem = resolve_origin(opts.live_origin, session, report)
    if opts.ltc:
        add_ltc_track(session, origem, stereo_dir, report)
    write_session(
        session,
        opts.template or default_template(),
        opts.output,
        WriteOptions(
            sample_rate=session.sample_rate,
            bpm=opts.bpm,
            live_origin_samples=origem,
            permissive=opts.permissive,
            fades=opts.fades,
            stereo=opts.stereo,
            groups=opts.groups,
        ),
        report,
    )
    return session


def _mensagens_das_tracks_removidas(reading: Report, nomes: set[str]) -> set[str]:
    """Messages of the issues that belong to the given removed tracks.

    Only the issues that name the track are taken: an issue of a group, of a
    return or of the main track carries the key ``tipo`` and is left alone.
    """
    mensagens = set()
    for issue in reading.issues:
        contexto = issue.context
        if "track" not in contexto or "kind" in contexto:
            continue
        if contexto["track"] not in nomes:
            continue
        mensagens.add(issue.message)
    return mensagens


def select_tracks(session: SessionIR, keep: Collection[int], reading: Report) -> set[str]:
    """Keeps only the chosen tracks and prunes what they no longer use.

    ``keep`` holds the positions of the audio tracks that stay: a track stays
    when its id is ``t{i}`` for an ``i`` in ``keep``. The media that no kept
    clip uses is dropped, and so is every feature string that is the message
    of an issue of the removed tracks. Returns the names of the removed
    tracks that share no name with a track that stays.
    """
    escolhidos = {f"t{i}" for i in keep}
    mantidas = [track for track in session.tracks if track.id in escolhidos]
    removidas = [track for track in session.tracks if track.id not in escolhidos]
    session.tracks = mantidas

    midias_usadas = {clip.media_id for track in mantidas for clip in track.clips if clip.media_id}
    session.media = [asset for asset in session.media if asset.id in midias_usadas]

    nomes_mantidas = {track.name for track in mantidas}
    nomes_removidas = {track.name for track in removidas} - nomes_mantidas
    mensagens = _mensagens_das_tracks_removidas(reading, nomes_removidas)
    session.unsupported_export_features = [
        feature for feature in session.unsupported_export_features if feature not in mensagens
    ]
    return nomes_removidas


def replay_issues(reading: Report, report: Report, skip_tracks: set[str]) -> None:
    """Replays the issues of the kept tracks into the report of the export.

    The issues of the removed tracks are left out. In a strict report the
    first error raises, exactly as a direct read would.
    """
    for issue in reading.issues:
        if issue.context.get("track") in skip_tracks:
            continue
        report.add(issue.severity, issue.code, issue.message, issue.context)


def export_session(
    input_path: str | Path,
    output_path: str | Path,
    report: Report,
    *,
    media: str = "link",
    tracks: Collection[int] | None = None,
) -> SessionIR:
    """Reads the set and writes it to the destination, keeping the selection.

    Without a selection the set is read into ``report`` itself. With one the
    set is read into a report that does not raise, the issues are replayed
    into ``report`` and only then the destination is written: a strict report
    stops at the first error before anything is written. An empty selection
    is an error: nothing is read and nothing is written. A selection that
    names no track of the set is an error too: no empty file is written.
    """
    if tracks is None:
        session = read_session(input_path, report)
    else:
        escolhidos = list(tracks)
        if not escolhidos:
            raise NoTracksError("the export has no track selected")
        reading = Report(strict=False)
        session = read_session(input_path, reading)
        removidas = select_tracks(session, escolhidos, reading)
        if not session.tracks:
            raise NoTracksError("the export has no track selected")
        replay_issues(reading, report, removidas)
    write_destination(session, output_path, report, media=media)
    return session
