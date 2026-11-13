"""Descobre a estrutura real do template .als e imprime um mapa.

Uso:
    python scripts/introspect_template.py templates/live12.als
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aafton.als_xml import load_als  # noqa: E402

PROTOTYPES = ("AudioTrack", "GroupTrack", "ReturnTrack", "MidiTrack", "AudioClip", "Locator")


def paths_of(root: ET.Element) -> dict[str, str]:
    """Mapeia cada elemento para o caminho de tags que leva ate ele."""
    result: dict[str, str] = {}

    def walk(node: ET.Element, prefix: str) -> None:
        for child in node:
            path = f"{prefix}/{child.tag}"
            result.setdefault(child.tag, path)
            walk(child, path)

    walk(root, root.tag)
    return result


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = load_als(sys.argv[1])
    print(f"Creator: {root.get('Creator')}")
    print(f"MinorVersion: {root.get('MinorVersion')}")
    print(f"SchemaChangeCount: {root.get('SchemaChangeCount')}")
    print()

    counts = Counter(node.tag for node in root.iter())
    print("REQUIRED PROTOTYPES")
    for tag in PROTOTYPES:
        status = "OK" if counts.get(tag, 0) else "MISSING"
        print(f"  {tag:14s} {counts.get(tag, 0):4d}  {status}")
    print()

    for tag in PROTOTYPES:
        node = root.find(f".//{tag}")
        if node is None:
            continue
        print(f"--- {tag} (direct children) ---")
        for child in node:
            value = child.get("Value")
            shown = f" Value={value!r}" if value is not None else ""
            print(f"  {child.tag}{shown}")
        print()

    clip = root.find(".//AudioClip")
    if clip is not None:
        print("--- AudioClip: full tree of tags ---")
        for path in sorted(set(paths_of(clip).values())):
            print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
