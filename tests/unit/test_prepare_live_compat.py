"""E2 do R07.6: o pacote de casos para o operador e a medicao da volta.

O operador so ve, por caso, o .als de entrada e a pergunta; o resto fica em
_maquina. A medicao compara o que voltar e deixa pendente o que nao voltou.
Preparar recusa um pacote que ja tem devolucao do operador, e medir recusa
evidencia que nao bate com o manifesto.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import prepare_live_compat as pacote_live  # noqa: E402

from aafton.als_compare import VALOR_DIVERGENTE  # noqa: E402
from aafton.als_xml import load_als  # noqa: E402
from aafton.live_compat_proveniencia import ProvenienciaInvalida, sha256_arquivo  # noqa: E402
from aafton.sidecar import sidecar_path_for  # noqa: E402


@pytest.fixture(scope="module")
def pacote(tmp_path_factory):
    destino = tmp_path_factory.mktemp("pacote")
    pacote_live.preparar(destino, ["C05", "C07"])
    return destino


@pytest.fixture
def pacote_proprio(tmp_path):
    """Pacote de um caso so, para os testes que mexem no pacote."""
    destino = tmp_path / "pacote"
    pacote_live.preparar(destino, ["C05"])
    return destino


def test_padrao_do_pacote_e_o_reteste_e_preparar_exige_pacote_explicito(monkeypatch, capsys):
    """O padrao de medir e out/r077b; preparar exige --pacote e captura MidiaLocalInvalida."""
    assert pacote_live.PADRAO == Path("out/r077b")
    assert pacote_live.PADRAO != Path("out/r077")
    with pytest.raises(SystemExit):
        pacote_live.main(["preparar"])

    def falhar_midia(*_args, **_kwargs):
        raise pacote_live.MidiaLocalInvalida("midia local invalida simulada")

    monkeypatch.setattr(pacote_live, "preparar", falhar_midia)
    assert pacote_live.main(["preparar", "--pacote", "out/qualquer"]) == 1
    assert "midia local invalida simulada" in capsys.readouterr().err


def test_todo_caso_tem_pergunta_de_uma_linha_com_resultado_esperado():
    ids = [caso.id for caso in pacote_live.CASOS]
    assert len(ids) == len(set(ids))
    for caso in pacote_live.CASOS:
        assert "\n" not in caso.pergunta
        assert "Resultado esperado:" in caso.pergunta, caso.id


def test_cada_pasta_de_caso_so_tem_o_als_e_a_pergunta(pacote):
    manifesto = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in manifesto["casos"]] == ["C05", "C07"]
    for caso in manifesto["casos"]:
        pasta = pacote / caso["pasta"]
        assert sorted(p.name for p in pasta.iterdir()) == [
            pacote_live.PERGUNTA,
            pacote_live.ENTRADA,
        ]
        texto = (pasta / pacote_live.PERGUNTA).read_text(encoding="utf-8")
        assert pacote_live.SAIDA in texto and "Resultado esperado:" in texto
        assert caso["saida_esperada"].endswith(pacote_live.SAIDA)
        assert (pacote / caso["aaf"]).is_file()
        assert caso["build_do_gerado"]["MinorVersion"] == "12.0_12402"


def test_caso_sem_volta_fica_pendente_e_nao_e_medido(pacote):
    resultado = pacote_live.medir(pacote, fixtures=False)
    situacoes = {m["id"]: m["situacao"] for m in resultado["casos"]}
    assert all(s.startswith("pendente") for s in situacoes.values())
    assert all("depois" not in m for m in resultado["casos"])
    assert all(m["antes"]["valor"] == 1.0 for m in resultado["casos"])


def test_volta_com_clip_movido_e_medida_e_acusada(pacote):
    manifesto = json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))
    caso = next(c for c in manifesto["casos"] if c["id"] == "C05")
    raiz = load_als(pacote / caso["als_de_entrada"])
    clip = raiz.find(".//AudioClip")
    clip.find("CurrentStart").set("Value", str(float(clip.find("CurrentStart").get("Value")) + 1))
    saida = pacote / caso["saida_esperada"]
    saida.write_bytes(gzip.compress(ET.tostring(raiz, encoding="utf-8")))
    try:
        resultado = pacote_live.medir(pacote, fixtures=False)
    finally:
        saida.unlink()
    medida = next(m for m in resultado["casos"] if m["id"] == "C05")
    assert medida["situacao"] == "medido"
    assert medida["contagem"][VALOR_DIVERGENTE] == 1
    assert medida["depois"]["valor"] < medida["antes"]["valor"]
    assert "C05" in (pacote / "medicao.md").read_text(encoding="utf-8")


def _snapshot(pacote: Path) -> dict[str, str]:
    """Hash de cada arquivo do pacote, para comparar antes e depois."""
    return {
        caminho.relative_to(pacote).as_posix(): sha256_arquivo(caminho)
        for caminho in sorted(pacote.rglob("*"))
        if caminho.is_file()
    }


def test_manifesto_amarra_o_hash_dos_cinco_arquivos(pacote_proprio):
    manifesto = json.loads((pacote_proprio / "manifesto.json").read_text(encoding="utf-8"))
    caso = manifesto["casos"][0]
    assert set(caso["hashes"]) == {"entrada", "als_gerado", "sidecar", "aaf", "relatorio"}
    for campo, relativo in caso["caminhos_proveniente"].items():
        assert (pacote_proprio / relativo).is_file()
        assert caso["hashes"][campo] == sha256_arquivo(pacote_proprio / relativo)
    assert caso["hashes"]["entrada"] == caso["hashes"]["als_gerado"]


def test_preparar_recusa_devolucao_do_operador_e_preserva_o_pacote(tmp_path, capsys):
    """Devolucao em caso que nem entra no rebuild tambem recusa o pacote."""
    destino = tmp_path / "pacote"
    pacote_live.preparar(destino, ["C05", "C07"])
    manifesto = json.loads((destino / "manifesto.json").read_text(encoding="utf-8"))
    caso = next(c for c in manifesto["casos"] if c["id"] == "C05")
    (destino / caso["pasta"] / pacote_live.SAIDA).write_bytes(b"devolucao do operador")
    antes = _snapshot(destino)
    with pytest.raises(pacote_live.PacoteComDevolucao):
        pacote_live.preparar(destino, ["C07"])
    assert _snapshot(destino) == antes
    codigo = pacote_live.main(["preparar", "--pacote", str(destino)])
    assert codigo == 1
    assert "diretorio novo" in capsys.readouterr().err


def test_medir_recusa_arquivo_alterado_e_nao_sobrescreve_a_medicao(pacote_proprio):
    """Evidencia alterada e recusada, e a medicao antiga nao e reescrita."""
    primeiro = pacote_live.medir(pacote_proprio, fixtures=False)
    assert all(m["situacao"].startswith("pendente") for m in primeiro["casos"])
    antigo = (pacote_proprio / "medicao.json").read_bytes()
    manifesto = json.loads((pacote_proprio / "manifesto.json").read_text(encoding="utf-8"))
    caso = manifesto["casos"][0]
    for relativo in caso["caminhos_proveniente"].values():
        caminho = pacote_proprio / relativo
        original = caminho.read_bytes()
        caminho.write_bytes(original + b"\n")
        try:
            with pytest.raises(ProvenienciaInvalida):
                pacote_live.medir(pacote_proprio, fixtures=False)
        finally:
            caminho.write_bytes(original)
        assert (pacote_proprio / "medicao.json").read_bytes() == antigo


def test_medir_recusa_manifesto_antigo_sem_hash(pacote_proprio):
    """Manifesto sem hash nao serve: o caminho e refazer o pacote em outro lugar."""
    caminho = pacote_proprio / "manifesto.json"
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    for caso in manifesto["casos"]:
        caso.pop("hashes")
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote_proprio, fixtures=False)
    assert "new directory" in str(erro.value)
    assert not (pacote_proprio / "medicao.json").exists()


def test_medir_recusa_entrada_que_nao_e_o_gerado(pacote_proprio):
    """A entrada do operador tem de ser o .als gerado, byte a byte."""
    caminho = pacote_proprio / "manifesto.json"
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    caso = manifesto["casos"][0]
    entrada = pacote_proprio / caso["caminhos_proveniente"]["entrada"]
    entrada.write_bytes(b"outro conteudo")
    hashes = dict(caso["hashes"])
    hashes["entrada"] = sha256_arquivo(entrada)
    caso["hashes"] = hashes
    caminho.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote_proprio, fixtures=False)
    assert "not the generated .als" in str(erro.value)
    assert not (pacote_proprio / "medicao.json").exists()


def _manifesto(pacote: Path) -> dict:
    return json.loads((pacote / "manifesto.json").read_text(encoding="utf-8"))


def _gravar(pacote: Path, manifesto: dict) -> None:
    (pacote / "manifesto.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_medir_recusa_entrada_alterada_e_preserva_o_pacote(pacote_proprio):
    """A entrada que a medicao le tem de ser a que o manifesto hashou.

    O manifesto aponta para um arquivo novo, invalido e sem hash: a medicao
    recusa antes de ler, e o pacote fica como estava.
    """
    pacote_live.medir(pacote_proprio, fixtures=False)
    manifesto = _manifesto(pacote_proprio)
    caso = manifesto["casos"][0]
    (pacote_proprio / caso["pasta"] / "entrada-alterada.als").write_bytes(b"bytes invalidos")
    caso["als_de_entrada"] = f"{caso['pasta']}/entrada-alterada.als"
    _gravar(pacote_proprio, manifesto)
    antes = _snapshot(pacote_proprio)
    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote_proprio, fixtures=False)
    assert "als_de_entrada" in str(erro.value)
    assert _snapshot(pacote_proprio) == antes


@pytest.mark.parametrize("alias", ["als_de_entrada", "als_gerado", "aaf"])
def test_medir_recusa_alias_consumido_diferente_do_validado(pacote_proprio, alias):
    """O caminho consumido tem de ser o que o manifesto hashou."""
    manifesto = _manifesto(pacote_proprio)
    caso = manifesto["casos"][0]
    caso[alias] = f"{caso['pasta']}/outro.als"
    _gravar(pacote_proprio, manifesto)
    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote_proprio, fixtures=False)
    assert alias in str(erro.value)
    assert not (pacote_proprio / "medicao.json").exists()


@pytest.mark.parametrize(
    "saida",
    [
        "{pasta}/entrada.als",
        "_maquina/saida_do_live.als",
        "../fora/saida_do_live.als",
        "C:\\fora\\saida_do_live.als",
    ],
)
def test_medir_recusa_saida_esperada_fora_do_contrato(pacote_proprio, saida):
    """A volta do operador tem de ser o arquivo de volta, na pasta do caso.

    A primeira opcao e o contraexemplo do revisor: a saida apontada para a
    entrada marca o caso como medido sem o operador devolver nada.
    """
    manifesto = _manifesto(pacote_proprio)
    caso = manifesto["casos"][0]
    caso["saida_esperada"] = saida.format(pasta=caso["pasta"])
    _gravar(pacote_proprio, manifesto)
    with pytest.raises(ProvenienciaInvalida) as erro:
        pacote_live.medir(pacote_proprio, fixtures=False)
    assert "saida" in str(erro.value)
    assert not (pacote_proprio / "medicao.json").exists()


def test_caso_invalido_depois_nao_deixa_sidecar_do_caso_anterior(tmp_path):
    """A validacao de todos os casos vem antes de qualquer escrita de medicao."""
    destino = tmp_path / "pacote"
    pacote_live.preparar(destino, ["C05", "C07"])
    manifesto = _manifesto(destino)
    c05 = next(c for c in manifesto["casos"] if c["id"] == "C05")
    c07 = next(c for c in manifesto["casos"] if c["id"] == "C07")
    (destino / c05["saida_esperada"]).write_bytes((destino / c05["als_de_entrada"]).read_bytes())
    (destino / c07["pasta"] / "entrada-alterada.als").write_bytes(b"bytes invalidos")
    c07["als_de_entrada"] = f"{c07['pasta']}/entrada-alterada.als"
    _gravar(destino, manifesto)
    with pytest.raises(ProvenienciaInvalida):
        pacote_live.medir(destino, fixtures=False)
    assert not (destino / c05["pasta"] / "saida_do_live.aafton.json").exists()


def test_validar_pacote_devolve_os_caminhos_na_ordem_do_manifesto(pacote):
    """Cada caso medido recebe os caminhos que a validacao conferiu, na ordem."""
    manifesto = _manifesto(pacote)
    validados = pacote_live.validar_pacote(pacote, manifesto)
    assert [caso["id"] for caso in manifesto["casos"]] == ["C05", "C07"]
    assert len(validados) == len(manifesto["casos"])
    for caso, conferido in zip(manifesto["casos"], validados, strict=True):
        bruto = caso["caminhos_proveniente"]["entrada"]
        assert conferido["entrada"] == (pacote / bruto).resolve()


def test_medir_consome_o_arquivo_conferido_e_nao_a_sombra_do_alias(pacote_proprio, monkeypatch):
    """A medicao le os arquivos conferidos, nao os que o caminho cru aponta.

    Contraexemplo do revisor: o caminho cru do manifesto aponta para um alias
    que resolve (link de arquivo) para o .als gerado de verdade. O hash
    confere o arquivo real; a medicao antiga lia o relatorio e copiava o
    sidecar que estavam ao lado do alias, e nenhum dos dois foi conferido.

    Aqui o link e simulado no ``resolve`` (o host do teste nao cria link
    simbolico) e a sombra e envenenada: se a medicao a tocar, o teste quebra.
    """
    manifesto = _manifesto(pacote_proprio)
    caso = manifesto["casos"][0]
    maquina = pacote_proprio / "_maquina" / "C05"
    alias = pacote_proprio / "_maquina" / "alias"
    alias.mkdir()
    (alias / "gerado.als").write_bytes((maquina / "caso.als").read_bytes())
    # A sombra do relatorio nem e UTF-8: se a medicao a ler, quebra na hora.
    (alias / "relatorio.md").write_bytes(b"\xffSOMBRA nao conferida\n")
    (alias / "gerado.aafton.json").write_bytes(b'{"sombra": true}\n')
    caso["als_gerado"] = "_maquina/alias/gerado.als"
    caso["caminhos_proveniente"]["als_gerado"] = "_maquina/alias/gerado.als"
    _gravar(pacote_proprio, manifesto)

    raiz = pacote_proprio.resolve()
    bruto = raiz / "_maquina" / "alias" / "gerado.als"
    real = raiz / "_maquina" / "C05" / "caso.als"
    original = Path.resolve

    def resolve_do_link(self, *args, **kwargs):
        """Simula o link de arquivo: o alias resolve para o .als de verdade."""
        if self == bruto:
            return real
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_do_link)

    arvore = load_als(pacote_proprio / caso["caminhos_proveniente"]["entrada"])
    clip = arvore.find(".//AudioClip")
    inicio = clip.find("CurrentStart")
    inicio.set("Value", str(float(inicio.get("Value")) + 1))
    saida = pacote_proprio / caso["saida_esperada"]
    saida.write_bytes(gzip.compress(ET.tostring(arvore, encoding="utf-8")))

    resultado = pacote_live.medir(pacote_proprio, fixtures=False)
    medida = resultado["casos"][0]
    assert medida["situacao"] == "medido"
    assert medida["antes"]["valor"] == 1.0
    copiado = pacote_proprio / caso["pasta"] / "saida_do_live.aafton.json"
    assert copiado.read_bytes() == (maquina / "caso.aafton.json").read_bytes()


@pytest.mark.parametrize("alvo", ["aaf", "saida"])
def test_medir_nao_trunca_o_arquivo_apontado_pelo_link_do_sidecar(pacote_proprio, alvo):
    """O sidecar da saida nao pode passar por cima do arquivo ligado.

    Contraexemplo do revisor: o sidecar da saida e um link de arquivo para o
    AAF de origem ou para a volta do operador. A copia direta abria o destino
    para escrever e truncava o alvo ligado; a evidencia que o manifesto hashou
    sumia no meio da medicao. A troca atomica troca o nome e nao toca no alvo.

    A volta aqui e sintetica (a entrada copiada): prova que a medicao mediu e
    que a evidencia ficou intacta, nao que o Live rodou.
    """
    manifesto = _manifesto(pacote_proprio)
    caso = manifesto["casos"][0]
    aaf = pacote_proprio / caso["aaf"]
    saida = pacote_proprio / caso["saida_esperada"]
    saida.write_bytes((pacote_proprio / caso["als_de_entrada"]).read_bytes())
    ligado = aaf if alvo == "aaf" else saida
    sidecar_saida = sidecar_path_for(saida)
    os.link(ligado, sidecar_saida)
    antes = (sha256_arquivo(aaf), sha256_arquivo(saida))

    resultado = pacote_live.medir(pacote_proprio, fixtures=False)

    medida = resultado["casos"][0]
    assert medida["situacao"] == "medido"
    assert medida["contagem"][VALOR_DIVERGENTE] == 0
    assert (sha256_arquivo(aaf), sha256_arquivo(saida)) == antes
    gerado = pacote_proprio / caso["caminhos_proveniente"]["sidecar"]
    assert sidecar_saida.read_bytes() == gerado.read_bytes()
    assert not os.path.samefile(sidecar_saida, ligado)
