"""Linha do relatorio com o resultado da compactacao da organizacao de sessao."""

from __future__ import annotations

from .organize_plan import Proposal
from .report import Report


def relatar_compactacao(proposal: Proposal, report: Report) -> None:
    """Relata quantas tracks cada grupo tinha e quantas ficaram depois de compactar."""
    if not proposal.compactacao:
        return
    partes = [f"{kind} {antes}->{depois}" for kind, (antes, depois) in proposal.compactacao.items()]
    report.info(
        "ORGANIZE_COMPACTED",
        "tracks per group after compacting: " + ", ".join(partes),
        {
            "groups": {
                kind: [antes, depois] for kind, (antes, depois) in proposal.compactacao.items()
            }
        },
    )
