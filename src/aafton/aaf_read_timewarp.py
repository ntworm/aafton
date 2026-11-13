"""Deteccao do efeito de velocidade (time warp) na leitura do AAF.

O Media Composer grava o efeito de velocidade como um OperationGroup cuja
OperationDef traz IsTimeWarp. A operacao pode estar no topo da Sequence ou
escondida dentro de outro OperationGroup, como o 'Audio Gain' que embrulha
o warp; nos dois casos o audio chega com o tempo trocado.
"""

from __future__ import annotations

from .aaf_read_components import _type_name, _valor_de_propriedade

PROFUNDIDADE_MAXIMA = 16


def _operation_def(componente):
    """A OperationDef de um OperationGroup, quando ela existe."""
    try:
        operation = getattr(componente, "operation", None)
    except Exception:
        operation = None
    if operation is None:
        try:
            operation = getattr(componente, "operationdef", None)
        except Exception:
            operation = None
    return operation


def _parametro_named(parametros, nome) -> object | None:
    """Valor de um parametro da operacao, pelo nome, quando ele existir."""
    for parametro in parametros or []:  # pyaaf2 expoe name e value como atributo
        try:
            chave, valor = (getattr(parametro, "name", None), getattr(parametro, "value", None))
            chave = chave if chave is not None else _valor_de_propriedade(parametro, "Name")
            valor = valor if valor is not None else _valor_de_propriedade(parametro, "Value")
            if str(chave) == nome:
                return valor
        except Exception:
            continue
    return None


def _filhos_do_time_warp(no) -> list:
    """Filhos de `no` onde o grupo de efeito de velocidade pode estar.

    OperationGroup usa os segmentos e Sequence usa os components;
    os demais tipos de no nao tem filho para procurar.
    """
    if _type_name(no) == "OperationGroup":
        return list(getattr(no, "segments", None) or [])
    if _type_name(no) == "Sequence":
        return list(getattr(no, "components", None) or [])
    return []


def _tem_is_time_warp(no) -> bool:
    """O OperationGroup `no` traz a propriedade IsTimeWarp?"""
    operation_def = _operation_def(no)
    if operation_def is None:
        return False
    return bool(_valor_de_propriedade(operation_def, "IsTimeWarp"))


def _grupo_do_time_warp(no, visitados: set[int], profundidade: int):
    """OperationGroup com IsTimeWarp, procurado em `no` e nos filhos dele."""
    if profundidade > PROFUNDIDADE_MAXIMA or id(no) in visitados:
        return None
    visitados.add(id(no))
    if _type_name(no) == "OperationGroup" and _tem_is_time_warp(no):
        return no
    for filho in _filhos_do_time_warp(no):
        achado = _grupo_do_time_warp(filho, visitados, profundidade + 1)
        if achado is not None:
            return achado
    return None


def _is_time_warp(componente) -> bool:
    """O componente e um OperationGroup de efeito de velocidade?

    O efeito pode vir no topo da Sequence ou dentro de outro grupo.
    """
    if _type_name(componente) != "OperationGroup":
        return False
    return _grupo_do_time_warp(componente, set(), 0) is not None


def _descricao_do_time_warp(componente, nome_da_track: str) -> tuple[str, dict]:
    """Mensagem e contexto do erro do efeito de velocidade.

    O nome e o SpeedRatio vem do OperationGroup que carrega o IsTimeWarp.
    """
    grupo = _grupo_do_time_warp(componente, set(), 0)
    operation_def = _operation_def(grupo) if grupo is not None else None
    nome = _valor_de_propriedade(operation_def, "Name") if operation_def is not None else None
    nome = str(nome) if nome else "unknown"
    parametros = getattr(grupo, "parameters", []) if grupo is not None else []
    ratio = _parametro_named(parametros, "SpeedRatio")
    mensagem = f"the operation {nome!r} in the track {nome_da_track!r} changes the audio time"
    if ratio is not None:
        mensagem += f" (SpeedRatio {ratio})"
    mensagem += "; the reader does not import that effect"
    return mensagem, {"track": nome_da_track, "operation": nome}
