"""Testes das falhas fechadas do oraculo de refatoracao."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts import refactor_oracle as ro  # noqa: E402


class _CliFalsa:
    """CLI falsa que registra os argumentos e escreve no stderr."""

    def __init__(self) -> None:
        """Guarda os argumentos recebidos."""
        self.chamadas: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        """Registra a chamada e escreve uma mensagem no stderr."""
        self.chamadas.append(list(argv))
        print("aviso do teste", file=sys.stderr)
        return 0


def test_run_command_registra_o_stderr(tmp_path: Path) -> None:
    """O que a CLI escreve no stderr fica registrado no comando."""

    def cli(argv: list[str]) -> int:
        """Escreve no stderr e devolve um codigo de falha."""
        print("erro: conversao interrompida", file=sys.stderr)
        return 1

    registro = ro.run_command(cli, ["import", "entrada.aaf"], tmp_path)
    assert registro["stderr"].strip() == "erro: conversao interrompida"
    assert registro["exit_code"] == 1


def test_run_command_normaliza_o_caminho_do_caso(tmp_path: Path) -> None:
    """O caminho do caso no stderr vira um marcador estavel."""

    def cli(argv: list[str]) -> int:
        """Escreve no stderr o caminho do caso."""
        print(f"erro em {Path.cwd()}", file=sys.stderr)
        return 1

    registro = ro.run_command(cli, ["import", "entrada.aaf"], tmp_path)
    assert "<CAMINHO>" in registro["stderr"]
    assert str(tmp_path) not in registro["stderr"]


def _roda_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Roda o worker com uma CLI falsa e devolve a pasta de trabalho."""
    monkeypatch.setattr(ro, "entradas", lambda: [("exemplo", tmp_path / "entrada.aaf")])
    trabalho = tmp_path / "work"
    trabalho.mkdir()
    ro.worker_casos(trabalho, _CliFalsa(), tmp_path / "template.als")
    return trabalho


