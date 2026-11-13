"""Contagem e formatacao da cobertura da verificacao AAF -> ALS.

Cobertura responde a duas perguntas. Quantos eventos de audio foram comparados
depois da comparacao (a resposta sai das linhas do relatorio) e quantos slots
de origem o .als mapeia (a resposta sai do sidecar). Slot mapeado nao prova
evento comparado: o slot pode estar mapeado e nao ter evento nenhum, e video
nao conta como slot de audio.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mapeamento:
    """Slots de audio da origem e slots cobertos pelo mapa do .als."""

    slots_origem: int
    slots_mapeados: int
    tracks_destino: int
    nao_mapeados: tuple[int, ...] = ()


def slots_por_track(origem) -> list[list[int]]:
    """Indices de origem de cada faixa da origem, na ordem em que a origem traz."""
    return [list(track.original_source_indices or [track.source_index]) for track in origem.tracks]


def universo_de_slots(origem) -> set[int]:
    """Indices de origem das faixas de audio: video fora, indice negativo fora."""
    universo: set[int] = set()
    for track, indices in zip(origem.tracks, slots_por_track(origem)):
        if getattr(track, "is_video", False):
            continue
        for indice in indices:
            if indice >= 0:
                universo.add(indice)
    return universo


def mapeamento_de_slots(origem, source_indices) -> Mapeamento | None:
    """Slots de audio cobertos pelo mapa do .als; None sem mapa de identidade."""
    if source_indices is None:
        return None
    universo = universo_de_slots(origem)
    indices = [indice for trilha in source_indices for indice in trilha if indice >= 0]
    mapeados = universo.intersection(indices)
    tracks = sum(1 for trilha in source_indices if any(indice >= 0 for indice in trilha))
    return Mapeamento(
        slots_origem=len(universo),
        slots_mapeados=len(mapeados),
        tracks_destino=tracks,
        nao_mapeados=tuple(sorted(universo.difference(mapeados))),
    )


def formatar_cobertura(resultado, mapeamento: Mapeamento | None = None) -> str:
    """Monta a linha de cobertura depois da comparacao.

    O resultado entra por duck typing: o verify passa o proprio VerifyResult e
    este modulo nao importa o verify para nao fechar um ciclo.
    """
    partes = [
        f"Coverage: {resultado.audio_compared} of {resultado.audio_expected} audio events "
        f"compared ({resultado.audio_missing} missing, {resultado.audio_extra} extra)"
    ]
    if mapeamento is not None:
        partes.append(_formatar_mapeamento(mapeamento))
    return "; ".join(partes)


def _formatar_mapeamento(mapeamento: Mapeamento) -> str:
    """Monta a parte de mapeamento, separada da contagem de eventos."""
    texto = (
        f"Mapping: {mapeamento.slots_mapeados} of {mapeamento.slots_origem} slots mapped, "
        f"across {mapeamento.tracks_destino} tracks of the .als"
    )
    if mapeamento.nao_mapeados:
        faltando = ", ".join(str(indice) for indice in mapeamento.nao_mapeados)
        texto += f"; {len(mapeamento.nao_mapeados)} not mapped: {faltando}"
    return texto
