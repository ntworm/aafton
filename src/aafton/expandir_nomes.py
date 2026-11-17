"""Normalizacao de nomes e chaves de busca para o casamento da expansao."""

from __future__ import annotations

import re
from pathlib import Path

from .bwf_meta import BwfInfo
from .ir import ClipIR

_WAV_FINAL_RE = re.compile(r"\.wav$", re.IGNORECASE)
_SUFIXO_LR_RE = re.compile(r"[_.-][LR]$", re.IGNORECASE)


def nome_de_casamento(nome: str) -> str:
    """Normaliza o nome de um clip ou original para o casamento por nome."""
    limpo = _WAV_FINAL_RE.sub("", nome)
    limpo = _SUFIXO_LR_RE.sub("", limpo)
    limpo = _WAV_FINAL_RE.sub("", limpo)
    if limpo.startswith("@"):
        limpo = limpo[1:]
    return limpo.casefold()


def _original_filename_real(info: BwfInfo | None) -> str | None:
    """HISTORY/ORIGINAL_FILENAME apenas quando veio de um chunk iXML real."""
    ixml = info.ixml if info is not None else None
    if ixml is None or ixml.source != "ixml" or not ixml.original_filename:
        return None
    return ixml.original_filename


def _chaves(nomes: tuple[str | None, ...]) -> tuple[str, ...]:
    """Normaliza os nomes dados, mantendo a ordem e sem repetir nem aceitar vazio."""
    unicas: dict[str, None] = {}
    for nome in nomes:
        if not nome:
            continue
        chave = nome_de_casamento(nome)
        if chave:
            unicas[chave] = None
    return tuple(unicas)


def nomes_do_original(path: Path, info: BwfInfo | None) -> tuple[str, ...]:
    """Chaves do original no indice: name, stem e ORIGINAL_FILENAME real."""
    return _chaves((path.name, path.stem, _original_filename_real(info)))


def nomes_do_clip(clip: ClipIR, info: BwfInfo | None) -> tuple[str, ...]:
    """Chaves de busca do clip: nome do clip e ORIGINAL_FILENAME real da midia."""
    return _chaves((clip.name, _original_filename_real(info)))
