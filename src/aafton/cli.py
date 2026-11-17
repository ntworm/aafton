"""Linha de comando do aafton."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .aaf_read import read_aaf
from .als_ancora import start_da_linha
from .als_read import read_session
from .als_render import recusar_se_parado
from .bundled import default_template
from .formats import ADAPTADORES
from .formats.destino import write_destination
from .formats.origem import read_origin
from .ir import SessionIR
from .ltc import RATES_LTC, parse_ltc_rate
from .expandir import planejar, resolve_for_inspect, resumo
from .pipeline import EXPANSION_INPUT_REFUSED, PRE_ROLL_SEGUNDOS, ImportOptions, import_session
from .pipeline import media_roots
from .pipeline import resolve_origin as _resolve_origin
from .report import Report, StrictModeError, erros_no_stderr
from .som_direto import is_production_input, read_production
from .som_direto_tracks import com_os_dois_tempos
from .som_direto_verify import verify_production
from .timecode import TimecodeRate, samples_to_label
from .verify import verify_conversion
from .cli_organize import OrganizeArgsError, inspect_organize, resolve_organize
from .cli_locators import add_locators_parser
from .cli_reconform import add_reconform_parser
from .cli_ltc import add_ltc_parser
from . import __version__
from .cli_parsers import (
    add_app_parser,
    add_audit_audio_parser,
    add_collect_parser,
    add_export_parser,
    add_import_parser,
    add_inspect_parser,
    add_turnover_parser,
    add_ui_parsers,
    add_verify_parser,
)

__all__ = [
    "PRE_ROLL_SEGUNDOS",
    "_resolve_origin",
    "_session_to_dict",
    "build_parser",
    "cmd_app",
    "cmd_audit_audio",
    "cmd_collect",
    "cmd_export",
    "cmd_import",
    "cmd_inspect",
    "cmd_turnover",
    "cmd_ui",
    "cmd_verify",
    "main",
    "read_session",
    "run_cli",
    "write_destination",
]


def _session_to_dict(session: SessionIR) -> dict:
    def rotulo(samples: int) -> str:
        return samples_to_label(samples, session.timecode_rate, session.sample_rate)

    max_end_samples = session.start_timecode_samples
    total_clips = 0
    for track in session.tracks:
        for clip in track.clips:
            total_clips += 1
            if clip.timeline_end_samples > max_end_samples:
                max_end_samples = clip.timeline_end_samples

    duration_samples = max(0, max_end_samples - session.start_timecode_samples)
    duration_sec = round(duration_samples / session.sample_rate, 3) if session.sample_rate else 0.0

    return {
        "name": session.name,
        "source_path": session.source_path,
        "sample_rate": session.sample_rate,
        "timecode_rate": asdict(session.timecode_rate)
        if hasattr(session.timecode_rate, "__dataclass_fields__")
        else str(session.timecode_rate),
        "start_timecode": rotulo(session.start_timecode_samples),
        "start_timecode_samples": session.start_timecode_samples,
        "end_timecode": rotulo(max_end_samples),
        "end_timecode_samples": max_end_samples,
        "duration_timecode": rotulo(duration_samples),
        "duration_seconds": duration_sec,
        "total_tracks": len(session.tracks),
        "total_clips": total_clips,
        "total_media": len(session.media),
        "embedded_media_count": sum(1 for m in session.media if m.embedded_mob_id is not None),
        "resolved_media_count": sum(1 for m in session.media if m.status == "resolved"),
        "media": [asdict(asset) for asset in session.media],
        "markers": [
            {**asdict(marker), "timecode": rotulo(marker.position_samples)}
            for marker in session.markers
        ],
        "tracks": [
            {
                "name": track.name,
                "kind": track.kind,
                "channel_count": track.channel_count,
                "clips_count": len(track.clips),
                "clips": [
                    {
                        "name": clip.name,
                        "media_id": clip.media_id,
                        "start_samples": clip.timeline_start_samples,
                        "end_samples": clip.timeline_end_samples,
                        "duration_samples": clip.duration_samples,
                        "source_offset_samples": clip.source_offset_samples,
                        "gain_linear": clip.gain_linear,
                        "tc_in": rotulo(clip.timeline_start_samples),
                        "tc_out": rotulo(clip.timeline_end_samples),
                    }
                    for clip in track.clips
                ],
            }
            for track in session.tracks
        ],
    }


def _write_report(report: Report, destination: str | None) -> None:
    if destination:
        Path(destination).write_text(report.to_json(), encoding="utf-8")


def _taxa_edl(args: argparse.Namespace) -> TimecodeRate | None:
    """Taxa de frames da EDL ja lida pelo argparse; None quando nao foi dada."""
    return getattr(args, "edl_rate", None)


def _edl_taxa(texto: str) -> TimecodeRate:
    """Taxa de frames da EDL vinda da linha de comando."""
    try:
        return parse_ltc_rate(texto)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{exc}; use {', '.join(RATES_LTC)}") from None


def cmd_import(args: argparse.Namespace) -> int:
    report = Report(strict=not args.permissive)
    try:
        organizacao = resolve_organize(args)
    except OrganizeArgsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    options = ImportOptions(
        input=args.input,
        output=args.output,
        template=args.template or default_template(),
        live_origin=args.live_origin,
        bpm=args.bpm,
        media=args.media,
        fades=args.fades,
        audio_dir=args.audio_dir,
        video=args.video,
        video_start=args.video_start,
        video_no_burn=args.video_no_burn,
        session_text=args.session_text,
        permissive=args.permissive,
        ltc=args.ltc,
        edl_rate=_taxa_edl(args),
        **organizacao,
    )

    import_session(
        options,
        report,
        circled_only=args.circled_only,
        production_audio=getattr(args, "production_audio", None),
    )

    _write_report(report, args.report)
    print(report.to_markdown())
    return 1 if report.incomplete else 0


def _opcoes_do_set(args: argparse.Namespace) -> dict:
    """Inicio e clips de render de um set sem registro do aafton, vindos da linha."""
    return {
        "start": start_da_linha(getattr(args, "start_tc", None), getattr(args, "frame_rate", None)),
        "leave_out_render": bool(getattr(args, "leave_out_render", False)),
    }


def _taxa_de_frames(texto: str) -> TimecodeRate:
    """Taxa de frames de --frame-rate; 29.97df e drop frame."""
    try:
        return parse_ltc_rate(texto)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"unknown frame rate: {texto!r}; use {', '.join(RATES_LTC)}"
        ) from None


def _add_set_options(sub) -> None:
    """--start-tc, --frame-rate e --leave-out-render em export, verify e inspect."""
    for nome in ("export", "verify", "inspect"):
        parser = sub.choices[nome]
        parser.add_argument(
            "--start-tc",
            default=None,
            metavar="TC",
            help="timecode of bar 1 of a set without an aafton record (default 01:00:00:00)",
        )
        parser.add_argument(
            "--frame-rate",
            type=_taxa_de_frames,
            default=None,
            help=f"timecode rate of --start-tc: {', '.join(RATES_LTC)} (default 24)",
        )
        parser.add_argument(
            "--leave-out-render",
            action="store_true",
            help="leaves out the clips that need a render in Live (warp, loop, transpose)",
        )


def cmd_export(args: argparse.Namespace) -> int:
    report = Report(strict=not args.permissive)
    session = read_session(args.input, report, **_opcoes_do_set(args))
    recusar_se_parado(report)
    write_destination(session, args.output, report, media=args.media)
    _write_report(report, args.report)
    print(report.to_markdown())
    return 1 if report.incomplete else 0


def _print_json(dados: dict) -> None:
    """JSON em UTF-8 no stdout, mesmo redirecionado num console que nao e UTF-8.

    No Windows o stdout redirecionado usa a pagina de codigo do sistema, e um
    nome acentuado quebrava em ``charmap``: os bytes UTF-8 vao direto para o
    buffer quando ele existe.
    """
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(texto)
        return
    sys.stdout.flush()
    buffer.write(texto.encode("utf-8"))
    buffer.flush()


def _inspect_expansion(args: argparse.Namespace, caminho: Path, report: Report) -> int:
    """Inspect com --production-audio: por take, o original casado e as entradas acrescentadas."""
    if is_production_input(caminho):
        raise ValueError(EXPANSION_INPUT_REFUSED)
    if not Path(args.production_audio).is_dir():
        raise ValueError(f"the production audio folder {args.production_audio} does not exist")
    session = read_origin(caminho, report, edl_rate=_taxa_edl(args))
    resolve_for_inspect(session, media_roots(str(caminho), args.audio_dir), report)
    dados = _session_to_dict(session)
    dados["production_audio"] = resumo(planejar(session, args.production_audio))
    _print_json(dados)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    report = Report(strict=False)
    caminho = Path(args.input)
    sufixo = caminho.suffix.lower()
    if getattr(args, "production_audio", None):
        return _inspect_expansion(args, caminho, report)
    if getattr(args, "organize", None):
        return inspect_organize(args)
    if is_production_input(caminho):
        session = read_production(caminho, report)
        _print_json(com_os_dois_tempos(session, _session_to_dict(session)))
        return 0
    if sufixo == ".aaf":
        session = read_aaf(caminho, report)
    elif sufixo == ".als":
        session = read_session(caminho, report, allow_production=True, **_opcoes_do_set(args))
    elif sufixo in ADAPTADORES:
        session = read_origin(caminho, report, edl_rate=_taxa_edl(args))
    else:
        print(f"unrecognized extension: {caminho.suffix}", file=sys.stderr)
        return 2
    _print_json(_session_to_dict(session))
    return erros_no_stderr(report) if sufixo in ADAPTADORES else 0


def _inicio_do_verify(args: argparse.Namespace):
    """Inicio vindo da linha para o verify; None quando nada foi passado."""
    start_tc = getattr(args, "start_tc", None)
    frame_rate = getattr(args, "frame_rate", None)
    if start_tc is None and frame_rate is None:
        return None
    return start_da_linha(start_tc, frame_rate)


def cmd_verify(args: argparse.Namespace) -> int:
    if is_production_input(args.aaf):
        resultado = verify_production(args.aaf, args.als)
    else:
        resultado = verify_conversion(
            args.aaf,
            args.als,
            override_tolerance_samples=args.tolerance_samples,
            start=_inicio_do_verify(args),
            leave_out_render=bool(getattr(args, "leave_out_render", False)),
        )
    print(resultado.to_table())
    if args.report:
        Path(args.report).write_text(resultado.to_markdown(), encoding="utf-8")
    return 0 if resultado.ok else 1


def cmd_audit_audio(args: argparse.Namespace) -> int:
    from .audio_audit import audit_audio, json_safe, to_markdown

    result = audit_audio(
        args.reference,
        args.candidate,
        args.sidecar,
        mapping=args.map,
        mask=args.mask,
        tolerance_db=args.tolerance_db,
        max_lag=args.max_lag,
        reference_start=args.reference_start,
        mode=args.mode,
        release_mask=args.release_mask,
    )
    markdown = to_markdown(result)
    if args.report:
        Path(args.report).write_text(markdown, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    print(markdown)
    return 0 if result["ok"] else 1


def cmd_turnover(args: argparse.Namespace) -> int:
    from .turnover import TurnoverError, create_turnover_package

    try:
        res = create_turnover_package(
            session_file=args.session,
            output_dir=args.output,
            session_name=args.name,
            strict=not args.permissive,
        )
        report_text = res.report_path.read_text(encoding="utf-8")
        print(report_text)
        if args.json:
            Path(args.json).write_text(
                json.dumps(res.manifest_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return 0 if res.is_valid else 1
    except TurnoverError as exc:
        print(f"error in the turnover: {exc}", file=sys.stderr)
        return 1


def cmd_collect(args: argparse.Namespace) -> int:
    from .collect import CollectError, run_collect

    seen_files: set[str] = set()

    def _progress(copied: int, total: int, filename: str) -> None:
        if filename not in seen_files:
            seen_files.add(filename)
            print(f"copying {filename} ({copied}/{total} bytes)", file=sys.stderr)

    try:
        res = run_collect(
            args.source,
            args.dest,
            name=args.name,
            include_video=not args.no_video,
            progress=_progress,
        )
    except CollectError as exc:
        print(f"error in the collect: {exc}", file=sys.stderr)
        return 2

    if args.json:
        _print_json(res.report.to_dict())
    else:
        print(res.report.to_markdown())
    return res.exit_code


def cmd_ui(args: argparse.Namespace) -> int:
    from .web_app import run_studio_server

    port = args.port
    print(f"aafton running at http://127.0.0.1:{port} (press Ctrl+C to stop)")
    server = run_studio_server(port=port, open_browser=not args.no_browser)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
    return 0


def cmd_app(args: argparse.Namespace) -> int:
    """Abre o studio em uma janela propria, ou sobe o servidor em modo smoke."""
    from .desktop import run_app

    return run_app(smoke=args.smoke)


def _parser_import(sub) -> None:
    """Subparser do import, com as opcoes do AAF e dos formatos de troca."""
    add_import_parser(sub, cmd_import, _edl_taxa, RATES_LTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aafton", description="AAF <-> Ableton Live")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="shows the aafton version and exits",
    )
    sub = parser.add_subparsers(dest="command")

    add_ui_parsers(sub, cmd_ui)
    add_app_parser(sub, cmd_app)
    _parser_import(sub)
    add_export_parser(sub, cmd_export)
    add_inspect_parser(sub, cmd_inspect, _edl_taxa, RATES_LTC)
    add_verify_parser(sub, cmd_verify)
    _add_set_options(sub)
    add_audit_audio_parser(sub, cmd_audit_audio)
    add_turnover_parser(sub, cmd_turnover)
    add_collect_parser(sub, cmd_collect)

    add_ltc_parser(sub)
    add_locators_parser(sub)
    add_reconform_parser(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2
    try:
        return args.func(args)
    except StrictModeError as exc:
        print(f"conversion stopped in strict mode: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def run_cli(argv: list[str] | None = None) -> int:
    """Entrada do terminal: main() sem traceback cru para o operador."""
    try:
        return main(argv)
    except FileNotFoundError as exc:
        if exc.filename:
            print(f"error: file not found: {exc.filename}", file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\noperation cancelled by the user.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(run_cli())
