"""Leitura da origem para a proposta de organizacao (inspect e Studio).

A proposta tem de ver a mesma midia que o import ve: a essencia embarcada no
AAF e extraida e a midia solta e resolvida, para o sinal de metadados ler o
iXML e o bext dos WAVs. A pasta de trabalho recebe as extracoes e precisa
existir enquanto a proposta e montada.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from .formats.origem import read_origin
from .ir import SessionIR
from .organize_apply import annotate_origin_keys
from .pipeline import media_roots, resolve_media
from .report import Report


def read_for_proposal(
    origem: str | Path,
    pasta_trabalho: Path,
    *,
    audio_dir: str | None = None,
    edl_rate=None,
) -> SessionIR:
    """Le AAF, EDL ou XML como o import, com chaves de origem marcadas."""
    report = Report(strict=False)
    extracao = pasta_trabalho / "media"
    extracao.mkdir(parents=True, exist_ok=True)
    sessao = read_origin(
        Path(origem),
        report,
        extract_dir=extracao,
        fades="absorb",
        edl_rate=edl_rate,
        media_roots=media_roots(str(origem), audio_dir),
        mxf_dir=extracao,
    )
    opcoes = SimpleNamespace(
        input=str(origem), audio_dir=audio_dir, output=str(pasta_trabalho / "proposta.als")
    )
    resolve_media(opcoes, sessao, report)
    annotate_origin_keys(sessao)
    return sessao
