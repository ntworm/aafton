"""Parte de aaf_read: componentes, descritores, mob, slots e samples.

Na fase 1, _source_mob_ref foi dividida em etapas (os helpers novos saem dela);
as demais definicoes vieram de aaf_read sem mudanca.
"""

from __future__ import annotations

import re

import urllib.parse

import urllib.request

from dataclasses import dataclass

from fractions import Fraction


from pathlib import Path


from .media_names import sem_extensao_de_audio
from .report import Report

from .timecode import exact_edit_rate


_SEPARADOR_ANTES_DA_UNIDADE = re.compile(r"^[\\/](?=[A-Za-z]:)")


def _url_para_caminho(url: str) -> str:
    """Converte a URLString de um locator em caminho de sistema de arquivos.

    O url2pathname deixa uma barra inicial quando a letra de unidade vem
    percent-encoded, por exemplo file:///D%3a/..., e o caminho resultante fica
    nao-absoluto e sem drive. Com a letra literal, file:///C:/..., ele remove a
    barra sozinho. A substituicao normaliza os dois casos.
    """
    caminho = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
    return _SEPARADOR_ANTES_DA_UNIDADE.sub("", caminho)


_FADE_MOB_RE = re.compile(r"^Fade\s+\d+$", re.IGNORECASE)


_MONO_AUDIO_PAN_AUID = "9d2ea893-0968-11d3-8a38-0050040ef7d2"


_PAN_PARAMETER_AUID = "e4962322-2267-11d3-8a4c-0050040ef7d2"


_AUDIO_CHANNEL_COMBINER_AUID = "6b46dd7a-132d-4856-ab21-8b751d8462ec"


_PROFUNDIDADE_MAXIMA = 16


def _type_name(obj: object) -> str:
    return type(obj).__name__


def _is_audio_channel_combiner(component: object) -> bool:
    seen = set()
    while _type_name(component) == "OperationGroup" and id(component) not in seen:
        seen.add(id(component))
        operation_def = getattr(component, "operation", getattr(component, "operationdef", None))
        if str(getattr(operation_def, "auid", "") or "").lower() == _AUDIO_CHANNEL_COMBINER_AUID:
            return True
        segments = list(getattr(component, "segments", []) or [])
        if len(segments) != 1:
            break
        component = segments[0]
    return False


def _is_fade_source_clip(source_clip) -> bool:
    mob = getattr(source_clip, "mob", None)
    if mob is not None:
        name = getattr(mob, "name", None) or ""
        if _FADE_MOB_RE.match(name.strip()):
            return True
        usage = getattr(mob, "usage", None)
        if usage == "Usage_Fade" or str(usage).endswith("Fade"):
            return True
    src_file = _source_filename(source_clip)
    if src_file:
        stem = Path(src_file).stem
        if _FADE_MOB_RE.match(stem.strip()):
            return True
    return False


def _unwrap(component, report: Report = None):
    if _type_name(component) != "OperationGroup":
        return [component], []

    op_def_name = getattr(component, "operation", getattr(component, "operationdef", None))
    nome_op = getattr(op_def_name, "name", "Unknown") if op_def_name else "Unknown"

    operacoes_conhecidas = {
        "Audio Gain",
        "Mono Audio Gain",
        "Mono Audio Pan",
        "Audio Channel Combiner",
        "Avid Audio Dissolve",
        "Mono Audio Dissolve",
    }
    if report is not None and nome_op not in operacoes_conhecidas:
        if not hasattr(report, "_ignored_plugins"):
            report._ignored_plugins = []
        report._ignored_plugins.append(nome_op)

    segmentos = list(getattr(component, "segments", []) or [])
    parametros = list(getattr(component, "parameters", []) or [])
    if not segmentos:
        return [component], parametros

    all_internos = []
    for seg in segmentos:
        internos, params = _unwrap(seg, report)
        all_internos.extend(internos)
        parametros.extend(params)
    return all_internos, parametros + internos


def _source_filename(component) -> str | None:
    nome, _ = _source_mob_ref(component)
    return nome


def _valor_de_propriedade(componente, nome):
    """Valor de uma propriedade do componente, ou None quando ela nao existe.

    O pyaaf2 expoe as propriedades pelo nome do AAF, como "Selected",
    "Alternates" e "IsTimeWarp". Propriedade ausente nao e erro: devolve
    None e quem chama decide o que fazer com a falta.
    """
    if componente is None:
        return None
    try:
        prop = componente.get(nome)
    except Exception:
        prop = None
    if prop is None:
        try:
            prop = componente[nome]
        except Exception:
            return None
    try:
        return prop.value
    except Exception:
        return None


