"""Rotulos de canal que o AAF exportado carrega no nome dos MasterMobs.

O Pro Tools declara o canal de cada slot no nome do MasterMob que o slot
referencia (``...-08.L``, ``...-08.C``). O leitor ja sabe ler essa evidencia;
quando o escritor nao a escreve, o round-trip volta a adivinhar o layout pelo
SMPTE e troca o nome das tracks do 5.1.

Este modulo nao inventa layout: ele espelha o que o leitor aceita. Um grupo
so entra quando tem os seis canais do 5.1 com rotulos distintos; qualquer
outro grupo (stereo, ambisonico, quad, chN) fica como esta.

O rotulo de um MasterMob so pode ser escrito uma vez: quando dois grupos do
5.1 compartilham o mesmo mob, renomear por grupo dava dois nomes ao mesmo mob
e o primeiro grupo perdia a evidencia que acabara de declarar. Por isso o mapa
de mob para rotulos e montado uma unica vez, sobre todos os grupos.
"""

from __future__ import annotations

from typing import NamedTuple

from .aaf_channel_labels import CONJUNTO_51, _source_clips, rotulo_do_slot
from .aaf_write_tracks import _LAYOUT_SUFFIX_RE, _strip_channel_suffix
from .report import Report

# Sufixo de canal aceito no nome da track e a grafia canonica do CONJUNTO_51.
_ROTULOS_CANONICOS = {
    "L": "L",
    "C": "C",
    "R": "R",
    "LS": "Ls",
    "RS": "Rs",
    "LFE": "LFE",
}

# Motivo do grupo cuja busca de SourceClips passou do teto de clipes.
_MOTIVO_DE_BUSCA_TRUNCADA = "truncated search: the search for SourceClips passed the limit"


class _Grupo51(NamedTuple):
    """Grupo do 5.1 com os slots, os rotulos e o que a busca de clips achou."""

    base: str
    entradas: list[tuple[object, str]]
    rotulos: list[str]
    clips_por_slot: list
    truncado: bool


def rotular_mastermobs(slots_escritos, report: Report) -> None:
    """Escreve o rotulo de canal no nome do MasterMob de cada slot do 5.1.

    ``slots_escritos`` traz os pares (slot, nome da track) ja escritos no AAF.
    """
    grupos = _grupos_do_51(slots_escritos)
    mapa = _mapa_de_rotulos(grupos)
    _renomear(mapa)
    for grupo in grupos:
        _reportar_grupo(grupo, mapa, report)


def _grupos(slots_escritos) -> dict[str, list[tuple[object, str]]]:
    """Slots escritos agrupados pelo nome base da track."""
    grupos: dict[str, list[tuple[object, str]]] = {}
    for slot, nome in slots_escritos:
        grupos.setdefault(_strip_channel_suffix(nome), []).append((slot, nome))
    return grupos


def _grupos_do_51(slots_escritos) -> list[_Grupo51]:
    """Grupos do 5.1 com evidencia completa, na ordem de primeira aparicao."""
    grupos = []
    for base, entradas in _grupos(slots_escritos).items():
        rotulos = _rotulos_do_grupo(entradas)
        if rotulos is None:
            continue
        clips_por_slot, truncado = _clips_do_grupo(entradas)
        grupos.append(_Grupo51(base, entradas, rotulos, clips_por_slot, truncado))
    return grupos


def _rotulo_canonico(nome: str) -> str | None:
    """Rotulo de canal no fim do nome da track, ou None quando nao ha."""
    achado = _LAYOUT_SUFFIX_RE.search(nome or "")
    if achado is None:
        return None
    return _ROTULOS_CANONICOS.get(achado.group("suffix").strip().upper())


def _rotulos_do_grupo(entradas) -> list[str] | None:
    """Os seis rotulos do grupo, quando o grupo tem evidencia completa."""
    if len(entradas) != len(CONJUNTO_51):
        return None
    rotulos = []
    for _slot, nome in entradas:
        rotulo = _rotulo_canonico(nome)
        if rotulo is None:
            return None
        rotulos.append(rotulo)
    if len(set(rotulos)) != len(rotulos):
        return None
    if set(rotulos) != set(CONJUNTO_51):
        return None
    return rotulos


