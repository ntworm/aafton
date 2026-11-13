"""Formatos de intercambio: EDL, XML do FCP e FCPXML.

A EDL e o XML do FCP passam pelo OpenTimelineIO e precisam do extra formats.
O FCPXML e lido e escrito pelo codigo do produto e nao precisa do extra. Sem
ele o pacote ainda importa: quem precisar do extra recebe ExtraAusente.
"""

ADAPTADORES = {
    ".edl": "cmx_3600",
    ".xml": "fcp_xml",
    ".fcpxml": "fcpx_xml",
}

MENSAGEM_EXTRA = "EDL and FCP XML support needs the formats extra: pip install 'aafton[formats]'"


class ExtraAusente(RuntimeError):
    """Erro levantado quando o extra opcional formats nao esta instalado."""


def carregar_otio():
    """Importa o opentimelineio agora; levanta ExtraAusente quando falta."""
    try:
        import opentimelineio
    except ImportError as exc:
        raise ExtraAusente(MENSAGEM_EXTRA) from exc
    return opentimelineio
