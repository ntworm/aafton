"""HTTP glue of the app page: the routes of ``/api/app/*`` and the bundled fonts.

The read and the convert are answered by the modules of their own step; this
module only carries the HTTP. The open, the reveal and the report never act on
a path that comes from the request: they work on the file the server itself
wrote in this run.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .. import __version__
from . import app_convert, app_read, dialogs
from .app_read import error

if TYPE_CHECKING:
    from ..web_app import StudioRequestHandler

# The folder of the fonts bundled with the page.
FONTS_DIR = Path(__file__).resolve().parent / "static" / "fonts"

# The prefix of the font route.
FONT_PREFIX = "/static/fonts/"

# The only font names the route serves: letters, digits, hyphen and the suffix.
FONT_NAME = re.compile(r"[A-Za-z0-9-]+\.woff2")

# How long the browser keeps a font in its cache.
CACHE_SECONDS = 31536000

# The code of an answer that says the request itself was malformed.
BAD_REQUEST = "bad_request"


def _status(data: dict) -> int:
    """The HTTP status of an answer: 400 when the request itself was malformed.

    Every other answer is an expected failure and keeps the status 200.
    """
    erro = data.get("error") or {}
    return 400 if erro.get("code") == BAD_REQUEST else 200


def _responde(handler: StudioRequestHandler, data: dict) -> None:
    """Sends the answer of the app with the status its error code deserves."""
    handler._send_json(data, status=_status(data))


def handle_get(handler: StudioRequestHandler, path: str) -> bool:
    """Answers the GET routes of the app; True when the route was served."""
    try:
        return _rotas_get(handler, path)
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        _responde(handler, error("failed", str(exc)))
        return True


def _rotas_get(handler: StudioRequestHandler, path: str) -> bool:
    """The GET routes of the app; False when the path is not one of them."""
    if path == "/api/app/info":
        _responde(handler, _info())
        return True
    if path.startswith(FONT_PREFIX):
        return _font(handler, path[len(FONT_PREFIX) :])
    return False


def handle_post(handler: StudioRequestHandler, path: str, payload: dict) -> bool:
    """Answers the POST routes of the app; True when the route was served."""
    try:
        return _rotas_post(handler, path, payload)
    except Exception as exc:  # noqa: BLE001 - the message of the engine is the answer
        _responde(handler, error("failed", str(exc)))
        return True


def _rotas_post(handler: StudioRequestHandler, path: str, payload: dict) -> bool:
    """The POST routes of the app; False when the path is not one of them."""
    if path == "/api/app/browse":
        _browse(handler, payload)
        return True
    if path == "/api/app/read":
        _responde(handler, app_read.read(payload))
        return True
    if path == "/api/app/convert":
        _responde(handler, app_convert.convert(payload))
        return True
    if path == "/api/app/open":
        _responde(handler, _open(payload))
        return True
    if path == "/api/app/reveal":
        _responde(handler, _reveal(payload))
        return True
    if path == "/api/app/report":
        _responde(handler, _report(payload))
        return True
    return False


def launch(path: Path) -> None:
    """Opens a file with the program the system uses by default."""
    alvo = str(path)
    if sys.platform == "win32":
        os.startfile(alvo)
        return
    comando = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([comando, alvo], check=False)


def reveal(path: Path) -> None:
    """Shows a file in the folder that holds it."""
    if sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{path}"], check=False)
        return
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
        return
    launch(path.parent)


def _info() -> dict:
    """What the page needs to know about the server that answers it."""
    return {
        "ok": True,
        "version": __version__,
        "platform": sys.platform,
        "dialogs": dialogs.has_backend(),
    }


def _font(handler: StudioRequestHandler, name: str) -> bool:
    """Serves one bundled font; False when the name is not a font of the page."""
    if not FONT_NAME.fullmatch(name):
        return False
    arquivo = FONTS_DIR / name
    if not arquivo.is_file():
        return False
    dados = arquivo.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "font/woff2")
    handler.send_header("Content-Length", str(len(dados)))
    handler.send_header("Cache-Control", f"public, max-age={CACHE_SECONDS}, immutable")
    handler.end_headers()
    handler.wfile.write(dados)
    return True


def _browse(handler: StudioRequestHandler, payload: dict) -> None:
    """Opens the native dialog of the system and gives back the chosen path."""
    result = dialogs.pick(
        kind=str(payload.get("kind") or "open_file"),
        filter_type=str(payload.get("filter") or "all"),
        title=str(payload.get("title") or "Select"),
    )
    if "error" in result:
        _responde(
            handler,
            {
                "ok": False,
                "error": {
                    "code": str(result.get("code") or "no_dialog"),
                    "message": str(result["error"]),
                },
            },
        )
        return
    _responde(handler, {"ok": True, "path": result.get("path")})


def _ultimo(direction: str) -> dict | None:
    """The last conversion of the direction, with the report of that run."""
    return app_convert.LAST.get(direction)


def _ultimo_arquivo(direction: str) -> Path | None:
    """The last file written in the direction; None when nothing was written."""
    entrada = _ultimo(direction)
    if entrada is None:
        return None
    saida: Path | None = entrada.get("output")
    if saida is None:
        return None
    return Path(saida)


def _open(payload: dict) -> dict:
    """Opens the last file written in the direction with the default program."""
    saida = _ultimo_arquivo(str(payload.get("direction") or ""))
    if saida is None:
        return error("nothing", "There is no file to open yet.")
    try:
        launch(saida)
    except OSError as exc:
        return error("nothing", f"The file {saida} could not be opened ({exc}).")
    return {"ok": True, "path": str(saida)}


def _reveal(payload: dict) -> dict:
    """Shows the last file written in the direction in the folder that holds it."""
    saida = _ultimo_arquivo(str(payload.get("direction") or ""))
    if saida is None:
        return error("nothing", "There is no file to show yet.")
    try:
        reveal(saida)
    except OSError as exc:
        return error("nothing", f"The file {saida} could not be shown ({exc}).")
    return {"ok": True, "path": str(saida)}


def _report(payload: dict) -> dict:
    """Writes the last report of the direction in the path the user chose."""
    entrada = _ultimo(str(payload.get("direction") or ""))
    if entrada is None:
        return error("nothing", "There is no report to write yet.")
    destino = str(payload.get("path") or "").strip()
    if not destino:
        return error("bad_request", "The path of the report is required.")
    arquivo = Path(destino).resolve()
    sufixo = arquivo.suffix.lower()
    if sufixo in {".md", ".txt"}:
        texto = entrada["report"].to_markdown()
    elif sufixo == ".json":
        texto = entrada["report"].to_json()
    else:
        return error("bad_request", f"The format {sufixo} is not a report.")
    try:
        arquivo.write_text(texto, encoding="utf-8")
    except OSError as exc:
        return error("bad_request", f"The report could not be written ({exc}).")
    return {"ok": True, "path": str(arquivo)}
