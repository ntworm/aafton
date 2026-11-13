"""Tests of the desktop build: the check of the folder and the entry scripts."""

from __future__ import annotations

import runpy
import sys
import types
from pathlib import Path

import pytest

from scripts.build_desktop import check_folder, expected_files

REPO = Path(__file__).resolve().parents[2]
CLI_ENTRY = REPO / "packaging" / "aafton_cli_entry.py"
APP_ENTRY = REPO / "packaging" / "aafton_app_entry.py"
INDEX_HTML = "_internal/aafton/studio/static/index.html"


def _make_folder(folder: Path) -> None:
    """Makes a folder of the build with every file that the check wants."""
    for name in expected_files():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


def _run_entry(path: Path, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Runs an entry script with a fake run_cli and gives back its arguments."""
    calls: list[list[str]] = []

    def fake_run_cli(args: list[str]) -> int:
        calls.append(list(args))
        return 0

    module = types.ModuleType("aafton.cli")
    module.run_cli = fake_run_cli
    monkeypatch.setitem(sys.modules, "aafton.cli", module)
    monkeypatch.setattr(sys, "argv", [str(path), *argv])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(path), run_name="__main__")
    assert exit_info.value.code == 0
    return calls[0]


def test_check_folder_passes_with_every_file(tmp_path: Path) -> None:
    folder = tmp_path / "aafton"
    _make_folder(folder)
    assert check_folder(folder) == 0


def test_check_folder_reports_the_missing_file(tmp_path: Path, capsys) -> None:
    folder = tmp_path / "aafton"
    _make_folder(folder)
    (folder / INDEX_HTML).unlink()
    assert check_folder(folder) == 1
    assert "index.html" in capsys.readouterr().out


def test_cli_entry_calls_run_cli_with_the_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_entry(CLI_ENTRY, ["inspect", "set.aaf"], monkeypatch) == ["inspect", "set.aaf"]


def test_app_entry_calls_run_cli_with_the_app_command(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _run_entry(APP_ENTRY, ["--smoke"], monkeypatch) == ["app", "--smoke"]
