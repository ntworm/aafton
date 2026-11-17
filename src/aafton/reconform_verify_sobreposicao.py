"""Conferencia de clips sobrepostos na mesma track da copia aplicada.

Dois AudioClips sobrepostos na mesma track do Live nao tocam os dois: um
esconde o outro. O verify confere isso direto na copia, sem o mapa.
"""

from __future__ import annotations

from .reconform_verify import _Clip, _Ctx

# Uma amostra: a folga da grade de ticks do Live entre um clip e o seguinte.
_TOLERANCIA = 1


def conferir_sobreposicao(ctx: _Ctx, aplicado: dict[str, list[_Clip]]) -> None:
    """Acusa RECONFORM_VERIFY_OVERLAP para cada par de clips sobrepostos."""
    for nome, clips in aplicado.items():
        ordem = sorted(clips, key=lambda clip: (clip.start, clip.end))
        for anterior, seguinte in zip(ordem, ordem[1:]):
            if seguinte.start < anterior.end - _TOLERANCIA:
                ctx.problema(
                    "RECONFORM_VERIFY_OVERLAP",
                    nome,
                    f"clips overlap: {anterior.start}-{anterior.end} and "
                    f"{seguinte.start}-{seguinte.end}",
                )