def _contar_source_clips(componente) -> int:
    """Quantos SourceClips ficam alcancaveis a partir de um componente.

    Serve para decidir se um componente desconhecido esconde audio. Um
    componente que so tem lacuna e inofensivo; um que esconde SourceClip
    faz o leitor perder material, e ai o problema e erro, e nao aviso.
    """
    return _contar_source_clips_rec(componente, set(), 0)


def _contar_source_clips_rec(componente, visitados: set[int], profundidade: int) -> int:
    """Conta SourceClips alcancaveis, com guarda de ciclo e de profundidade."""
    if componente is None or profundidade > _PROFUNDIDADE_MAXIMA:
        return 0
    if _type_name(componente) == "SourceClip":
        return 1
    if id(componente) in visitados:
        return 0
    visitados.add(id(componente))
    total = 0
    for filho in _filhos_do_componente(componente):
        total += _contar_source_clips_rec(filho, visitados, profundidade + 1)
    return total


def _filhos_do_componente(componente) -> list:
    """Componentes que um componente do AAF pode conter."""
    tipo = _type_name(componente)
    if tipo == "Sequence":
        return _lista(getattr(componente, "components", None))
    if tipo == "OperationGroup":
        return _lista(getattr(componente, "segments", None))
    if tipo == "Selector":
        return _filhos_do_selector(componente)
    if tipo == "NestedScope":
        return [_segmento_do_slot(slot) for slot in _lista(getattr(componente, "slots", None))]
    if tipo == "EssenceGroup":
        return _lista(getattr(componente, "choices", None))
    return []


def _filhos_do_selector(componente) -> list:
    """O material escolhido e os alternativos de um Selector."""
    filhos = []
    selected = _valor_de_propriedade(componente, "Selected")
    if selected is not None:
        filhos.append(selected)
    filhos.extend(_lista(_valor_de_propriedade(componente, "Alternates")))
    return filhos


def _lista(valor) -> list:
    """Lista de um atributo que pode faltar ou vir nulo."""
    return list(valor or [])


def _segmento_do_slot(slot):
    """O segmento de um slot, seja o slot ele mesmo ou um objeto com segmento."""
    if slot is None:
        return None
    segmento = getattr(slot, "segment", None)
    return segmento if segmento is not None else slot


@dataclass
class _CaminhoMob:
    """Estado acumulado da caminhada da cadeia de mobs."""

    mob_id_essencia: str | None = None
    mob_id_arquivo: str | None = None
    caminho_arquivo: str | None = None
    sufixo_slot: str = ""


def _slot_id_de(component) -> object | None:
    """Le o SourceMobSlotID de um componente sem levantar excecao."""
    prop = component.get("SourceMobSlotID") if hasattr(component, "get") else None
    if prop is None:
        return None
    try:
        return prop.value
    except Exception:
        return None


def _descritor_fisico(descritor) -> bool:
    """Descritor de midia fisica, sem arquivo nem essence."""
    return any(
        b.__name__ in ("TapeDescriptor", "FilmDescriptor", "PhysicalDescriptor")
        for b in getattr(descritor.__class__, "__mro__", [])
    )


def _caminho_do_locator(descritor) -> str | None:
    """Caminho declarado na URLString do primeiro Locator com URL."""
    locators_prop = descritor.get("Locator") if hasattr(descritor, "get") else None
    if not locators_prop:
        return None
    for locator in locators_prop.value:
        url_prop = locator.get("URLString") if hasattr(locator, "get") else None
        if url_prop and url_prop.value:
            return _url_para_caminho(url_prop.value)
    return None


def _passo_descritor(curr, estado: _CaminhoMob) -> None:
    """Le o descritor do mob: ids de arquivo e de essence, e caminho do arquivo."""
    descritor = getattr(curr, "descriptor", None)
    if descritor is None:
        return
    if _descritor_fisico(descritor):
        return

    curr_mob_id = getattr(curr, "mob_id", None)
    if curr_mob_id is not None:
        estado.mob_id_arquivo = str(curr_mob_id)
    if getattr(curr, "essence", None) is not None:
        estado.mob_id_essencia = str(curr_mob_id)

    # O caminho do Locator vale mesmo vazio (sobrescreve o anterior); so entao o
    # file_name do descritor entra como reserva.
    caminho = _caminho_do_locator(descritor)
    if caminho is not None:
        estado.caminho_arquivo = caminho
    if not estado.caminho_arquivo:
        nome_arquivo = getattr(descritor, "file_name", None)
        if nome_arquivo:
            estado.caminho_arquivo = str(nome_arquivo)


