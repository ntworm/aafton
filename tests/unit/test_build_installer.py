"""Unit tests for scripts/build_installer.py without invoking ISCC.exe."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import build_installer


def _setup_fake_repo(tmp_path: Path, version: str = "0.1.0a1", with_exe: bool = True) -> Path:
    """Creates a minimal fake repository layout under tmp_path."""
    init_file = tmp_path / "src" / "aafton" / "__init__.py"
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text(f'__version__ = "{version}"\n', encoding="utf-8")

    iss_file = tmp_path / "packaging" / "windows" / "aafton.iss"
    iss_file.parent.mkdir(parents=True, exist_ok=True)
    iss_file.write_text("; stub\n", encoding="utf-8")

    if with_exe:
        exe_file = tmp_path / "dist" / "aafton" / "aafton.exe"
        exe_file.parent.mkdir(parents=True, exist_ok=True)
        exe_file.write_text("stub", encoding="utf-8")
    return tmp_path


def _make_fake_iscc(path: Path) -> Path:
    """Creates an empty placeholder file standing in for ISCC.exe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stub", encoding="utf-8")
    return path


def test_read_version_reads_repo_init() -> None:
    assert build_installer.read_version(build_installer.REPO) == "0.1.0a1"


def test_read_version_raises_when_missing(tmp_path: Path) -> None:
    init_file = tmp_path / "src" / "aafton" / "__init__.py"
    init_file.parent.mkdir(parents=True, exist_ok=True)
    init_file.write_text("# empty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="__version__"):
        build_installer.read_version(tmp_path)


def test_find_iscc_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = _make_fake_iscc(tmp_path / "explicit" / "ISCC.exe")
    env_iscc = _make_fake_iscc(tmp_path / "env" / "ISCC.exe")
    local_root = tmp_path / "local"
    local_iscc = _make_fake_iscc(local_root / "Programs" / "Inno Setup 6" / "ISCC.exe")
    system_iscc = _make_fake_iscc(tmp_path / "sys" / "ISCC.exe")

    monkeypatch.setenv("ISCC", str(env_iscc))
    monkeypatch.setenv("LOCALAPPDATA", str(local_root))
    monkeypatch.setattr(build_installer, "DEFAULT_SYSTEM_ISCC", system_iscc)

    # --iscc takes priority over environment and default paths.
    assert build_installer.find_iscc(explicit=str(explicit)) == explicit
    # ISCC env var comes next when --iscc is not provided.
    assert build_installer.find_iscc() == env_iscc

    # LOCALAPPDATA path is used when ISCC env var is absent.
    monkeypatch.delenv("ISCC")
    assert build_installer.find_iscc() == local_iscc

    # System Program Files (x86) path is the final fallback.
    local_iscc.unlink()
    assert build_installer.find_iscc() == system_iscc

    # Missing everywhere raises FileNotFoundError.
    system_iscc.unlink()
    with pytest.raises(FileNotFoundError, match="ISCC.exe"):
        build_installer.find_iscc()


def test_find_iscc_rejects_missing_explicit_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="--iscc"):
        build_installer.find_iscc(explicit=str(tmp_path / "missing.exe"))


def test_main_exits_2_when_desktop_exe_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _setup_fake_repo(tmp_path, with_exe=False)
    monkeypatch.setattr(build_installer, "REPO", repo)

    code = build_installer.main(["--dry-run"])
    assert code == 2
    assert "scripts/build_desktop.py" in capsys.readouterr().err


def test_main_dry_run_prints_command_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _setup_fake_repo(tmp_path, version="1.2.3", with_exe=True)
    fake_iscc = _make_fake_iscc(tmp_path / "inno" / "ISCC.exe")
    monkeypatch.setattr(build_installer, "REPO", repo)

    def fail_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("subprocess.run must not be called in --dry-run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    code = build_installer.main(["--iscc", str(fake_iscc), "--dry-run"])
    assert code == 0

    out = capsys.readouterr().out.strip()
    assert str(fake_iscc) in out
    assert "/DAppVersion=1.2.3" in out
    assert f"/DOutputDir={repo / 'dist'}" in out
    assert "/DOutputBaseFilename=aafton-1.2.3-windows-setup" in out
    assert str(repo / "packaging" / "windows" / "aafton.iss") in out
