"""Ressalva da verificacao: a midia que sai das pastas de busca.

O verify procura a midia pelas pastas que o sidecar do .als registra e pela
pasta do arquivo de origem. Quando o arquivo de midia sai de todas elas, a
verificacao reprova e o relatorio nomeia a midia que sumiu.
"""

from __future__ import annotations

import shutil

from aafton.cli import main
from tests.unit.test_acomp_verify_midia import ARQUIVO, EDL, OUTRO, _cena, _importar


def test_verify_reprova_quando_a_midia_sai_de_todas_as_pastas(tmp_path, capsys):
    """C3: a midia fora da EDL e das pastas do sidecar reprova o verify."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"
    assert _importar(edl / EDL, als, str(midia)) == 0

    terceira = tmp_path / "terceira"
    terceira.mkdir()
    for nome in (ARQUIVO, OUTRO):
        shutil.move(str(midia / nome), str(terceira / nome))

    assert main(["verify", str(edl / EDL), str(als)]) == 1
    assert "MEDIA_MISSING" in capsys.readouterr().out
