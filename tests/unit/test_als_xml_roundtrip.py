import gzip
from pathlib import Path

import pytest
from aafton.als_xml import load_als, save_als

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


def test_template_descomprime_como_gzip_e_parseia_como_xml():
    root = load_als(TEMPLATE)
    assert root.tag == "Ableton"
    assert root.get("MinorVersion", "").startswith("12.")


def test_ida_e_volta_preserva_a_contagem_de_elementos(tmp_path):
    root = load_als(TEMPLATE)
    antes = len(list(root.iter()))
    destino = tmp_path / "saida.als"
    save_als(root, destino)
    depois = len(list(load_als(destino).iter()))
    assert depois == antes


def test_arquivo_salvo_e_gzip_de_verdade(tmp_path):
    root = load_als(TEMPLATE)
    destino = tmp_path / "saida.als"
    save_als(root, destino)
    with gzip.open(destino, "rb") as handle:
        cabecalho = handle.read(64)
    assert cabecalho.startswith(b"<?xml")


def test_arquivo_que_nao_e_gzip_falha_com_mensagem_clara(tmp_path):
    ruim = tmp_path / "ruim.als"
    ruim.write_bytes(b"nao sou gzip")
    with pytest.raises(ValueError, match="does not decompress"):
        load_als(ruim)
