"""Subcomando ltc da linha de comando do aafton."""

from __future__ import annotations

import argparse

from .ltc import parse_ltc_rate, write_ltc_wav
from .timecode import TimecodeRate, label_to_frames


def add_ltc_parser(sub: argparse._SubParsersAction) -> None:
    """Registra o subcomando ltc no parser principal."""
    parser = sub.add_parser("ltc", help="generates a WAV with an LTC signal")
    parser.add_argument("--start", required=True, help="start timecode (HH:MM:SS:FF)")
    parser.add_argument(
        "--rate", required=True, help="LTC rate (23.976, 24, 25, 29.97, 29.97df, 30)"
    )
    janela = parser.add_mutually_exclusive_group(required=True)
    janela.add_argument(
        "--duration",
        help="duration as a TC label at the chosen rate (in drop frame, use ';')",
    )
    janela.add_argument("--end", help="end timecode, exclusive")
    parser.add_argument(
        "--sample-rate", type=int, default=48000, help="sample rate (default: 48000)"
    )
    parser.add_argument(
        "--level-dbfs", type=float, default=-18.0, help="signal level (default: -18.0)"
    )
    parser.add_argument("output", help="path of the output WAV")
    parser.set_defaults(func=cmd_ltc)


def _total_de_frames(args: argparse.Namespace, rate: TimecodeRate, primeiro: int) -> int:
    """Quantos frames o pedido do comando cobre."""
    if args.end is not None:
        fim = label_to_frames(args.end, rate)
        if fim <= primeiro:
            raise ValueError("the end of the LTC must be after the start")
        return fim - primeiro
    total = label_to_frames(args.duration, rate)
    if total <= 0:
        raise ValueError("the duration of the LTC must have at least one frame")
    return total


def cmd_ltc(args: argparse.Namespace) -> int:
    """Executa o subcomando ltc."""
    rate = parse_ltc_rate(args.rate)
    primeiro = label_to_frames(args.start, rate)
    total = _total_de_frames(args, rate, primeiro)
    saida = write_ltc_wav(args.output, args.start, rate, total, args.sample_rate, args.level_dbfs)
    print(f"LTC {args.start} {rate.num}/{rate.den} fps: {total} frames -> {saida}")
    return 0
