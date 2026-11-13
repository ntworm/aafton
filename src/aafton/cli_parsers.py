"""Subparsers do aafton: recebem os callbacks por parametro, sem importar cli."""

from __future__ import annotations

import argparse


def add_ui_parsers(sub: argparse._SubParsersAction, func) -> None:
    """Registra os subcomandos ui e gui, que sobem a interface web local."""
    for nome in ("ui", "gui"):
        p_web = sub.add_parser(nome, help="starts the local web interface in the browser")
        p_web.add_argument(
            "--port", type=int, default=8765, help="port of the local server (default: 8765)"
        )
        p_web.add_argument(
            "--no-browser", action="store_true", help="does not open the browser automatically"
        )
        p_web.set_defaults(func=func)


def add_app_parser(sub: argparse._SubParsersAction, func) -> None:
    """Registra o subcomando app, que abre a janela do studio."""
    p_app = sub.add_parser("app", help="opens the studio in its own window")
    p_app.add_argument(
        "--smoke",
        action="store_true",
        help="starts the server and answers, without opening the window",
    )
    p_app.set_defaults(func=func)


def add_import_parser(sub: argparse._SubParsersAction, func, edl_taxa, rates_ltc) -> None:
    """Subparser do import, com as opcoes do AAF e dos formatos de troca."""
    p_import = sub.add_parser("import", help="AAF, EDL, XML or FCPXML -> .als")
    p_import.add_argument("input", help="source file (.aaf, .edl, .xml or .fcpxml)")
    p_import.add_argument("output", help="path of the .als to be generated")
    p_import.add_argument(
        "--template",
        default=None,
        help="Live template used as the base of the .als (default: the template in the package)",
    )
    p_import.add_argument(
        "--live-origin",
        default=None,
        help="timecode that lands on the beat zero; the default is the first clip minus 10s",
    )
    p_import.add_argument(
        "--bpm", type=float, default=60.0, help="project tempo in BPM (default: 60.0)"
    )
    p_import.add_argument(
        "--media",
        choices=["extract", "link"],
        default="extract",
        help="extracts the media to the project or keeps the absolute path (default: extract)",
    )
    p_import.add_argument(
        "--stereo",
        choices=["pair", "split"],
        default="pair",
        help="Join split stereo tracks [L]/[R] into L/R channels (default: pair)",
    )
    p_import.add_argument(
        "--fades",
        choices=["absorb", "keep"],
        default="absorb",
        help="absorbs the rendered fades of Pro Tools into native fades or keeps them as independent clips",
    )
    p_import.add_argument(
        "--groups",
        choices=["prefix", "none"],
        default="none",
        help="how to group tracks: prefix deduces groups by name, none leaves them all separate",
    )
    _add_import_extra_args(p_import, edl_taxa, rates_ltc)
    p_import.set_defaults(func=func)


def _add_import_extra_args(p_import, edl_taxa, rates_ltc) -> None:
    """Opcoes extras do import: midia, video, texto, permissivo, LTC e relatorio."""
    p_import.add_argument("--audio-dir", default=None, help="extra folder to look for the media")
    p_import.add_argument("--video", default=None, help="Path of the video to use")
    p_import.add_argument("--video-start", default=None, help="Anchor timecode of the video")
    p_import.add_argument("--video-transcode", action="store_true", help="Generate H.264 proxy")
    p_import.add_argument(
        "--video-no-burn",
        action="store_true",
        help="Do not burn the timecode into the video proxy",
    )
    p_import.add_argument(
        "--session-text",
        default=None,
        help="Pro Tools session text export to import markers",
    )
    p_import.add_argument(
        "--permissive", action="store_true", help="does not stop the conversion on an error"
    )
    p_import.add_argument("--ltc", action="store_true", help="creates an LTC track in the set")
    p_import.add_argument(
        "--report", default=None, help="path of the JSON report of the conversion"
    )
    p_import.add_argument("--edl-rate", type=edl_taxa, help=f"EDL rate: {', '.join(rates_ltc)}")


def add_export_parser(sub: argparse._SubParsersAction, func) -> None:
    """Subparser do export, que grava a sessao no formato do destino."""
    p_export = sub.add_parser("export", help=".als -> AAF, EDL, XML or FCPXML")
    p_export.add_argument("input", help="source .als session")
    p_export.add_argument("output", help="destination file (.aaf, .edl, .xml or .fcpxml)")
    p_export.add_argument(
        "--media",
        choices=["link", "embed"],
        default="link",
        help=(
            "AAF media: link references the files; embed incorporates the audio "
            "(default: link; other formats ignore this option)"
        ),
    )
    p_export.add_argument(
        "--permissive", action="store_true", help="does not stop the conversion on an error"
    )
    p_export.add_argument(
        "--report", default=None, help="path of the JSON report of the conversion"
    )
    p_export.set_defaults(func=func)


