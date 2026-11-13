"""Roda a suite e reprova se algum teste falhar ou pular.

Skips por midia externa ja esconderam perda de clips:
16 testes pularam em silencio enquanto a suite dizia "passed". Este script
conta o resultado de cada teste por um plugin do pytest e sai 1 se houver
falha, erro, pulo ou xfail, ou se nenhum teste passar.

Rode a partir da raiz do repositorio com PYTHONPATH=src. O script confere que
`aafton` vem do src deste checkout: o .venv do checkout principal tem
instalacao editavel e, num worktree, testaria o codigo errado.

Uso:
    python scripts/check_suite_no_skips.py [--allow-skip-prefix TEXT] [argumentos extras do pytest]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ARGS_PADRAO = [
    "-o",
    "addopts=",
    "-q",
    "-p",
    "no:cacheprovider",
    "--rootdir",
    str(REPO),
    "--confcutdir",
    str(REPO),
]


class ContadorDeResultados:
    """Plugin do pytest que guarda o desfecho de cada teste e coleta."""

    def __init__(self, allow_skip_prefixes: tuple[str, ...] | list[str] = ()) -> None:
        self.allow_skip_prefixes = tuple(allow_skip_prefixes)
        self.passed: list[str] = []
        self.failed: list[str] = []
        self.errors: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self.accepted: list[tuple[str, str]] = []
        self.xfailed: list[str] = []

    def _registrar_pulo(self, nodeid: str, motivo: str) -> None:
        if motivo_aceito(motivo, self.allow_skip_prefixes):
            self.accepted.append((nodeid, motivo))
        else:
            self.skipped.append((nodeid, motivo))

    def pytest_runtest_logreport(self, report) -> None:
        if hasattr(report, "wasxfail"):
            if report.when == "call" or report.skipped:
                self.xfailed.append(report.nodeid)
            return
        if report.failed:
            destino = self.failed if report.when == "call" else self.errors
            destino.append(f"{report.nodeid} ({report.when})")
        elif report.skipped:
            self._registrar_pulo(report.nodeid, _motivo(report))
        elif report.when == "call" and report.passed:
            self.passed.append(report.nodeid)

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.errors.append(f"{report.nodeid or '<sessao>'} (coleta)")
        elif report.skipped:
            self._registrar_pulo(report.nodeid, _motivo(report))

    def resumo(self) -> str:
        total_skipped = len(self.skipped) + len(self.accepted)
        return (
            f"passed={len(self.passed)} failed={len(self.failed)} "
            f"errors={len(self.errors)} skipped={total_skipped} xfailed={len(self.xfailed)}"
        )

    def aprovado(self) -> bool:
        return bool(self.passed) and not (
            self.failed or self.errors or self.skipped or self.xfailed
        )


def _motivo(report) -> str:
    longrepr = report.longrepr
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return f"{longrepr[0]}:{longrepr[1]}: {longrepr[2]}"
    return str(longrepr)


def extrair_razao(motivo: str) -> str:
    """Extrai a razao de um motivo (texto apos 'Skipped: ' ou o motivo bruto)."""
    marcador = "Skipped: "
    if marcador in motivo:
        return motivo.split(marcador, 1)[1]
    return motivo


def motivo_aceito(motivo: str, prefixos: tuple[str, ...] | list[str]) -> bool:
    """Verifica se o motivo (ou razao extraida) comeca com algum prefixo aceito."""
    if not prefixos:
        return False
    razao = extrair_razao(motivo)
    return any(razao.startswith(p) or motivo.startswith(p) for p in prefixos)


def parse_allow_skip_args(args: list[str]) -> tuple[list[str], list[str]]:
    """Separa --allow-skip-prefix TEXT dos argumentos repassados ao pytest."""
    prefixes: list[str] = []
    pytest_args: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--allow-skip-prefix":
            if i + 1 < len(args):
                prefixes.append(args[i + 1])
                i += 2
            else:
                i += 1
        elif arg.startswith("--allow-skip-prefix="):
            prefixes.append(arg.split("=", 1)[1])
            i += 1
        else:
            pytest_args.append(arg)
            i += 1
    return prefixes, pytest_args


def _pacote_fora_do_checkout() -> str | None:
    try:
        import aafton
    except ImportError as erro:
        return f"aafton nao importa ({erro}); rode com PYTHONPATH=src a partir de {REPO}"
    origem = Path(aafton.__file__).resolve()
    src = (REPO / "src").resolve()
    if src not in origem.parents:
        return (
            f"aafton vem de {origem}, fora de {src}; rode com PYTHONPATH=src "
            f"a partir de {REPO} para testar este checkout"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    brutos = list(sys.argv[1:] if argv is None else argv)
    prefixes, extras = parse_allow_skip_args(brutos)
    problema = _pacote_fora_do_checkout()
    if problema:
        print(f"check_suite_no_skips: {problema}", file=sys.stderr)
        return 1

    contador = ContadorDeResultados(allow_skip_prefixes=prefixes)
    codigo = pytest.main(ARGS_PADRAO + extras, plugins=[contador])

    print()
    print(f"check_suite_no_skips: {contador.resumo()} (pytest exit {int(codigo)})")
    for nodeid, motivo in contador.accepted:
        print(f"  ACEITO {nodeid}: {motivo}")
    for nodeid, motivo in contador.skipped:
        print(f"  PULOU {nodeid}: {motivo}")
    for nodeid in contador.xfailed:
        print(f"  XFAIL {nodeid}")
    for nodeid in contador.failed + contador.errors:
        print(f"  FALHOU {nodeid}")

    if int(codigo) == 0 and contador.aprovado():
        print("check_suite_no_skips: OK, zero pulos e zero falhas")
        return 0
    print("check_suite_no_skips: REPROVADO", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
