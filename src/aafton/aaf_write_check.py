"""Parte de aaf_write: conferencia dos comprimentos e issues do AAF exportado.

Na fase 1, check_exported_aaf foi dividida em etapas (os helpers novos saem
dela); _iter_source_clips e _mob_com_descritor vieram de aaf_write sem mudanca.
"""

import math


from pathlib import Path

import aaf2

import fractions


from .report import Report


def _iter_source_clips(segment, visited: set[int] | None = None):
    """Percorre wrappers/Sequences e devolve os SourceClip alcançáveis."""
    if segment is None:
        return
    if visited is None:
        visited = set()
    marker = id(segment)
    if marker in visited:
        return
    visited.add(marker)

    if type(segment).__name__ == "SourceClip":
        yield segment
        return

    for attribute in ("components", "segments"):
        for child in getattr(segment, attribute, []) or []:
            yield from _iter_source_clips(child, visited)


def _mob_com_descritor(mob, comprimentos: dict) -> int | None:
    """Desce a cadeia MasterMob -> SourceClip -> SourceMob ate achar um descritor.

    Devolve o comprimento declarado desse descritor, ou None quando a cadeia
    nao chega a nenhum.
    """
    visitados: set[int] = set()
    fila = [mob]
    while fila:
        atual = fila.pop(0)
        if atual is None or id(atual) in visitados:
            continue
        visitados.add(id(atual))
        chave = str(getattr(atual, "mob_id", "") or "")
        if chave in comprimentos:
            return comprimentos[chave]
        for slot in getattr(atual, "slots", []) or []:
            segmento = getattr(slot, "segment", None)
            for source_clip in _iter_source_clips(segmento):
                alvo = getattr(source_clip, "mob", None)
                if alvo is not None:
                    fila.append(alvo)
    return None


def _comprimentos_declarados(mobs) -> dict[str, int]:
    """Mapa de mob_id para o comprimento declarado no descritor de cada fonte."""
    comprimentos: dict[str, int] = {}
    for mob in mobs:
        descritor = getattr(mob, "descriptor", None)
        if descritor is None:
            continue
        comprimento = getattr(descritor, "length", None)
        if comprimento:
            comprimentos[str(mob.mob_id)] = int(comprimento)
    return comprimentos


def _pedido_sourceclip(alvo, componente, slot) -> int:
    """Samples pedidos por um SourceClip, convertidos para o edit rate da composicao."""
    target_slot = alvo.slot_at(componente.slot_id)
    source_rate = fractions.Fraction(str(target_slot.edit_rate))
    composition_rate = fractions.Fraction(str(slot.edit_rate))
    duration = fractions.Fraction(componente.length or 0) * source_rate / composition_rate
    return (componente.start or 0) + math.ceil(duration)


def _pior_caso(pior, pedido: int, declarado: int, track: str):
    """Mantem o pior caso: o maior excesso pedido sobre o comprimento declarado."""
    if pior is not None and pedido - declarado <= pior[0] - pior[1]:
        return pior
    return (pedido, declarado, track)


def _conferir_slots_do_mob(mob, comprimentos: dict):
    """Confere os slots de um CompositionMob e devolve contagens e pior caso."""
    excedentes = 0
    sem_descritor = 0
    pior: tuple[int, int, str] | None = None
    for slot in mob.slots:
        segmento = getattr(slot, "segment", None)
        for componente in _iter_source_clips(segmento):
            alvo = getattr(componente, "mob", None)
            if alvo is None:
                continue
            declarado = _mob_com_descritor(alvo, comprimentos)
            if declarado is None:
                sem_descritor += 1
                continue
            pedido = _pedido_sourceclip(alvo, componente, slot)
            if pedido > declarado:
                excedentes += 1
                pior = _pior_caso(pior, pedido, declarado, str(getattr(slot, "name", "") or ""))
    return excedentes, sem_descritor, pior


