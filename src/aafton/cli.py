"""Linha de comando do aafton."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .aaf_read import read_aaf
from .als_read import read_session
from .bundled import default_template
from .formats import ADAPTADORES
from .formats.destino import write_destination
from .formats.origem import read_origin
from .ir import SessionIR
from .ltc import RATES_LTC, parse_ltc_rate
from .pipeline import PRE_ROLL_SEGUNDOS, ImportOptions, import_session
from .pipeline import resolve_origin as _resolve_origin
from .report import Report, StrictModeError, erros_no_stderr
from .timecode import TimecodeRate, samples_to_label
from .verify import verify_conversion
from .cli_ltc import add_ltc_parser
from . import __version__
from .cli_parsers import (
    add_app_parser,
    add_audit_audio_parser,
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
    options = ImportOptions(
        input=args.input,
        output=args.output,
        template=args.template or default_template(),
        live_origin=args.live_origin,
        bpm=args.bpm,
        media=args.media,
        stereo=args.stereo,
        fades=args.fades,
        groups=args.groups,
        audio_dir=args.audio_dir,
        video=args.video,
        video_start=args.video_start,
        video_no_burn=args.video_no_burn,
        session_text=args.session_text,
        permissive=args.permissive,
        ltc=args.ltc,
        edl_rate=_taxa_edl(args),
    )

    import_session(options, report)

    _write_report(report, args.report)
    print(report.to_markdown())
    return 1 if report.incomplete else 0


def cmd_export(args: argparse.Namespace) -> int:
    report = Report(strict=not args.permissive)
    session = read_session(args.input, report)
    write_destination(session, args.output, report, media=args.media)
    _write_report(report, args.report)
    print(report.to_markdown())
    return 1 if report.incomplete else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    report = Report(strict=False)
    caminho = Path(args.input)
    sufixo = caminho.suffix.lower()
    if sufixo == ".aaf":
        session = read_aaf(caminho, report)
    elif sufixo == ".als":
        session = read_session(caminho, report)
    elif sufixo in ADAPTADORES:
        session = read_origin(caminho, report, edl_rate=_taxa_edl(args))
    else:
        print(f"unrecognized extension: {caminho.suffix}", file=sys.stderr)
        return 2
    print(json.dumps(_session_to_dict(session), ensure_ascii=False, indent=2))
    return erros_no_stderr(report) if sufixo in ADAPTADORES else 0


def cmd_verify(args: argparse.Namespace) -> int:
    resultado = verify_conversion(
        args.aaf, args.als, override_tolerance_samples=args.tolerance_samples
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
            media_dir=args.media_dir,
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
    add_audit_audio_parser(sub, cmd_audit_audio)
    add_turnover_parser(sub, cmd_turnover)

    add_ltc_parser(sub)

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
