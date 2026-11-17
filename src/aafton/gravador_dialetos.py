"""Reconhecimento de dialetos de gravadores pelo nome e metadados do WAV."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .bext_description import campos
from .bwf_meta import BwfInfo

_DR40_RE = re.compile(r"^(?P<base>.+)_(?P<num>\d{4})S(?P<canal>[1-9]{1,2})$", re.IGNORECASE)
_X8_RE = re.compile(
    r"^(?P<base>[A-Za-z0-9]{6,9})_(?P<num>\d{4})(?P<f>F)?"
    r"(?:_(?P<div>[A-Z]|\d{2}))?_(?P<suf>[1-9]-[1-9]|[1-9]{1,2}|[1-9]CH|MIX)$",
    re.IGNORECASE,
)
_ZOOM_RE = re.compile(
    r"^(?P<circ>@)?(?P<cena>.*?\d{1,4})[-_](?P<take>\d{3})"
    r"(?:_Tr(?P<tr>\d)(?:_(?P<tr2>\d))?|_(?P<mix>TL_R))?"
    r"(?:_(?P<fp>32FP))?(?:_(?P<pedaco>\d{4}))?$",
    re.IGNORECASE,
)
_SD_RE = re.compile(
    r"^(?P<circ>@)?(?P<dup>\+)?(?:(?P<proj>.+?)[;%=])?"
    r"(?P<cena>.+?)(?P<des>[T+-])(?P<take>\d+)(?P<letra>[A-Z])?$",
    re.IGNORECASE,
)
_DIGITOS_RE = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class Partes:
    """Partes lidas do nome do arquivo quando um dialeto e reconhecido."""

    dialeto: str
    identidade: tuple
    pedaco: int
    marca_de_pedaco: bool
    letra: str | None
    canais_do_nome: tuple[int, ...] | None
    circulado: bool
    alternativa: str | None
    confirmado: bool


class ComPartes(Protocol):
    """Objeto com caminho e partes mutaveis para resolver letras na pasta."""

    path: Path
    partes: Partes | None


def _bext_campos(info: BwfInfo | None) -> tuple[str | None, dict[str, str]]:
    if info is None or not info.bext:
        return None, {}
    return campos(info.bext.get("description"))


def _ixml_real(info: BwfInfo | None) -> bool:
    return info is not None and info.ixml is not None and info.ixml.source == "ixml"


def _tascam_dr40(stem: str) -> Partes | None:
    match = _DR40_RE.match(stem)
    if match is None:
        return None
    canais = tuple(int(c) for c in match["canal"])
    return Partes(
        dialeto="tascam_dr40",
        identidade=(match["base"].casefold(), match["num"]),
        pedaco=1,
        marca_de_pedaco=False,
        letra=None,
        canais_do_nome=canais,
        circulado=False,
        alternativa=None,
        confirmado=True,
    )


def _x8_canais(suf: str) -> tuple[int, ...] | None:
    chave = suf.upper()
    if chave == "MIX":
        return None
    if chave.endswith("CH"):
        return tuple(range(1, int(chave[:-2]) + 1))
    if "-" in chave:
        inicio, fim = chave.split("-")
        return (int(inicio), int(fim))
    if len(chave) == 2:
        return (int(chave[0]), int(chave[1]))
    return (int(chave),)


def _x8_pedaco(div: str | None) -> tuple[int, bool]:
    if div is None:
        return 1, False
    if div.isascii() and div.isdigit():
        return int(div), True
    return ord(div.upper()) - ord("A") + 1, True


def _tascam_x8(stem: str) -> Partes | None:
    match = _X8_RE.match(stem)
    if match is None:
        return None
    pedaco, marca = _x8_pedaco(match["div"])
    suf = match["suf"].upper()
    alternativa = "float" if match["f"] else ("mix" if suf == "MIX" else None)
    return Partes(
        dialeto="tascam_x8",
        identidade=(match["base"].casefold(), match["num"]),
        pedaco=pedaco,
        marca_de_pedaco=marca,
        letra=None,
        canais_do_nome=_x8_canais(suf),
        circulado=False,
        alternativa=alternativa,
        confirmado=True,
    )


def _tascam(stem: str) -> Partes | None:
    return _tascam_dr40(stem) or _tascam_x8(stem)


def _zoom_canais(match: re.Match[str]) -> tuple[int, ...] | None:
    if match["tr"] is None:
        return None
    if match["tr2"] is not None:
        return (int(match["tr"]), int(match["tr2"]))
    return (int(match["tr"]),)


def _zoom(stem: str) -> Partes | None:
    match = _ZOOM_RE.match(stem)
    if match is None:
        return None
    pedaco = int(match["pedaco"]) if match["pedaco"] else 1
    alternativa = "float" if match["fp"] else ("mix" if match["mix"] else None)
    return Partes(
        dialeto="zoom",
        identidade=(match["cena"].casefold(), match["take"].casefold()),
        pedaco=pedaco,
        marca_de_pedaco=match["pedaco"] is not None,
        letra=None,
        canais_do_nome=_zoom_canais(match),
        circulado=match["circ"] is not None,
        alternativa=alternativa,
        confirmado=True,
    )


def _eh_zoom(caminho: Path, prefixo: str | None) -> bool:
    return prefixo == "z" or caminho.parent.name.casefold().endswith(".take")


def _igual_metadado(valor: str | None, do_nome: str) -> bool:
    if not valor or not do_nome:
        return False
    if valor.casefold() == do_nome.casefold():
        return True
    if _DIGITOS_RE.fullmatch(valor) and _DIGITOS_RE.fullmatch(do_nome):
        return int(valor) == int(do_nome)
    return False


def _sd_metadado(
    info: BwfInfo | None,
    prefixo: str | None,
    bext_mapa: dict[str, str],
) -> tuple[str | None, str | None, bool] | None:
    ixml = info.ixml if info is not None else None
    if ixml is not None and ixml.source == "ixml":
        return ixml.scene, ixml.take, True
    if prefixo == "s":
        if ixml is not None and ixml.source == "bext":
            return ixml.scene, ixml.take, False
        if ixml is None:
            return bext_mapa.get("SCENE"), bext_mapa.get("TAKE"), False
    return None


def _sound_devices(
    stem: str,
    info: BwfInfo | None,
    prefixo: str | None,
    bext_mapa: dict[str, str],
) -> Partes | None:
    match = _SD_RE.match(stem)
    if match is None:
        return None
    meta = _sd_metadado(info, prefixo, bext_mapa)
    if meta is None:
        return None
    scene, take, confirmado = meta
    if not _igual_metadado(scene, match["cena"]) or not _igual_metadado(take, match["take"]):
        return None
    letra = match["letra"].upper() if match["letra"] else None
    campos_id = ("dup", "proj", "cena", "des", "take", "letra")
    identidade = tuple((match[campo] or "").casefold() for campo in campos_id)
    return Partes(
        dialeto="sound_devices",
        identidade=identidade,
        pedaco=1,
        marca_de_pedaco=False,
        letra=letra,
        canais_do_nome=None,
        circulado=match["circ"] is not None,
        alternativa=None,
        confirmado=confirmado,
    )


def reconhecer(path: Path | str, info: BwfInfo | None) -> Partes | None:
    """Reconhece o dialeto do gravador pelo nome e pelos metadados de um WAV."""
    caminho = Path(path)
    stem = caminho.stem
    prefixo, bext_mapa = _bext_campos(info)
    if not _ixml_real(info) and prefixo not in ("z", "s"):
        if partes := _tascam(stem):
            return partes
    if _eh_zoom(caminho, prefixo):
        if partes := _zoom(stem):
            return partes
    if prefixo != "z":
        return _sound_devices(stem, info, prefixo, bext_mapa)
    return None


def _grupo_eh_divisao(grupo: list[ComPartes]) -> bool:
    letras = {
        a.partes.letra.upper() if a.partes.letra else None for a in grupo if a.partes is not None
    }
    # O primeiro pedaco e A: so B sem A e duplicata, nao divisao.
    return {"A", "B"} <= letras and None not in letras


def _trocar_letra(arq: ComPartes) -> None:
    partes = arq.partes
    if partes is None or partes.letra is None:
        return
    pedaco = ord(partes.letra.upper()) - ord("A") + 1
    arq.partes = replace(
        partes,
        identidade=(*partes.identidade[:5], ""),
        pedaco=pedaco,
        marca_de_pedaco=pedaco >= 2,
    )


def resolver_letras(arquivos: Iterable[ComPartes]) -> None:
    """Resolve sufixo de letra da Sound Devices como divisao de arquivo por pasta."""
    grupos: dict[tuple[Path, tuple], list[ComPartes]] = {}
    for arq in arquivos:
        partes = arq.partes
        if partes is not None and partes.dialeto == "sound_devices":
            chave = (arq.path.parent, partes.identidade[:5])
            grupos.setdefault(chave, []).append(arq)
    for grupo in grupos.values():
        if _grupo_eh_divisao(grupo):
            for arq in grupo:
                _trocar_letra(arq)
