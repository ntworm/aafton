# -*- mode: python ; coding: utf-8 -*-
"""Spec of the desktop build of aafton.

The build makes one folder with two programs: the command line (aafton-cli)
and the window (aafton). The folder holds the runtime of the programs, the
data files of the package (the template of the Live set and the static files
of the studio) and the modules that PyInstaller cannot see by itself.

The build runs with:
    python scripts/build_desktop.py
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# The folder of the spec file; the repository is the parent of it. PyInstaller
# gives the folder of the spec, but the code also takes the spec itself.
SPEC_DIR = Path(SPECPATH).resolve()
if SPEC_DIR.suffix == ".spec":
    SPEC_DIR = SPEC_DIR.parent
REPO = SPEC_DIR.parent
SRC = REPO / "src"

# The package has to be importable when the spec runs, to find its data files.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _data_files() -> list[tuple[str, str]]:
    """The data files of the package, at their paths inside the package.

    The studio is a subpackage with files of its own, so it is collected by
    its own name; the entries that come twice keep one copy.
    """
    found: set[tuple[str, str]] = set()
    for package in ("aafton", "aafton.studio"):
        found.update(collect_data_files(package))
    return sorted(found)


def _submodules(package: str) -> list[str]:
    """The submodules of a package; nothing when the package is not there."""
    try:
        return collect_submodules(package)
    except Exception:  # noqa: BLE001 - a package that is not there is not a failure
        return []


def _analysis(entry: Path):
    """The analysis of one program of the folder."""
    return Analysis(
        [str(entry)],
        pathex=[str(SRC)],
        binaries=[],
        datas=DATAS,
        hiddenimports=HIDDEN,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
    )


def _program(pyz, scripts, name: str, console: bool):
    """One program of the folder; the binaries come later, with the COLLECT."""
    return EXE(
        pyz,
        scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        strip=False,
        upx=False,
        console=console,
    )


# The data files of the package: the template of the Live set and the studio.
DATAS = _data_files()

# The modules that the imports inside the code do not show to PyInstaller.
HIDDEN = _submodules("aafton") + _submodules("webview")

cli = _analysis(REPO / "packaging" / "aafton_cli_entry.py")
cli_exe = _program(PYZ(cli.pure), cli.scripts, "aafton-cli", True)

app = _analysis(REPO / "packaging" / "aafton_app_entry.py")
app_exe = _program(PYZ(app.pure), app.scripts, "aafton", False)

# The two programs import the same package with the same hidden imports, so
# the folder holds one copy of the runtime and of the data files: the COLLECT
# takes them from the analysis of the window, which has the most.
folder = COLLECT(
    cli_exe,
    app_exe,
    app.binaries,
    app.datas,
    strip=False,
    upx=False,
    name="aafton",
)

if sys.platform == "darwin":
    # On macOS the folder also travels in an application bundle; the bundle
    # wraps the whole folder, with the runtime, the data files and the
    # programs.
    app_bundle = BUNDLE(
        folder,
        name="aafton.app",
        bundle_identifier="io.github.ntworm.aafton",
    )
