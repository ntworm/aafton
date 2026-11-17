"""Acumulador das tracks de destino da organizacao de sessao.

Guarda o _HomeBuilder, o acumulador mutavel que cada track de destino usa
enquanto a disposicao e montada, e o teste de encaixe de uma unidade na track.
Fica fora de organize_plan.py para o modulo da compactacao poder usar o mesmo
acumulador sem importar o plano.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .organize_units import Unit


@dataclass
class _HomeBuilder:
    """Acumulador mutavel interno para construir uma track de destino."""

    kind: str
    source_track: str | None
    source_indices: tuple[int, ...]
    channel_count: int
    units: list[Unit] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)
    key: str = ""


def _unit_fits(spans: list[tuple[int, int]], unit: Unit) -> bool:
    """Verifica se a unidade cabe na track sem sobrepor nenhuma unidade existente."""
    return all(unit.end_samples <= start or unit.start_samples >= end for start, end in spans)
