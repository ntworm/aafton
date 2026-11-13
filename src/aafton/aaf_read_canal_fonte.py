"""Canal do arquivo que um SourceClip le.

O SourceClip aponta para um slot do MasterMob, que aponta para um slot do
SourceMob do arquivo. O PhysicalTrackNumber desse ultimo slot diz qual canal
do arquivo o clip toca: o Resolve grava uma track estereo como dois
SourceClips que leem os canais 1 e 2 do mesmo WAV estereo. Sem saber isso,
um par que le o mesmo canal duas vezes (dual mono) ou os canais trocados
seria tratado como estereo normal.
"""

from __future__ import annotations

from .aaf_read_components import _descritor_fisico, _slot_id_de

PROFUNDIDADE_MAXIMA = 16


def canal_da_fonte(source_clip) -> int | None:
    """Canal do arquivo (1, 2, ...) lido pelo SourceClip; None quando nao se sabe."""
    mob = getattr(source_clip, "mob", None)
    slot_id = _slot_id_de(source_clip)
    visitados: set[int] = set()
    for _ in range(PROFUNDIDADE_MAXIMA):
        if mob is None or id(mob) in visitados:
            return None
        visitados.add(id(mob))
        slot = _slot_por_id(mob, slot_id)
        if slot is None:
            return None
        if _tem_descritor_de_arquivo(mob):
            return _numero_fisico(slot)
        mob, slot_id = _proximo(slot)
    return None


def _slot_por_id(mob, slot_id):
    """O slot do mob com o id pedido."""
    for slot in getattr(mob, "slots", None) or []:
        if getattr(slot, "slot_id", None) == slot_id:
            return slot
    return None


def _tem_descritor_de_arquivo(mob) -> bool:
    """O mob descreve um arquivo de audio, e nao fita ou filme."""
    descritor = getattr(mob, "descriptor", None)
    return descritor is not None and not _descritor_fisico(descritor)


def _numero_fisico(slot) -> int | None:
    """PhysicalTrackNumber do slot, quando o AAF o declara."""
    try:
        prop = slot.get("PhysicalTrackNumber")
        valor = prop.value if prop is not None else None
    except Exception:
        return None
    return int(valor) if isinstance(valor, int) and valor > 0 else None


def _proximo(slot):
    """Mob e slot id para onde o segmento do slot aponta."""
    segmento = getattr(slot, "segment", None)
    componentes = getattr(segmento, "components", None) or ([segmento] if segmento else [])
    for componente in componentes:
        if getattr(componente, "mob", None) is not None:
            return componente.mob, _slot_id_de(componente)
    return None, None
