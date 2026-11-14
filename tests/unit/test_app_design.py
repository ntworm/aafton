"""Testes do desenho do app: as regras de docs/DESIGN.md que um arquivo consegue provar."""

from __future__ import annotations

import base64
import re
import struct
from importlib import resources
from pathlib import Path

BRAND = Path(__file__).resolve().parents[2] / "resources" / "brand"

# The pixel sizes of the icon that ship as one PNG each.
PNG_SIZES = (16, 32, 48, 64, 128, 256, 512, 1024)

# The sizes inside the Windows icon; 256 is written as 0 in the directory.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# The first word of every colour token that is not the accent.
GROUND_INK_STATE = ("bg", "sunk", "surface", "raised", "rule", "ink", "label", "ok", "flux", "red")

HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLOUR_FUNCTION = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(")
ICON_LINK = re.compile(
    r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml;base64,([^"]+)">'
)


def _static(name: str) -> str:
    return (resources.files("aafton.studio") / "static" / name).read_text(encoding="utf-8")


def _rules(css: str) -> str:
    """The stylesheet without its comments and without the token block."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    start = css.index(":root{")
    return css[:start] + css[css.index("}", start) + 1 :]


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def _ico_sizes(data: bytes) -> list[int]:
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for index in range(count):
        width, height = data[6 + 16 * index], data[7 + 16 * index]
        assert width == height
        sizes.append(width or 256)
    return sorted(sizes)


def test_the_stylesheet_names_colours_only_in_the_token_block():
    """Uma cor fora de :root e uma cor fora do sistema."""
    rules = _rules(_static("studio.css"))
    assert HEX_COLOUR.findall(rules) == []
    assert COLOUR_FUNCTION.findall(rules) == []


def test_the_stylesheet_has_no_radius_no_gradient_and_no_soft_shadow():
    rules = _rules(_static("studio.css"))
    assert set(re.findall(r"border-radius:([^;}]+)", rules)) <= {"0"}
    assert "gradient(" not in rules
    assert "filter:" not in rules
    assert "text-shadow:" not in rules
    for shadow in re.findall(r"box-shadow:([^;}]+)", rules):
        assert re.fullmatch(r"inset -?\d+px 0 0 var\(--[a-z-]+\)", shadow), shadow


def test_the_stylesheet_has_one_accent():
    """O acento e o ambar; as outras cores com nome sao chao, tinta e estado."""
    css = _static("studio.css")
    start = css.index(":root{")
    tokens = re.findall(r"--([a-z0-9-]+):#", css[start : css.index("}", start)])
    accents = [name for name in tokens if name.split("-")[0] not in GROUND_INK_STATE]
    assert accents == ["amber", "amber-hi", "amber-lo"]


def test_the_page_carries_the_favicon_of_the_brand_folder():
    found = ICON_LINK.search(_static("index.html"))
    assert found
    assert base64.b64decode(found.group(1)) == (BRAND / "favicon.svg").read_bytes()


def test_the_brand_folder_holds_every_icon_size():
    for size in PNG_SIZES:
        assert _png_size((BRAND / f"aafton-{size}.png").read_bytes()) == (size, size), size
    assert _png_size((BRAND / "aafton-social-1200x630.png").read_bytes()) == (1200, 630)


def test_the_windows_icons_hold_their_sizes():
    assert _ico_sizes((BRAND / "aafton.ico").read_bytes()) == list(ICO_SIZES)
    assert _ico_sizes((BRAND / "favicon.ico").read_bytes()) == [16, 32, 48]


def test_the_macos_icon_is_an_icns_with_the_large_sizes():
    data = (BRAND / "aafton.icns").read_bytes()
    assert data[:4] == b"icns"
    assert struct.unpack(">I", data[4:8])[0] == len(data)
    kinds, pos = [], 8
    while pos < len(data):
        kinds.append(data[pos : pos + 4].decode("ascii"))
        pos += struct.unpack(">I", data[pos + 4 : pos + 8])[0]
    for kind in ("ic07", "ic08", "ic09", "ic10", "ic11", "ic12", "ic13", "ic14"):
        assert kind in kinds, kind


def test_the_symbol_files_are_drawings_without_text_or_script():
    for name in (
        "aafton-symbol.svg",
        "aafton-symbol-bare.svg",
        "aafton-wordmark.svg",
        "aafton-lockup.svg",
    ):
        text = (BRAND / name).read_text(encoding="utf-8")
        assert text.startswith("<svg "), name
        assert "<text" not in text and "<script" not in text and "href" not in text, name
    assert (BRAND / "favicon.svg").read_bytes() == (BRAND / "aafton-symbol.svg").read_bytes()
