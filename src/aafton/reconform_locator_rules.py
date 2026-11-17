"""Regras dos locators que a reconformacao mantem fixos.

Fica em um modulo proprio para que o verify do reconform possa usar a regra
sem carregar o apply, que depende do mapa de tempo e do diff dos cortes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .als_xml import get_value
from .timecode import _LABEL_RE


def _is_fixed_locator(loc: ET.Element, name: str) -> bool:
    """Verdadeiro para ancora TC, IsSongStart ou marcador de timecode puro."""
    song_start = (get_value(loc, "IsSongStart") or "").strip().lower() == "true"
    return song_start or name.startswith("TC ") or bool(_LABEL_RE.match(name))
