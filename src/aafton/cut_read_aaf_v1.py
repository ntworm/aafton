"""Escolha da camada de imagem (V1) dentro de um NestedScope.

O Avid grava a imagem num NestedScope com uma camada por slot, e o V1 vem
primeiro; a camada de graficos fica por cima. A regra do leitor e a mesma que
ele ja usa dentro da Sequence: vale o primeiro slot que tem SourceClip; sem
nenhum, vale o primeiro.

Escolhido o slot, o leitor desce ate a Sequence dele pela mesma regra:
OperationGroup pelo primeiro segmento, NestedScope pelo primeiro slot que tem
SourceClip. Sem Sequence, o segmento do slot e lido como esta.
"""

from __future__ import annotations

from .aaf_read_components import _contar_source_clips, _segmento_do_slot, _type_name
from .report import Report
from .timecode import TimecodeRate, frames_to_label

CODIGO_CAMADA_IGNORADA = "RECONFORM_UPPER_LAYER_IGNORED"


def _slot_com_clip(escopo):
    """Segmento do primeiro slot do escopo que tem SourceClip."""
    segmentos = [_segmento_do_slot(slot) for slot in getattr(escopo, "slots", None) or []]
    if not segmentos:
        return None
    for segmento in segmentos:
        if _contar_source_clips(segmento) > 0:
            return segmento
    return segmentos[0]


def _descida_do_slot(segmento) -> tuple[object | None, list]:
    """Sequence do segmento de um slot de imagem e os escopos da descida.

    A descida segue a regra do primeiro clip: OperationGroup pelo primeiro
    segmento, NestedScope pelo primeiro slot que tem SourceClip. Os
    NestedScopes atravessados na descida sao devolvidos, para que o leitor
    avise das camadas de cima de cada um deles. Sem Sequence, a descida
    devolve None e quem chama le o segmento do slot como esta.
    """
    atual = segmento
    visitados: set[int] = set()
    escopos: list = []
    while atual is not None and id(atual) not in visitados:
        visitados.add(id(atual))
        tipo = _type_name(atual)
        if tipo == "Sequence":
            return atual, escopos
        if tipo == "NestedScope":
            escopos.append(atual)
            atual = _slot_com_clip(atual)
        elif tipo == "OperationGroup":
            segmentos = list(getattr(atual, "segments", None) or [])
            atual = segmentos[0] if segmentos else None
        else:
            return None, escopos
    return None, escopos


def _avisar_camadas_superiores(
    report: Report, rate: TimecodeRate, start_frames: int, escopo
) -> None:
    """Avisa quando outra camada tem clip sobre um trecho sem clip da escolhida.

    A camada escolhida e a primeira que tem SourceClip; as outras camadas
    ficam de fora do corte, e o reader diz onde.
    """
    camadas = [_segmento_do_slot(slot) for slot in getattr(escopo, "slots", None) or []]
    if len(camadas) < 2:
        return
    escolhida = _indice_escolhido(camadas)
    for comeco in _comecos_ignorados(camadas, escolhida):
        _reportar_camada_ignorada(report, rate, start_frames, comeco)


def _indice_escolhido(camadas: list) -> int:
    """Indice da camada escolhida: a primeira que tem SourceClip."""
    for indice, camada in enumerate(camadas):
        if _contar_source_clips(camada) > 0:
            return indice
    return 0


def _comecos_ignorados(camadas: list, escolhida: int) -> list[int]:
    """Comecos dos trechos em que outra camada tem clip e a escolhida nao."""
    vazios = [
        (inicio, fim) for inicio, fim, tem_clip in _trechos(camadas[escolhida]) if not tem_clip
    ]
    comecos: list[int] = []
    for indice, camada in enumerate(camadas):
        if indice == escolhida:
            continue
        for inicio, fim, tem_clip in _trechos(camada):
            if not tem_clip:
                continue
            for vazio_ini, vazio_fim in vazios:
                comeco = max(inicio, vazio_ini)
                if comeco < min(fim, vazio_fim) and comeco not in comecos:
                    comecos.append(comeco)
    return sorted(comecos)


def _trechos(camada) -> list[tuple[int, int, bool]]:
    """Trechos (inicio, fim, tem_clip) de uma camada do NestedScope.

    A transicao cobre o fim do plano anterior, e o leitor de corte recua o
    cursor pelo comprimento dela; a conta dos trechos e a mesma.
    """
    if _type_name(camada) == "Sequence":
        componentes = list(getattr(camada, "components", None) or [])
    else:
        componentes = [camada]
    trechos: list[tuple[int, int, bool]] = []
    cursor = 0
    for componente in componentes:
        comprimento = int(getattr(componente, "length", 0) or 0)
        if _type_name(componente) == "Transition":
            cursor -= comprimento
            continue
        trechos.append((cursor, cursor + comprimento, _contar_source_clips(componente) > 0))
        cursor += comprimento
    return trechos


def _reportar_camada_ignorada(
    report: Report, rate: TimecodeRate, start_frames: int, comeco: int
) -> None:
    """Registra o info da camada de cima que ficou de fora do corte."""
    frames = start_frames + comeco
    tc = frames_to_label(frames, rate)
    report.info(
        CODIGO_CAMADA_IGNORADA,
        f"the layer above has a clip over the gap at {tc}; the picture cut reads "
        f"only the first layer with clips",
        {"timecode": tc, "frames": frames},
    )
