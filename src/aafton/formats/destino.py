"""Escolha do formato de saida pelo sufixo do arquivo de destino.

O sufixo desconhecido e erro, nunca um arquivo silencioso.
"""

from __future__ import annotations

from pathlib import Path

from ..aaf_write import write_aaf
from ..ir import SessionIR
from ..report import Report
from .edl_write import write_edl
from .fcpxml_write import write_fcpxml
from .otio_write import write_fcp_xml


def write_destination(
    session: SessionIR, destination: str | Path, report: Report, *, media: str = "link"
) -> None:
    """Grava a sessao no formato pedido pelo sufixo do destino.

    A opcao ``media`` so vale para o AAF: link referencia os arquivos e embed
    incorpora o audio. Os demais formatos ignoram esse parametro.
    """
    caminho = Path(destination)
    sufixo = caminho.suffix.lower()
    if sufixo == ".aaf":
        write_aaf(session, caminho, report, media=media)
    elif sufixo == ".edl":
        write_edl(session, caminho, report)
    elif sufixo == ".xml":
        write_fcp_xml(session, caminho, report)
    elif sufixo == ".fcpxml":
        write_fcpxml(session, caminho, report)
    else:
        report.error("FORMAT_UNKNOWN_SUFFIX", f"format not supported: {sufixo}")
