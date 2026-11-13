"""OperationGroup de ganho que envolve uma Sequence: ganho sobre um grupo de clips.

O Avid e o OTIO gravam assim um ganho aplicado a varios clips de uma vez. A
Sequence de dentro e lida no mesmo estado da track, como uma Sequence
aninhada, e o ganho constante de cada grupo da cadeia e multiplicado no
ganho de cada clip lido ali.

So entram grupos de ganho conhecidos. Pan, plugin ou qualquer outra operacao
sobre a Sequence, e ganho que varia no tempo, seguem o caminho antigo, que e
erro explicito: aplicar so parte do efeito seria perder audio em silencio.
"""

from __future__ import annotations

from .aaf_read_components import _type_name

PROFUNDIDADE_MAXIMA = 16
OPERACOES_DE_GANHO = frozenset({"Audio Gain", "Mono Audio Gain"})


def sequencia_do_grupo(componente):
    """A Sequence envolvida e a lista de parametros de cada grupo de ganho.

    Desce por OperationGroups de ganho com um segmento so. Devolve
    (sequence, [parametros_do_grupo, ...]) ou None quando o componente nao e
    esse caso.
    """
    if _type_name(componente) != "OperationGroup":
        return None
    grupos: list[list] = []
    atual = componente
    for _ in range(PROFUNDIDADE_MAXIMA):
        if _type_name(atual) == "Sequence":
            return (atual, grupos) if grupos else None
        if _type_name(atual) != "OperationGroup" or not _e_ganho(atual):
            return None
        grupos.append(list(getattr(atual, "parameters", None) or []))
        segmentos = list(getattr(atual, "segments", None) or [])
        if len(segmentos) != 1:
            return None
        atual = segmentos[0]
    return None


def _e_ganho(grupo) -> bool:
    """O OperationGroup e uma operacao de ganho conhecida."""
    operacao = getattr(grupo, "operation", None) or getattr(grupo, "operationdef", None)
    return str(getattr(operacao, "name", "") or "") in OPERACOES_DE_GANHO


def componente_grupo_sequencia(estado, componente, comprimento, ler_sequencia) -> bool:
    """Le o grupo como Sequence aninhada com o ganho da cadeia; False se nao couber."""
    achado = sequencia_do_grupo(componente)
    if achado is None:
        return False
    sequencia, grupos = achado
    ctx = estado.ctx
    if any(ctx.has_varying_gain(parametros) for parametros in grupos):
        return False
    ganho = 1.0
    for parametros in grupos:
        ganho *= ctx.constant_gain(parametros, ctx.report, estado.track.name)
    antes = len(estado.raw_items)
    ler_sequencia(estado, sequencia, comprimento)
    for item in estado.raw_items[antes:]:
        if item.get("clip_assets"):
            # Acumula: um grupo dentro de outro ja marcou o proprio ganho.
            item["group_gain"] = item.get("group_gain", 1.0) * ganho
        for clip, _asset in item.get("clip_assets") or []:
            clip.gain_linear *= ganho
    ctx.report.info(
        "AAF_GROUP_SEQUENCE_READ",
        f"a gain over a group of clips in the track {estado.track.name!r} was read "
        f"clip by clip, with gain {ganho:.4f} applied to each one",
        {"track": estado.track.name, "gain": ganho},
    )
    return True


def conferir_ganho_de_grupo_com_envelope(ctx, track, raw_items) -> None:
    """Erro quando a track virou envelope de ganho e tem grupo com ganho.

    A curva da track zera o ganho de cada clip e so le os parametros de cada
    item; o ganho de um grupo nao entra nela e se perderia.
    """
    if not track.gain_points:
        return
    perdidos = [it for it in raw_items if abs(it.get("group_gain", 1.0) - 1.0) > 1e-9]
    if perdidos:
        ctx.report.error(
            "AAF_GROUP_GAIN_WITH_TRACK_AUTOMATION",
            f"the track {track.name!r} has gain automation and a gain over a group of "
            f"clips that the envelope does not represent ({len(perdidos)} item(s))",
            {"track": track.name, "items": len(perdidos)},
        )
