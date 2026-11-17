"""Conferencia dos ClipEnvelopes dos clips da copia aplicada.

Cada peca de um clip fatiado tem de carregar os eventos do ClipEnvelope do
clip de origem que caem no seu trecho, rebaseados para o inicio da peca, e
os sentinelas que o envelope de origem carrega.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from .als_write_base import TIMELINE, samples_to_beats
from .reconform_verify import _base_saiu
from .reconform_verify_extra import _ClipCampos, _mesmo_valor
from .sidecar import Anchor

_CAMINHO_ENVELOPES = "Envelopes/Envelopes/ClipEnvelope"


def _candidato_de_origem(piece: _ClipCampos, origem: _ClipCampos) -> bool:
    """Verdadeiro quando o clip de origem pode ter gerado a peca."""
    base = _base_saiu(piece.track) or piece.track
    return (
        piece.caminho == origem.caminho
        and base == origem.track
        and origem.loop_start <= piece.loop_start < origem.loop_end
    )


def _tem_envelope(clip: _ClipCampos) -> bool:
    """Verdadeiro quando o clip carrega pelo menos um ClipEnvelope."""
    return bool(clip.node.findall(_CAMINHO_ENVELOPES))


def _numero_de_envelopes(clip: _ClipCampos) -> int:
    """Quantos ClipEnvelopes o clip carrega."""
    return len(clip.node.findall(_CAMINHO_ENVELOPES))


def _candidatos_com_envelope(piece: _ClipCampos, origem: list[_ClipCampos]) -> list[_ClipCampos]:
    """Candidatos de origem com envelope que a peca pode ter seguido.

    O numero de envelopes separa as pecas: uma peca com o mesmo numero de
    envelopes do candidato veio dele. Sem essa pista a origem da peca fica
    ambigua, e a peca nao e conferida.
    """
    candidatos = [c for c in origem if _candidato_de_origem(piece, c)]
    com_envelope = [c for c in candidatos if _tem_envelope(c)]
    if not com_envelope:
        return []
    if any(_numero_de_envelopes(piece) == _numero_de_envelopes(c) for c in com_envelope):
        return com_envelope
    if any(not _tem_envelope(c) for c in candidatos):
        return []
    return com_envelope


def _eventos(env: ET.Element) -> list[tuple[float, str]]:
    """Tempo e valor de cada evento do ClipEnvelope."""
    caixa = env.find("Automation/Events")
    if caixa is None:
        return []
    saida: list[tuple[float, str]] = []
    for ev in caixa:
        bruto = ev.get("Time")
        if bruto is None:
            continue
        try:
            tempo = float(bruto)
        except ValueError:
            continue
        if not math.isfinite(tempo):
            continue
        saida.append((tempo, ev.get("Value") or ""))
    return saida


def _pontos(env: ET.Element) -> list[tuple[float, str]]:
    """Eventos nao-sentinela do ClipEnvelope, com tempo e valor."""
    return [(tempo, valor) for tempo, valor in _eventos(env) if tempo >= 0.0]


def _sentinelas(env: ET.Element) -> list[tuple[float, str]]:
    """Eventos sentinela do ClipEnvelope, com tempo e valor."""
    return [(tempo, valor) for tempo, valor in _eventos(env) if tempo < 0.0]


def _tolerancia(anchor: Anchor) -> float:
    """Uma amostra em beats do clip."""
    return samples_to_beats(1, anchor.sample_rate, anchor.bpm, TIMELINE)


def _faixa(piece: _ClipCampos, origem: _ClipCampos, anchor: Anchor) -> tuple[float, float]:
    """Inicio e fim, em beats do clip, da parte da fonte que a peca cobre."""
    offset = round((piece.loop_start - origem.loop_start) * anchor.sample_rate)
    inicio = samples_to_beats(offset, anchor.sample_rate, anchor.bpm, TIMELINE)
    duracao = round((piece.loop_end - piece.loop_start) * anchor.sample_rate)
    fim = inicio + samples_to_beats(duracao, anchor.sample_rate, anchor.bpm, TIMELINE)
    return inicio, fim


def _evento_presente(lista: list[tuple[float, str]], tempo: float, valor: str, tol: float) -> bool:
    """Verdadeiro quando algum evento tem o valor pedido na posicao."""
    return any(abs(t - tempo) <= tol and _mesmo_valor(v, valor) for t, v in lista)


def _envelope_confere(
    piece: ET.Element, origem: ET.Element, inicio: float, fim: float, tol: float
) -> bool:
    """Confere os eventos de um ClipEnvelope da peca contra os da origem."""
    for tempo, valor in _pontos(origem):
        if not (inicio - tol <= tempo <= fim + tol):
            continue
        if not _evento_presente(_pontos(piece), tempo - inicio, valor, tol):
            return False
    for tempo, valor in _sentinelas(origem):
        if not _evento_presente(_sentinelas(piece), tempo, valor, tol):
            return False
    return True


def _conferir_peca(piece: _ClipCampos, origem: _ClipCampos, anchor: Anchor) -> bool:
    """Confere os envelopes da peca contra os de um clip de origem."""
    tol = _tolerancia(anchor)
    inicio, fim = _faixa(piece, origem, anchor)
    envs_piece = piece.node.findall(_CAMINHO_ENVELOPES)
    envs_origem = origem.node.findall(_CAMINHO_ENVELOPES)
    if len(envs_piece) != len(envs_origem):
        return False
    return all(
        _envelope_confere(env_p, env_o, inicio, fim, tol)
        for env_p, env_o in zip(envs_piece, envs_origem)
    )


def conferir_clip_envelopes(
    ctx,
    origem: list[_ClipCampos],
    aplicados: list[_ClipCampos],
    anchor: Anchor,
) -> None:
    """Confere os ClipEnvelopes dos clips da copia contra os da origem."""
    for piece in aplicados:
        candidatos = _candidatos_com_envelope(piece, origem)
        if not candidatos:
            continue
        if any(_conferir_peca(piece, c, anchor) for c in candidatos):
            continue
        ctx.problema(
            "RECONFORM_VERIFY_CLIP_ENVELOPE",
            piece.track,
            f"the clip at {piece.inicio} lost an event of the clip envelope",
        )
