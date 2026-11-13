"""The dialog of macOS, through AppleScript.

The scripts of the dialogs of files and folders of macOS live here, with the
process that runs them.
"""

from __future__ import annotations

import subprocess

# The extension the save dialog proposes when the filter has none of its own.
DEFAULT_EXTENSION = "txt"


def _escape_title(title: str) -> str:
    """Escapes the quotes and the backslashes of the AppleScript string."""
    return str(title).replace("\\", "\\\\").replace('"', '\\"')


def build_script(kind: str, title: str, extensions: list[str]) -> str:
    """Builds the AppleScript of the dialog by kind, title and extensions."""
    safe = _escape_title(title)
    if kind == "folder":
        chooser = f'choose folder with prompt "{safe}"'
    elif kind == "save_file":
        ext = extensions[0] if extensions else DEFAULT_EXTENSION
        chooser = f'choose file name with prompt "{safe}" default name "untitled.{ext}"'
    else:
        chooser = f'choose file with prompt "{safe}"'
    return f"POSIX path of ({chooser})"


def run_osascript(script: str) -> tuple[int, str, str]:
    """Runs the AppleScript and gives back the code, the output and the error."""
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except OSError as exc:
        return -1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr
