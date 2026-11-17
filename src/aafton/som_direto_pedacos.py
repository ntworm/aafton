"""Separacao de alternativas e juncao de pedacos no som direto."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .report import Report

if TYPE_CHECKING:
    from .gravador_dialetos import Partes
    from .som_direto import Arquivo, Take


@dataclass
class _Peca:
    """Estado de um take durante a juncao de pedacos."""

    take: Take
    ultimo_pedaco: int
    ultimo_fim_tc: int | None


def _partes(take: Take) -> Partes | None:
    """Partes reconhecidas do take pelo primeiro arquivo."""
    return take.arquivos[0].partes if take.arquivos else None


def _chave_identidade(take: Take, partes: Partes) -> tuple[Path, str, tuple]:
    return (take.day, partes.dialeto, partes.identidade)


def _eh_alternativa_redundante(take: Take, principais: set[tuple[Path, str, tuple]]) -> bool:
    partes = _partes(take)
    if partes is None or partes.alternativa is None:
        return False
    return _chave_identidade(take, partes) in principais


def _filtrar_alternativas(takes: list[Take], report: Report) -> list[Take]:
    """Remove copias float/mix quando existe take principal na mesma diaria."""
    principais = {
        _chave_identidade(take, partes)
        for take in takes
        if (partes := _partes(take)) is not None and partes.alternativa is None
    }
    mantidos: list[Take] = []
    removidos = 0
    for take in takes:
        if _eh_alternativa_redundante(take, principais):
            removidos += 1
            continue
        mantidos.append(take)
    if removidos:
        plural = "take" if removidos == 1 else "takes"
        report.info(
            "PRODUCTION_ALTERNATES",
            f"{removidos} alternate {plural} left out because the main take is present",
            {"count": removidos},
        )
    return mantidos


def _family_uids(take: Take) -> set[str]:
    """FAMILY_UIDs reais (chunk iXML, nao fallback do bext) dos arquivos do take."""
    uids: set[str] = set()
    for arq in take.arquivos:
        ixml = arq.ixml
        if ixml is not None and ixml.source == "ixml" and ixml.family_uid:
            uids.add(ixml.family_uid)
    return uids


def _family_uid_diverge(alvo: Take, candidato: Take) -> bool:
    """True quando os dois takes trazem FAMILY_UIDs reais diferentes."""
    uids_alvo = _family_uids(alvo)
    uids_cand = _family_uids(candidato)
    return bool(uids_alvo and uids_cand and uids_alvo != uids_cand)


def _fim_tc(take: Take) -> int | None:
    """TimeReference do fim do take considerando os frames atuais dos arquivos."""
    tc = take.timecode
    if tc is None:
        return None
    frames = max((a.info.frames or 0 for a in take.arquivos), default=0)
    return tc + frames


def _pode_juntar(alvo: _Peca, candidato: Take, partes: Partes) -> bool:
    """True quando o candidato e o pedaco seguinte e continua exatamente apos o alvo."""
    if not partes.marca_de_pedaco or partes.pedaco != alvo.ultimo_pedaco + 1:
        return False
    tc_cand = candidato.timecode
    if alvo.ultimo_fim_tc is None or tc_cand is None or tc_cand != alvo.ultimo_fim_tc:
        return False
    return not _family_uid_diverge(alvo.take, candidato)


def _ordem_arquivo(arq: Arquivo) -> tuple[int, str]:
    return (arq.partes.pedaco if arq.partes else 0, arq.path.name.casefold())


def _anexar_pedaco(alvo: _Peca, candidato: Take, partes: Partes) -> None:
    """Anexa os arquivos do candidato ao take alvo com offset desde o primeiro pedaco."""
    offset = (candidato.timecode or 0) - (alvo.take.timecode or 0)
    for arq in candidato.arquivos:
        arq.offset = offset
    alvo.take.arquivos.extend(candidato.arquivos)
    alvo.take.arquivos.sort(key=_ordem_arquivo)
    alvo.ultimo_pedaco = partes.pedaco
    alvo.ultimo_fim_tc = _fim_tc(candidato)


def _ordem_pedaco(item: tuple[Take, Partes]) -> tuple[int, bool, int, str]:
    take, partes = item
    return (partes.pedaco, take.timecode is None, take.timecode or 0, take.name_key)


def _juntar_grupo(grupo: list[tuple[Take, Partes]]) -> tuple[list[Take], int]:
    """Junta pedacos sequenciais de um mesmo grupo e conta os pedacos > 1 mantidos."""
    pecas: list[_Peca] = []
    nao_juntados = 0
    for take, partes in sorted(grupo, key=_ordem_pedaco):
        alvo = next((p for p in pecas if _pode_juntar(p, take, partes)), None)
        if alvo is not None:
            _anexar_pedaco(alvo, take, partes)
            continue
        pecas.append(_Peca(take=take, ultimo_pedaco=partes.pedaco, ultimo_fim_tc=_fim_tc(take)))
        if partes.pedaco > 1:
            nao_juntados += 1
    return [p.take for p in pecas], nao_juntados


def _juntar_pedacos(takes: list[Take], report: Report) -> list[Take]:
    """Junta pedacos seguidos de um mesmo take e relata os pedacos > 1 mantidos."""
    sem_dialeto: list[Take] = []
    grupos: dict[tuple[Path, str, tuple, str | None], list[tuple[Take, Partes]]] = {}
    for take in takes:
        partes = _partes(take)
        if partes is None:
            sem_dialeto.append(take)
            continue
        chave = (take.day, partes.dialeto, partes.identidade, partes.alternativa)
        grupos.setdefault(chave, []).append((take, partes))
    resultado = list(sem_dialeto)
    nao_juntados = 0
    for grupo in grupos.values():
        juntados, mantidos = _juntar_grupo(grupo)
        resultado.extend(juntados)
        nao_juntados += mantidos
    if nao_juntados:
        plural = "piece" if nao_juntados == 1 else "pieces"
        report.info(
            "PRODUCTION_PIECES_KEPT",
            f"{nao_juntados} split {plural} kept as separate takes",
            {"count": nao_juntados},
        )
    return sorted(resultado, key=lambda t: (str(t.day), t.name_key, str(t.arquivos[0].path)))


def resolver_pedacos(takes: list[Take], report: Report) -> list[Take]:
    """Separa alternativas com principal presente e junta pedacos sequenciais."""
    return _juntar_pedacos(_filtrar_alternativas(takes, report), report)
