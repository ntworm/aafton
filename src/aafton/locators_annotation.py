"""Formato do Annotation de um locator de cue de ADR: linhas `chave: valor`."""

from __future__ import annotations


def format_adr_annotation(out_tc: str | None, character: str, line: str, comment: str) -> str:
    """Format ADR annotation lines: Out, Character, Line, Note."""
    lines = []
    if out_tc:
        lines.append(f"Out: {out_tc}")
    if character:
        lines.append(f"Character: {character}")
    if line:
        lines.append(f"Line: {line}")
    if comment:
        lines.append(f"Note: {comment}")
    return "\n".join(lines)


def is_adr_locator(name: str, annotation: str) -> bool:
    """Check if locator has ADR annotation keys."""
    for line in annotation.splitlines():
        if line.startswith(("Out: ", "Character: ", "Line: ")):
            return True
    return False


def parse_adr_annotation_data(annotation: str) -> dict[str, str]:
    prefix_map = {"Out: ": "out", "Character: ": "character", "Line: ": "line", "Note: ": "note"}
    data: dict[str, str] = {}
    for line in annotation.splitlines():
        for prefix, key in prefix_map.items():
            if line.startswith(prefix):
                data[key] = line[len(prefix) :].strip()
                break
    return data
