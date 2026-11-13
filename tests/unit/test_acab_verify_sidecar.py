"""Verify com a copia da midia do lado do arquivo de origem.

O sidecar do .als registra o caminho absoluto de cada arquivo de midia: uma
copia do mesmo nome na pasta da origem nao pode fazer o verify reprovar por
ambiguidade. Sem o caminho que o sidecar registra, a busca e a de sempre.
"""

from __future__ import annotations

import shutil

from aafton.cli import main
from tests.unit.test_acomp_verify_midia import ARQUIVO, EDL, _cena, _importar


def test_verify_usa_o_caminho_do_sidecar_com_a_copia_na_pasta_da_edl(tmp_path):
    """Uma copia do mesmo nome na pasta da EDL nao reprova o verify."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"
    assert _importar(edl / EDL, als, str(midia)) == 0
    shutil.copy(midia / ARQUIVO, edl / ARQUIVO)

    assert main(["verify", str(edl / EDL), str(als)]) == 0


def test_verify_sem_o_sidecar_continua_reprovando(tmp_path):
    """Sem o sidecar, a origem nao tem taxa de frames e o verify nao aprova."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"
    assert _importar(edl / EDL, als, str(midia)) == 0
    shutil.copy(midia / ARQUIVO, edl / ARQUIVO)
    als.with_name(als.stem + ".aafton.json").unlink()

    assert main(["verify", str(edl / EDL), str(als)]) != 0