def _slots_ordenados(slots: list, source_slot_id) -> list:
    """Coloca o slot pedido na frente, mantendo a ordem dos demais."""
    if source_slot_id is None:
        return slots
    frente = [s for s in slots if getattr(s, "slot_id", None) == source_slot_id]
    resto = [s for s in slots if getattr(s, "slot_id", None) != source_slot_id]
    return frente + resto


def _sufixo_do_slot(slot) -> str:
    """Sufixo do slot, pelo nome do slot ou pelo seu id."""
    s_name = getattr(slot, "name", None)
    s_id = getattr(slot, "slot_id", None)
    if s_name:
        return f"_{s_name}"
    if s_id is not None:
        return f"_A{s_id}"
    return ""


def _proximo_mob_do_slot(slot, visitados: set[int]) -> tuple[object | None, object | None]:
    """Proximo mob referenciado por um slot e o slot id pedido pelo filho."""
    segmento = getattr(slot, "segment", None)
    componentes = getattr(segmento, "components", None) or ([segmento] if segmento else [])
    for filho in componentes:
        candidato = getattr(filho, "mob", None)
        if candidato is not None and id(candidato) not in visitados:
            return candidato, _slot_id_de(filho)
    return None, None


def _passo_slots(
    curr, slots: list, slots_to_check: list, visitados: set[int], estado: _CaminhoMob
) -> tuple[object | None, object | None]:
    """Percorre os slots: fixa o sufixo do MasterMob e acha o proximo mob."""
    proximo = None
    next_slot_id = None
    for slot in slots_to_check:
        if _type_name(curr) == "MasterMob" and len(slots) > 1 and not estado.sufixo_slot:
            estado.sufixo_slot = _sufixo_do_slot(slot)

        proximo, next_slot_id = _proximo_mob_do_slot(slot, visitados)
        if proximo is not None:
            break
    return proximo, next_slot_id


def _nome_final(component, master_name, estado: _CaminhoMob, final_mob_id) -> str | None:
    """Escolhe o nome final: caminho do arquivo, nome sugerido ou fallback."""
    if estado.caminho_arquivo:
        return estado.caminho_arquivo
    if final_mob_id:
        return f"{sem_extensao_de_audio(master_name) or 'audio'}{estado.sufixo_slot}.wav"
    nome_fallback = master_name or getattr(getattr(component, "mob", None), "name", None)
    if nome_fallback and not str(nome_fallback).lower().endswith(".wav"):
        nome_fallback = f"{nome_fallback}{estado.sufixo_slot}.wav"
    return nome_fallback


def _source_mob_ref(component) -> tuple[str | None, str | None]:
    """Retorna (nome_arquivo, mob_id_do_source_mob) percorrendo a cadeia
    CompositionMob -> SourceClip -> MasterMob -> SourceMob.

    mob_id_do_source_mob e o UMID string do SourceMob que carrega a essencia
    (o mob com descritor de arquivo ou EssenceData associado). Usado para localizar
    o EssenceData na extracao sem depender de casamento por nome.

    Retorna (None, None) quando nao ha mob referenciado.
    """
    mob = getattr(component, "mob", None)
    if mob is None:
        return None, None

    master_name = getattr(mob, "name", None)
    source_slot_id = _slot_id_de(component)
    visitados: set[int] = set()
    estado = _CaminhoMob()

    curr = mob
    while curr is not None and id(curr) not in visitados:
        visitados.add(id(curr))
        _passo_descritor(curr, estado)

        slots = list(getattr(curr, "slots", []) or [])
        slots_to_check = _slots_ordenados(slots, source_slot_id)
        curr, source_slot_id = _passo_slots(curr, slots, slots_to_check, visitados, estado)

    final_mob_id = estado.mob_id_essencia or estado.mob_id_arquivo
    return _nome_final(component, master_name, estado, final_mob_id), final_mob_id


