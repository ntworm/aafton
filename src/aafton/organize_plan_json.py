"""Serializacao e desserializacao JSON das estruturas de proposta e edicoes.

Separa os conversores dict/JSON de organize_plan.py e organize_units.py para
manter cada modulo abaixo dos limites do gate de qualidade.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .organize_plan import DestinationTrack, Edits, LeftTrack, Proposal, ProposalGroup
    from .organize_units import OriginKey, Unit


def unit_to_dict(unit: Unit) -> dict[str, object]:
    """Converte Unit para dicionario serializavel em JSON."""
    return {
        "origin_keys": [[s, e] for s, e in unit.origin_keys],
        "source_tracks": list(unit.source_tracks),
        "source_label": unit.source_label,
        "channel_count": unit.channel_count,
        "start_samples": unit.start_samples,
        "end_samples": unit.end_samples,
        "name": unit.name,
        "media_filename": unit.media_filename,
        "kind": unit.kind,
        "score": unit.score,
        "conflict": unit.conflict,
        "reasons": list(unit.reasons),
        "accepted": unit.accepted,
        "mixed_kinds": unit.mixed_kinds,
        "is_family": unit.is_family,
    }


def unit_from_dict(cls: type[Unit], data: dict[str, object]) -> Unit:
    """Reconstroi Unit a partir de dicionario desserializado de JSON."""
    raw_keys = data.get("origin_keys", [])
    raw_tracks = data.get("source_tracks", [])
    raw_reasons = data.get("reasons", [])
    media_fn = data.get("media_filename")
    kind_val = data.get("kind")
    return cls(
        origin_keys=tuple((int(p[0]), int(p[1])) for p in raw_keys),  # type: ignore[index]
        source_tracks=tuple(int(idx) for idx in raw_tracks),  # type: ignore[union-attr]
        source_label=str(data["source_label"]),
        channel_count=int(data["channel_count"]),  # type: ignore[arg-type]
        start_samples=int(data["start_samples"]),  # type: ignore[arg-type]
        end_samples=int(data["end_samples"]),  # type: ignore[arg-type]
        name=str(data["name"]),
        media_filename=str(media_fn) if media_fn is not None else None,
        kind=str(kind_val) if kind_val is not None else None,
        score=int(data["score"]),  # type: ignore[arg-type]
        conflict=bool(data["conflict"]),
        reasons=tuple(str(r) for r in raw_reasons),  # type: ignore[union-attr]
        accepted=bool(data["accepted"]),
        mixed_kinds=bool(data.get("mixed_kinds", False)),
        is_family=bool(data.get("is_family", False)),
    )


def parse_origin_edits(raw_origins: object) -> dict[OriginKey, bool]:
    """Converte a colecao serializada de edicoes por chave de origem."""
    if isinstance(raw_origins, dict):
        return {
            (int(str(k).split(":")[0]), int(str(k).split(":")[1])): bool(v)
            for k, v in raw_origins.items()
        }
    if isinstance(raw_origins, list):
        return {
            (int(item["origin"][0]), int(item["origin"][1])): bool(item["accepted"])
            for item in raw_origins
        }
    return {}


def edits_to_dict(edits: Edits) -> dict[str, object]:
    """Converte Edits para dicionario serializavel em JSON."""
    dados: dict[str, object] = {
        "groups": dict(sorted(edits.groups.items())),
        "tracks": dict(sorted(edits.tracks.items())),
        "origins": [
            {"origin": [k[0], k[1]], "accepted": bool(v)} for k, v in sorted(edits.origins.items())
        ],
        "group_names": dict(sorted(edits.group_names.items())),
        "track_names": dict(sorted(edits.track_names.items())),
    }
    # Sem ancora nenhuma o JSON fica igual ao de antes do campo existir.
    if edits.track_name_origins:
        dados["track_name_origins"] = {
            chave: [[slot, idx] for slot, idx in origem]
            for chave, origem in sorted(edits.track_name_origins.items())
        }
    return dados


def edits_to_json(edits: Edits) -> str:
    """Serializa Edits em JSON deterministico."""
    return json.dumps(edits_to_dict(edits), ensure_ascii=False, sort_keys=True)


def edits_from_dict(cls: type[Edits], data: dict[str, object] | None) -> Edits:
    """Reconstroi Edits a partir de dicionario desserializado de JSON."""
    if not data:
        return cls()
    raw_groups = data.get("groups") or {}
    raw_tracks = data.get("tracks") or {}
    raw_gnames = data.get("group_names") or {}
    raw_tnames = data.get("track_names") or {}
    raw_torigens = data.get("track_name_origins") or {}
    return cls(
        groups={str(k): bool(v) for k, v in raw_groups.items()},  # type: ignore[union-attr]
        tracks={str(k): bool(v) for k, v in raw_tracks.items()},  # type: ignore[union-attr]
        origins=parse_origin_edits(data.get("origins") or []),
        group_names={str(k): str(v) for k, v in raw_gnames.items()},  # type: ignore[union-attr]
        track_names={str(k): str(v) for k, v in raw_tnames.items()},  # type: ignore[union-attr]
        track_name_origins=_ancoras_de_nome(raw_torigens),
    )


def _ancoras_de_nome(bruto: object) -> dict[str, tuple[tuple[int, int], ...]]:
    """Le as ancoras dos nomes de track; entrada malformada vira ValueError."""
    if not isinstance(bruto, dict):
        raise ValueError("track_name_origins must be an object")
    try:
        ancoras = {
            str(chave): tuple((int(par[0]), int(par[1])) for par in pares)
            for chave, pares in bruto.items()
        }
        if any(len(par) != 2 for pares in bruto.values() for par in pares):
            raise ValueError("each anchor origin is a [slot, index] pair")
        return ancoras
    except (TypeError, IndexError, KeyError) as exc:
        raise ValueError(f"malformed track_name_origins: {exc}") from exc


def edits_from_json(cls: type[Edits], text: str) -> Edits:
    """Desserializa Edits a partir de uma string JSON."""
    return edits_from_dict(cls, json.loads(text))


def destination_track_to_dict(track: DestinationTrack) -> dict[str, object]:
    """Converte DestinationTrack para dicionario serializavel em JSON."""
    return {
        "key": track.key,
        "name": track.name,
        "kind": track.kind,
        "channel_count": track.channel_count,
        "units": [u.to_dict() for u in track.units],
        "source_track": track.source_track,
        "source_indices": list(track.source_indices),
    }


def destination_track_from_dict(
    cls: type[DestinationTrack],
    unit_cls: type[Unit],
    data: dict[str, object],
) -> DestinationTrack:
    """Reconstroi DestinationTrack a partir de dicionario."""
    src_track = data.get("source_track")
    return cls(
        key=str(data["key"]),
        name=str(data["name"]),
        kind=str(data["kind"]),
        channel_count=int(data["channel_count"]),  # type: ignore[arg-type]
        units=tuple(unit_cls.from_dict(u) for u in (data.get("units") or [])),  # type: ignore[union-attr]
        source_track=str(src_track) if src_track is not None else None,
        source_indices=tuple(int(i) for i in (data.get("source_indices") or [])),  # type: ignore[union-attr]
    )


def proposal_group_to_dict(group: ProposalGroup) -> dict[str, object]:
    """Converte ProposalGroup para dicionario serializavel em JSON."""
    return {
        "key": group.key,
        "label": group.label,
        "short_name": group.short_name,
        "is_family": group.is_family,
        "tracks": [t.to_dict() for t in group.tracks],
    }


def proposal_group_from_dict(
    cls: type[ProposalGroup],
    track_cls: type[DestinationTrack],
    data: dict[str, object],
) -> ProposalGroup:
    """Reconstroi ProposalGroup a partir de dicionario."""
    return cls(
        key=str(data["key"]),
        label=str(data["label"]),
        short_name=str(data["short_name"]),
        is_family=bool(data.get("is_family", False)),
        tracks=tuple(track_cls.from_dict(t) for t in (data.get("tracks") or [])),  # type: ignore[union-attr]
    )


def left_track_to_dict(track: LeftTrack) -> dict[str, object]:
    """Converte LeftTrack para dicionario serializavel em JSON."""
    return {
        "key": track.key,
        "name": track.name,
        "channel_count": track.channel_count,
        "source_indices": list(track.source_indices),
        "units": [u.to_dict() for u in track.units],
        "excluded": track.excluded,
    }


def left_track_from_dict(
    cls: type[LeftTrack],
    unit_cls: type[Unit],
    data: dict[str, object],
) -> LeftTrack:
    """Reconstroi LeftTrack a partir de dicionario."""
    return cls(
        key=str(data["key"]),
        name=str(data["name"]),
        channel_count=int(data["channel_count"]),  # type: ignore[arg-type]
        source_indices=tuple(int(i) for i in (data.get("source_indices") or [])),  # type: ignore[union-attr]
        units=tuple(unit_cls.from_dict(u) for u in (data.get("units") or [])),  # type: ignore[union-attr]
        excluded=bool(data.get("excluded", False)),
    )


def compactacao_to_dict(compactacao: dict[str, tuple[int, int]]) -> dict[str, list[int]]:
    """Converte as contagens de tracks por tipo em dicionario serializavel em JSON."""
    return {kind: [antes, depois] for kind, (antes, depois) in compactacao.items()}


def compactacao_from_dict(raw: object) -> dict[str, tuple[int, int]]:
    """Le as contagens de tracks por tipo vindas da compactacao."""
    if not isinstance(raw, dict):
        return {}
    contagem: dict[str, tuple[int, int]] = {}
    for kind, valores in raw.items():
        if isinstance(valores, (list, tuple)) and len(valores) == 2:
            contagem[str(kind)] = (int(valores[0]), int(valores[1]))
    return contagem


def proposal_to_dict(proposal: Proposal) -> dict[str, object]:
    """Converte Proposal para dicionario serializavel em JSON."""
    return {
        "stereo": proposal.stereo,
        "level": proposal.level,
        "signals": list(proposal.signals),
        "groups": [g.to_dict() for g in proposal.groups],
        "left_tracks": [t.to_dict() for t in proposal.left_tracks],
        "edits": proposal.edits.to_dict(),
        "compactacao": compactacao_to_dict(proposal.compactacao),
    }


def proposal_to_json(proposal: Proposal) -> str:
    """Serializa Proposal em JSON deterministico."""
    return json.dumps(proposal_to_dict(proposal), ensure_ascii=False, sort_keys=True)


def proposal_from_dict(
    cls: type[Proposal],
    group_cls: type[ProposalGroup],
    left_cls: type[LeftTrack],
    edits_cls: type[Edits],
    data: dict[str, object],
) -> Proposal:
    """Reconstroi Proposal a partir de dicionario desserializado de JSON."""
    raw_edits = data.get("edits")
    return cls(
        stereo=str(data["stereo"]),
        level=str(data["level"]),
        signals=tuple(str(s) for s in (data.get("signals") or [])),  # type: ignore[union-attr]
        groups=tuple(group_cls.from_dict(g) for g in (data.get("groups") or [])),  # type: ignore[union-attr]
        left_tracks=tuple(left_cls.from_dict(t) for t in (data.get("left_tracks") or [])),  # type: ignore[union-attr]
        edits=edits_cls.from_dict(raw_edits if isinstance(raw_edits, dict) else None),
        compactacao=compactacao_from_dict(data.get("compactacao")),
    )


def proposal_from_json(
    cls: type[Proposal],
    group_cls: type[ProposalGroup],
    left_cls: type[LeftTrack],
    edits_cls: type[Edits],
    text: str,
) -> Proposal:
    """Desserializa Proposal a partir de uma string JSON."""
    return proposal_from_dict(cls, group_cls, left_cls, edits_cls, json.loads(text))
