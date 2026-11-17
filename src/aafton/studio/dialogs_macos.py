"""The dialog of macOS, through AppleScript.

The scripts of the dialogs of files and folders of macOS live here, with the
process that runs them.
"""

from __future__ import annotations

import subprocess


def _escape_title(title: str) -> str:
    """Escapes the quotes and the backslashes of the AppleScript string."""
    return str(title).replace("\\", "\\\\").replace('"', '\\"')


def _inicio(folder: str, name: str) -> str:
    """O nome e a pasta que o dialogo de salvar oferece; vazio sem os dois."""
    partes = ""
    if name:
        partes += f' default name "{_escape_title(name)}"'
    if folder:
        partes += f' default location (POSIX file "{_escape_title(folder)}")'
    return partes


def build_script(
    kind: str, title: str, extensions: list[str], folder: str = "", name: str = ""
) -> str:
    """Builds the AppleScript of the dialog by kind, title and extensions.

    The save dialog offers ``name`` in ``folder`` when they are given; without
    them it offers no name. The extensions are kept for the open dialog.
    """
    safe = _escape_title(title)
    if kind == "folder":
        chooser = f'choose folder with prompt "{safe}"'
    elif kind == "save_file":
        chooser = f'choose file name with prompt "{safe}"{_inicio(folder, name)}'
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