def _declared_source_samples(component, sample_rate: int) -> int | None:
    """Comprimento da fonte, em samples, como o proprio AAF o declara.

    O `MasterMob` referenciado por um `SourceClip` traz esse numero no slot,
    na `edit_rate` dele. Vem do AAF e nao do disco, entao esta disponivel
    durante a leitura, antes de qualquer resolucao de arquivo.

    Isso importa por uma razao concreta: a decisao de absorver um fade depende
    de saber se o arquivo tem material para cobri-lo, e essa decisao acontece
    muito antes de a midia ser resolvida em disco. Sem este numero a checagem
    ficava atras de um `duration_samples` que era sempre nulo, e portanto nunca
    rodava.

    Devolve None quando o AAF nao declara, que e diferente de declarar zero.
    """
    mob = getattr(component, "mob", None)
    if mob is None:
        return None
    for slot in getattr(mob, "slots", []) or []:
        segmento = getattr(slot, "segment", None)
        comprimento = getattr(segmento, "length", None)
        if not comprimento:
            continue
        taxa = getattr(slot, "edit_rate", None)
        try:
            taxa = Fraction(taxa) if taxa is not None else None
        except (TypeError, ValueError):
            taxa = None
        if taxa is None or taxa == sample_rate:
            return int(comprimento)
        # Slot em outra unidade (frames, por exemplo): converte para samples.
        return int(Fraction(int(comprimento)) / taxa * sample_rate)
    return None


def _fonte_cobre_o_fade(raw_items: list[dict], fade_samples: int) -> bool:
    """O arquivo do clip anterior tem material para cobrir este fade?

    Absorver um fade-out estende o clip anterior por cima da regiao do fade,
    lendo do mesmo arquivo. Quando o Pro Tools renderizou um fade mais longo do
    que o material que sobra depois do corte, essa extensao aponta para alem do
    fim do arquivo e o trecho toca silencio.

    Medido na `TESTE_AAF`: o `Fade 71` tem 417792 samples e restava 1 segundo de
    material, entao a extensao pedia 1475584 de um arquivo com 1105792 e sete
    segundos e meio de fade-out sumiam.

    Na duvida devolve True, mantendo a absorcao, que e o comportamento padrao:
    so recusa quando ha numero que prove que nao cabe.
    """
    for item in reversed(raw_items):
        if item.get("kind") != "CLIP":
            continue
        for clip, asset in item.get("clip_assets") or []:
            if asset is None or asset.duration_samples is None:
                continue
            fim_na_fonte = (clip.source_offset_samples or 0) + (
                clip.timeline_end_samples - clip.timeline_start_samples
            )
            if asset.duration_samples - fim_na_fonte < fade_samples:
                return False
        return True
    return True


def _edit_units_to_samples(unidades: int, edit_rate, sample_rate: int) -> Fraction:
    """Converte posicao ou comprimento da unidade do slot para samples.

    A unidade e definida pelo edit_rate do slot. Quando ele ja e a taxa de
    amostragem a conversao e identidade; quando e taxa de frame, multiplica.
    """
    taxa = exact_edit_rate(edit_rate)
    return Fraction(unidades) * Fraction(sample_rate) / taxa


def _source_offset_samples(source_clip, sample_rate: int, fallback_rate, report: Report) -> int:
    """StartTime pertence ao slot referenciado; Length pertence à composição."""
    from .timecode import quantize_samples

    mob = getattr(source_clip, "mob", None)
    slot_id = getattr(source_clip, "slot_id", None)
    target_slot = next((s for s in getattr(mob, "slots", []) if s.slot_id == slot_id), None)
    rate = getattr(target_slot, "edit_rate", None)
    if rate is None:
        rate = fallback_rate
        report.warning(
            "SOURCE_EDIT_RATE_UNKNOWN",
            "source slot rate unknown; confirm offset",
            {"slot_id": slot_id},
        )
    value, residual = quantize_samples(
        _edit_units_to_samples(int(source_clip.start or 0), rate, sample_rate)
    )
    if residual:
        report.info(
            "SOURCE_OFFSET_ROUNDING",
            "source offset between samples; rounded",
            {"residual_samples": float(residual), "source_edit_rate": str(rate)},
        )
    return value


def _descer_ate_sequence(segmento):
    """Acha a Sequence dentro de um segmento de slot.

    O Premiere embrulha cada trilha num OperationGroup de operacao
    "Mono Audio Pan", e usa NestedScope para o video. Um leitor que espere
    Sequence direto no slot perde a trilha inteira sem aviso.
    """
    visto = set()
    pilha = [segmento]
    while pilha:
        atual = pilha.pop()
        if atual is None or id(atual) in visto:
            continue
        visto.add(id(atual))
        if _type_name(atual) == "Sequence":
            return atual
        pilha.extend(list(getattr(atual, "segments", None) or []))
        pilha.extend(list(getattr(atual, "slots", None) or []))
    return None