def _clips_do_grupo(entradas):
    """SourceClips de cada slot do grupo e a marca de busca truncada."""
    clips_por_slot = []
    for slot, _nome in entradas:
        clips, truncado = _source_clips(getattr(slot, "segment", None))
        if truncado:
            return [], True
        clips_por_slot.append(clips)
    return clips_por_slot, False


def _mapa_de_rotulos(grupos) -> dict[int, tuple[object, set[str]]]:
    """Mapa unico de mob para os rotulos que o referenciam, em todos os grupos."""
    mapa: dict[int, tuple[object, set[str]]] = {}
    for grupo in grupos:
        if grupo.truncado:
            continue
        for mob, rotulo in _mobs_do_grupo(grupo.clips_por_slot, grupo.rotulos):
            if id(mob) not in mapa:
                mapa[id(mob)] = (mob, set())
            mapa[id(mob)][1].add(rotulo)
    return mapa


def _mobs_do_grupo(clips_por_slot, rotulos):
    """Cada mob do grupo com o rotulo do slot que o referencia."""
    pares = []
    for clips, rotulo in zip(clips_por_slot, rotulos):
        for clip in clips:
            mob = getattr(clip, "mob", None)
            if mob is not None:
                pares.append((mob, rotulo))
    return pares


def _renomear(mapa) -> None:
    """Acrescenta o rotulo no nome do mob que um unico rotulo referencia."""
    for mob, rotulos in mapa.values():
        if len(rotulos) != 1:
            continue
        rotulo = next(iter(rotulos))
        nome = str(getattr(mob, "name", "") or "")
        if not nome or nome.endswith("." + rotulo):
            continue
        try:
            mob.name = nome + "." + rotulo
        except Exception:  # noqa: BLE001 - mob quebrado e evidencia ausente
            continue


def _reportar_grupo(grupo, mapa, report) -> None:
    """Relata o grupo do 5.1 depois de todas as renomeacoes do AAF."""
    if grupo.truncado:
        _reporta_perdido(grupo.base, grupo.rotulos, report, _MOTIVO_DE_BUSCA_TRUNCADA)
        return
    if _evidencia_completa(grupo.entradas, grupo.rotulos):
        _reporta_carried(grupo.base, grupo.rotulos, report)
    else:
        _reporta_perdido(grupo.base, grupo.rotulos, report, _motivo_da_perda(grupo, mapa))


def _evidencia_completa(entradas, rotulos) -> bool:
    """Confere se cada slot do grupo declara o rotulo que o grupo espera."""
    for (slot, _nome), rotulo in zip(entradas, rotulos):
        if rotulo_do_slot(slot) != rotulo:
            return False
    return True


def _motivo_da_perda(grupo, mapa) -> str:
    """Motivo pelo qual a evidencia de canal do grupo nao chegou ao AAF."""
    for mob, _rotulo in _mobs_do_grupo(grupo.clips_por_slot, grupo.rotulos):
        rotulos = mapa.get(id(mob), (None, set()))[1]
        if len(rotulos) > 1:
            return "a MasterMob of the group is referenced by two labels"
    return "the channel evidence of the AAF does not confirm the labels of the group"


def _reporta_carried(base, rotulos, report: Report) -> None:
    """Informa que os rotulos de canal foram escritos no AAF."""
    report.info(
        "WRITE_CHANNEL_LABELS_CARRIED",
        f"the channel labels of the 5.1 of the track {base!r} were written in the AAF",
        {"track": base, "labels": list(rotulos)},
    )


def _reporta_perdido(base, rotulos, report: Report, motivo) -> None:
    """Informa que os rotulos de canal nao sobreviveram no AAF."""
    report.warning(
        "WRITE_CHANNEL_LABELS_LOST",
        f"the channel labels of the 5.1 of the track {base!r} were not written in the AAF; "
        f"the reimport will guess the layout ({motivo})",
        {"track": base, "labels": list(rotulos), "reason": motivo},
    )
