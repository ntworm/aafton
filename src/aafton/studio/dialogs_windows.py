"""The dialog of Windows, through PowerShell.

The scripts of the dialogs of files and folders of Windows live here, with
the filter strings of the app page and the process that runs them.
"""

from __future__ import annotations

import subprocess

# The filter of the open dialog of each type of file.
OPEN_FILTERS = {
    "aaf": "AAF files (*.aaf)|*.aaf|All files (*.*)|*.*",
    "als": "Ableton Live Sets (*.als)|*.als|All files (*.*)|*.*",
    "edl": "EDL files (*.edl)|*.edl|All files (*.*)|*.*",
    "xml": "FCP 7 XML files (*.xml)|*.xml|All files (*.*)|*.*",
    "fcpxml": "FCPXML files (*.fcpxml)|*.fcpxml|All files (*.*)|*.*",
    "report": "Report files (*.md;*.txt;*.json)|*.md;*.txt;*.json|All files (*.*)|*.*",
    "session": "Session files (*.aaf;*.als;*.ptx)|*.aaf;*.als;*.ptx|AAF files (*.aaf)|*.aaf|Ableton Live Sets (*.als)|*.als|Pro Tools (*.ptx)|*.ptx|All files (*.*)|*.*",
    "source": "Source files (*.aaf;*.edl;*.xml;*.fcpxml)|*.aaf;*.edl;*.xml;*.fcpxml|All files (*.*)|*.*",
    "video": "Video files (*.mov;*.mp4;*.mxf;*.m4v)|*.mov;*.mp4;*.mxf;*.m4v|All files (*.*)|*.*",
    "text": "Text files (*.txt)|*.txt|All files (*.*)|*.*",
}


def _escape_title(title: str) -> str:
    """Escapes the single quotes for a safe use inside the PowerShell script."""
    return str(title).replace("'", "''")


def _save_filter(filter_type: str) -> tuple[str, str]:
    """Chooses the filter and the default extension of the save dialog."""
    if filter_type == "als":
        return "Ableton Live Set (*.als)|*.als", ".als"
    if filter_type == "edl":
        return "EDL (*.edl)|*.edl", ".edl"
    if filter_type == "xml":
        return "FCP 7 XML (*.xml)|*.xml", ".xml"
    if filter_type == "fcpxml":
        return "FCPXML (*.fcpxml)|*.fcpxml", ".fcpxml"
    if filter_type == "report":
        return "Markdown report (*.md)|*.md", ".md"
    return "AAF (*.aaf)|*.aaf|All (*.*)|*.*", ".aaf"


def _open_filter(filter_type: str) -> str:
    """Chooses the filter of the open dialog by the type of the file."""
    return OPEN_FILTERS.get(filter_type, "All files (*.*)|*.*")


def _folder_script(title: str) -> str:
    """Builds the script of the folder picker of Windows."""
    return f"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.FolderBrowserDialog
$f.Description = '{title}'
$f.ShowNewFolderButton = $true
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::WriteLine($f.SelectedPath)
}}
"""


def _save_script(title: str, ext_filter: str, def_ext: str) -> str:
    """Builds the script of the save dialog of a file."""
    return f"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.SaveFileDialog
$f.Title = '{title}'
$f.Filter = '{ext_filter}'
$f.DefaultExt = '{def_ext}'
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::WriteLine($f.FileName)
}}
"""


def _open_script(title: str, ext_filter: str) -> str:
    """Builds the script of the open dialog of a file."""
    return f"""
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.OpenFileDialog
$f.Title = '{title}'
$f.Filter = '{ext_filter}'
if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::WriteLine($f.FileName)
}}
"""


def build_dialog_script(kind: str, filter_type: str, title: str) -> str:
    """Builds the PowerShell script of the native dialog by kind, filter and title."""
    safe_title = _escape_title(title)
    if kind == "folder":
        return _folder_script(safe_title)
    if kind == "save_file":
        ext_filter, def_ext = _save_filter(filter_type)
        return _save_script(safe_title, ext_filter, def_ext)
    return _open_script(safe_title, _open_filter(filter_type))


def run_powershell(script: str) -> str | None:
    """Runs the PowerShell script and gives back what it wrote.

    None when the process failed or timed out, the chosen path when the
    dialog worked and an empty text when the dialog was cancelled.
    """
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()
