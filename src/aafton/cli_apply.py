"""Subcommand apply of the aafton command line, and the inspect of an AAF with its media."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from .aaf_apply import apply_edits
from .aaf_inspect_media import embedded_media_ids, errors_by_stage, read_with_media
from .aaf_read import read_aaf
from .report import Report


def cmd_apply(args: argparse.Namespace) -> int:
    """Writes the edits of the JSON into a copy of the AAF."""
    report = Report(strict=False)
    try:
        edicoes = json.loads(Path(args.edits).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        edicoes = None
        report.error("APPLY_BAD_EDITS", f"the edits could not be read: {exc}")
    if edicoes is None:
        resultado = {"ok": False, "output": args.output, "mode": None, "clips_changed": 0}
        resultado["aux_media_dir"] = None
        resultado["warnings"] = []
        resultado["errors"] = [{"code": i.code, "message": i.message} for i in report.issues]
    else:
        resultado = apply_edits(args.input, edicoes, args.output, report)
    from .cli import _print_json  # the same UTF-8 writer of the inspect

    _print_json(resultado)
    if not resultado["ok"]:
        print(resultado["errors"][0]["message"], file=sys.stderr)
        return 1
    return 0


def add_apply_parser(sub: argparse._SubParsersAction) -> None:
    """Registers the apply subcommand in the main parser."""
    parser = sub.add_parser(
        "apply", help="writes into a copy of an AAF the slips and splits of a JSON of edits"
    )
    parser.add_argument("input", help="AAF to read")
    parser.add_argument("edits", help="JSON with the final pieces of each changed clip")
    parser.add_argument("output", help="AAF to write; never the input")
    parser.set_defaults(func=cmd_apply)


def inspect_aaf(
    args: argparse.Namespace, caminho: Path, to_dict: Callable, print_json: Callable
) -> int:
    """Inspect of an AAF: ids, mutes and gains, and with --resolve-media the media.

    Exit 1 when the reading itself has an error; an error of a media only
    marks that media, because the standalone refuses just its clips.
    """
    report = Report(strict=False)
    if getattr(args, "resolve_media", False):
        extract_dir = getattr(args, "extract_dir", None)
        if extract_dir:
            Path(extract_dir).mkdir(parents=True, exist_ok=True)
        session, read_issues = read_with_media(
            caminho, report, extract_dir=extract_dir, audio_dir=args.audio_dir
        )
    else:
        session, read_issues = _read_only(caminho, report)
    if session is None:
        dados = {"source_path": str(caminho), "media": [], "tracks": [], "markers": []}
    else:
        dados = to_dict(session)
        embutidas = embedded_media_ids(caminho, session)
        for media in dados["media"]:
            media["embedded"] = media["id"] in embutidas
        dados["unsupported_export_features"] = list(session.unsupported_export_features)
    dados["errors"] = errors_by_stage(report, read_issues)
    print_json(dados)
    de_leitura = [e for e in dados["errors"] if e["stage"] == "read"]
    if de_leitura:
        print(f"{de_leitura[0]['code']}: {de_leitura[0]['message']}", file=sys.stderr)
        return 1
    return 0


def _read_only(caminho: Path, report: Report):
    """The reading of the inspect without the media, with the same error rule."""
    if not caminho.is_file():
        raise FileNotFoundError(2, "file not found", str(caminho))
    try:
        session = read_aaf(caminho, report)
    except Exception as exc:  # noqa: BLE001 - an AAF that does not open is an error of the reading
        report.error("AAF_UNREADABLE", f"the AAF could not be read: {exc}", {"path": str(caminho)})
        return None, len(report.issues)
    return session, len(report.issues)
