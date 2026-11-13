"""Explorador de arquivos locais do Studio.

Listagem de pastas e arquivos usada pelo modal web, isolada do modulo HTTP
para manter o servico de arquivos pequeno e facil de testar.
"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Any


def _resolve_dir(path_str: str | None) -> Path:
    """Resolve o diretorio alvo, caindo para a area de trabalho quando necessario."""
    if not path_str or not Path(path_str).exists():
        curr = Path.home() / "Desktop"
        if not curr.is_dir():
            curr = Path.home()
    else:
        curr = Path(path_str)
        if not curr.is_dir():
            curr = curr.parent

    try:
        return curr.resolve()
    except Exception:
        return Path.home()


def _parent_entry(curr: Path) -> str | None:
    """Devolve o diretorio pai do alvo, ou None quando o alvo ja e a raiz."""
    return str(curr.parent) if curr.parent != curr else None


def _drive_list() -> list[str]:
    """Lista as unidades de disco disponiveis no Windows."""
    drives = []
    for letter in string.ascii_uppercase:
        d_path = Path(f"{letter}:\\")
        try:
            if d_path.exists():
                drives.append(str(d_path))
        except Exception:
            pass
    return drives


def _bookmarks() -> dict[str, str]:
    """Marcadores de pastas locais exibidos no explorador de arquivos."""
    return {
        "Desktop": str(Path.home() / "Desktop"),
        "Documents": str(Path.home() / "Documents"),
        "Music": str(Path.home() / "Music"),
        "Downloads": str(Path.home() / "Downloads"),
    }


def _dir_entries(curr: Path, filter_ext: list[str] | None) -> list[dict[str, Any]]:
    """Lista as entradas do diretorio, respeitando o filtro de extensoes e a ordem."""
    items = []
    try:
        for entry in curr.iterdir():
            try:
                is_d = entry.is_dir()
                ext = entry.suffix.lower()
                if is_d or not filter_ext or ext in filter_ext:
                    items.append(
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": is_d,
                            "ext": ext,
                            "size": entry.stat().st_size if not is_d else 0,
                        }
                    )
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items


def get_fs_listing(
    path_str: str | None = None, filter_ext: list[str] | None = None
) -> dict[str, Any]:
    """Lista diretorios e arquivos locais de forma segura e instantanea para o modal web."""
    curr = _resolve_dir(path_str)
    return {
        "current": str(curr),
        "parent": _parent_entry(curr),
        "drives": _drive_list(),
        "bookmarks": _bookmarks(),
        "items": _dir_entries(curr, filter_ext)[:200],
    }