def add_inspect_parser(sub: argparse._SubParsersAction, func, edl_taxa, rates_ltc) -> None:
    """Subparser do inspect, que despeja o SessionIR como JSON."""
    p_inspect = sub.add_parser("inspect", help="dumps the SessionIR as JSON")
    p_inspect.add_argument("input", help="file to inspect (.aaf, .als or exchange format)")
    p_inspect.add_argument("--edl-rate", type=edl_taxa, help=f"EDL rate: {', '.join(rates_ltc)}")
    p_inspect.set_defaults(func=func)


def add_verify_parser(sub: argparse._SubParsersAction, func) -> None:
    """Subparser do verify, que compara a origem com o .als gerado."""
    p_verify = sub.add_parser(
        "verify", help="compares the origin (AAF, EDL, XML or FCPXML) with the generated .als"
    )
    p_verify.add_argument("aaf", help="source file (.aaf, .edl, .xml or .fcpxml)")
    p_verify.add_argument("als", help="generated .als session to compare")
    p_verify.add_argument(
        "--tolerance-samples",
        type=int,
        default=None,
        help="deviation tolerance in samples; without a value, the AAF regime applies",
    )
    p_verify.add_argument(
        "--report", default=None, help="path of the markdown report of the verification"
    )
    p_verify.set_defaults(func=func)


def add_audit_audio_parser(sub: argparse._SubParsersAction, func) -> None:
    """Subparser do audit-audio, que compara renders por audio."""
    p_audit = sub.add_parser(
        "audit-audio", help="compares renders by audio, without the readers of the conversion"
    )
    p_audit.add_argument("--reference", required=True, help="folder with the reference WAVs")
    p_audit.add_argument("--candidate", required=True, help="folder with the candidate render WAVs")
    p_audit.add_argument("--sidecar", required=True, help="project JSON sidecar (.aafton.json)")
    _add_audit_audio_options(p_audit)
    p_audit.set_defaults(func=func)


def _add_audit_audio_options(p_audit) -> None:
    """Opcoes de comparacao do audit-audio: mapa, mascara, tolerancias e saidas."""
    p_audit.add_argument(
        "--map",
        default=None,
        help="JSON of pairs by exact name; the list [L.wav, R.wav] composes stereo",
    )
    p_audit.add_argument(
        "--mask", default=None, help="absolute intervals in samples, used only in absorb"
    )
    p_audit.add_argument(
        "--mode", choices=["keep", "absorb"], default=None, help="default: fades from the sidecar"
    )
    p_audit.add_argument(
        "--reference-start", default=None, help="HH:MM:SS:FF non-drop when BWF reports no start"
    )
    p_audit.add_argument(
        "--tolerance-db",
        type=float,
        default=-90.0,
        help="peak accepted in dBFS, in the residual and in the active audio (default: -90.0)",
    )
    p_audit.add_argument(
        "--max-lag",
        type=int,
        default=4800,
        help="largest delay searched in the correlation, in samples (default: 4800)",
    )
    p_audit.add_argument(
        "--release-mask",
        type=int,
        default=None,
        metavar="N",
        help="ignores up to N samples after each clip end of the sidecar (Live declick, D14); applies in keep",
    )
    p_audit.add_argument(
        "--report", default=None, help="path of the markdown report of the audio audit"
    )
    p_audit.add_argument("--json", default=None, help="path of the JSON report of the audit")


def add_turnover_parser(sub: argparse._SubParsersAction, func) -> None:
    """Subparser do turnover, que cria o pacote autocontido."""
    p_turnover = sub.add_parser(
        "turnover", help="creates a self-contained package with a SHA-256 manifest and a report"
    )
    p_turnover.add_argument("session", help="session file (.als or .aaf)")
    p_turnover.add_argument("--media-dir", default=None, help="folder with the audio files")
    p_turnover.add_argument(
        "--output", "-o", default=None, help="destination folder of the package"
    )
    p_turnover.add_argument("--name", default=None, help="session name in the manifest")
    p_turnover.add_argument(
        "--permissive", action="store_true", help="ignores integrity errors in media"
    )
    p_turnover.add_argument(
        "--json", default=None, help="saves the manifest as json in the given path"
    )
    p_turnover.set_defaults(func=func)
