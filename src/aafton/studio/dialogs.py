"""The dialog layer of the studio: one entry and three backends.

The page asks for a path and the layer answers with the path, with None when
the dialog was cancelled, or with the message and the code of an error. The
backend is read when the call happens: the window of the desktop app first,
then the PowerShell dialog of Windows, then the AppleScript dialog of macOS.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from . import dialogs_macos, dialogs_windows

# The filters the page sends: the name, the label of the dialog and the
# extensions it holds, without the dot.
FILTERS: dict[str, tuple[str, list[str]]] = {
    "source": ("Source files", ["aaf", "edl", "xml", "fcpxml"]),
    "als": ("Ableton Live Sets", ["als"]),
    "aaf": ("AAF files", ["aaf"]),
    "edl": ("EDL files", ["edl"]),
    "xml": ("FCP 7 XML files", ["xml"]),
    "fcpxml": ("FCPXML files", ["fcpxml"]),
    "video": ("Video files", ["mov", "mp4", "mxf", "m4v"]),
    "text": ("Text files", ["txt"]),
    "report": ("Report files", ["md", "txt", "json"]),
    "session": ("Session files", ["aaf", "als", "ptx"]),
    "all": ("All files", []),
}

# The codes of the answers that carry a failure of the dialog.
NO_DIALOG = "no_dialog"
WRONG_EXTENSION = "wrong_extension"
DIALOG_FAILED = "dialog_failed"
WINDOW_NOT_OPEN = "window_not_open"


class DialogError(Exception):
    """The failure of a dialog of the system, with the message of it."""


# The backend of the desktop window: kind, label, extensions and title in;
# the path the user chose, or None, out.
WindowBackend = Callable[[str, str, list[str], str], "str | None"]

# The backend of the desktop window, when the app registered one.
_window: WindowBackend | None = None


def set_window_backend(backend: WindowBackend | None) -> None:
    """Registers the backend of the desktop window; None removes it."""
    global _window
    _window = backend


def has_backend() -> bool:
    """Whether the system has a dialog backend; nothing is opened."""
    return _window is not None or sys.platform in {"win32", "darwin"}


def _error(message: str, code: str) -> dict:
    """The answer of a failure: the message of it and the code of the cause."""
    return {"error": message, "code": code}


def _filter(filter_type: str) -> tuple[str, list[str]]:
    """The label and the extensions of the filter the page sends."""
    return FILTERS.get(filter_type, FILTERS["all"])


def _windows(kind: str, filter_type: str, title: str) -> dict:
    """The Windows dialog: the path the user chose, or None when it was cancelled."""
    script = dialogs_windows.build_dialog_script(kind, filter_type, title)
    chosen = dialogs_windows.run_powershell(script)
    if chosen is None:
        return _error("the dialog failed", DIALOG_FAILED)
    return {"path": chosen or None}


def _macos(kind: str, title: str, extensions: list[str]) -> dict:
    """The macOS dialog: the path, None when it was cancelled, the error otherwise."""
    script = dialogs_macos.build_script(kind, title, extensions)
    code, out, err = dialogs_macos.run_osascript(script)
    if code != 0:
        if code == 1 and "-128" in err:
            return {"path": None}
        return _error(err.strip() or "the dialog failed", DIALOG_FAILED)
    chosen = out.strip()
    if not chosen:
        return {"path": None}
    if kind == "open_file" and extensions:
        suffix = Path(chosen).suffix.lower().lstrip(".")
        if suffix not in extensions:
            expected = ", ".join(f".{ext}" for ext in extensions)
            return _error(f"expected a {expected} file", WRONG_EXTENSION)
    return {"path": chosen}


def pick(kind: str, filter_type: str, title: str) -> dict:
    """Opens the dialog of the system and gives back the path the user chose.

    The answer is {"path": str} for a chosen file, {"path": None} for a
    cancelled dialog and {"error": str, "code": str} when the dialog failed.
    """
    label, extensions = _filter(filter_type)
    if _window is not None:
        try:
            chosen = _window(kind, label, extensions, title)
        except DialogError as exc:
            return _error(str(exc), WINDOW_NOT_OPEN)
        return {"path": None} if chosen is None else {"path": str(chosen)}
    if sys.platform == "win32":
        return _windows(kind, filter_type, title)
    if sys.platform == "darwin":
        return _macos(kind, title, extensions)
    return _error("no file dialog on this system", NO_DIALOG)
