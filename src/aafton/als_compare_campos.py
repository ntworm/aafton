"""Projecao semantica de um .als para o comparador diferencial (R07.6, E1).

O Live reescreve o arquivo inteiro no Save As: o gzip, a ordem dos filhos e os
ids mudam sem mudar o significado. A projecao troca o XML por um dicionario de
caminho semantico -> valor, so com os campos declarados abaixo. Cada entidade
(track, clip, locator) e identificada pelo nome e pela ordem entre as de mesmo
nome, nunca pelo Id; os ponteiros por Id (grupo da track, alvo da automacao)
sao resolvidos para o nome do que apontam.

A lista de campos e a do R07.5 (docs/ALS_SCHEMA.md), por classe. ``escrito``
diz se o aafton grava o campo (False: o valor veio do template por clonagem).
A lista ``CAMPOS_NUMERICOS`` diz quais campos sao quantidade: so eles comparam
``4096`` com ``4096.0``. A lista e fechada: um filho desconhecido da grade nao
tem tipo declarado e continua texto, e texto nao vira numero: ``001`` nunca e
``1``. Nome, anotacao, caminho de midia e campo desconhecido sao texto.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

TRILHAS = ("AudioTrack", "GroupTrack")
CAMINHO_CLIPS = "DeviceChain/MainSequencer/Sample/ArrangerAutomation/Events/AudioClip"
CAMINHO_ENVELOPES = "AutomationEnvelopes/Envelopes/AutomationEnvelope"
CAMINHO_LOCATORS = "Locators/Locators/Locator"
PARAMETROS_DO_MIXER = ("Volume", "Pan", "Speaker", "SplitStereoPanL", "SplitStereoPanR")
SEM_GRUPO = "-1"


@dataclass(frozen=True)
class CampoDeclarado:
    """Um campo comparado: caminho relativo ao no da entidade.

    ``@Nome`` e um atributo do proprio no; ``X/*`` sao os filhos de ``X``, um
    campo por filho.
    """

    caminho: str
    escrito: bool = True


def _nao_escrito(caminho: str) -> CampoDeclarado:
    return CampoDeclarado(caminho, escrito=False)


FADES_ESCRITOS = (
    "FadeInLength",
    "FadeOutLength",
    "CrossfadeInState",
    "FadeInCurveSkew",
    "FadeInCurveSlope",
    "FadeOutCurveSkew",
    "FadeOutCurveSlope",
    "IsDefaultFadeIn",
    "IsDefaultFadeOut",
)

CAMPOS_CLIP: tuple[CampoDeclarado, ...] = (
    # A posicao do clip no arranjo e o atributo Time, que acompanha CurrentStart.
    CampoDeclarado("@Time"),
    CampoDeclarado("CurrentStart"),
    CampoDeclarado("CurrentEnd"),
    CampoDeclarado("Loop/LoopStart"),
    CampoDeclarado("Loop/LoopEnd"),
    CampoDeclarado("Loop/OutMarker"),
    CampoDeclarado("Loop/StartRelative"),
    CampoDeclarado("Loop/LoopOn"),
    CampoDeclarado("Loop/HiddenLoopStart"),
    CampoDeclarado("Loop/HiddenLoopEnd"),
    CampoDeclarado("SampleVolume"),
    *(CampoDeclarado(f"Fades/{nome}") for nome in FADES_ESCRITOS),
    # Os filhos diretos do FileRef alem de Path e RelativePath entram como
    # texto herdado do template. O curinga vem antes dos campos nomeados para
    # que Path e RelativePath, que o aafton escreve, fiquem escritos.
    _nao_escrito("SampleRef/FileRef/*"),
    CampoDeclarado("SampleRef/FileRef/Path"),
    CampoDeclarado("SampleRef/FileRef/RelativePath"),
    _nao_escrito("SampleRef/FileRef/RelativePathType"),
    _nao_escrito("SampleRef/FileRef/Type"),
    _nao_escrito("SampleRef/DefaultDuration"),
    _nao_escrito("SampleRef/DefaultSampleRate"),
    CampoDeclarado("Name"),
    _nao_escrito("Color"),
    CampoDeclarado("IsWarped"),
    _nao_escrito("WarpMode"),
    _nao_escrito("LaunchQuantisation"),
    # A grade vem do template: nao ha escritor de Grid no aafton.
    _nao_escrito("Grid/*"),
)

CAMPOS_TRACK: tuple[CampoDeclarado, ...] = (
    CampoDeclarado("Name/UserName"),
    CampoDeclarado("Name/EffectiveName"),
    CampoDeclarado("Name/Annotation"),
    _nao_escrito("Color"),
    CampoDeclarado("DeviceChain/Mixer/Pan/Manual"),
    _nao_escrito("DeviceChain/Mixer/PanMode"),
    _nao_escrito("DeviceChain/Mixer/SplitStereoPanL"),
    _nao_escrito("DeviceChain/Mixer/SplitStereoPanR"),
    CampoDeclarado("DeviceChain/Mixer/Volume/Manual"),
    CampoDeclarado("DeviceChain/Mixer/Speaker/Manual"),
    _nao_escrito("TakeLanes"),
    _nao_escrito("TakeLanesListWrapper"),
)

CAMPOS_LIVESET: tuple[CampoDeclarado, ...] = (
    CampoDeclarado("SmpteFormat"),
    _nao_escrito("GlobalQuantisation"),
    _nao_escrito("AutoQuantisation"),
    # A grade vem do template: nao ha escritor de Grid no aafton.
    _nao_escrito("Grid/*"),
    # O tempo nao esta na lista do R07.5, mas toda posicao em batida depende dele.
    CampoDeclarado("MainTrack/DeviceChain/Mixer/Tempo/Manual"),
)

CAMPOS_LOCATOR: tuple[CampoDeclarado, ...] = (
    CampoDeclarado("Time"),
    CampoDeclarado("Name"),
    CampoDeclarado("Annotation"),
    CampoDeclarado("IsSongStart"),
)

# Campos cujo valor e uma quantidade (tempo, volume, painel, fade). A lista e
# fechada: o que nao esta aqui e texto, e texto se compara como texto. Os fades
# sao os de FADES_ESCRITOS, um a um: prefixo nao serve, porque filho
# desconhecido da grade nao tem tipo declarado.
CAMPOS_NUMERICOS: frozenset[str] = frozenset(
    "@Time CurrentStart CurrentEnd SampleVolume Time SmpteFormat "
    "Loop/LoopStart Loop/LoopEnd Loop/OutMarker Loop/StartRelative Loop/LoopOn "
    "Loop/HiddenLoopStart Loop/HiddenLoopEnd SampleRef/DefaultDuration "
    "SampleRef/DefaultSampleRate DeviceChain/Mixer/Pan/Manual "
    "DeviceChain/Mixer/Volume/Manual DeviceChain/Mixer/Speaker/Manual "
    "DeviceChain/Mixer/SplitStereoPanL DeviceChain/Mixer/SplitStereoPanR "
    "MainTrack/DeviceChain/Mixer/Tempo/Manual".split()
) | frozenset(f"Fades/{nome}" for nome in FADES_ESCRITOS)


def e_numerico(caminho: str) -> bool:
    """O campo do caminho (relativo a entidade) e uma quantidade?

    So se o caminho esta na lista fechada: um filho desconhecido do ``Grid``
    e texto, e ``001`` nao e ``1``.
    """
    return caminho in CAMPOS_NUMERICOS


@dataclass(frozen=True)
class Ruido:
    """O que o Live muda no Save As sem mudar o significado: nunca comparado.

    ``tags`` sao elementos ignorados em qualquer lugar; ``atributos`` sao
    atributos ignorados em qualquer elemento (o Id e indice posicional ou
    ponteiro; os ponteiros sao resolvidos pelo nome antes). ``relocacao`` sao os
    campos que o Live reescreve quando troca o caminho absoluto da midia por um
    relativo (``RelativePathType=1``).
    """

    tags: frozenset[str]
    atributos: frozenset[str]
    relocacao: frozenset[str] = frozenset()


RUIDO_PADRAO = Ruido(
    tags=frozenset({"LomId", "LomIdView", "IsContentSelectedInDocument", "ViewData"}),
    # Id: ids de Device, AutomationLanes, ClipSlot e ids proprios de clip.
    atributos=frozenset({"Id", "LomId", "Revision"}),
    relocacao=frozenset({"SampleRef/FileRef/RelativePathType", "SampleRef/FileRef/RelativePath"}),
)


@dataclass
class Projecao:
    """Um .als visto pelo comparador: entidades e campos com chave semantica."""

    entidades: set[str] = field(default_factory=set)
    # chave -> (entidade, valor, escrito)
    campos: dict[str, tuple[str, str, bool]] = field(default_factory=dict)
    # pasta do .als: sem ela o RelativePath da midia nao pode ser resolvido
    diretorio: str | None = None


def achatar(no: ET.Element, ruido: Ruido) -> str:
    """Valor de um no: o Value, ou todos os valores da subarvore em ordem estavel."""
    if "Value" in no.attrib and len(no) == 0:
        return no.attrib["Value"]
    partes: list[str] = []
    _achatar_em(no, "", ruido, partes)
    return ";".join(sorted(partes))


def _achatar_em(no: ET.Element, prefixo: str, ruido: Ruido, partes: list[str]) -> None:
    for nome, valor in no.attrib.items():
        if nome not in ruido.atributos:
            partes.append(f"{prefixo}@{nome}={valor}")
    for filho in no:
        if filho.tag not in ruido.tags:
            _achatar_em(filho, f"{prefixo}{filho.tag}/", ruido, partes)


def valores_do_campo(no: ET.Element, campo: CampoDeclarado, ruido: Ruido) -> list[tuple[str, str]]:
    """Pares (caminho, valor) do campo no no; vazio quando o campo falta."""
    if campo.caminho.startswith("@"):
        valor = no.get(campo.caminho[1:])
        return [] if valor is None else [(campo.caminho, valor)]
    if campo.caminho.endswith("/*"):
        base = campo.caminho[:-2]
        alvo = no.find(base)
        if alvo is None:
            return []
        return [
            (f"{base}/{filho.tag}", achatar(filho, ruido))
            for filho in alvo
            if filho.tag not in ruido.tags
        ]
    alvo = no.find(campo.caminho)
    return [] if alvo is None else [(campo.caminho, achatar(alvo, ruido))]


def _registrar(
    projecao: Projecao,
    entidade: str,
    no: ET.Element,
    campos: tuple[CampoDeclarado, ...],
    ruido: Ruido,
) -> None:
    projecao.entidades.add(entidade)
    for campo in campos:
        for caminho, valor in valores_do_campo(no, campo, ruido):
            projecao.campos[f"{entidade}/{caminho}"] = (entidade, valor, campo.escrito)


def _chaves_por_nome(prefixo: str, nomes: list[str]) -> list[str]:
    """Chave de cada entidade: nome e ordem entre as de mesmo nome."""
    vistos: dict[str, int] = {}
    chaves = []
    for nome in nomes:
        indice = vistos.get(nome, 0)
        vistos[nome] = indice + 1
        chaves.append(f"{prefixo}[{nome}#{indice}]")
    return chaves


def _valor(no: ET.Element, caminho: str) -> str:
    alvo = no.find(caminho)
    return "" if alvo is None else alvo.get("Value", "")


def _nome_da_track(no: ET.Element) -> str:
    return _valor(no, "Name/UserName") or _valor(no, "Name/EffectiveName")


def _tempo(no: ET.Element, caminho: str) -> float:
    bruto = no.get(caminho[1:]) if caminho.startswith("@") else _valor(no, caminho)
    try:
        return float(bruto)
    except ValueError:
        return 0.0


def _ordenados(nos: list[ET.Element], caminho_do_tempo: str, caminho_do_nome: str):
    return sorted(nos, key=lambda n: (_tempo(n, caminho_do_tempo), _valor(n, caminho_do_nome)))


def _projetar_clips(projecao: Projecao, chave_track: str, track: ET.Element, ruido: Ruido) -> None:
    clips = _ordenados(track.findall(CAMINHO_CLIPS), "CurrentStart", "Name")
    nomes = [_valor(clip, "Name") for clip in clips]
    for chave, clip in zip(_chaves_por_nome(f"{chave_track}/AudioClip", nomes), clips):
        _registrar(projecao, chave, clip, CAMPOS_CLIP, ruido)


def _alvos_do_mixer(track: ET.Element) -> dict[str, str]:
    """Id do AutomationTarget de cada parametro do mixer -> nome do parametro."""
    alvos = {}
    for parametro in PARAMETROS_DO_MIXER:
        alvo = track.find(f"DeviceChain/Mixer/{parametro}/AutomationTarget")
        if alvo is not None and alvo.get("Id") is not None:
            alvos[alvo.get("Id")] = parametro
    return alvos


def _eventos(envelope: ET.Element) -> str:
    eventos = envelope.findall("Automation/Events/*")
    pares = sorted((_tempo(e, "@Time"), e.get("Time", ""), e.get("Value", "")) for e in eventos)
    return ";".join(f"{tempo}:{valor}" for _, tempo, valor in pares)


def _projetar_automacao(projecao: Projecao, chave_track: str, track: ET.Element) -> None:
    alvos = _alvos_do_mixer(track)
    envelopes = track.findall(CAMINHO_ENVELOPES)
    nomes = [
        alvos.get(_valor(env, "EnvelopeTarget/PointeeId"), "target-outside-the-mixer")
        for env in envelopes
    ]
    for chave, env in zip(_chaves_por_nome(f"{chave_track}/Automacao", nomes), envelopes):
        projecao.campos[chave] = (chave_track, _eventos(env), True)


def _projetar_tracks(projecao: Projecao, liveset: ET.Element, ruido: Ruido) -> None:
    tracks = [no for no in liveset.findall("Tracks/*") if no.tag in TRILHAS]
    chaves = [
        chave
        for tag in TRILHAS
        for chave in _chaves_por_nome(tag, [_nome_da_track(t) for t in tracks if t.tag == tag])
    ]
    ordem = [t for tag in TRILHAS for t in tracks if t.tag == tag]
    por_id = {t.get("Id"): chave for chave, t in zip(chaves, ordem)}
    for chave, track in zip(chaves, ordem):
        _registrar(projecao, chave, track, CAMPOS_TRACK, ruido)
        grupo = _valor(track, "TrackGroupId")
        destino = (
            "-" if grupo in ("", SEM_GRUPO) else por_id.get(grupo, f"nonexistent-group:{grupo}")
        )
        projecao.campos[f"{chave}/TrackGroupId"] = (chave, destino, True)
        _projetar_automacao(projecao, chave, track)
        _projetar_clips(projecao, chave, track, ruido)


def _projetar_locators(projecao: Projecao, liveset: ET.Element, ruido: Ruido) -> None:
    locators = _ordenados(liveset.findall(CAMINHO_LOCATORS), "Time", "Name")
    nomes = [_valor(loc, "Name") for loc in locators]
    for chave, loc in zip(_chaves_por_nome("Locator", nomes), locators):
        _registrar(projecao, chave, loc, CAMPOS_LOCATOR, ruido)


def projetar(
    raiz: ET.Element, ruido: Ruido = RUIDO_PADRAO, diretorio: str | None = None
) -> Projecao:
    """Projecao semantica do documento inteiro.

    ``diretorio`` e a pasta do .als: sem ela o ``RelativePath`` da midia nao
    pode ser resolvido, e o comparador fica conservador (a diferenca aparece).
    """
    projecao = Projecao(diretorio=diretorio)
    liveset = raiz.find("LiveSet")
    if liveset is None:
        raise ValueError("document without LiveSet: not a Live .als")
    _registrar(projecao, "LiveSet", liveset, CAMPOS_LIVESET, ruido)
    _projetar_tracks(projecao, liveset, ruido)
    _projetar_locators(projecao, liveset, ruido)
    return projecao