def _conferir_comprimentos(mobs, comprimentos: dict):
    """Soma as contagens de todos os CompositionMob do AAF exportado."""
    excedentes = 0
    sem_descritor = 0
    pior: tuple[int, int, str] | None = None
    for mob in mobs:
        if type(mob).__name__ != "CompositionMob":
            continue
        excedentes_mob, sem_descritor_mob, pior_mob = _conferir_slots_do_mob(mob, comprimentos)
        excedentes += excedentes_mob
        sem_descritor += sem_descritor_mob
        if pior_mob is not None:
            pior = _pior_caso(pior, pior_mob[0], pior_mob[1], pior_mob[2])
    return excedentes, sem_descritor, pior


def _emitir_issues(
    destination, report: Report, fontes: int, sem_descritor: int, excedentes: int, pior
) -> None:
    """Emite as issues do self-check, na mesma ordem e com os mesmos dados de sempre."""
    if sem_descritor:
        report.warning(
            "EXPORT_SOURCECLIP_SEM_DESCRITOR",
            f"{sem_descritor} SourceClip do not reach any source descriptor; "
            f"their length cannot be checked.",
            {"count": sem_descritor},
        )
    if fontes == 0:
        report.error(
            "EXPORT_NO_AUDIO_SOURCES",
            "the exported AAF contains no audio source; the delivery would be empty",
            {"path": str(destination)},
        )
    elif excedentes:
        detalhe = ""
        if pior is not None:
            detalhe = f"; the worst asks {pior[0]} of {pior[1]} samples, in the track {pior[2]!r}"
        # Erro, e nao aviso: um AAF assim e invalido para quem o receber, e a
        # causa conhecida ja esta tratada na importacao, onde um fade que a
        # fonte nao cobre passou a ser mantido como clip em vez de absorvido.
        # Se isto voltar a disparar, e sinal de um caminho novo produzindo a
        # mesma inconsistencia, e queremos parar em vez de entregar.
        report.error(
            "EXPORT_SOURCECLIP_EXCEEDS_SOURCE",
            f"{excedentes} SourceClip ask for more material than the source descriptor "
            f"declares{detalhe}. The AAF is internally inconsistent and another application "
            f"may refuse the relink or truncate the material.",
            {
                "count": excedentes,
                "worst_requested": pior[0] if pior else None,
                "worst_declared": pior[1] if pior else None,
            },
        )
    elif not sem_descritor:
        report.info(
            "EXPORT_SELF_CHECK_OK",
            "every SourceClip of the exported AAF fits in the declared length of its source",
            {"sources": fontes},
        )


def check_exported_aaf(destination: str | Path, report: Report) -> None:
    """Reabre o AAF recem-escrito e confere que ele e coerente consigo mesmo.

    Isto existe porque o `aafton verify` compara o AAF de origem com o `.als`
    gerado e **nunca abre o AAF exportado**. Foi por essa fresta que passou um
    `duration_ts` fixo em sessenta segundos: todo descritor de fonte declarava
    2880000 samples enquanto os SourceClip pediam ate dezenove minutos, e a
    exportacao terminava com exit 0 em todas as sessoes.

    A invariante conferida aqui nao depende de como o comprimento foi obtido,
    so de o arquivo ser coerente consigo mesmo, que e o que outro aplicativo
    vai cobrar dele.
    """
    destination = Path(destination)
    excedentes = 0
    sem_descritor = 0
    pior: tuple[int, int, str] | None = None
    fontes = 0
    try:
        with aaf2.open(str(destination), "r") as f:
            mobs = f.content.mobs
            comprimentos = _comprimentos_declarados(mobs)

            fontes = len(comprimentos)
            excedentes, sem_descritor, pior = _conferir_comprimentos(mobs, comprimentos)

    except Exception as exc:
        # So a leitura entra neste try. Emitir as issues aqui dentro faria o
        # StrictModeError do proprio report ser capturado e reetiquetado como
        # arquivo ilegivel, escondendo o defeito que acabou de ser encontrado.
        report.error(
            "EXPORT_UNREADABLE",
            f"the newly written AAF cannot be reopened for checking: {exc}",
            {"path": str(destination)},
        )
        return

    _emitir_issues(destination, report, fontes, sem_descritor, excedentes, pior)
