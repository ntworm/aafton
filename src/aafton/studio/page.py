"""Studio page assembled from the static files.

The markup, the stylesheet and the three scripts of ``SCRIPTS`` live in
``static/``. The page served by ``GET /`` is the markup with the stylesheet
and the scripts, joined in the order of ``SCRIPTS``, put in the place of the
two markers the markup holds.
"""

from importlib import resources

CSS_MARK = "/*__STUDIO_CSS__*/"
JS_MARK = "/*__STUDIO_JS__*/"

# The scripts of the page, in the order they enter the same script.
SCRIPTS = ("app-core.js", "app-sheet.js", "app-flow.js")


def _static(nome: str) -> str:
    return (resources.files(__package__) / "static" / nome).read_text(encoding="utf-8")


def load_page() -> str:
    """The whole Studio page."""
    html = _static("index.html")
    scripts = "\n".join(_static(nome) for nome in SCRIPTS)
    return html.replace(CSS_MARK, _static("studio.css")).replace(JS_MARK, scripts)


def load_timecode_page() -> str:
    """The timecode clock page."""
    return _static("timecode.html")
