"""The desktop app of aafton: the studio page inside a window of its own.

The window comes from pywebview, an optional dependency that the extra
"desktop" installs. The window is also the dialog backend of the studio:
the page asks for a path and the window answers with the native dialog.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.request
from typing import Any

from . import web_app
from .studio import dialogs

__all__ = ["run_app"]

# The message of a build without the extra that brings the window.
MISSING_EXTRA = 'aafton app needs the desktop extra: pip install "aafton[desktop]"'

# The message of a window that did not open.
WINDOW_FAILED = (
    "the window could not start: {erro}; on Windows it needs the Microsoft Edge WebView2 Runtime"
)

# The kind of the window dialog for each kind of the page.
DIALOG_KINDS = {"open_file": "OPEN", "save_file": "SAVE", "folder": "FOLDER"}

# The size of the window and the smallest size it takes.
WIDTH = 1280
HEIGHT = 860
MIN_SIZE = (960, 640)

# How long the backend waits for the window to show itself.
SHOWN_TIMEOUT = 10

# How long the smoke test waits for the answer of the server.
HTTP_TIMEOUT = 10


def run_app(smoke: bool = False, webview_module: Any = None) -> int:
    """Runs the app: the window of the page, or the smoke test of the server."""
    server = web_app.run_studio_server(port=0, open_browser=False)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if smoke:
        return _run_smoke(server, url)
    return _run_window(server, url, webview_module)


def _run_smoke(server: Any, url: str) -> int:
    """Asks the server two questions and tells whether the app answers."""
    serving = False
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        serving = True
        info = json.loads(_fetch(url + "api/app/info").decode("utf-8"))
        _fetch(url)
        print(f"aafton {info['version']} ok")
        return 0
    except Exception as exc:  # noqa: BLE001 - the message of the failure is the answer
        _fail(f"aafton app failed the smoke test: {exc}")
        return 1
    finally:
        _close(server, serving)


def _run_window(server: Any, url: str, webview_module: Any) -> int:
    """Opens the window of the page and serves the studio to it."""
    try:
        webview = webview_module if webview_module is not None else _import_webview()
    except ImportError:
        _fail(MISSING_EXTRA)
        _close(server, False)
        return 2
    try:
        window = webview.create_window(
            "aafton",
            url,
            width=WIDTH,
            height=HEIGHT,
            min_size=MIN_SIZE,
        )
    except Exception as exc:  # noqa: BLE001 - the message of the failure is the answer
        _fail(WINDOW_FAILED.format(erro=exc))
        _close(server, False)
        return 2
    dialogs.set_window_backend(_backend(window, webview))
    serving = False
    try:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        serving = True
        webview.start()
        return 0
    except Exception as exc:  # noqa: BLE001 - the message of the failure is the answer
        _fail(WINDOW_FAILED.format(erro=exc))
        return 2
    finally:
        _close(server, serving)
        dialogs.set_window_backend(None)


def _import_webview() -> Any:
    """Imports the window library, which is an optional dependency."""
    import webview

    return webview


def _backend(window: Any, webview: Any) -> dialogs.WindowBackend:
    """The backend of the dialogs of the studio: the dialogs of the window."""

    def backend(kind: str, label: str, extensions: list[str], title: str) -> str | None:
        """Opens the dialog of the window and gives back the chosen path."""
        if not window.events.shown.wait(SHOWN_TIMEOUT):
            raise dialogs.DialogError("the window did not open")
        dialog_type = getattr(webview.FileDialog, DIALOG_KINDS.get(kind, "OPEN"))
        file_types = _file_types(label, extensions) if kind != "folder" else ()
        save_name = _save_name(kind, extensions)
        answer = window.create_file_dialog(dialog_type, "", False, save_name, file_types)
        return _first_path(answer)

    return backend


def _file_types(label: str, extensions: list[str]) -> tuple[str, ...]:
    """The filters of the dialog: the label with the extensions, then all."""
    if not extensions:
        return ("All files (*.*)",)
    pattern = ";".join(f"*.{extension}" for extension in extensions)
    return (f"{label} ({pattern})", "All files (*.*)")


def _save_name(kind: str, extensions: list[str]) -> str:
    """The name the save dialog offers: untitled with the first extension."""
    if kind != "save_file":
        return ""
    return f"untitled.{extensions[0]}" if extensions else "untitled"


def _first_path(answer: Any) -> str | None:
    """The first path of the answer of the dialog; None when there is none."""
    if answer is None:
        return None
    if isinstance(answer, str):
        return str(answer)
    paths = list(answer)
    if not paths:
        return None
    return str(paths[0])


def _fetch(url: str) -> bytes:
    """Asks the server for a page and gives back the body."""
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as answer:
        return answer.read()


def _close(server: Any, serving: bool) -> None:
    """Stops the server: the loop first, the socket after."""
    if serving:
        server.shutdown()
    server.server_close()


def _fail(message: str) -> None:
    """Shows the failure where the operator can see it."""
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if (sys.stderr is None or getattr(sys, "frozen", False)) and sys.platform == "win32":
        _show_box(message)


def _show_box(message: str) -> None:
    """Shows the message in a window of the system, when there is one."""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "aafton", 0x10)
    except Exception:  # noqa: BLE001 - the box is a courtesy, never a failure
        pass
