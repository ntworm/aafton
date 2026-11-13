"""Inferencia dos grupos de tracks a partir do nome.

O AAF nao carrega as pastas do Live, entao o agrupamento por prefixo e
reconstruido a partir do nome das tracks. Este modulo faz apenas a
inferencia; a emissao dos grupos fica em ``als_write_tracks``.
"""

import re
from collections import defaultdict

from .als_write_base import WriteOptions
from .ir import SessionIR


def infer_track_groups(session: SessionIR, options: WriteOptions) -> dict[int, str]:
    """Mapeia ``id(track)`` para o nome do grupo inferido pelo nome da track."""
    track_to_group: dict[int, str] = {}
    if options.groups != "prefix":
        return track_to_group
    _apply_prefix_rule(session, track_to_group)
    _apply_suffix_rule(session, track_to_group)
    _rename_suffix_only_groups(session, track_to_group)
    return track_to_group


def _apply_prefix_rule(session: SessionIR, track_to_group: dict[int, str]) -> None:
    """Agrupa as tracks cujo nome comeca com um prefixo de texto seguido de digito.

    As tracks com mais de dois canais ficam de fora: elas saem no grupo
    multicanal proprio, e o prefixo delas nao entra na contagem.
    """
    group_counts: dict[str, int] = {}
    for t in session.tracks:
        if t.channel_count > 2:
            continue
        m = re.match(r"^([a-zA-Z\s]+?)\s*\d", t.name)
        if m:
            prefix = m.group(1).strip()
            group_counts[prefix] = group_counts.get(prefix, 0) + 1

    for t in session.tracks:
        if t.channel_count > 2:
            continue
        m = re.match(r"^([a-zA-Z\s]+?)\s*\d", t.name)
        if m:
            prefix = m.group(1).strip()
            if group_counts[prefix] >= 2:
                track_to_group[id(t)] = prefix


def _apply_suffix_rule(session: SessionIR, track_to_group: dict[int, str]) -> None:
    """Agrupa as sobras pelo texto que precede o sufixo entre colchetes.

    As tracks com mais de dois canais ficam de fora, como na regra do
    prefixo: elas saem no grupo multicanal proprio, e o sufixo delas nao
    entra na contagem nem na atribuicao. Sem isso a track 5.1 entrava no
    grupo do nome base e saia duas vezes no set: uma como AudioTrack de
    seis canais dentro do grupo e outra como o grupo multicanal.
    """
    suffix_counts: dict[str, int] = {}
    for t in session.tracks:
        if t.channel_count > 2:
            continue
        if id(t) not in track_to_group:
            m = re.match(r"^(.*) \[[^\]]+\]$", t.name)
            if m:
                base = m.group(1).strip()
                suffix_counts[base] = suffix_counts.get(base, 0) + 1

    for t in session.tracks:
        if t.channel_count > 2:
            continue
        if id(t) not in track_to_group:
            m = re.match(r"^(.*) \[[^\]]+\]$", t.name)
            if m:
                base = m.group(1).strip()
                if suffix_counts.get(base, 0) >= 2:
                    track_to_group[id(t)] = base


def _rename_suffix_only_groups(session: SessionIR, track_to_group: dict[int, str]) -> None:
    """Renomeia o grupo quando todos os membros tem sufixo e a base e a mesma."""
    group_to_tracks = _collect_group_members(session, track_to_group)
    for g, tracks in group_to_tracks.items():
        bases = set()
        all_have_suffix = True
        for t in tracks:
            m = re.match(r"^(.*) \[[^\]]+\]$", t.name)
            if m:
                bases.add(m.group(1).strip())
            else:
                all_have_suffix = False
                break
        if all_have_suffix and len(bases) == 1:
            new_name = bases.pop()
            for t in tracks:
                track_to_group[id(t)] = new_name


def _collect_group_members(session: SessionIR, track_to_group: dict[int, str]) -> dict[str, list]:
    """Junta as tracks ja atribuidas pelo nome do grupo."""
    group_to_tracks: dict[str, list] = defaultdict(list)
    for t in session.tracks:
        g = track_to_group.get(id(t))
        if g:
            group_to_tracks[g].append(t)
    return group_to_tracks
