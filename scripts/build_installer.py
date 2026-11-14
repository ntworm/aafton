"""Builds the Windows installer of aafton with Inno Setup 6.

Reads the package version from src/aafton/__init__.py, verifies that the
PyInstaller desktop build exists in dist/aafton/aafton.exe, locates ISCC.exe,
and compiles packaging/windows/aafton.iss into dist/aafton-<version>-windows-setup.exe.

Usage: python scripts/build_installer.py [--iscc PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

# The root of the repository, found from the place of this script.
REPO = Path(__file__).resolve().parents[1]

# Expected PyInstaller window binary and Inno Setup script relative to REPO.
DESKTOP_EXE = Path("dist") / "aafton" / "aafton.exe"
ISS_SCRIPT = Path("packaging") / "windows" / "aafton.iss"
INIT_FILE = Path("src") / "aafton" / "__init__.py"

# System-wide default installation path of Inno Setup 6.
DEFAULT_SYSTEM_ISCC = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
LOCAL_ISCC_REL = Path("Programs") / "Inno Setup 6" / "ISCC.exe"

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def read_version(repo: Path = REPO) -> str:
    """Reads __version__ from src/aafton/__init__.py without importing the package."""
    init_path = repo / INIT_FILE
    text = init_path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if match is None:
        raise ValueError(f"could not find __version__ in {init_path}")
    return match.group(1)


def _iscc_candidates(env: Mapping[str, str]) -> list[Path]:
    """Builds the ordered list of fallback paths where ISCC.exe may live."""
    candidates: list[Path] = []
    env_iscc = env.get("ISCC", "").strip()
    if env_iscc:
        candidates.append(Path(env_iscc))
    local_app_data = env.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / LOCAL_ISCC_REL)
    candidates.append(DEFAULT_SYSTEM_ISCC)
    return candidates


def find_iscc(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Finds ISCC.exe via --iscc, ISCC env var, LOCALAPPDATA, or Program Files (x86)."""
    if explicit is not None:
        path = Path(explicit)
        if path.is_file():
            return path
        raise FileNotFoundError(f"ISCC.exe not found at --iscc path: {path}")

    active_env = os.environ if env is None else env
    for candidate in _iscc_candidates(active_env):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("ISCC.exe not found; pass --iscc, set ISCC, or install Inno Setup 6")


def installer_filename(version: str) -> str:
    """Returns the setup base filename (without .exe) for a given version."""
    return f"aafton-{version}-windows-setup"


def build_command(iscc: Path, version: str, repo: Path = REPO) -> list[str]:
    """Builds the ISCC command line arguments for the Windows installer."""
    output_dir = repo / "dist"
    base_name = installer_filename(version)
    return [
        str(iscc),
        f"/DAppVersion={version}",
        f"/DOutputDir={output_dir}",
        f"/DOutputBaseFilename={base_name}",
        str(repo / ISS_SCRIPT),
    ]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command line arguments for building the Windows installer."""
    parser = argparse.ArgumentParser(description="Build the Windows installer for aafton.")
    parser.add_argument("--iscc", default=None, help="Path to ISCC.exe.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the ISCC command without executing it.",
    )
    return parser.parse_args(argv)


def _run_iscc(command: list[str], repo: Path, expected_exe: Path) -> int:
    """Executes ISCC and checks that the installer executable was produced."""
    result = subprocess.run(command, cwd=repo, check=False)
    if result.returncode != 0:
        return result.returncode
    if not expected_exe.is_file():
        print(f"installer missing after ISCC run: {expected_exe}", file=sys.stderr)
        return 1
    print(f"installer ok: {expected_exe}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Validates the desktop build, resolves ISCC.exe, and runs or prints the command."""
    args = _parse_args(argv)
    version = read_version(REPO)

    exe_path = REPO / DESKTOP_EXE
    if not exe_path.is_file():
        print(
            f"missing desktop build: {exe_path}; run scripts/build_desktop.py first",
            file=sys.stderr,
        )
        return 2

    try:
        iscc = find_iscc(explicit=args.iscc)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    command = build_command(iscc, version, REPO)
    if args.dry_run:
        print(subprocess.list2cmdline(command))
        return 0

    expected_exe = REPO / "dist" / f"{installer_filename(version)}.exe"
    return _run_iscc(command, REPO, expected_exe)


if __name__ == "__main__":
    raise SystemExit(main())
