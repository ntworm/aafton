"""Proveniencia do pacote do Live: o hash amarra a evidencia aos insumos.

O hash liga os arquivos que a medicao leu aos arquivos que o manifesto gravou.
Ele nao autentica o Save As do operador nem certifica que o Live rodou: mostra
que os bytes nao mudaram entre a preparacao e a medicao. O hash confere
integridade, nao confia no manifesto: por isso os caminhos tambem sao
conferidos, e nenhum caminho do manifesto pode sair do pacote.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aafton.live_compat_proveniencia import (
    CAMPOS,
    ProvenienciaInvalida,
    hashes_do_caso,
    sha256_arquivo,
    substituir_sidecar_atomico,
    validar_pacote,
)

SHA_DE_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_hash_do_conteudo_conhecido(tmp_path):
    caminho = tmp_path / "a.txt"
    caminho.write_bytes(b"abc")
    assert sha256_arquivo(caminho) == SHA_DE_ABC
    caminho.write_bytes(b"abd")
    assert sha256_arquivo(caminho) != SHA_DE_ABC


def _pacote(tmp_path: Path) -> tuple[Path, dict]:
    """Um pacote de mentira com os cinco arquivos e o manifesto que os amarra.

    O caso sai no formato que a medicao consome: os cinco caminhos com hash e
    os que ela le: entrada, .als gerado, AAF, pasta do operador e saida.
    """
    caminhos = {
        "entrada": "caso/entrada.als",
        "als_gerado": "_maquina/caso.als",
        "sidecar": "_maquina/caso.aafton.json",
        "aaf": "_maquina/origem/fonte.aaf",
        "relatorio": "_maquina/relatorio.md",
    }
    for relativo in caminhos.values():
        arquivo = tmp_path / relativo
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        # A entrada do operador e uma copia do .als gerado: mesmo conteudo.
        conteudo = b"o mesmo als" if relativo.endswith(".als") else relativo.encode("utf-8")
        arquivo.write_bytes(conteudo)
    hashes = hashes_do_caso(tmp_path, caminhos)
    caso = {
        "id": "C05",
        "dialeto": "pyaaf2",
        "pasta": "caso",
        "als_de_entrada": caminhos["entrada"],
        "als_gerado": caminhos["als_gerado"],
        "aaf": caminhos["aaf"],
        "saida_esperada": "caso/saida_do_live.als",
        "caminhos_proveniente": caminhos,
        "hashes": hashes,
    }
    return tmp_path, {"casos": [caso]}


def test_pacote_intacto_passa_na_validacao(tmp_path):
    pacote, manifesto = _pacote(tmp_path)
    assert set(manifesto["casos"][0]["hashes"]) == set(CAMPOS)
    validar_pacote(pacote, manifesto)


def test_arquivo_alterado_ou_faltando_e_recusado(tmp_path):
    """Evidencia que mudou ou sumiu nao serve: o caso e recusado."""
    pacote, manifesto = _pacote(tmp_path)
    (pacote / "caso/entrada.als").write_bytes(b"outro conteudo")
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "changed" in str(erro.value)
    pacote, manifesto = _pacote(tmp_path)
    (pacote / "_maquina/relatorio.md").unlink()
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "missing" in str(erro.value)


def test_manifesto_sem_hash_e_recusado_com_o_caminho_do_diretorio_novo(tmp_path):
    pacote, manifesto = _pacote(tmp_path)
    manifesto["casos"][0].pop("hashes")
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "new directory" in str(erro.value)


def test_entrada_diferente_do_gerado_e_recusada(tmp_path):
    """A entrada do operador tem de ser o .als gerado, byte a byte."""
    pacote, manifesto = _pacote(tmp_path)
    entrada = pacote / "caso/entrada.als"
    entrada.write_bytes(b"outro conteudo")
    hashes = dict(manifesto["casos"][0]["hashes"])
    hashes["entrada"] = sha256_arquivo(entrada)
    manifesto["casos"][0]["hashes"] = hashes
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "not the generated .als" in str(erro.value)


@pytest.mark.parametrize("alias", ["als_de_entrada", "als_gerado", "aaf"])
def test_caminho_consumido_diferente_do_validado_e_recusado(tmp_path, alias):
    """O que a medicao le tem de ser exatamente o que o manifesto hashou."""
    pacote, manifesto = _pacote(tmp_path)
    manifesto["casos"][0][alias] = "caso/outro.als"
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert alias in str(erro.value)


@pytest.mark.parametrize(
    ("campo", "destino"),
    [("relatorio", "_maquina/outro.md"), ("sidecar", "_maquina/outro.aafton.json")],
)
def test_relatorio_e_sidecar_tem_de_vir_do_als_gerado(tmp_path, campo, destino):
    """O relatorio e o sidecar lidos sao os derivados do .als gerado consumido."""
    pacote, manifesto = _pacote(tmp_path)
    caso = manifesto["casos"][0]
    caso["caminhos_proveniente"][campo] = destino
    (pacote / destino).write_bytes(b"outro conteudo")
    caso["hashes"][campo] = sha256_arquivo(pacote / destino)
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert campo in str(erro.value)


@pytest.mark.parametrize("campo", CAMPOS)
def test_caminho_de_proveniencia_fora_do_pacote_e_recusado(tmp_path, campo):
    """Toda evidencia do manifesto tem de morar dentro do pacote."""
    pacote, manifesto = _pacote(tmp_path)
    manifesto["casos"][0]["caminhos_proveniente"][campo] = "../fora/arquivo"
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "package" in str(erro.value)


def test_link_simbolico_para_fora_do_pacote_e_recusado(tmp_path):
    """Um link que sai do pacote e recusado antes do hashing."""
    pacote, manifesto = _pacote(tmp_path)
    fora = tmp_path.parent / "fora.als"
    fora.write_bytes(b"conteudo de fora")
    alvo = pacote / "caso/entrada.als"
    alvo.unlink()
    try:
        alvo.symlink_to(fora)
    except OSError:
        pytest.skip("o host nao cria link simbolico")
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert "package" in str(erro.value)


def _sombra(pacote: Path, caminho: str) -> None:
    """O arquivo que o caminho cru do manifesto aponta, e que nao tem hash."""
    alvo = pacote / f" {caminho} "
    alvo.parent.mkdir(parents=True, exist_ok=True)
    if alvo.suffix:
        alvo.write_bytes(b"conteudo de sombra")


@pytest.mark.parametrize("campo", [*CAMPOS, "als_de_entrada", "saida_esperada", "pasta"])
def test_caminho_com_espaco_no_bordo_e_recusado(tmp_path, campo):
    """O caminho e conferido como a medicao consome: espaco no bordo e recusa."""
    pacote, manifesto = _pacote(tmp_path)
    caso = manifesto["casos"][0]
    alvos = caso["caminhos_proveniente"] if campo in CAMPOS else caso
    caminho = alvos[campo]
    _sombra(pacote, caminho)
    alvos[campo] = f" {caminho} "
    with pytest.raises(ProvenienciaInvalida) as erro:
        validar_pacote(pacote, manifesto)
    assert campo in str(erro.value)
    assert "space" in str(erro.value)


def test_caminho_com_espaco_no_meio_do_nome_e_aceito(tmp_path):
    """Espaco no meio do nome e um nome de arquivo valido: o caminho vale."""
    pacote, manifesto = _pacote(tmp_path)
    caso = manifesto["casos"][0]
    (pacote / "_maquina").rename(pacote / "_maquina com espaco")
    caminhos = {
        campo: caminho.replace("_maquina/", "_maquina com espaco/")
        for campo, caminho in caso["caminhos_proveniente"].items()
    }
    caso["caminhos_proveniente"] = caminhos
    caso["als_gerado"] = caminhos["als_gerado"]
    caso["aaf"] = caminhos["aaf"]
    validar_pacote(pacote, manifesto)


def test_validar_pacote_devolve_os_caminhos_conferidos(tmp_path):
    """A medicao consome o que a validacao conferiu: os caminhos resolvidos."""
    pacote, manifesto = _pacote(tmp_path)
    caso = manifesto["casos"][0]
    validados = validar_pacote(pacote, manifesto)
    assert len(validados) == len(manifesto["casos"])
    conferido = validados[0]
    assert set(conferido) == {*CAMPOS, "saida"}
    for campo in CAMPOS:
        assert conferido[campo] == (pacote / caso["caminhos_proveniente"][campo]).resolve()
    assert conferido["saida"] == (pacote / caso["saida_esperada"]).resolve()


def test_sidecar_substituido_nao_trunca_o_arquivo_ligado(tmp_path):
    """Trocar o sidecar nao abre o destino: o alvo do link fica intacto."""
    origem = tmp_path / "origem.aafton.json"
    origem.write_bytes(b"sidecar gerado")
    alvo = tmp_path / "fonte.aaf"
    alvo.write_bytes(b"bytes do aaf")
    destino = tmp_path / "saida_do_live.aafton.json"
    os.link(alvo, destino)

    substituir_sidecar_atomico(origem, destino)

    assert destino.read_bytes() == b"sidecar gerado"
    assert alvo.read_bytes() == b"bytes do aaf"
    assert not os.path.samefile(destino, alvo)


def test_falha_na_troca_apaga_o_temporario_e_propaga(tmp_path, monkeypatch):
    """A troca que falha nao deixa temporario na pasta e nao vira sucesso."""
    origem = tmp_path / "origem.aafton.json"
    origem.write_bytes(b"sidecar gerado")
    destino = tmp_path / "saida_do_live.aafton.json"

    def troca_recusada(*_args, **_kwargs):
        raise OSError("troca recusada")

    monkeypatch.setattr(os, "replace", troca_recusada)
    with pytest.raises(OSError):
        substituir_sidecar_atomico(origem, destino)
    assert [p.name for p in tmp_path.iterdir()] == ["origem.aafton.json"]
