"""CLI subcommands for importing and exporting locators / cue sheets."""

from __future__ import annotations

import argparse
import sys

from .cue_sheet import export_cue_sheet
from .locators_write import import_locators
from .timecode import TimecodeRate


def _parse_frame_rate(texto: str) -> TimecodeRate:
    from .cli import _taxa_de_frames

    return _taxa_de_frames(texto)


def _parse_columns(col_args: list[str] | None) -> dict[str, str] | None:
    if not col_args:
        return None
    cols: dict[str, str] = {}
    for item in col_args:
        if "=" in item:
            k, v = item.split("=", 1)
            cols[k.strip()] = v.strip()
    return cols


def _print_report(report, as_json: bool) -> None:
    """Relatorio em UTF-8 no stdout, mesmo redirecionado num console cp1252."""
    texto = (report.to_json() if as_json else report.to_markdown()) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(texto)
        return
    sys.stdout.flush()
    buffer.write(texto.encode("utf-8"))
    buffer.flush()


def _locators_help(parser: argparse.ArgumentParser) -> int:
    parser.print_help(sys.stderr)
    return 2


def add_locators_parser(sub: argparse._SubParsersAction) -> None:
    """Register locators subcommand with import and export."""
    p_loc = sub.add_parser("locators", help="import or export locators / cue sheets")
    sub_loc = p_loc.add_subparsers(dest="locators_command")
    p_loc.set_defaults(func=lambda _args: _locators_help(p_loc))

    p_imp = sub_loc.add_parser("import", help="import locators or ADR cues from CSV to Live set")
    p_imp.add_argument("set", help="source Live set (.als)")
    p_imp.add_argument("csv", help="source markers/ADR CSV file")
    p_imp.add_argument("--output", "-o", default=None, help="path for the new Live set")
    p_imp.add_argument(
        "--kind",
        choices=["auto", "markers", "adr"],
        default="auto",
        help="type of list: auto (default), markers, or adr",
    )
    p_imp.add_argument(
        "--column",
        action="append",
        metavar="FIELD=HEADER",
        help="override header mapping, e.g. --column tc_in=MeuTC",
    )
    p_imp.add_argument("--offset", default=None, help="timecode offset, e.g. -00:59:00:00")
    p_imp.add_argument("--cue-prefix", default="ADR", help="prefix for ADR cues (default: ADR)")
    p_imp.add_argument("--start-tc", default=None, help="start timecode for set without sidecar")
    p_imp.add_argument(
        "--frame-rate",
        type=_parse_frame_rate,
        default=None,
        help="frame rate for set without sidecar",
    )
    p_imp.add_argument(
        "--permissive",
        action="store_true",
        help="skip rows with errors and write the others",
    )
    p_imp.add_argument("--json", action="store_true", help="output report as JSON on stdout")
    p_imp.set_defaults(func=cmd_locators_import)

    p_exp = sub_loc.add_parser("export", help="export locators from Live set to cue sheet CSV")
    p_exp.add_argument("set", help="source Live set (.als)")
    p_exp.add_argument("--output", "-o", required=True, help="path for output CSV file")
    p_exp.add_argument(
        "--only-cues",
        action="store_true",
        help="export only ADR cues, omitting general markers",
    )
    p_exp.add_argument("--start-tc", default=None, help="start timecode for set without sidecar")
    p_exp.add_argument(
        "--frame-rate",
        type=_parse_frame_rate,
        default=None,
        help="frame rate for set without sidecar",
    )
    p_exp.add_argument("--json", action="store_true", help="output report as JSON on stdout")
    p_exp.set_defaults(func=cmd_locators_export)


def cmd_locators_import(args: argparse.Namespace) -> int:
    """Execute locators import."""
    columns = _parse_columns(getattr(args, "column", None))
    res = import_locators(
        set_path=args.set,
        csv_path=args.csv,
        output=getattr(args, "output", None),
        kind=getattr(args, "kind", "auto"),
        columns=columns,
        offset=getattr(args, "offset", None),
        cue_prefix=getattr(args, "cue_prefix", "ADR"),
        start_tc=getattr(args, "start_tc", None),
        frame_rate=getattr(args, "frame_rate", None),
        strict=not getattr(args, "permissive", False),
    )
    _print_report(res.report, getattr(args, "json", False))
    if not res.written or res.report.incomplete or bool(res.skipped):
        return 1
    return 0


def cmd_locators_export(args: argparse.Namespace) -> int:
    """Execute locators export."""
    res = export_cue_sheet(
        set_path=args.set,
        output=args.output,
        only_cues=getattr(args, "only_cues", False),
        start_tc=getattr(args, "start_tc", None),
        frame_rate=getattr(args, "frame_rate", None),
    )
    _print_report(res.report, getattr(args, "json", False))
    if not res.written or res.report.incomplete:
        return 1
    return 0
