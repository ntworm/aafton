"""Testes da procura de midia do verify quando a origem e uma EDL.

A pasta que o sidecar do .als registra vem antes da pasta da origem: uma
copia do mesmo nome na pasta da EDL nao pode fazer o verify reprovar por
ambiguidade. E o sidecar guarda o caminho absoluto do arquivo, para que um
--audio-dir relativo ainda sirva ao verify.
"""

from __future__ import annotations

import json
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
ARQUIVO = "dial01.wav"
OUTRO = "dial02.wav"


def _cena(tmp_path: Path) -> tuple[Path, Path]:
    """Pasta da EDL e pasta da midia, separadas uma da outra."""
    edl = tmp_path / "edl"
    midia = tmp_path / "midia"
    edl.mkdir()
    midia.mkdir()
    shutil.copy(FIXTURES / EDL, edl / EDL)
    _wav(midia / ARQUIVO, 20, TC_ARQUIVO)
    _wav(midia / OUTRO, 10, 0)
    return edl, midia


def _importar(origem: Path, saida: Path, audio_dir: str) -> int:
    """Importa a EDL da origem dada, com a pasta de midia pedida."""
    return main(
        [
            "import",
            str(origem),
            str(saida),
            "--template",
            str(TEMPLATE),
            "--edl-rate",
            "25",
            "--audio-dir",
            audio_dir,
        ]
    )


def _hashes_do_sidecar(als: Path) -> dict[str, str]:
    """Hashes de midia que o sidecar do .als registra."""
    sidecar = als.with_name(als.stem + ".aafton.json")
    dados = json.loads(sidecar.read_text(encoding="utf-8"))
    return dados.get("media_hashes") or {}


def test_verify_usa_a_pasta_do_sidecar_antes_da_pasta_da_edl(tmp_path):
    """Uma copia do mesmo nome na pasta da EDL nao reprova o verify."""
    edl, midia = _cena(tmp_path)
    copia = edl / "copia"
    copia.mkdir()
    shutil.copy(midia / ARQUIVO, copia / ARQUIVO)
    shutil.copy(midia / OUTRO, copia / OUTRO)
    als = tmp_path / "cena.als"

    assert _importar(edl / EDL, als, str(midia)) == 0
    assert main(["verify", str(edl / EDL), str(als)]) == 0


def test_verify_com_audio_dir_relativo_guarda_o_caminho_absoluto(tmp_path, monkeypatch):
    """Com --audio-dir relativo, o sidecar guarda o caminho absoluto."""
    monkeypatch.chdir(tmp_path)
    _cena(tmp_path)
    als = tmp_path / "cena.als"

    assert _importar(Path("edl") / EDL, Path("cena.als"), "midia") == 0

    hashes = _hashes_do_sidecar(als)
    assert any(Path(chave).is_absolute() for chave in hashes)
    esperados = {Path(chave).resolve() for chave in hashes}
    assert (tmp_path / "midia" / ARQUIVO).resolve() in esperados
    assert main(["verify", str(Path("edl") / EDL), str(Path("cena.als"))]) == 0
