"""Despeja a estrutura de um AAF para descobrir o dialeto do programa de origem.

Uso:
    python scripts/dump_aaf.py tests/fixtures/aaf/dialect_resolve.aaf
"""

from __future__ import annotations

import sys
from collections import Counter

import aaf2


def describe(component, depth: int) -> None:
    tipo = type(component).__name__
    comprimento = getattr(component, "length", None)
    inicio = getattr(component, "start", None)
    extra = []
    if comprimento is not None:
        extra.append(f"length={comprimento}")
    if inicio is not None:
        extra.append(f"start={inicio}")
    mob = getattr(component, "mob", None)
    if mob is not None:
        extra.append(f"mob={getattr(mob, 'name', None)!r}")
    parametros = getattr(component, "parameters", None)
    if parametros:
        nomes = [getattr(p, "name", type(p).__name__) for p in parametros]
        extra.append(f"params={nomes}")
    print("  " * depth + f"{tipo} " + " ".join(extra))
    for filho in getattr(component, "components", []) or []:
        describe(filho, depth + 1)
    interno = getattr(component, "segments", None) or []
    for filho in interno:
        describe(filho, depth + 1)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    with aaf2.open(sys.argv[1], "r") as f:
        print(f"=== {sys.argv[1]} ===")
        contagem = Counter(type(mob).__name__ for mob in f.content.mobs)
        print(f"Mobs: {dict(contagem)}")
        print()
        for mob in f.content.mobs:
            print(f"--- {type(mob).__name__} {mob.name!r} ---")
            for slot in mob.slots:
                tipo_slot = type(slot).__name__
                taxa = getattr(slot, "edit_rate", None)
                print(f"  {tipo_slot} id={slot.slot_id} name={slot.name!r} edit_rate={taxa}")
                segmento = getattr(slot, "segment", None)
                if segmento is not None:
                    describe(segmento, 2)
            descritor = getattr(mob, "descriptor", None)
            if descritor is not None:
                print(f"  descriptor={type(descritor).__name__}")
                for atributo in ("sample_rate", "channels", "length", "file_name"):
                    if hasattr(descritor, atributo):
                        print(f"    {atributo}={getattr(descritor, atributo)}")
                for locator in getattr(descritor, "locators", []) or []:
                    print(
                        f"    locator={getattr(locator, 'path', None) or getattr(locator, 'url', None)}"
                    )
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
