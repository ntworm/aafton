"""Controles da busca de midia do verify quando o sidecar nao serve.

O sidecar do .als registra o caminho absoluto de cada arquivo de midia. Quando
o arquivo some desse caminho, ou quando a chave do sidecar e relativa, o
verify procura a midia pelas pastas: as que o sidecar registra e a da origem.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from aafton.cli import main
from tests.unit.test_acomp_verify_midia import ARQUIVO, EDL, OUTRO, _cena, _importar


def _sidecar(als: Path) -> Path:
    """Caminho do sidecar JSON que o import gravou ao lado do .als."""
    return als.with_name(als.stem + ".aafton.json")


def _mover_a_midia(edl: Path, midia: Path) -> None:
    """Move os arquivos de midia da pasta de importacao para a pasta da EDL."""
    for nome in (ARQUIVO, OUTRO):
        shutil.move(str(midia / nome), str(edl / nome))


def _chaves_relativas(als: Path, pasta: Path) -> None:
    """Troca as chaves absolutas do sidecar por caminhos relativos."""
    caminho = _sidecar(als)
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    hashes = dados.get("media_hashes") or {}
    dados["media_hashes"] = {
        (str(pasta / Path(chave).name) if Path(chave).is_absolute() else chave): valor
        for chave, valor in hashes.items()
    }
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def test_verify_acha_a_midia_na_pasta_da_edl_quando_o_caminho_do_sidecar_some(tmp_path):
    """Sem o arquivo no caminho do sidecar, o verify procura pela pasta."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"
    assert _importar(edl / EDL, als, str(midia)) == 0
    _mover_a_midia(edl, midia)

    assert main(["verify", str(edl / EDL), str(als)]) == 0


def test_verify_com_a_chave_relativa_no_sidecar_procura_pela_pasta(tmp_path):
    """Uma chave relativa no sidecar nao entra na busca: vale a pasta da origem."""
    edl, midia = _cena(tmp_path)
    als = tmp_path / "cena.als"
    assert _importar(edl / EDL, als, str(midia)) == 0
    _mover_a_midia(edl, midia)
    _chaves_relativas(als, Path("midia"))

    assert main(["verify", str(edl / EDL), str(als)]) == 0
