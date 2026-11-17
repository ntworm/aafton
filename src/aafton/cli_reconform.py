"""Subcomando reconform: change list entre dois cortes e aplicacao numa copia do set."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

from .cli_locators import _parse_frame_rate, _print_report
from .cut_read import read_cut
from .reconform_apply import apply_reconform
from .reconform_diff import ChangeList, diff_cuts
from .reconform_locators import write_change_locators
from .reconform_report import change_list_text, write_change_list_csv
from .reconform_verify import verify_reconform
from .report import Report
from .sidecar import read_anchor, sidecar_path_for
from .timecode import TimecodeRate


def add_reconform_parser(sub: argparse._SubParsersAction) -> None:
    """Registra o subcomando reconform."""
    p = sub.add_parser(
        "reconform",
        help="compare two picture cuts and conform a copy of a Live set to the new cut",
    )
    p.add_argument("old", help="old cut (EDL, FCP7 XML or AAF)")
    p.add_argument("new", help="new cut (EDL, FCP7 XML or AAF)")
    p.add_argument("--frame-rate", type=_parse_frame_rate, default=None, help="EDL frame rate")
    p.add_argument("--start-tc", default=None, help="start timecode for set without sidecar")
    p.add_argument("--csv", default=None, help="write the change list as CSV to this path")
    p.add_argument("--set", default=None, help="Live set (.als) to conform; only read")
    p.add_argument("--apply", action="store_true", help="write the conformed copy of the set")
    p.add_argument("--out", default=None, help="path of the copy, in the folder of the set")
    p.add_argument("--verify", action="store_true", help="verify the conformed copy")
    p.add_argument("--json", action="store_true", help="output as JSON on stdout")
    p.set_defaults(func=cmd_reconform, _parser=p)


def _validar(args: argparse.Namespace) -> None:
    parser = args._parser
    if args.set is None and (args.apply or args.out or args.verify):
        parser.error("--apply, --out and --verify need --set")
    if args.verify and not args.apply:
        parser.error("--verify needs --apply")


def _taxa(args: argparse.Namespace) -> TimecodeRate | None:
    """--frame-rate, ou a taxa do sidecar do set para ler uma EDL."""
    if args.frame_rate is not None or args.set is None:
        return args.frame_rate
    if not sidecar_path_for(args.set).exists():
        return None
    try:
        return read_anchor(args.set).timecode_rate
    except (ValueError, KeyError):
        return None


def _escrever(texto: str) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(texto + "\n")
        return
    sys.stdout.flush()
    buffer.write((texto + "\n").encode("utf-8"))
    buffer.flush()


def _json(cl: ChangeList | None, report: Report) -> str:
    corpo = {"report": json.loads(report.to_json())}
    if cl is not None:
        corpo["counts"] = cl.counts
        corpo["ambiguous"] = cl.ambiguous
        corpo["changes"] = [dataclasses.asdict(c) for c in cl.changes]
    return json.dumps(corpo, ensure_ascii=False, indent=2)


def _sha256(caminho: str) -> str:
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


def _aplicar(args, cl: ChangeList, old, new, report: Report) -> bool:
    """Aplica numa copia e, com --verify, confere a copia escrita."""
    sha = _sha256(args.set)
    lado = sidecar_path_for(args.set)
    sha_sidecar = _sha256(lado) if lado.exists() else None
    res = apply_reconform(args.set, cl, report, output=args.out, start_tc=args.start_tc)
    if not res.written:
        return False
    report.metadata["output"] = str(res.output)
    if not args.verify:
        return True
    verificado = verify_reconform(
        old, new, args.set, res.output, report, source_sha256=sha, source_sidecar_sha256=sha_sidecar
    )
    return verificado.ok


def _gravar_set(args, cl: ChangeList, old, new, report: Report) -> bool:
    if args.apply:
        return _aplicar(args, cl, old, new, report)
    res = write_change_locators(args.set, cl, report, output=args.out, start_tc=args.start_tc)
    if res.written:
        report.metadata["output"] = str(res.output)
    return res.written


def _diff(args, report: Report):
    taxa = _taxa(args)
    old = read_cut(args.old, report, frame_rate=taxa)
    new = read_cut(args.new, report, frame_rate=taxa)
    if old is None or new is None:
        return None, old, new
    return diff_cuts(old, new, report), old, new


def _imprimir_lista(cl: ChangeList | None, args: argparse.Namespace) -> None:
    """Imprime a lista de mudancas, fora do modo JSON."""
    if cl is None or args.json:
        return
    # A lista de mudancas sai antes da copia: quem aplica le o que vai
    # mudar antes de o set gravado existir.
    _escrever(change_list_text(cl))


def cmd_reconform(args: argparse.Namespace) -> int:
    """Executa o reconform: change list, CSV e, com --set, a copia do set."""
    _validar(args)
    report = Report(strict=False)
    cl, old, new = _diff(args, report)
    ok = cl is not None
    if ok and args.csv:
        write_change_list_csv(cl, args.csv)
    _imprimir_lista(cl, args)
    if ok and args.set is not None:
        ok = _gravar_set(args, cl, old, new, report)
    if args.json:
        _escrever(_json(cl, report))
    else:
        _print_report(report, False)
    return 0 if ok and not report.incomplete else 1
