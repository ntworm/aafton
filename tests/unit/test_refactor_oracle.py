"""Testes do oraculo de refatoracao."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import refactor_oracle as ro  # noqa: E402

ARQUIVOS = {
    "comandos.json": "[]\n",
    "sessao/session.als.xml": "<live-set>\n  <track name='x'/>\n</live-set>\n",
    "sessao/back-aaf-dump.txt": "header.Length=100\n",
}


class _PropFalsa:
    """Propriedade falsa de um objeto AAF, com nome e valor."""

    def __init__(self, nome: str, valor: object) -> None:
        """Guarda o nome e o valor da propriedade."""
        self.name = nome
        self.value = valor


class _ObjFalso:
    """Objeto falso que expoe uma lista de propriedades."""

    def __init__(self, propriedades: list[_PropFalsa]) -> None:
        """Guarda as propriedades do objeto."""
        self._propriedades = propriedades

    def properties(self) -> list[_PropFalsa]:
        """Devolve as propriedades do objeto."""
        return self._propriedades


def _cli_main_falso(_argv: list[str]) -> int:
    """Finge a CLI e devolve um codigo de saida de falha."""
    return 1


def _cria_snapshot(destino: Path, arquivos: dict[str, str]) -> Path:
    """Cria um diretorio de instantaneo com arquivos e manifesto."""
    destino.mkdir(parents=True, exist_ok=True)
    for nome, conteudo in arquivos.items():
        caminho = destino / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_text(conteudo, encoding="utf-8")
    ro.write_manifest(destino)
    return destino


def test_instantaneos_iguais_comparam_iguais(tmp_path: Path) -> None:
    """Dois instantaneos identicos nao produzem diferencas."""
    a = _cria_snapshot(tmp_path / "a", dict(ARQUIVOS))
    b = _cria_snapshot(tmp_path / "b", dict(ARQUIVOS))
    assert ro.compare(a, b) == []
    assert ro.main(["compare", str(a), str(b)]) == 0


def test_byte_alterado_mostra_arquivo_e_linha(tmp_path: Path) -> None:
    """Uma mudanca de byte aponta o arquivo e a linha afetados."""
    a = _cria_snapshot(tmp_path / "a", dict(ARQUIVOS))
    outros = dict(ARQUIVOS)
    outros["sessao/session.als.xml"] = "<live-set>\n  <track name='y'/>\n</live-set>\n"
    b = _cria_snapshot(tmp_path / "b", outros)
    diferencas = ro.compare(a, b)
    assert len(diferencas) == 1
    assert "session.als.xml" in diferencas[0]
    assert ":2:" in diferencas[0]
    assert ro.main(["compare", str(a), str(b)]) == 1


def test_arquivo_faltante_e_reportado(tmp_path: Path) -> None:
    """Um arquivo que falta em um lado aparece na lista de diferencas."""
    a = _cria_snapshot(tmp_path / "a", dict(ARQUIVOS))
    b = _cria_snapshot(tmp_path / "b", {"comandos.json": "[]\n"})
    diferencas = ro.compare(a, b)
    assert len(diferencas) == 2
    assert all("faltando em" in diferenca for diferenca in diferencas)
    assert any("sessao/session.als.xml" in diferenca for diferenca in diferencas)


def test_normalizacao_apaga_mobid_data_e_auid() -> None:
    """MobID, SourceID, CreationTime e GenerationAUID nao geram diferencas."""
    base = [
        "header.MobID='uuid-1'",
        "header.SourceID='uuid-1'",
        "header.CreationTime='2024-01-01 00:00:00'",
        "header.LastModified='2024-01-01 00:00:00'",
        "header.GenerationAUID='aaaa-bbbb'",
        "header.Length=100",
    ]
    outro = [
        "header.MobID='uuid-9'",
        "header.SourceID='uuid-9'",
        "header.CreationTime='2030-12-31 23:59:59'",
        "header.LastModified='2030-12-31 23:59:59'",
        "header.GenerationAUID='cccc-dddd'",
        "header.Length=100",
    ]
    assert ro.normalize_dump_lines(base) == ro.normalize_dump_lines(outro)


def test_normalizacao_nao_altera_propriedade_comum() -> None:
    """Propriedades sem normalizacao continuam diferentes."""
    base = ["header.Length=100"]
    outro = ["header.Length=200"]
    assert ro.normalize_dump_lines(base) != ro.normalize_dump_lines(outro)


def test_normalizacao_tira_o_fps_do_locator_tc() -> None:
    """O sufixo de fps sai do nome do locator TC."""
    xml = '<Name Value="TC 00:59:37:12 @ 48000 @ 23.976" />'
    esperado = '<Name Value="TC 00:59:37:12 @ 48000" />'
    assert ro.normalize_locator_tc(xml) == esperado


def test_normalizacao_preserva_outros_nomes() -> None:
    """Nomes fora do padrao do locator TC voltam iguais."""
    sem_fps = '<Name Value="TC 00:59:37:12 @ 48000" />'
    outro = '<Name Value="DX1 @ 48000 @ 24" />'
    assert ro.normalize_locator_tc(sem_fps) == sem_fps
    assert ro.normalize_locator_tc(outro) == outro


def test_run_command_registra_codigo_de_saida(tmp_path: Path) -> None:
    """O codigo de saida devolvido pela CLI fica registrado no comando."""
    registro = ro.run_command(_cli_main_falso, ["inspect", "entrada.aaf"], tmp_path)
    assert registro["exit_code"] == 1


def test_dump_marca_profundidade_maxima(monkeypatch: pytest.MonkeyPatch) -> None:
    """O dump sinaliza o corte da recursao pelo limite de profundidade."""
    monkeypatch.setattr(ro, "LIMITE_PROFUNDIDADE", 1)
    folha = _ObjFalso([_PropFalsa("Nome", "folha")])
    raiz = _ObjFalso([_PropFalsa("Nome", "raiz"), _PropFalsa("Filho", folha)])
    coletor = ro._Coletor()
    coletor.dump(raiz, "header")
    dump = "\n".join(ro.normalize_dump_lines(coletor.linhas)) + "\n"
    assert "<PROFUNDIDADE MAXIMA>" in dump


def test_against_recebe_o_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    """O subcomando --against repassa o SHA para o git worktree add."""
    chamadas: list[list[str]] = []
    monkeypatch.setattr(ro, "_git_worktree", lambda args: chamadas.append(args) or 1)
    assert ro.main(["--against", "abc123"]) == 2
    assert any(args[0] == "add" and "abc123" in args for args in chamadas)