def test_worker_cria_a_pasta_estrita(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O worker cria a pasta pair-absorb-strict com import e export sem --permissive."""
    trabalho = _roda_worker(tmp_path, monkeypatch)
    estrita = trabalho / "exemplo" / "pair-absorb-strict"
    assert estrita.is_dir()
    registro = json.loads((estrita / "commands.json").read_text(encoding="utf-8"))
    assert "--permissive" not in registro[0]["argv"]
    assert "--permissive" not in registro[2]["argv"]
    permissiva = json.loads(
        (trabalho / "exemplo" / "pair-absorb" / "commands.json").read_text(encoding="utf-8")
    )
    assert "--permissive" in permissiva[0]["argv"]


def test_worker_registra_o_stderr_do_comando_estrito(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parada estrita fica registrada como resultado normal, com codigo e stderr."""
    trabalho = _roda_worker(tmp_path, monkeypatch)
    estrita = trabalho / "exemplo" / "pair-absorb-strict"
    registro = json.loads((estrita / "commands.json").read_text(encoding="utf-8"))
    assert registro[0]["stderr"].strip() == "aviso do teste"
    assert registro[0]["exception"] == ""
    assert registro[0]["exit_code"] == 0


def _sem_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deixa o oraculo sem o corpus preservado e sem o flag que o desliga."""
    # O setenv antes do delenv faz o pytest devolver a variavel ao ambiente
    # depois do teste: o oraculo liga o flag sozinho e nao pode vazar.
    monkeypatch.setenv(ro.SEM_CORPUS_ENV, "")
    monkeypatch.delenv(ro.CORPUS_ENV, raising=False)
    monkeypatch.delenv(ro.SEM_CORPUS_ENV, raising=False)


def _corpus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Cria uma pasta de corpus com um AAF e aponta a variavel de ambiente para ela."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "final24.aaf").write_bytes(b"")
    monkeypatch.setenv(ro.CORPUS_ENV, str(corpus))
    return corpus


def test_against_sem_o_corpus_para_com_codigo_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sem o corpus preservado, o --against para com codigo 2."""
    _sem_corpus(monkeypatch)
    monkeypatch.setattr(ro, "_git_worktree", lambda args: 0)
    assert ro.cmd_against(["--against", "abc123"]) == 2
    assert ro.CORPUS_ENV in capsys.readouterr().out


def test_against_com_corpus_que_nao_e_pasta_para(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Com o corpus apontando para um arquivo, o --against para com codigo 2."""
    arquivo = tmp_path / "corpus.txt"
    arquivo.write_text("x", encoding="utf-8")
    monkeypatch.setenv(ro.CORPUS_ENV, str(arquivo))
    monkeypatch.setattr(ro, "_git_worktree", lambda args: 0)
    assert ro.cmd_against(["--against", "abc123"]) == 2
    assert ro.CORPUS_ENV in capsys.readouterr().out


def test_against_com_sem_corpus_avisa_e_continua(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Com --sem-corpus, o --against avisa e continua so com as fixtures."""
    _sem_corpus(monkeypatch)
    monkeypatch.setattr(ro, "_git_worktree", lambda args: 0)
    monkeypatch.setattr(ro, "snapshot", lambda src, label, out: None)
    assert ro.cmd_against(["--against", "abc123", "--out", str(tmp_path), "--sem-corpus"]) == 0
    assert "aviso" in capsys.readouterr().out


def test_against_com_o_corpus_valido_continua(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Com o corpus preservado em uma pasta, o --against continua."""
    _corpus(monkeypatch, tmp_path)
    monkeypatch.delenv(ro.SEM_CORPUS_ENV, raising=False)
    monkeypatch.setattr(ro, "_git_worktree", lambda args: 0)
    monkeypatch.setattr(ro, "snapshot", lambda src, label, out: None)
    assert ro.cmd_against(["--against", "abc123", "--out", str(tmp_path)]) == 0


def _instantaneo(raiz: Path, nome: str, conteudo: str) -> None:
    """Grava um instantaneo com um arquivo de texto e o seu manifesto."""
    pasta = raiz / nome
    pasta.mkdir(parents=True)
    (pasta / "studio-get-root.txt").write_text(conteudo, encoding="utf-8")
    manifesto = {"studio-get-root.txt": conteudo}
    (pasta / "manifest.json").write_text(json.dumps(manifesto), encoding="utf-8")


def test_against_compara_os_instantaneos_da_pasta_que_o_pedido_nomeia(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """O --against le os instantaneos da pasta do --out, e nao de outra."""
    _sem_corpus(monkeypatch)
    monkeypatch.setattr(ro, "_git_worktree", lambda args: 0)
    monkeypatch.setattr(ro, "snapshot", lambda src, label, out: None)

    destino = tmp_path / "out"
    _instantaneo(destino, "base", '<html lang="pt-BR">')
    _instantaneo(destino, "tree", '<html lang="en">')
    assert ro.cmd_against(["--against", "abc123", "--out", str(destino), "--sem-corpus"]) == 1

    vazio = tmp_path / "vazio"
    vazio.mkdir()
    assert ro.cmd_against(["--against", "abc123", "--out", str(vazio), "--sem-corpus"]) == 0


def test_snapshot_sem_o_corpus_para_com_codigo_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem o corpus preservado, o snapshot para com codigo 2."""
    _sem_corpus(monkeypatch)
    assert ro.cmd_snapshot(["--src", "arvore", "--label", "base"]) == 2


def test_corpus_preservado_entra_nos_casos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Com o corpus preservado, os AAF dele entram como casos."""
    _corpus(monkeypatch, tmp_path)
    monkeypatch.delenv(ro.SEM_CORPUS_ENV, raising=False)
    nomes = [nome for nome, _ in ro.entradas()]
    assert "corpus-final24" in nomes


def test_sem_corpus_tira_o_corpus_dos_casos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Com o flag --sem-corpus, os AAF do corpus nao entram como casos."""
    _corpus(monkeypatch, tmp_path)
    monkeypatch.setenv(ro.SEM_CORPUS_ENV, "1")
    nomes = [nome for nome, _ in ro.entradas()]
    assert "corpus-final24" not in nomes
