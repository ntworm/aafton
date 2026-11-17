"""Import and export pipeline of the aafton, shared by the CLI and the app."""

from __future__ import annotations

import wave
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

from .aaf_read import check_frame_alignment, check_pulldown
from .als_ancora import StartSpec
from .als_read import read_session
from .als_render import filtrar_render_issue, recusar_se_parado
from .als_write import WriteOptions, write_session
from .bundled import default_template
from .expandir import expand_production_channels
from .formats.destino import write_destination
from .formats.origem import conferir_canais, read_origin
from .ir import MediaAsset, SessionIR
from .ir_trim import trim_to_media_end
from .ltc_track import add_ltc_track
from .media_extract import AmbiguousMediaError, read_audio_metadata, resolve_asset
from .organize_relatorio import relatar_compactacao as _relatar_compactacao
from .report import Report
from .som_direto import is_production_input, read_production
from .som_direto_tracks import dividir_em_monos
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
    organize: str = "off"
    certainty: str = "permissive"
    organize_signals: tuple[str, ...] = ("file", "meta", "name", "family", "color", "compact")
    organize_edits: dict | None = None


# Recusas da expansao de canais pelo som direto.
EXPANSION_LINK_REFUSED = (
    "--media link is refused with --production-audio: "
    "production audio is always split into mono files"
)
EXPANSION_INPUT_REFUSED = (
    "--production-audio is refused for a production audio input: "
    "it adds recorder inputs to an AAF, EDL or XML import"
)


# Recusa do set gravado dentro da pasta do gravador: os monos seriam lidos
# como takes no proximo import e no verify. O app usa a mesma conferencia.
SET_INSIDE_RECORDER = "The Live set cannot go inside the recorder folder."

# --live-origin no som direto: cada bloco tem o proprio inicio, a opcao e ignorada.
LIVE_ORIGIN_IGNORED = "each block of the set has its own start; --live-origin ignored"

# Opcoes do import que nao valem no som direto: o set em blocos nao tem um
# timecode continuo, entao cada uma e recusada com o nome dela.
OPCOES_RECUSADAS_NO_SOM_DIRETO = (
    ("ltc", "--ltc", "the set has no continuous timecode for an LTC track"),
    ("video", "--video", "the set has no continuous timecode to anchor a video"),
    ("video_start", "--video-start", "the set has no continuous timecode to anchor a video"),
    ("session_text", "--session-text", "a session text belongs to an AAF session"),
)


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


def _check_production_options(opts: ImportOptions, report: Report) -> None:
    """Refuses the import options that do not apply to production audio."""
    if opts.media == "link":
        raise ValueError(
            "--media link is refused: production audio is always split into mono files"
        )
    for campo, opcao, motivo in OPCOES_RECUSADAS_NO_SOM_DIRETO:
        if getattr(opts, campo):
            raise ValueError(f"{opcao} is refused for production audio: {motivo}")
    if opts.groups != "none":
        report.info("PRODUCTION_GROUPS_IGNORED", "--groups is ignored for production audio")
    if opts.live_origin:
        report.info("PRODUCTION_LIVE_ORIGIN_IGNORED", LIVE_ORIGIN_IGNORED)


def check_production_output(input_path, output) -> None:
    """Refuses a Live set whose media folder lies inside the recorder folder.

    Para um WAV solto, a pasta do gravador e a pasta do proprio WAV (a diaria):
    os monos do set ali seriam lidos como takes na proxima leitura da pasta.
    """
    fonte = Path(input_path)
    raiz = fonte if fonte.is_dir() else fonte.parent
    if media_dir_for(str(output)).resolve().is_relative_to(raiz.resolve()):
        raise ValueError(SET_INSIDE_RECORDER)


