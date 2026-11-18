"""Resolucao por ordem do material repetido no verify do reconform.

O verify refaz, com codigo proprio, a regra que resolve por ordem o material
repetido: o mesmo plano usado mais de uma vez. Duas pecas que se sobrepoem no
corte antigo ou no corte novo sao um conflito, e as pecas de um grupo se
resolvem pela ordem dos planos: a ancora valida mais proxima da peca da o
deslocamento de referencia, e vence a peca mais proxima desse deslocamento,
depois a mais longa. O vencedor nao tira os outros inteiros: cada candidato
que se sobrepoe a ele e aparado, no antigo e no novo juntos, ate a parte que
nao se sobrepoe, e a parte que sobra volta para o grupo. Candidatos empatados
nos dois criterios e sobrepostos entre si nao vencem: o trecho deles fica sem
correspondencia, e todo candidato que se sobrepoe a esse trecho e aparado da
mesma forma.

O trecho empatado e listado por regiao: cada regiao do empate, no antigo e no
novo, vira um 'nao resolvido'.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Sequence

from .cut_ir import CutIR
from .reconform_verify_velocidade import Peca, chave


@dataclass(frozen=True)
class NaoResolvido:
    """Trecho que a ordem dos planos nao resolveu, em uma regiao do empate."""

    old_in: int
    old_out: int
    new_in: int
    new_out: int
    source: str | None


@dataclass(frozen=True)
class Resolucao:
    """Resultado da resolucao por ordem do material repetido."""

    casados: list[Peca]
    nao_resolvidos: list[NaoResolvido]
    descartados: list[Peca]
    grupos: int


def _faixa(peca: Peca, lado: str) -> tuple[int, int]:
    """Intervalo da peca no lado escolhido da linha do tempo."""
    if lado == "old":
        return peca.old_in, peca.old_out
    return peca.new_in, peca.new_out


def _sobrepoem(a: Peca, b: Peca) -> bool:
    """Verdadeiro quando as pecas se sobrepoem no corte antigo ou no novo."""
    antigo = a.old_in < b.old_out and b.old_in < a.old_out
    novo = a.new_in < b.new_out and b.new_in < a.new_out
    return antigo or novo


def _lis(valores: Sequence[int]) -> set[int]:
    """Indices da maior subsequencia estritamente crescente dos valores."""
    if not valores:
        return set()
    caudas: list[int] = []
    indices: list[int] = []
    pais: list[int] = [-1] * len(valores)
    for idx, valor in enumerate(valores):
        pos = bisect_left(caudas, valor)
        if pos == len(caudas):
            caudas.append(valor)
            indices.append(idx)
        else:
            caudas[pos] = valor
            indices[pos] = idx
        if pos > 0:
            pais[idx] = indices[pos - 1]
    guarda: set[int] = set()
    atual = indices[-1]
    while atual != -1:
        guarda.add(atual)
        atual = pais[atual]
    return guarda


def _raiz(pais: list[int], idx: int) -> int:
    """Raiz do conjunto do indice, com compressao de caminho."""
    while pais[idx] != idx:
        pais[idx] = pais[pais[idx]]
        idx = pais[idx]
    return idx


def _juntar(pais: list[int], a: int, b: int) -> None:
    """Junta os conjuntos dos dois indices."""
    raiz_a, raiz_b = _raiz(pais, a), _raiz(pais, b)
    if raiz_a != raiz_b:
        pais[max(raiz_a, raiz_b)] = min(raiz_a, raiz_b)


def _conflitos(pecas: Sequence[Peca], lado: str) -> list[tuple[int, int]]:
    """Pares de pecas que se sobrepoem no lado escolhido da linha do tempo."""
    indices = sorted(range(len(pecas)), key=lambda i: _faixa(pecas[i], lado)[0])
    pares: list[tuple[int, int]] = []
    ativos: list[tuple[int, int]] = []
    for idx in indices:
        inicio, fim = _faixa(pecas[idx], lado)
        ativos = [(outro, termino) for outro, termino in ativos if termino > inicio]
        for outro, _ in ativos:
            pares.append((outro, idx))
        ativos.append((idx, fim))
    return pares


def _grupos(pecas: Sequence[Peca]) -> list[list[Peca]]:
    """Grupos de conflito: componentes conexas do grafo de sobreposicao."""
    pais = list(range(len(pecas)))
    for lado in ("old", "new"):
        for a, b in _conflitos(pecas, lado):
            _juntar(pais, a, b)
    grupos: dict[int, list[Peca]] = {}
    for idx, peca in enumerate(pecas):
        grupos.setdefault(_raiz(pais, idx), []).append(peca)
    return list(grupos.values())


def _ancoras(
    pecas: Sequence[Peca],
    grupos: Sequence[list[Peca]],
) -> tuple[list[Peca], list[Peca]]:
    """Ancoras da ordem e as ancoras validas (as da maior sequencia crescente)."""
    no_grupo = {id(peca) for grupo in grupos if len(grupo) > 1 for peca in grupo}
    ancoras = [peca for peca in pecas if id(peca) not in no_grupo]
    ordenadas = sorted(ancoras, key=chave)
    lis = _lis([peca.new_in for peca in ordenadas])
    validas = [peca for idx, peca in enumerate(ordenadas) if idx in lis]
    return ancoras, validas


def _referencia(ancoras: Sequence[Peca], peca: Peca, delta_padrao: int) -> int:
    """Deslocamento de referencia da peca: a ancora valida mais proxima dela."""
    antes = [ancora for ancora in ancoras if ancora.old_out <= peca.old_in]
    if antes:
        return max(antes, key=lambda ancora: ancora.old_out).delta
    depois = [ancora for ancora in ancoras if ancora.old_in >= peca.old_in]
    if depois:
        return min(depois, key=lambda ancora: ancora.old_in).delta
    return delta_padrao


def _pontuacao(peca: Peca, ancoras: Sequence[Peca], delta_padrao: int) -> tuple[int, int]:
    """Distancia do deslocamento a referencia e duracao da peca, para a escolha."""
    referencia = _referencia(ancoras, peca, delta_padrao)
    return (abs(peca.delta - referencia), -(peca.old_out - peca.old_in))


def _melhores(
    pendentes: Sequence[Peca],
    ancoras: Sequence[Peca],
    delta_padrao: int,
) -> list[Peca]:
    """Candidatos com a melhor pontuacao do grupo."""
    pontos = [(_pontuacao(peca, ancoras, delta_padrao), peca) for peca in pendentes]
    melhor = min(pontos, key=lambda ponto: ponto[0])[0]
    return [peca for ponto, peca in pontos if ponto == melhor]


def _bloqueados(empatados: Sequence[Peca]) -> list[Peca]:
    """Empatados que se sobrepoem entre si: a ordem nao decide entre eles."""
    return [
        peca
        for peca in empatados
        if any(_sobrepoem(peca, outro) for outro in empatados if outro is not peca)
    ]


def _fundir(faixas: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Funde faixas que se tocam ou se sobrepoem."""
    fundidas: list[tuple[int, int]] = []
    for inicio, fim in sorted(faixas):
        if fundidas and inicio <= fundidas[-1][1]:
            fundidas[-1] = (fundidas[-1][0], max(fundidas[-1][1], fim))
        else:
            fundidas.append((inicio, fim))
    return fundidas


