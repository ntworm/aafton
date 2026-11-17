"""aafton - Web UI Local Server.

Servidor HTTP nativo sem dependências externas com interface gráfica moderna
para conversão AAF <-> Ableton Live 12, explorador de arquivos e pastas in-page,
integração com diálogo nativo do Windows via PowerShell STA e auditoria criptográfica.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import urllib.parse
import webbrowser
from typing import Any

from . import __version__
from .aaf_read import check_frame_alignment, check_pulldown, read_aaf
from .aaf_write import write_aaf
from .als_read import read_session
from .als_write import WriteOptions, write_session
from .bundled import default_template
from .cli import _resolve_origin, _session_to_dict
from .media_extract import AmbiguousMediaError, resolve_asset
from .media_oracle import verify_session_media
from .report import Report, StrictModeError
from .turnover import TurnoverError, create_turnover_package
from .verify import verify_conversion


from .studio import app_routes, dialogs, timecode
from .studio.fs import get_fs_listing as get_fs_listing
from .studio.page import load_page


def pick_windows_dialog(
    kind: str = "open_file", filter_type: str = "all", title: str = "Select"
) -> str:
    """Invoca o dialogo nativo do sistema e devolve o caminho, ou "" quando cancelado."""
    result = dialogs.pick(kind, filter_type, title)
    caminho = result.get("path")
    return str(caminho) if caminho else ""


HTML_PAGE = load_page()


def _issues(report: Report) -> list[dict[str, Any]]:
    """Converte os problemas do relatorio em uma lista serializavel."""
    return [
        {"code": i.code, "message": i.message, "severity": i.severity, "context": i.context}
        for i in report.issues
    ]


def _project_dir(output_p: Path) -> Path:
    """Define a pasta de projeto do Live a partir do caminho de saida."""
    if output_p.parent.name.endswith("Project"):
        return output_p.parent
    return output_p.parent / f"{output_p.stem} Project"


def _media_dir(project_dir: Path, payload: dict) -> Path | None:
    """Define a pasta de midia extraida quando a extracao esta habilitada."""
    if payload.get("media", "extract") != "extract":
        return None
    return (project_dir / "Samples" / "Imported").resolve()


def _resolve_media(session: Any, input_path: str, payload: dict, report: Report) -> None:
    """Resolve os arquivos de midia referenciados pela sessao importada."""
    raizes = [Path(input_path).parent]
    if payload.get("audio_dir"):
        raizes.insert(0, Path(payload["audio_dir"]))
    for asset in session.media:
        if asset.status == "resolved":
            continue
        try:
            resolve_asset(asset, raizes, report)
        except AmbiguousMediaError as exc:
            report.error("MEDIA_AMBIGUOUS", str(exc), {"filename": asset.filename})
        except Exception as exc:  # noqa: BLE001
            report.error("MEDIA_RESOLVE_ERROR", str(exc), {"filename": asset.filename})


def _apply_session_text(session: Any, payload: dict, input_path: str, report: Report) -> None:
    """Aplica o texto de sessao informado pelo usuario."""
    from .session_text import process_session_text

    session_text_arg = payload.get("session_text")
    if session_text_arg:
        session_text_arg = session_text_arg.strip() or None
    process_session_text(session, Path(input_path), session_text_arg, report)


def _apply_stereo(session: Any, payload: dict, project_dir: Path, report: Report) -> None:
    """Processa o pareamento stereo da sessao importada."""
    from .stereo import process_stereo_pairing

    stereo_dir = (project_dir / "Samples" / "Imported").resolve()
    process_stereo_pairing(session, payload.get("stereo", "pair"), report, stereo_dir)


def _apply_video(session: Any, payload: dict, input_path: str, report: Report) -> None:
    """Processa a referencia de video da sessao importada."""
    from .video import process_video

    video_arg = payload.get("video")
    if video_arg:
        video_arg = video_arg.strip() or None
    video_start_arg = payload.get("video_start")
    if video_start_arg:
        video_start_arg = video_start_arg.strip() or None
    process_video(session, Path(input_path), video_arg, video_start_arg, report)


class StudioRequestHandler(BaseHTTPRequestHandler):
    """Handler REST e servidor estático da Web UI do aafton."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suprime logs verbosos de acesso no stdout."""

    def _local_request(self) -> bool:
        """Impede páginas de outra origem e DNS rebinding de acessar APIs locais."""
        try:
            valid = self._valid_host() and self._valid_origin()
            valid = valid and self.headers.get("Sec-Fetch-Site", "none") != "cross-site"
        except ValueError:
            valid = False
        if not valid:
            self._send_json({"ok": False, "error": "Invalid local origin"}, status=403)
        return valid

    def _valid_host(self) -> bool:
        """Confere o cabecalho Host contra as interfaces locais e a porta do servidor."""
        host = urllib.parse.urlsplit("http://" + self.headers.get("Host", ""))
        return (
            host.hostname in {"127.0.0.1", "localhost"}
            and not host.username
            and not host.password
            and (host.port or 80) == self.server.server_port
        )

    def _valid_origin(self) -> bool:
        """Confere o cabecalho Origin quando o navegador o envia."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urllib.parse.urlsplit("http://" + self.headers.get("Host", ""))
        source = urllib.parse.urlsplit(origin)
        return (
            source.scheme == "http"
            and source.hostname == host.hostname
            and (source.port or 80) == (host.port or 80)
        )

    def do_GET(self) -> None:
        """Despacha as requisicoes GET para o handler de cada rota."""
        if not self._local_request():
            return
        path = urllib.parse.urlparse(self.path).path
        if app_routes.handle_get(self, path):
            return
        routes = {
            "/": self._handle_page,
            "/index.html": self._handle_page,
            "/api/status": self._handle_status,
            "/api/fs/list": self._handle_fs_list,
            "/api/fs/detect_video": self._handle_fs_video,
            "/timecode": lambda: timecode.get_page(self),
            "/api/timecode/state": lambda: timecode.get_state(self),
            "/api/timecode/video": lambda: timecode.get_video(self),
        }
        route = routes.get(path)
        if route is None:
            self.send_error(404, "Not Found")
            return
        route()

    def do_POST(self) -> None:
        """Despacha as requisicoes POST para o handler de cada rota."""
        if not self._local_request():
            return
        url = urllib.parse.urlparse(self.path)
        payload = self._read_payload()
        if payload is None:
            return
        routes = {
            "/api/fs/powershell_browse": self._handle_powershell_browse,
            "/api/inspect": self._handle_inspect,
            "/api/convert/import": self._handle_import,
            "/api/convert/export": self._handle_export,
            "/api/oracle/verify": self._handle_oracle,
            "/api/turnover/generate": self._handle_turnover,
            "/api/verify": self._handle_verify,
            "/api/timecode/load": lambda dados: timecode.post_load(self, dados),
            "/api/timecode/goto": lambda dados: timecode.post_goto(self, dados),
            "/api/timecode/start": lambda dados: timecode.post_start(self, dados),
        }
        try:
            if app_routes.handle_post(self, url.path, payload):
                return
            route = routes.get(url.path)
            if route is None:
                self.send_error(404, "Unknown API route")
                return
            route(payload)
        except StrictModeError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def _read_payload(self) -> dict | None:
        """Le e valida o corpo JSON da requisicao; None quando o payload e invalido."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length < 0 or length > 1024 * 1024:
                raise ValueError("invalid payload size")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Content-Type must be application/json")
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return None
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON object expected")
        except (ValueError, UnicodeDecodeError):
            self.send_error(400, "Invalid JSON payload")
            return None
        return payload

    def _send_json(self, data: dict, status: int = 200) -> None:
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle_page(self) -> None:
        """Responde com a pagina HTML do Studio."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def _handle_status(self) -> None:
        """Responde com o estado atual do servidor."""
        self._send_json(
            {
                "status": "ok",
                "version": __version__,
                "engine": "Ableton Live 12 / Pro Tools Interop",
            }
        )

    def _handle_fs_list(self) -> None:
        """Responde com a listagem de pastas e arquivos locais."""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        target_path = qs.get("path", [""])[0] or None
        ext_raw = qs.get("ext", [""])[0]
        filter_ext = (
            [e.strip().lower() for e in ext_raw.split(",") if e.strip()] if ext_raw else None
        )
        listing = get_fs_listing(target_path, filter_ext)
        self._send_json(listing)

    def _handle_fs_video(self) -> None:
        """Responde com o video detectado ao lado do arquivo informado."""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        target_path = qs.get("path", [""])[0]
        if not target_path or not Path(target_path).is_file():
            self._send_json({"ok": False, "error": "Invalid path"}, status=400)
            return

        from .video import find_video_file

        res = find_video_file(Path(target_path), None)
        if isinstance(res, Path):
            self._send_json({"ok": True, "video_path": str(res)})
        elif isinstance(res, list) and len(res) > 0:
            self._send_json(
                {"ok": True, "video_path": str(res[0]), "candidates": [str(c) for c in res]}
            )
        else:
            self._send_json({"ok": True, "video_path": None})

    def _handle_powershell_browse(self, payload: dict) -> None:
        """Abre o dialogo nativo do sistema e devolve o caminho escolhido."""
        result = dialogs.pick(
            kind=str(payload.get("kind") or "open_file"),
            filter_type=str(payload.get("filter_type") or "all"),
            title=str(payload.get("title") or "Select"),
        )
        if "error" in result:
            self._send_json({"ok": False, "error": str(result["error"])})
            return
        self._send_json({"ok": True, "path": result.get("path")})

    def _handle_inspect(self, payload: dict) -> None:
        """Inspeciona um arquivo AAF, ALS ou PTX e devolve o resultado."""
        path = Path(payload.get("path", "").strip())
        if not path.is_file():
            self._send_json({"ok": False, "error": f"file not found: {path}"}, status=400)
            return

        ext = path.suffix.lower()
        report = Report(strict=False)
        if ext == ".aaf":
            self._inspect_aaf(path, report)
        elif ext == ".als":
            self._inspect_als(path, report)
        elif ext == ".ptx":
            self._inspect_ptx(path)
        else:
            self._send_json({"ok": False, "error": f"Format not supported: {ext}"}, status=400)

    def _inspect_aaf(self, path: Path, report: Report) -> None:
        """Le o AAF e responde com a sessao convertida e os problemas encontrados."""
        session = read_aaf(path, report)
        self._send_json(
            {
                "ok": True,
                "type": "aaf",
                "session": _session_to_dict(session),
                "issues": _issues(report),
            }
        )

    def _inspect_als(self, path: Path, report: Report) -> None:
        """Le o ALS e responde com a sessao convertida e os problemas encontrados."""
        session = read_session(path, report)
        self._send_json(
            {
                "ok": True,
                "type": "als",
                "session": _session_to_dict(session),
                "issues": _issues(report),
            }
        )

    def _inspect_ptx(self, path: Path) -> None:
        """Le o PTX e responde com as faixas e referencias detectadas."""
        import re

        raw = path.read_bytes()
        wavs = sorted(list(set(re.findall(rb"[\w\s\.-]+\.wav", raw, re.IGNORECASE))))
        cal_tracks = sorted(list(set(re.findall(rb"CAL_[A-Z0-9_]+", raw))))
        self._send_json(
            {
                "ok": True,
                "type": "ptx",
                "session_name": path.name,
                "size_bytes": len(raw),
                "referenced_wavs": [w.decode("latin1") for w in wavs],
                "detected_tracks": [t.decode("latin1") for t in cal_tracks],
            }
        )

    def _handle_import(self, payload: dict) -> None:
        """Importa um AAF e grava o Live Set de saida."""
        input_path = payload.get("input", "").strip()
        output_path = payload.get("output", "").strip()
        if not input_path or not output_path:
            self._send_json({"ok": False, "error": "input and output are required"}, status=400)
            return

        report = Report(strict=not bool(payload.get("permissive", False)))
        project_dir = _project_dir(Path(output_path).resolve())
        session = read_aaf(
            input_path,
            report,
            extract_dir=_media_dir(project_dir, payload),
            fades=payload.get("fades", "absorb"),
        )
        _resolve_media(session, input_path, payload, report)
        _apply_session_text(session, payload, input_path, report)
        check_frame_alignment(session, report)
        _apply_stereo(session, payload, project_dir, report)
        _apply_video(session, payload, input_path, report)
        check_pulldown(session, report)

        origem = _resolve_origin(payload.get("live_origin"), session, report)
        write_session(
            session,
            payload.get("template") or default_template(),
            output_path,
            WriteOptions(
                sample_rate=session.sample_rate,
                bpm=float(payload.get("bpm", 60.0)),
                live_origin_samples=origem,
                permissive=bool(payload.get("permissive", False)),
                fades=payload.get("fades", "absorb"),
                stereo=payload.get("stereo", "pair"),
                groups=payload.get("groups", "none"),
            ),
            report,
        )
        self._send_json({"ok": not report.incomplete, "output": report.to_markdown()})

    def _handle_export(self, payload: dict) -> None:
        """Exporta um Live Set para AAF."""
        input_path = payload.get("input", "").strip()
        output_path = payload.get("output", "").strip()
        if not input_path or not output_path:
            self._send_json({"ok": False, "error": "input and output are required"}, status=400)
            return

        report = Report(strict=not bool(payload.get("permissive", False)))
        session = read_session(input_path, report)
        write_aaf(session, output_path, report)
        self._send_json({"ok": not report.incomplete, "output": report.to_markdown()})

    def _handle_oracle(self, payload: dict) -> None:
        """Audita as midias de uma pasta contra o sidecar da sessao."""
        media_dir = payload.get("media_dir")
        if not media_dir or not Path(media_dir).is_dir():
            self._send_json({"ok": False, "error": "invalid media_dir"}, status=400)
            return

        session_path = payload.get("session")
        sidecar_path = None
        if session_path:
            sidecar_candidate = Path(session_path).with_suffix(".aafton.json")
            if sidecar_candidate.is_file():
                sidecar_path = sidecar_candidate

        audit = verify_session_media(media_dir=media_dir, sidecar_path=sidecar_path)
        self._send_json({"ok": audit.is_valid, "audit": audit.to_dict()})

    def _handle_turnover(self, payload: dict) -> None:
        """Gera o pacote de turnover da sessao informada."""
        if payload.get("media_dir"):
            self._send_json(
                {
                    "ok": False,
                    "error": "media_dir is no longer supported; media is discovered from session references",
                },
                status=400,
            )
            return

        session = payload.get("session", "").strip()
        if not session or not Path(session).is_file():
            self._send_json({"ok": False, "error": f"Session not found: {session}"}, status=400)
            return

        try:
            res = create_turnover_package(
                session_file=session,
                output_dir=payload.get("output"),
                session_name=payload.get("name"),
                strict=not payload.get("permissive", False),
            )
            report_text = res.report_path.read_text(encoding="utf-8")
            self._send_json(
                {
                    "ok": res.is_valid,
                    "report": report_text,
                    "output_dir": str(res.package_dir),
                    "manifest": res.manifest_data,
                }
            )
        except TurnoverError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=400)

    def _handle_verify(self, payload: dict) -> None:
        """Compara o AAF e o ALS informados e devolve o relatorio de equivalencia."""
        aaf_path = payload.get("aaf", "").strip()
        als_path = payload.get("als", "").strip()
        if not Path(aaf_path).is_file() or not Path(als_path).is_file():
            self._send_json({"ok": False, "error": "the aaf and als files must exist"}, status=400)
            return

        res = verify_conversion(aaf_path, als_path)
        self._send_json({"ok": res.ok, "table": res.to_table(), "markdown": res.to_markdown()})


def run_studio_server(port: int = 8765, open_browser: bool = True) -> ThreadingHTTPServer:
    """Inicia o servidor HTTP do aafton."""
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioRequestHandler)
    # Cada requisicao tem a sua thread: um video que nao e lido nao segura
    # as outras rotas.
    server.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    return server
