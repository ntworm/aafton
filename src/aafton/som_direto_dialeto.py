"""Regras de dialeto no import de som direto: contagem, canais do nome e pastas .TAKE."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .report import Report
from .som_direto_blocos import data_do_bext

if TYPE_CHECKING:
    from .som_direto import Arquivo, Canal, Take


def relatar_dialetos(arquivos: Sequence[Arquivo], report: Report) -> None:
    """Emite PRODUCTION_DIALECT com a contagem por dialeto quando ha reconhecimento."""
    contagem = Counter(arq.partes.dialeto for arq in arquivos if arq.partes is not None)
    if not contagem:
        return
    ordenados = dict(sorted(contagem.items()))
    resumo = ", ".join(f"{dialeto}: {total}" for dialeto, total in ordenados.items())
    report.info(
        "PRODUCTION_DIALECT",
        f"recorder dialects recognized ({resumo})",
        {"dialects": ordenados},
    )


def _canal_divergiu(canal: Canal) -> bool:
    """True quando o canal caiu em Ch por divergencia entre nome e canais do WAV."""
    if canal.key[0] != "Ch":
        return False
    partes = canal.arquivo.partes
    if partes is None or partes.canais_do_nome is None:
        return False
    return len(partes.canais_do_nome) != canal.arquivo.info.channels


def relatar_canais_divergentes(takes: Iterable[Take], report: Report) -> None:
    """Avisa uma vez por arquivo quando os canais do nome nao batem com o WAV."""
    divergentes: set[Path] = set()
    for take in takes:
        for canal in take.canais:
            if _canal_divergiu(canal):
                divergentes.add(canal.arquivo.path)
    if not divergentes:
        return
    total = len(divergentes)
    plural = "file" if total == 1 else "files"
    report.warning(
        "PRODUCTION_NAME_CHANNELS",
        f"channel count in the file name differs from the WAV channels ({total} {plural}); "
        "using file channel order",
        {"count": total},
    )


def _tape_do_arquivo(arq: Arquivo) -> str:
    """TAPE do iXML (real ou do bext) ou vazio quando falta."""
    return (arq.ixml.tape or "") if arq.ixml is not None else ""


def _causa_take_folders(pasta: Path, raiz: Path, grupo: list[Arquivo]) -> str | None:
    """Causa para manter pastas .TAKE separadas sob ``pasta``; None quando juntam."""
    if not pasta.resolve().is_relative_to(raiz.resolve()):
        return "recording folder is outside the input"
    tapes = {_tape_do_arquivo(arq) for arq in grupo}
    if len(tapes) != 1:
        if "" in tapes:
            return "some files in .TAKE folders have TAPE and others do not"
        return "files in .TAKE folders have different TAPE values"
    validas = [d for d in (data_do_bext(arq.info.bext) for arq in grupo) if d is not None]
    if len(validas) != len(grupo):
        return "files in .TAKE folders lack a valid bext OriginationDate"
    if (max(validas) - min(validas)).days > 1:
        return "OriginationDate in .TAKE folders spans more than 1 day"
    return None


def resolver_pastas_take(arquivos: Sequence[Arquivo], raiz: Path, report: Report) -> None:
    """Usa a pasta de gravacao como diaria das pastas *.TAKE quando as regras batem."""
    if not raiz.is_dir():
        return  # um WAV so: a diaria fica a pasta dele, sem aviso
    por_pasta: dict[Path, list[Arquivo]] = {}
    for arq in arquivos:
        if arq.path.parent.name.casefold().endswith(".take"):
            por_pasta.setdefault(arq.path.parent.parent, []).append(arq)
    for pasta, grupo in por_pasta.items():
        causa = _causa_take_folders(pasta, raiz, grupo)
        if causa is None:
            for arq in grupo:
                arq.day = pasta
            continue
        nome = pasta.name or pasta.resolve().name
        pastas = len({arq.path.parent for arq in grupo})
        report.warning(
            "PRODUCTION_TAKE_FOLDERS_KEPT",
            f"{nome}: .TAKE folders kept as separate days ({causa})",
            {"folder": nome, "cause": causa, "take_folders": pastas},
        )
