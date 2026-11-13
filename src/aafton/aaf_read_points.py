"""Leitura dos pontos de automacao de ganho (PointList) de um OperationGroup.

Cada funcao aqui cuida de um passo da leitura, para deixar o leitor de aaf_read
(aaf_read._read_point_list) curto. _read_point_list continua em aaf_read porque
os testes a substituem por monkeypatch.
"""

from __future__ import annotations


def is_varying(parametro) -> bool:
    """Diz se o parametro e um VaryingValue, ou seja, tem PointList."""
    tipo = type(parametro).__name__
    return (
        tipo == "VaryingValue"
        or "VaryingValue" in tipo
        or hasattr(parametro, "point_list")
        or hasattr(parametro, "pointlist")
        or (hasattr(parametro, "__contains__") and "PointList" in parametro)
    )


def _ratio_value(valor) -> float:
    """Converte numerator/denominator em float; 1.0 quando nao da para converter."""
    numerador = getattr(valor, "numerator", None)
    denominador = getattr(valor, "denominator", None)
    if numerador is not None and denominador:
        return float(numerador) / float(denominador)
    return 1.0


def constant_points(parametro) -> list[tuple[float, float]]:
    """Pontos de um ConstantValue: dois pontos com o mesmo valor."""
    valor = getattr(parametro, "value", None)
    if valor is None:
        return []
    try:
        v = float(valor)
    except (TypeError, ValueError):
        v = _ratio_value(valor)
    return [(0.0, v), (1.0, v)]


def point_list_of(parametro) -> list | None:
    """PointList de um parametro variavel, ou None quando nao ha PointList."""
    if hasattr(parametro, "point_list"):
        return parametro.point_list
    if hasattr(parametro, "pointlist"):
        return parametro.pointlist
    if hasattr(parametro, "get"):
        prop = parametro.get("PointList")
        if prop is not None:
            return prop.value if hasattr(prop, "value") else prop
        return None
    if hasattr(parametro, "__getitem__"):
        try:
            prop = parametro["PointList"]
            return prop.value if hasattr(prop, "value") else prop
        except Exception:
            return None
    return None


def _read_field(cp, atributos: tuple[str, str], itens: tuple[str, str]):
    """Le um campo de um ponto: atributo, atributo capitalizado ou item."""
    for nome in atributos:
        valor = getattr(cp, nome, None)
        if valor is not None:
            return valor
    if not hasattr(cp, "__getitem__"):
        return None
    for nome in itens:
        try:
            valor = cp[nome]
        except Exception:
            continue
        if valor is not None:
            return valor
    return None


def _read_time(cp):
    """Tempo de um ponto de controle, ou None quando nao ha."""
    return _read_field(cp, ("time", "Time"), ("Time", "time"))


def _read_value(cp):
    """Valor de um ponto de controle, ou None quando nao ha."""
    valor = _read_field(cp, ("value", "Value"), ("Value", "value"))
    if hasattr(valor, "value"):
        valor = valor.value
    return valor


def _pair_from_sequence(cp) -> tuple[float, float] | None:
    """Par (tempo, valor) de um ponto guardado como tupla ou lista."""
    try:
        return (float(cp[0]), float(cp[1]))
    except (TypeError, ValueError):
        return None


def _pair_from_values(t_attr, v_attr) -> tuple[float, float] | None:
    """Par (tempo, valor) a partir do tempo e do valor lidos."""
    try:
        return (float(t_attr), float(v_attr))
    except (TypeError, ValueError):
        return None


def _append_raw_number(valor, raw_values) -> None:
    """Acrescenta um numero bruto a lista de valores sem tempo."""
    try:
        raw_values.append(float(valor))
    except (TypeError, ValueError):
        pass


def _append_raw_value(valor, raw_values) -> None:
    """Acrescenta um valor bruto, com fallback de numerator/denominator."""
    try:
        raw_values.append(float(valor))
        return
    except (TypeError, ValueError):
        pass
    numerador = getattr(valor, "numerator", None)
    denominador = getattr(valor, "denominator", None)
    if numerador is not None and denominador:
        raw_values.append(float(numerador) / float(denominador))


def _append_object_point(cp, result, raw_values) -> None:
    """Le um ponto de controle que e um objeto, com atributos ou itens."""
    t_attr = _read_time(cp)
    v_attr = _read_value(cp)
    if t_attr is not None and v_attr is not None:
        par = _pair_from_values(t_attr, v_attr)
        if par is not None:
            result.append(par)
        return
    if v_attr is not None:
        _append_raw_value(v_attr, raw_values)


def append_control_point(cp, result, raw_values) -> None:
    """Converte um ponto de controle em par (tempo, valor) ou em valor bruto."""
    if isinstance(cp, (tuple, list)) and len(cp) >= 2:
        par = _pair_from_sequence(cp)
        if par is not None:
            result.append(par)
        return
    if isinstance(cp, (int, float)):
        _append_raw_number(cp, raw_values)
        return
    _append_object_point(cp, result, raw_values)


def choose_points(result, raw_values) -> list[tuple[float, float]]:
    """Escolhe os pontos: pares com tempo, valores brutos espacados ou vazio."""
    if result:
        return result
    # Fallback: only raw values without time info
    if raw_values:
        step = 1.0 / max(len(raw_values) - 1, 1)
        return [(i * step, v) for i, v in enumerate(raw_values)]
    return []
