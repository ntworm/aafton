"""Testes unitarios para --allow-skip-prefix em scripts/check_suite_no_skips.py."""

from __future__ import annotations

from scripts.check_suite_no_skips import (
    extrair_razao,
    motivo_aceito,
    parse_allow_skip_args,
)


def test_motivo_aceito_com_prefixo_valido() -> None:
    prefixos = ["not distributed:"]

    motivo_completo = "tests/test_x.py:12: Skipped: not distributed: teste_novo (real media)"
    assert motivo_aceito(motivo_completo, prefixos)

    motivo_sem_linha = "Skipped: not distributed: teste_novo"
    assert motivo_aceito(motivo_sem_linha, prefixos)

    motivo_bruto = "not distributed: teste_novo"
    assert motivo_aceito(motivo_bruto, prefixos)


def test_motivo_rejeitado_quando_prefixo_diverge() -> None:
    prefixos = ["not distributed:"]

    assert not motivo_aceito("midia externa ausente", prefixos)
    assert not motivo_aceito("tests/test_x.py:12: Skipped: midia externa ausente", prefixos)
    assert not motivo_aceito("outro motivo qualquer", prefixos)
    assert not motivo_aceito("", prefixos)


def test_motivo_rejeitado_sem_prefixos_configurados() -> None:
    assert not motivo_aceito("not distributed: teste_novo", [])
    assert not motivo_aceito("tests/test_x.py:1: Skipped: not distributed: x", ())


def test_parse_allow_skip_args_remove_flag_dos_extras() -> None:
    brutos = [
        "--allow-skip-prefix",
        "not distributed:",
        "-q",
        "tests/unit/test_x.py",
        "-v",
    ]
    prefixos, extras = parse_allow_skip_args(brutos)

    assert prefixos == ["not distributed:"]
    assert extras == ["-q", "tests/unit/test_x.py", "-v"]
    assert "--allow-skip-prefix" not in extras
    assert "not distributed:" not in extras


def test_parse_allow_skip_args_sintaxe_com_igual() -> None:
    brutos = ["--allow-skip-prefix=not distributed:", "-k", "test_algo"]
    prefixos, extras = parse_allow_skip_args(brutos)

    assert prefixos == ["not distributed:"]
    assert extras == ["-k", "test_algo"]


def test_parse_allow_skip_args_repetidos() -> None:
    brutos = [
        "--allow-skip-prefix",
        "not distributed:",
        "tests/test_a.py",
        "--allow-skip-prefix",
        "custom prefix:",
        "--allow-skip-prefix=outro:",
    ]
    prefixos, extras = parse_allow_skip_args(brutos)

    assert prefixos == ["not distributed:", "custom prefix:", "outro:"]
    assert extras == ["tests/test_a.py"]


def test_extrair_razao() -> None:
    assert extrair_razao("caminho:10: Skipped: razao aqui") == "razao aqui"
    assert extrair_razao("Skipped: apenas razao") == "apenas razao"
    assert extrair_razao("razao pura") == "razao pura"
