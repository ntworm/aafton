"""Testes das correcoes do verify quando a origem e uma EDL.

A midia importada pode estar em uma pasta diferente da pasta da EDL: o
verify precisa achar o arquivo pelo caminho que o sidecar do .als gravou.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aafton.cli import main
from tests.unit.test_formats_import import _wav

pytest.importorskip("opentimelineio")

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "formatos"
TEMPLATE = REPO / "templates" / "live12.als"

EDL = "cena12.edl"
# Timecode de arquivo de dial01.wav: 10:00:00:00 a 25 fps.
TC_ARQUIVO = 1728000000


def _cena(tmp_path: Path) -> tuple[Path, Path]:
    """Pasta da EDL e pasta da midia, separadas uma da outra."""
    edl = tmp_path / "edl"
    midia = tmp_path / "midia"
    edl.mkdir()
    midia.mkdir()
    shutil.copy(FIXTURES / EDL, edl / EDL)
    _wav(midia / "dial01.wav", 20, TC_ARQUIVO)
    _wav(midia / "dial02.wav", 10, 0)
    return edl, midia


def test_verify_do_edl_encontra_a_midia_fora_da_pasta_da_edl(tmp_path):
    """Com a midia em outra pasta, o verify acha o arquivo e passa."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"

    importacao = [
        "import",
        str(edl / EDL),
        str(als),
        "--template",
        str(TEMPLATE),
        "--edl-rate",
        "25",
        "--audio-dir",
        str(midia),
    ]

    assert main(importacao) == 0
    assert main(["verify", str(edl / EDL), str(als)]) == 0