def _tocam(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Verdadeiro quando as faixas se tocam ou se sobrepoem."""
    return a[0] <= b[1] and b[0] <= a[1]


def _ligadas(a: Peca, b: Peca) -> bool:
    """Verdadeiro quando as duas pecas se tocam no antigo e no novo."""
    antigo = _tocam((a.old_in, a.old_out), (b.old_in, b.old_out))
    novo = _tocam((a.new_in, a.new_out), (b.new_in, b.new_out))
    return antigo and novo


def _clusters(bloqueados: Sequence[Peca]) -> list[list[Peca]]:
    """Candidatos bloqueados ligados nos dois cortes: componentes conexas."""
    pais = list(range(len(bloqueados)))
    for i in range(len(bloqueados)):
        for j in range(i + 1, len(bloqueados)):
            if _ligadas(bloqueados[i], bloqueados[j]):
                _juntar(pais, i, j)
    grupos: dict[int, list[Peca]] = {}
    for idx, peca in enumerate(bloqueados):
        grupos.setdefault(_raiz(pais, idx), []).append(peca)
    return list(grupos.values())


def _regioes(bloqueados: Sequence[Peca]) -> list[NaoResolvido]:
    """Trechos do empate: uma regiao por grupo de candidatos ligados."""
    regioes: list[NaoResolvido] = []
    for grupo in _clusters(bloqueados):
        antigo = _fundir([_faixa(peca, "old") for peca in grupo])
        novo = _fundir([_faixa(peca, "new") for peca in grupo])
        origens = {peca.source for peca in grupo}
        origem = origens.pop() if len(origens) == 1 else None
        regioes.append(NaoResolvido(antigo[0][0], antigo[-1][1], novo[0][0], novo[-1][1], origem))
    return regioes


def _interseccao(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    """Parte comum de dois intervalos, ou None quando eles nao se cruzam."""
    inicio = max(a[0], b[0])
    fim = min(a[1], b[1])
    return (inicio, fim) if inicio < fim else None


def _subtrair(
    intervalo: tuple[int, int],
    conflitos: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Partes do intervalo que nao estao em nenhum dos conflitos."""
    restantes = [intervalo]
    for inicio, fim in conflitos:
        guardadas: list[tuple[int, int]] = []
        for comeco, termino in restantes:
            if fim <= comeco or inicio >= termino:
                guardadas.append((comeco, termino))
                continue
            if comeco < inicio:
                guardadas.append((comeco, inicio))
            if fim < termino:
                guardadas.append((fim, termino))
        restantes = guardadas
    return restantes


def _copia(peca: Peca, inicio: int, fim: int) -> Peca:
    """A peca com o mesmo deslocamento, cortada no intervalo informado."""
    return Peca(inicio, fim, inicio + peca.delta, peca.evento_antigo, peca.evento_novo)


def _aparar(
    peca: Peca,
    bloqueios_old: Sequence[tuple[int, int]],
    bloqueios_new: Sequence[tuple[int, int]],
) -> tuple[list[Peca], list[Peca]]:
    """Apara a peca: devolve a parte que sobra e a parte que foi coberta."""
    conflitos: list[tuple[int, int]] = []
    for bloco in bloqueios_old:
        comum = _interseccao((peca.old_in, peca.old_out), bloco)
        if comum is not None:
            conflitos.append(comum)
    for bloco in bloqueios_new:
        comum = _interseccao((peca.new_in, peca.new_out), bloco)
        if comum is not None:
            conflitos.append((comum[0] - peca.delta, comum[1] - peca.delta))
    if not conflitos:
        return [peca], []
    sobra = [
        _copia(peca, inicio, fim)
        for inicio, fim in _subtrair((peca.old_in, peca.old_out), conflitos)
    ]
    coberta = [_copia(peca, inicio, fim) for inicio, fim in _fundir(conflitos)]
    return sobra, coberta


def _aparar_todas(
    pendentes: Sequence[Peca],
    bloqueios_old: Sequence[tuple[int, int]],
    bloqueios_new: Sequence[tuple[int, int]],
) -> tuple[list[Peca], list[Peca]]:
    """Apara os candidatos que conflitam com o bloqueio."""
    sobradas: list[Peca] = []
    cobertas: list[Peca] = []
    for peca in pendentes:
        sobra, coberta = _aparar(peca, bloqueios_old, bloqueios_new)
        sobradas.extend(sobra)
        cobertas.extend(coberta)
    return sobradas, cobertas


def _sem(pendentes: Sequence[Peca], removidos: Sequence[Peca]) -> list[Peca]:
    """Candidatos que ficaram depois de remover os indicados."""
    ids = {id(peca) for peca in removidos}
    return [peca for peca in pendentes if id(peca) not in ids]


def _faixas(pecas: Sequence[Peca], lado: str) -> list[tuple[int, int]]:
    """Faixas cobertas pelas pecas, fundidas, no lado escolhido."""
    return _fundir([_faixa(peca, lado) for peca in pecas])


def _resolver_grupo(
    grupo: Sequence[Peca],
    ancoras: Sequence[Peca],
    delta_padrao: int,
) -> tuple[list[Peca], list[NaoResolvido], list[Peca]]:
    """Escolhe os vencedores do grupo pela ordem e apara as perdedoras."""
    pendentes = list(grupo)
    vencedores: list[Peca] = []
    nao_resolvidos: list[NaoResolvido] = []
    descartados: list[Peca] = []
    while pendentes:
        empatados = _melhores(pendentes, ancoras, delta_padrao)
        bloqueados = _bloqueados(empatados)
        if bloqueados:
            nao_resolvidos.extend(_regioes(bloqueados))
            descartados.extend(bloqueados)
            pendentes = _sem(pendentes, bloqueados)
            pendentes, coberto = _aparar_todas(
                pendentes, _faixas(bloqueados, "old"), _faixas(bloqueados, "new")
            )
            descartados.extend(coberto)
            pendentes.sort(key=chave)
            continue
        vencedor = empatados[0]
        vencedores.append(vencedor)
        pendentes = _sem(pendentes, [vencedor])
        pendentes, coberto = _aparar_todas(
            pendentes,
            [(vencedor.old_in, vencedor.old_out)],
            [(vencedor.new_in, vencedor.new_out)],
        )
        descartados.extend(coberto)
        pendentes.sort(key=chave)
    return vencedores, nao_resolvidos, descartados


def resolver_reuso(pecas: Sequence[Peca], old: CutIR, new: CutIR) -> Resolucao:
    """Resolve por ordem as pecas que se conflitam e devolve as que casam."""
    grupos = [grupo for grupo in _grupos(pecas) if len(grupo) > 1]
    ancoras, validas = _ancoras(pecas, grupos)
    delta_padrao = new.start_frames - old.start_frames
    vencedores: list[Peca] = []
    nao_resolvidos: list[NaoResolvido] = []
    descartados: list[Peca] = []
    for grupo in grupos:
        found = _resolver_grupo(grupo, validas, delta_padrao)
        vencedores.extend(found[0])
        nao_resolvidos.extend(found[1])
        descartados.extend(found[2])
    vencedores.extend(ancoras)
    vencedores.sort(key=chave)
    return Resolucao(vencedores, nao_resolvidos, descartados, len(grupos))
