"""Rotulos de canal que o proprio AAF declara, sem adivinhar layout.

O Pro Tools exporta uma track 5.1 como seis slots de timeline, cada um com o
seu MasterMob, e o nome do MasterMob termina no rotulo do canal do slot
(``...-08.L``, ``...-08.C``). Quando os seis slots concordam, o rotulo vem
deles; quando nao concordam, o rotulo continua sendo o do layout padrao.

Este modulo nao decide nada: ele devolve a evidencia, ou ``None``.
"""

from __future__ import annotations

import re

# O rotulo do canal no fim do nome do MasterMob. O mais longo vem primeiro:
# 'LFE' antes de 'L', 'Ls' antes de 'L'.
_ROTULO = re.compile(r"\.(LFE|Ls|Rs|L|C|R)$")

# Um fade do Pro Tools nao tem rotulo de canal: ele nao entra na evidencia.
_FADE = re.compile(r"^Fade\s+\d+$", re.IGNORECASE)

# Os seis canais do 5.1 do Pro Tools, na ordem do export em texto da sessao.
CONJUNTO_51 = ("L", "C", "R", "Ls", "Rs", "LFE")

# Teto de SourceClips visitados por slot. Um AAF com muitos clipes nao pode
# fazer a busca crescer sem limite; quando o teto e alcancado, a evidencia
# fica incompleta e o chamador nao a usa.
LIMITE_DE_CLIPS = 256


def _mob_do_clip(clip):
    """O mob que o SourceClip referencia, ou None quando nao ha."""
    try:
        return getattr(clip, "mob", None)
    except Exception:  # noqa: BLE001 - mob quebrado e evidencia ausente
        return None


def _source_clips(segmento):
    """SourceClips do segmento e a marca de que a busca foi truncada.

    A busca desce por Sequence e OperationGroup, com um conjunto de visitados
    (nenhum objeto e visto duas vezes) e um teto de clipes.
    """
    achados = []
    vistos = set()
    pilha = [segmento]
    while pilha:
        atual = pilha.pop()
        if atual is None or id(atual) in vistos:
            continue
        vistos.add(id(atual))
        if type(atual).__name__ == "SourceClip":
            achados.append(atual)
            if len(achados) >= LIMITE_DE_CLIPS:
                return achados, True
            continue
        for filho in list(getattr(atual, "segments", None) or []):
            pilha.append(filho)
        for filho in list(getattr(atual, "components", None) or []):
            pilha.append(filho)
    return achados, False


def _rotulo_do_nome(nome):
    """Rotulo de canal no nome do MasterMob, ou None quando nao ha."""
    if not nome or _FADE.match(nome):
        return None
    achado = _ROTULO.search(nome)
    return achado.group(1) if achado else None


def _nome_do_mob(clip):
    """O nome do mob do clip, ou None quando o mob falta ou quebra."""
    mob = _mob_do_clip(clip)
    if mob is None:
        return None
    try:
        return str(getattr(mob, "name", "") or "").strip()
    except Exception:  # noqa: BLE001 - nome quebrado e evidencia ausente
        return None


def rotulo_do_slot(slot):
    """Rotulo de canal que o slot declara, ou None quando nao ha evidencia.

    A evidencia do slot e inteira ou nao e nada: um SourceClip que nao e um
    fade e nao declara rotulo, ou vem de um mob que falta ou que quebra,
    derruba o rotulo do slot inteiro. Quem resolve e o layout padrao, nao a
    fonte. So um fade reconhecido pelo nome pode ser ignorado.
    """
    clips, truncado = _source_clips(getattr(slot, "segment", None))
    if truncado:
        return None
    rotulos = set()
    for clip in clips:
        nome = _nome_do_mob(clip)
        if nome is None:
            return None
        if _FADE.match(nome):
            continue
        rotulo = _rotulo_do_nome(nome)
        if rotulo is None:
            return None
        rotulos.add(rotulo)
        if len(rotulos) > 1:
            return None
    return rotulos.pop() if len(rotulos) == 1 else None


def rotulos_do_grupo(slots):
    """Rotulo de cada slot, quando o grupo inteiro tem evidencia completa.

    Devolve None quando o grupo nao tem os seis slots do 5.1, quando algum
    slot nao declara rotulo, quando dois slots declaram o mesmo rotulo, ou
    quando o conjunto dos rotulos nao e o do 5.1. Nesses casos quem decide o
    rotulo e o layout padrao.
    """
    if len(slots) != len(CONJUNTO_51):
        return None
    rotulos = []
    for slot in slots:
        rotulo = rotulo_do_slot(slot)
        if rotulo is None:
            return None
        rotulos.append(rotulo)
    if len(set(rotulos)) != len(rotulos):
        return None
    if set(rotulos) != set(CONJUNTO_51):
        return None
    return rotulos
