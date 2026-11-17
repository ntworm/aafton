"""Agrupamento por familia de nomes para clips sem tipo vencedor.

Implementa os cinco passos da secao 'Grupos por nome' do plano de organizacao
de sessao (P1): normalizacao do nome do arquivo, remocao das palavras de
variante do fim, agrupamento por radical inteiro, agrupamento dos radicais
restantes pela primeira palavra (4+ letras e nao variante) e filtro de 2+
arquivos distintos.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .organize_classify import fold_text, strip_channel_and_extensions

_LR_END_RE = re.compile(r"[_.][lrLR]$")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_COPY_PREFIX_RE = re.compile(
    r"^(?:c(?:o\u0301?|\u00f3)pia[\s_.-]+de|copy[\s_.-]+of)[\s_.-]+",
    re.I,
)
_WORD_SPLIT_RE = re.compile(r"[\s_\-.,()]+")
_VERSION_TOKEN_RE = re.compile(r"v\d+")
_TK_TOKEN_RE = re.compile(r"tk\d*")
_VARIANT_WORDS = frozenset({"versao", "version", "take", "hd", "copy", "copia"})


@dataclass(frozen=True)
class Family:
    """Familia de arquivos sem tipo vencedor agrupados pelo nome."""

    key: str
    label: str
    files: tuple[str, ...]
    clip_count: int

    @property
    def score(self) -> int:
        """Nota da familia: 2 com 3+ clips, senao 1."""
        return family_score(self.clip_count)


def family_score(clip_count: int) -> int:
    """Calcula a nota da familia: 2 quando ha 3 ou mais clips, senao 1."""
    return 2 if clip_count >= 3 else 1


def _strip_copy_prefix(text: str) -> str:
    """Remove prefixos 'Copia de ' / 'Copy of ' repetidos do inicio."""
    current = text.lstrip()
    while True:
        match = _COPY_PREFIX_RE.match(current)
        if match is None:
            break
        current = current[match.end() :].lstrip()
    return current


def normalize(name: str) -> list[str]:
    """Passo 1: limpa _L/_R, extensoes, 'Copia de '/'Copy of ' e UUIDs; separa em palavras."""
    base, _ = strip_channel_and_extensions(name)
    without_copy = _strip_copy_prefix(base)
    without_uuid = _UUID_RE.sub(" ", without_copy)
    return [word for word in _WORD_SPLIT_RE.split(without_uuid) if word]


def is_variant_word(word: str) -> bool:
    """Indica se a palavra e um marcador de variante (numero, letra solta, versao, take, etc.)."""
    folded = fold_text(word)
    if not folded:
        return False
    if folded.isdigit():
        return True
    if len(folded) == 1 and folded.isalpha():
        return True
    if folded in _VARIANT_WORDS:
        return True
    return bool(_VERSION_TOKEN_RE.fullmatch(folded) or _TK_TOKEN_RE.fullmatch(folded))


def stem(name: str) -> tuple[str, ...]:
    """Passo 2: remove do fim as palavras de variante e devolve o radical."""
    words = normalize(name)
    while words and is_variant_word(words[-1]):
        words.pop()
    return tuple(words)


def _folded_stem(words: tuple[str, ...]) -> tuple[str, ...]:
    """Radical normalizado sem acento e sem caixa para comparacao."""
    return tuple(fold_text(word) for word in words)


def _can_group_by_first_word(first_word: str) -> bool:
    """Passo 4: primeira palavra com 4+ letras e que nao seja palavra de variante."""
    if is_variant_word(first_word):
        return False
    folded = fold_text(first_word)
    letter_count = sum(1 for ch in folded if ch.isalpha())
    return letter_count >= 4


def _common_prefix_words(stems: list[tuple[str, ...]]) -> tuple[str, ...]:
    """Palavras comuns do comeco com a grafia do primeiro arquivo."""
    first = stems[0]
    folded_all = [_folded_stem(s) for s in stems]
    prefix_len = 0
    for idx, token in enumerate(folded_all[0]):
        if all(idx < len(other) and other[idx] == token for other in folded_all[1:]):
            prefix_len = idx + 1
        else:
            break
    return first[:prefix_len]


def _canonical_file_name(name: str) -> str:
    """Nome do arquivo sem sufixo de canal _L/_R."""
    return _LR_END_RE.sub("", name.strip())


def _collect_distinct_files(
    names: Iterable[str],
) -> tuple[list[str], dict[str, int], dict[str, tuple[str, ...]]]:
    """Agrupa ocorrencias por arquivo distinto (sem _L/_R) preservando a ordem."""
    order: list[str] = []
    counts: dict[str, int] = {}
    stems: dict[str, tuple[str, ...]] = {}
    for raw in names:
        canonical = _canonical_file_name(raw)
        if not canonical:
            continue
        if canonical not in counts:
            stems_tuple = stem(canonical)
            if not stems_tuple:
                continue
            order.append(canonical)
            counts[canonical] = 0
            stems[canonical] = stems_tuple
        counts[canonical] += 1
    return order, counts, stems


def _build_family(
    file_list: list[str],
    counts: dict[str, int],
    stems: dict[str, tuple[str, ...]],
) -> Family:
    """Constroi Family a partir dos arquivos distintos do grupo."""
    group_stems = [stems[f] for f in file_list]
    common = _common_prefix_words(group_stems)
    label = " ".join(common)
    key = " ".join(fold_text(w) for w in common)
    total_clips = sum(counts[f] for f in file_list)
    return Family(
        key=key,
        label=label,
        files=tuple(file_list),
        clip_count=total_clips,
    )


def _group_leftovers_by_first_word(
    leftover_files: list[str],
    stems: dict[str, tuple[str, ...]],
    groups_by_first_file: dict[str, list[str]],
) -> None:
    """Passo 4: agrupa radicais restantes pela primeira palavra valida."""
    by_first_word: dict[str, list[str]] = {}
    for fname in leftover_files:
        first_word = stems[fname][0]
        if _can_group_by_first_word(first_word):
            w_key = fold_text(first_word)
            by_first_word.setdefault(w_key, []).append(fname)
    for group in by_first_word.values():
        if len(group) >= 2:
            groups_by_first_file[group[0]] = group


def families(names: Iterable[str]) -> list[Family]:
    """Passos 3 a 5: forma familias para 2+ arquivos distintos."""
    order, counts, stems = _collect_distinct_files(names)
    by_full_stem: dict[tuple[str, ...], list[str]] = {}
    for fname in order:
        f_key = _folded_stem(stems[fname])
        by_full_stem.setdefault(f_key, []).append(fname)

    leftover_files: list[str] = []
    groups_by_first_file: dict[str, list[str]] = {}
    for fname in order:
        group = by_full_stem[_folded_stem(stems[fname])]
        if len(group) >= 2:
            if fname == group[0]:
                groups_by_first_file[fname] = group
        else:
            leftover_files.append(fname)

    _group_leftovers_by_first_word(leftover_files, stems, groups_by_first_file)
    familias = [
        _build_family(groups_by_first_file[fname], counts, stems)
        for fname in order
        if fname in groups_by_first_file
    ]
    return _chaves_unicas(familias)


def _chaves_unicas(familias: list[Family]) -> list[Family]:
    """Duas familias com o mesmo trecho comum ganham chaves distintas (folia, folia 2).

    O sufixo pula as chaves que ja existem: com uma familia chamada "folia 2",
    a segunda "folia" vira "folia 3", e nenhuma chave se repete.
    """
    ocupadas = {fam.key for fam in familias}
    vistas: set[str] = set()
    proximo: dict[str, int] = {}
    saida: list[Family] = []
    for fam in familias:
        if fam.key not in vistas:
            vistas.add(fam.key)
            saida.append(fam)
            continue
        n = proximo.get(fam.key, 2)
        while f"{fam.key} {n}" in ocupadas:
            n += 1
        proximo[fam.key] = n + 1
        chave = f"{fam.key} {n}"
        ocupadas.add(chave)
        vistas.add(chave)
        saida.append(replace(fam, key=chave, label=f"{fam.label} {n}"))
    return saida
