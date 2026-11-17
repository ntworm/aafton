"""Leitura dos pares chave=valor do campo Description do chunk bext."""

from __future__ import annotations

import re

_LINHAS_RE = re.compile(r"\r\n|\n|\r")


def _prefixo(pares: list[tuple[str, str]]) -> str | None:
    """Devolve 'z' ou 's' quando ha chave de cena ou take com esse prefixo."""
    chaves = {k[0] + k[1:].upper() for k, _ in pares if len(k) > 1}
    if "zSCENE" in chaves or "zTAKE" in chaves:
        return "z"
    if "sSCENE" in chaves or "sTAKE" in chaves:
        return "s"
    return None


def _normalizar_chave(chave: str, prefixo: str | None) -> str:
    """Remove o prefixo reconhecido e devolve a chave em maiusculas."""
    if prefixo and len(chave) > 1 and chave.startswith(prefixo):
        return chave[1:].upper()
    return chave.upper()


def campos(description: str | None) -> tuple[str | None, dict[str, str]]:
    """Extrai (prefixo, campos) das linhas chave=valor do bext Description."""
    if not description:
        return None, {}
    pares: list[tuple[str, str]] = []
    for linha in _LINHAS_RE.split(description):
        if "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        if chave:
            pares.append((chave, valor.strip()))
    prefixo = _prefixo(pares)
    return prefixo, {_normalizar_chave(k, prefixo): v for k, v in pares}
