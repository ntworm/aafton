"""Builds the desktop program of aafton with PyInstaller.

The build makes one folder with two programs: the command line (aafton-cli)
and the window (aafton). The script runs PyInstaller from the root of the
repository and then checks that the folder has the two programs and the data
files that they need at run time.

Usage: python scripts/build_desktop.py [--check-only]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The root of the repository, found from the place of this script.
REPO = Path(__file__).resolve().parents[1]

# The spec of the build and the folder that comes out of it.
SPEC = "packaging/aafton.spec"
FOLDER = Path("dist") / "aafton"

# The names of the two programs, without the suffix of the system.
PROGRAMS = ("aafton-cli", "aafton")

# The data files that the programs need at run time, inside the folder.
DATA_FILES = (
    "_internal/aafton/templates/live12.als",
    "_internal/aafton/studio/static/index.html",
)


def expected_files() -> list[str]:
    """The files that the folder of the build must have."""
    suffix = ".exe" if sys.platform == "win32" else ""
    return [name + suffix for name in PROGRAMS] + list(DATA_FILES)


def check_folder(folder: Path) -> int:
    """Checks the folder of the build; gives back 0 when it has everything."""
    missing = [name for name in expected_files() if not (folder / name).is_file()]
    if missing:
        for name in missing:
            print(f"missing in the build: {name}")
        return 1
    print(f"build ok: {folder}")
    return 0


def build(root: Path) -> None:
    """Runs PyInstaller on the spec of the repository."""
    import PyInstaller.__main__

    before = Path.cwd()
    os.chdir(root)
    try:
        PyInstaller.__main__.run(
            [
                SPEC,
                "--noconfirm",
                "--clean",
                "--distpath",
                "dist",
                "--workpath",
                "build/pyinstaller",
            ]
        )
    finally:
        os.chdir(before)


def main(argv: list[str] | None = None) -> int:
    """Builds the desktop program and checks the folder that came out."""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--check-only" not in args:
        build(REPO)
    return check_folder(REPO / FOLDER)


if __name__ == "__main__":
    raise SystemExit(main())