def import_production(
    opts: ImportOptions, report: Report, *, circled_only: bool = False
) -> SessionIR:
    """Production audio: the folder or WAV becomes a set of mono tracks, days in blocks.

    ``circled_only`` leaves out the takes that are not circled.
    """
    check_production_output(opts.input, opts.output)
    _check_production_options(opts, report)
    session = read_production(opts.input, report, circled_only=circled_only)
    dividir_em_monos(session, media_dir_for(opts.output), report)
    write_session(
        session,
        opts.template or default_template(),
        opts.output,
        WriteOptions(
            sample_rate=session.sample_rate,
            bpm=opts.bpm,
            live_origin_samples=0,
            permissive=opts.permissive,
            fades=opts.fades,
            stereo=opts.stereo,
            groups="none",
        ),
        report,
    )
    return session


def import_session(
    opts: ImportOptions,
    report: Report,
    *,
    circled_only: bool = False,
    production_audio: str | None = None,
) -> SessionIR:
    """Runs the whole import: read, media, text, video and the Live set.

    A folder or a .wav is production audio; ``circled_only`` applies only there.
    ``production_audio`` is the recorder folder of an AAF, EDL or XML import:
    the inputs the editor left out come in as sibling tracks.
    """
    if is_production_input(opts.input):
        if production_audio:
            raise ValueError(EXPANSION_INPUT_REFUSED)
        return import_production(opts, report, circled_only=circled_only)
    _check_expansion_options(opts, production_audio)
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
    # Corta no proprio SessionIR lido o clip que passa menos de um frame do
    # fim da midia; a mudanca no lugar e aceita, o resto do import ve o corte.
    trim_to_media_end(session, report)
    _process_session(session, opts, report, stereo_dir, production_audio)

    check_pulldown(session, report)

    origem = resolve_origin(opts.live_origin, session, report)
    if opts.ltc:
        add_ltc_track(session, origem, stereo_dir, report)
    _write_live_set(session, opts, origem, report)
    return session


def _excluded_for_organize(session: SessionIR, production_audio: str | None) -> set[int]:
    """Descobre quais tracks do editor recebem irmas na expansao do som direto."""
    if not production_audio:
        return set()
    from .expandir import planejar

    plano = planejar(session, production_audio)
    return {take.dona.source_index for take in plano.takes.values() if take.faltando}


def _process_session(
    session: SessionIR,
    opts: ImportOptions,
    report: Report,
    stereo_dir: Path,
    production_audio: str | None,
) -> None:
    """Session text, frame alignment, stereo pairing, channel expansion, organize and video."""
    from .organize_apply import annotate_origin_keys, apply_proposal
    from .organize_plan import build_proposal
    from .session_text import process_session_text
    from .stereo import process_stereo_pairing
    from .video import process_video

    process_session_text(session, Path(opts.input), opts.session_text, report)
    check_frame_alignment(session, report)
    proposal = None
    if opts.organize == "type":
        annotate_origin_keys(session)
        excluded = _excluded_for_organize(session, production_audio)
        proposal = build_proposal(
            session,
            stereo=opts.stereo,
            level=opts.certainty,
            signals=opts.organize_signals,
            edits=opts.organize_edits,
            excluded_source_indices=excluded,
        )
        _relatar_compactacao(proposal, report)
    process_stereo_pairing(session, opts.stereo, report, stereo_dir)
    if production_audio:
        expand_production_channels(
            session,
            production_audio,
            stereo_dir,
            report,
            groups=opts.groups,
            editor=opts.input,
        )
    if proposal is not None:
        use_colors = "color" in opts.organize_signals
        for unidade in apply_proposal(session, proposal, colors=use_colors, report=report):
            report.warning(
                "ORGANIZE_UNIT_NOT_MOVED",
                f"clip {unidade.name!r} stays on the editor's track: its stereo pair "
                "did not become one clip",
                {"track": unidade.source_label, "destination": unidade.destination_track_name},
            )
    burn = bool(opts.video) and not opts.video_no_burn
    process_video(
        session,
        Path(opts.input),
        opts.video,
        opts.video_start,
        report,
        burn_timecode=burn,
    )


