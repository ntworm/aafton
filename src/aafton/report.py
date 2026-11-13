"""Relatorio de conversao. Nada e descartado em silencio.

Em modo estrito, que e o padrao do produto, qualquer issue de severidade
``error`` interrompe a conversao levantando ``StrictModeError``. Em modo
permissivo a conversao continua, mas o relatorio fica marcado como incompleto.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Literal

Severity = Literal["info", "warning", "error"]
SEVERITIES: tuple[Severity, ...] = ("info", "warning", "error")


class StrictModeError(RuntimeError):
    """Levantada quando um erro acontece em modo estrito."""


def _escape_cell(text: object) -> str:
    """Deixa um texto seguro dentro de uma celula de tabela Markdown.

    Uma mensagem de conversao carrega caminho de arquivo, nome de clip e lista
    de candidatos ambiguos, tudo vindo de arquivo de terceiro. Um unico ``|`` ou
    uma quebra de linha desmonta a tabela inteira do relatorio.
    """
    return str(text).replace("|", r"\|").replace("\r", " ").replace("\n", " ")


@dataclass
class ConversionIssue:
    severity: Severity
    code: str
    message: str
    context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")


@dataclass
class Report:
    strict: bool = True
    issues: list[ConversionIssue] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def add(self, severity: Severity, code: str, message: str, context: dict | None = None) -> None:
        issue = ConversionIssue(severity, code, message, context or {})
        self.issues.append(issue)
        if severity == "error" and self.strict:
            raise StrictModeError(f"{code}: {message}")

    def info(self, code: str, message: str, context: dict | None = None) -> None:
        self.add("info", code, message, context)

    def warning(self, code: str, message: str, context: dict | None = None) -> None:
        self.add("warning", code, message, context)

    def error(self, code: str, message: str, context: dict | None = None) -> None:
        self.add("error", code, message, context)

    def count(self, severity: Severity) -> int:
        return sum(1 for issue in self.issues if issue.severity == severity)

    @property
    def incomplete(self) -> bool:
        return self.count("error") > 0

    def to_json(self) -> str:
        payload = {
            "metadata": self.metadata,
            "counts": {severity: self.count(severity) for severity in SEVERITIES},
            "incomplete": self.incomplete,
            "issues": [asdict(issue) for issue in self.issues],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = ["# Conversion report", ""]
        for key, value in self.metadata.items():
            lines.append(f"- **{key}**: {value}")
        lines.extend(
            [
                "",
                f"Errors: {self.count('error')}, Warnings: {self.count('warning')}, Notes: {self.count('info')}",
                "",
                "| Severity | Code | Message |",
                "| --- | --- | --- |",
            ]
        )
        for issue in self.issues:
            lines.append(f"| {issue.severity} | `{issue.code}` | {_escape_cell(issue.message)} |")
        return "\n".join(lines) + "\n"


def erros_no_stderr(report: Report) -> int:
    """Erros do relatorio impressos no stderr; 1 quando o relatorio tem erro.

    A saida de quem chama continua no stdout: os erros vao para o stderr,
    para que o JSON do inspect possa ser lido por maquina.
    """
    for issue in report.issues:
        if issue.severity == "error":
            print(f"{issue.code}: {issue.message}", file=sys.stderr)
    return 1 if report.incomplete else 0
