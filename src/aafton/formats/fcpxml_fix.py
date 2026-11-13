"""Tempos do FCPXML em fracao exata.

O leitor de FCPXML do produto le o documento com o proprio codigo, sem o
OpenTimelineIO. O unico ajuste de que o formato precisa e a conversao de um
tempo do FCPXML (ex.: '3600s', '100/2500s') em segundos exatos, sem
arredondar para o frame.
"""

from __future__ import annotations

from fractions import Fraction


def tempo_fcpxml(texto: str) -> Fraction:
    """Converte um tempo FCPXML (ex.: '3600s', '100/2500s') em segundos exatos."""
    bruto = texto.strip()
    if not bruto.endswith("s"):
        raise ValueError(f"invalid fcpxml time: {texto}")
    try:
        return Fraction(bruto[:-1])
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid fcpxml time: {texto}") from exc