def _check_expansion_options(opts: ImportOptions, production_audio: str | None) -> None:
    """Refuses the options that do not go with --production-audio."""
    if not production_audio:
        return
    if opts.media == "link":
        raise ValueError(EXPANSION_LINK_REFUSED)
    if not Path(production_audio).is_dir():
        raise ValueError(f"the production audio folder {production_audio} does not exist")
    check_production_output(production_audio, opts.output)


def _write_live_set(session: SessionIR, opts: ImportOptions, origem: int, report: Report) -> None:
    """Writes the Live set of an AAF, EDL or XML import."""
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


def _issue_da_track_removida(issue, escolhidos: set[str] | None, nomes_removidas: set[str]) -> bool:
    """Diz se a issue pertence a uma faixa excluida pela selecao."""
    contexto = issue.context
    if "kind" in contexto:
        return False
    track_id = contexto.get("track_id")
    if track_id is not None and escolhidos is not None:
        return track_id not in escolhidos
    return "track" in contexto and contexto["track"] in nomes_removidas


def _mensagens_das_tracks_removidas(
    reading: Report, escolhidos: set[str], nomes: set[str]
) -> list[str]:
    """Mensagens das issues de suporte que pertencem as tracks removidas."""
    return [
        issue.message
        for issue in reading.issues
        if _issue_da_track_removida(issue, escolhidos, nomes)
    ]


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
    for mensagem in _mensagens_das_tracks_removidas(reading, escolhidos, nomes_removidas):
        if mensagem in session.unsupported_export_features:
            session.unsupported_export_features.remove(mensagem)
    return nomes_removidas


def replay_issues(
    reading: Report,
    report: Report,
    skip_tracks: set[str],
    keep: Collection[int] | None = None,
) -> None:
    """Replays the issues of the kept tracks into the report of the export.

    The issues of the removed tracks are left out. In a strict report the
    first error raises, exactly as a direct read would.
    """
    escolhidos = None if keep is None else {f"t{i}" for i in keep}
    for issue in reading.issues:
        if _issue_da_track_removida(issue, escolhidos, skip_tracks):
            continue
        if issue.code == "ALS_RENDER_IN_LIVE" and escolhidos is not None:
            filtrada = filtrar_render_issue(issue, escolhidos)
            if filtrada is None:
                continue
            mensagem, contexto = filtrada
            report.add(issue.severity, issue.code, mensagem, contexto)
            continue
        report.add(issue.severity, issue.code, issue.message, issue.context)


def export_session(
    input_path: str | Path,
    output_path: str | Path,
    report: Report,
    *,
    media: str = "link",
    tracks: Collection[int] | None = None,
    start: StartSpec | None = None,
    leave_out_render: bool = False,
) -> SessionIR:
    """Reads the set and writes it to the destination, keeping the selection.

    Without a selection the set is read into ``report`` itself. With one the
    set is read into a report that does not raise, the issues are replayed
    into ``report`` and only then the destination is written: a strict report
    stops at the first error before anything is written. An empty selection
    is an error: nothing is read and nothing is written. A selection that
    names no track of the set is an error too: no empty file is written.

    ``start`` anchors a set without the aafton sidecar and ``leave_out_render``
    leaves out its clips that need a render in Live (see ``read_session``).
    The list of those clips stops the export even in a permissive report.
    """
    opcoes: dict = {}
    if start is not None:
        opcoes["start"] = start
    if leave_out_render:
        opcoes["leave_out_render"] = True
    if tracks is None:
        session = read_session(input_path, report, **opcoes)
    else:
        escolhidos = list(tracks)
        if not escolhidos:
            raise NoTracksError("the export has no track selected")
        reading = Report(strict=False)
        session = read_session(input_path, reading, **opcoes)
        removidas = select_tracks(session, escolhidos, reading)
        if not session.tracks:
            raise NoTracksError("the export has no track selected")
        replay_issues(reading, report, removidas, escolhidos)
    recusar_se_parado(report)
    write_destination(session, output_path, report, media=media)
    return session
