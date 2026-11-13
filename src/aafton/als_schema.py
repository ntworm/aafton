"""Schema do modelo de objetos do Ableton Live, como contrato do .als.

O Live publica, em Resources/Schema/<MinorVersion>.txt, o schema do proprio
modelo de objetos: 412 classes, cada uma com os seus campos e a classe do
valor de cada campo. Esse arquivo e a segunda autoridade do .als, ao lado do
template (espécime salvo pelo Live). O template diz o que o Live gravou; o
schema diz o que o Live aceita.

O snapshot em resources/live_schema/ e a copia do arquivo publicado pelo
vendor para o build do template. SCHEMA_DIGEST e o sha256 do conteudo
descomprimido, para conferir a copia contra o arquivo instalado.

O schema diz que um campo e uma lista; ele nao diz quais classes podem ser
itens dela. Quem diz e o template do mesmo build, medido no XML: e a segunda
autoridade, com evidencia empirica, e nao informacao do vendor. O registro
LISTAS_MEDIDAS guarda essa medicao, e vale so para o build em que foi feita.
"""

from __future__ import annotations

import gzip
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA_BUILD = "12.0_12402"
SCHEMA_REL = "resources/live_schema/12.0_12402.txt.gz"
SCHEMA_SNAPSHOT = REPO / SCHEMA_REL
SCHEMA_DIGEST = "b7c9d4c767d8ca9b346ce7d517265446bbacd6de366cc058e155613789d0f299"

# O TranslatorNumber e a identidade do build dentro do schema: e o mesmo
# numero que aparece no sufixo do MinorVersion do .als ("12.0_12402").
BUILD_TRANSLATOR = SCHEMA_BUILD.split("_")[1]

# Pertencimento de lista, medido no template do build fixado.
#
# A chave e o par (classe que declara o campo, nome do campo) e o valor sao
# as classes que o template mostra como itens daquela lista. E evidencia
# empirica da segunda autoridade: o schema do vendor nao informa isso, e a
# medicao vale apenas para o build em que foi observada.
LISTAS_MEDIDAS: dict[str, dict[tuple[str, str], frozenset[str]]] = {
    BUILD_TRANSLATOR: {
        ("AutomationEnvelopes", "Envelopes"): frozenset({"AutomationEnvelope"}),
        ("ListAutomation", "Events"): frozenset({"EnumEvent", "FloatEvent"}),
        ("LiveSet", "Tracks"): frozenset({"GroupTrack", "AudioTrack", "ReturnTrack"}),
    },
}

# Raizes onde o Live instalado publica Resources/Schema.
LIVE_ROOTS = (
    Path("C:/ProgramData/Ableton"),
    Path("C:/Program Files/Ableton"),
)

# Campos do modelo que o .als serializa como atributo do elemento, e nao como
# elemento filho. O schema declara `Time` como campo de `AudioClip`; o .als
# grava o valor no atributo `@Time` do proprio <AudioClip>. O schema nao diz
# isso: e um mapeamento explicito do formato, e a resolucao de caminho do
# modelo continua separada dele.
ATRIBUTOS_XML: dict[tuple[str, str], str] = {
    ("AudioClip", "Time"): "Time",
}


def destino_xml(classe_raiz: str, caminho: str) -> tuple[str, str]:
    """Onde o valor do caminho mora no XML: ("elemento", caminho) ou ("atributo", nome).

    A maior parte dos campos do modelo e um elemento filho. Alguns sao
    atributos do proprio elemento: `AudioClip/Time` e o atributo `@Time` do
    <AudioClip>, e nao um elemento <Time>.
    """
    atributo = ATRIBUTOS_XML.get((classe_raiz, caminho))
    if atributo is not None:
        return ("atributo", atributo)
    return ("elemento", caminho)


@dataclass(frozen=True)
class Campo:
    """Um campo declarado pelo schema: nome, classe do valor e tipo."""

    nome: str
    classe: str | None
    tipo: str | None


@dataclass(frozen=True)
class Resolucao:
    """Resultado de resolver um caminho: classe do valor do campo.

    O schema declara a classe do valor, e o .als guarda esse valor dentro do
    elemento do campo. A cauda continua no contrato porque a auditoria le o
    resultado, mas o resolvedor nao a produz: um caminho so resolve quando o
    schema declara cada um dos seus segmentos e, quando o segmento e item de
    lista, quando a medicao no template prova o pertencimento.
    """

    classe: str
    cauda: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConceitoAAF:
    """Conceito que o AAF carrega e os caminhos que o Live usaria para ele.

    `contraparte` False marca o conceito que o AAF nao tem: o Live oferece,
    mas nao ha nada para converter. `caminhos` sao pares (classe, caminho),
    onde caminho e separado por "/" dentro da classe.
    """

    id: str
    titulo: str
    aaf: str
    caminhos: tuple[tuple[str, str], ...]
    contraparte: bool = True


def ler_bytes(caminho: Path) -> bytes:
    """Le o schema; aceita o snapshot .gz e o arquivo .txt do vendor."""
    bruto = caminho.read_bytes()
    if caminho.suffix == ".gz":
        bruto = gzip.decompress(bruto)
    return bruto


def digest_bytes(bruto: bytes) -> str:
    """Sha256 do conteudo do schema."""
    return hashlib.sha256(bruto).hexdigest()


class SchemaLive:
    """Schema de um build do Live: classes, campos e vocabulario de elementos."""

    def __init__(
        self,
        classes: dict[str, tuple[Campo, ...]],
        elementos: frozenset[str],
        versao: str | None,
        translator: str | None,
        change_count: str | None,
    ) -> None:
        self.classes = classes
        self.elementos = elementos
        self.vocabulario = elementos | frozenset(classes)
        self.versao = versao
        self.translator = translator
        self.change_count = change_count

    @classmethod
    def carregar(cls, caminho: Path) -> "SchemaLive":
        """Le um arquivo de schema do vendor e monta o registro de classes."""
        raiz = ET.fromstring(ler_bytes(caminho))
        classes: dict[str, tuple[Campo, ...]] = {}
        elementos: set[str] = set()
        for classe in raiz:
            campos: list[Campo] = []
            for filho in classe:
                elementos.add(filho.tag)
                campos.append(Campo(filho.tag, filho.get("Class"), filho.get("Type")))
            classes[classe.tag] = tuple(campos)
        return cls(
            classes,
            frozenset(elementos),
            raiz.get("Version"),
            raiz.get("TranslatorNumber"),
            raiz.get("ChangeCount"),
        )

    def tem_elemento(self, nome: str) -> bool:
        """Diz se o nome existe no schema, como campo ou como classe."""
        return nome in self.vocabulario

    def campos_de(self, classe: str) -> tuple[Campo, ...]:
        """Campos declarados por uma classe; vazio quando a classe nao existe.

        O vazio nao distingue classe ausente de classe sem campos declarados:
        quem precisa dessa distincao olha `classes`.
        """
        return self.classes.get(classe, ())

    def declaracoes(self) -> int:
        """Quantas declaracoes classe-campo o schema tem.

        E o total de pares (classe, campo). O total de nomes de campo
        distintos e `len(elementos)`: o mesmo nome pode ser declarado por
        varias classes, e e contado uma vez so.
        """
        return sum(len(campos) for campos in self.classes.values())

    def classe_do_campo(self, classe: str, nome: str) -> str | None:
        """Classe do valor de um campo; None quando o campo nao existe."""
        for campo in self.campos_de(classe):
            if campo.nome == nome:
                return campo.classe
        return None

    def listas_medidas(self) -> dict[tuple[str, str], frozenset[str]]:
        """O registro de pertencimento de lista que vale para este schema.

        Vazio para qualquer build que nao seja o fixado: a medicao e do
        template de um build, e nao se herda de outro.
        """
        return LISTAS_MEDIDAS.get(self.translator or "", {})

    def e_item_de_lista(self, lista: tuple[str, str], nome: str) -> bool:
        """O item `nome` pertence a lista declarada em `lista`.

        `lista` e o par (classe que declara o campo, nome do campo). A prova
        vem do registro medido no template do build; o schema do vendor diz
        que o campo e uma lista, nao quem pode estar nela.
        """
        return nome in self.listas_medidas().get(lista, frozenset())

    def resolver(self, caminho: str, classe_raiz: str) -> Resolucao | None:
        """Resolve um caminho de elementos sob uma classe; None quando nao bate.

        Um segmento casa com um campo declarado da classe atual. Depois de um
        campo de lista, o segmento seguinte pode ser o nome de uma classe: e o
        item da lista, que o .als nomeia pela classe. O item so resolve
        quando a medicao no template do build prova que ele pertence a
        aquela lista: nome de classe no schema, sozinho, nao autoriza.

        `None` significa que o contrato schema-template nao prova o caminho.
        Nao significa que o Live recuse o caminho: e falta de prova.
        """
        if classe_raiz not in self.classes:
            return None
        partes = caminho.split("/")
        if any(not parte for parte in partes):
            return None
        atual = classe_raiz
        pendente: tuple[str, str] | None = None
        for parte in partes:
            if pendente is not None and self.e_item_de_lista(pendente, parte):
                atual = parte
                pendente = None
                continue
            valor = self.classe_do_campo(atual, parte)
            if valor is None:
                return None
            pendente = (atual, parte)
            atual = valor
        return Resolucao(atual)

    def resolve_ou_falha(self, caminho: str, classe_raiz: str) -> Resolucao:
        """Como resolver, mas levanta ValueError com o caminho que nao bateu."""
        valor = self.resolver(caminho, classe_raiz)
        if valor is None:
            raise ValueError(
                f"{classe_raiz}/{caminho} is not proven by the schema-template contract of the build"
            )
        return valor


def schema_do_build(caminho: Path | None = None) -> SchemaLive:
    """Carrega o snapshot versionado do schema do build do template."""
    return SchemaLive.carregar(caminho or SCHEMA_SNAPSHOT)


def _candidatos(raiz: Path, minor_version: str) -> list[Path]:
    """Os arquivos de schema de um build dentro de uma raiz de instalacao."""
    return [
        pasta / f"{minor_version}.txt"
        for pasta in raiz.glob("*/Resources/Schema")
        if (pasta / f"{minor_version}.txt").is_file()
    ]


def _chave_de_candidato(caminho: Path) -> tuple[int, str]:
    """A instalacao estavel vem antes da beta; o caminho desempata."""
    return (1 if "beta" in str(caminho).lower() else 0, caminho.as_posix().lower())


def schema_instalado(
    minor_version: str = SCHEMA_BUILD,
    roots: tuple[Path, ...] | None = None,
) -> Path | None:
    """Acha o arquivo de schema de um build na instalacao do Live; None se nao houver.

    As duas raizes sao vasculhadas antes de escolher: a instalacao estavel
    pode estar na segunda e a beta na primeira, e a beta nao pode ganhar. Entre
    as estaveis, o caminho normalizado desempata, para a escolha ser a mesma
    em toda maquina.
    """
    candidatos: list[Path] = []
    for raiz in roots if roots is not None else LIVE_ROOTS:
        if not raiz.is_dir():
            continue
        candidatos.extend(_candidatos(raiz, minor_version))
    if not candidatos:
        return None
    return sorted(candidatos, key=_chave_de_candidato)[0]


def conferir_snapshot(caminho: Path | None = None) -> bool:
    """Diz se o snapshot bate com o digest do arquivo publicado pelo vendor."""
    alvo = caminho or SCHEMA_SNAPSHOT
    if not alvo.is_file():
        return False
    return digest_bytes(ler_bytes(alvo)) == SCHEMA_DIGEST


CONCEITOS_AAF: tuple[ConceitoAAF, ...] = (
    ConceitoAAF(
        id="posicao_e_duracao",
        titulo="Position and duration of the clip",
        aaf="SourceClip: Length of the composition slot and Start of the source slot",
        caminhos=(
            ("AudioClip", "CurrentStart"),
            ("AudioClip", "CurrentEnd"),
            ("AudioClip", "Time"),
            ("AudioClip", "Loop/LoopStart"),
            ("AudioClip", "Loop/LoopEnd"),
            ("AudioClip", "Loop/OutMarker"),
        ),
    ),
    ConceitoAAF(
        id="offset_da_fonte",
        titulo="Offset of the media inside the clip",
        aaf="SourceClip: Start of the source slot",
        caminhos=(
            ("AudioClip", "Loop/StartRelative"),
            ("AudioClip", "Loop/HiddenLoopStart"),
            ("AudioClip", "Loop/HiddenLoopEnd"),
        ),
    ),
    ConceitoAAF(
        id="ganho_do_clip",
        titulo="Static gain of the clip",
        aaf="OperationDef_MonoAudioGain (Amplitude multiplier)",
        caminhos=(("AudioClip", "SampleVolume"),),
    ),
    ConceitoAAF(
        id="automacao_de_ganho",
        titulo="Gain automation of the track",
        aaf="VaryingValue over the gain of the SourceClip",
        caminhos=(
            (
                "AudioTrack",
                "AutomationEnvelopes/Envelopes/AutomationEnvelope/Automation/Events/FloatEvent",
            ),
            ("AudioTrack", "DeviceChain/Mixer/Volume/AutomationTarget"),
        ),
    ),
    ConceitoAAF(
        id="fades",
        titulo="Fades and crossfade of the clip",
        aaf="Transition between SourceClips",
        caminhos=(
            ("AudioClip", "Fades/FadeInLength"),
            ("AudioClip", "Fades/FadeOutLength"),
            ("AudioClip", "Fades/CrossfadeInState"),
            ("AudioClip", "Fades/FadeInCurveSkew"),
            ("AudioClip", "Fades/FadeInCurveSlope"),
            ("AudioClip", "Fades/FadeOutCurveSkew"),
            ("AudioClip", "Fades/FadeOutCurveSlope"),
            ("AudioClip", "Fades/IsDefaultFadeIn"),
            ("AudioClip", "Fades/IsDefaultFadeOut"),
        ),
    ),
    ConceitoAAF(
        id="pan_volume_e_mute",
        titulo="Pan, volume and mute of the track",
        aaf="OperationDef_MonoAudioPan per channel and track gain",
        caminhos=(
            ("AudioTrack", "DeviceChain/Mixer/Pan/Manual"),
            ("AudioTrack", "DeviceChain/Mixer/PanMode"),
            ("AudioTrack", "DeviceChain/Mixer/SplitStereoPanL"),
            ("AudioTrack", "DeviceChain/Mixer/SplitStereoPanR"),
            ("AudioTrack", "DeviceChain/Mixer/Volume/Manual"),
            ("AudioTrack", "DeviceChain/Mixer/Speaker/Manual"),
        ),
    ),
    ConceitoAAF(
        id="marcadores",
        titulo="Markers of the timeline",
        aaf="OperationDef_Marker over the composition slot",
        caminhos=(
            ("Locator", "Time"),
            ("Locator", "Name"),
            ("Locator", "Annotation"),
            ("Locator", "IsSongStart"),
        ),
    ),
    ConceitoAAF(
        id="nome_da_faixa",
        titulo="Name of the track",
        aaf="name of the composition slot",
        caminhos=(
            ("AudioTrack", "Name/UserName"),
            ("AudioTrack", "Name/EffectiveName"),
            ("AudioTrack", "Name/Annotation"),
        ),
    ),
    ConceitoAAF(
        id="midia",
        titulo="Link to the media",
        aaf="AAFLocator and AAFMobSlot of the source",
        caminhos=(
            ("AudioClip", "SampleRef/FileRef/Path"),
            ("AudioClip", "SampleRef/FileRef/RelativePath"),
            ("AudioClip", "SampleRef/FileRef/RelativePathType"),
        ),
    ),
    ConceitoAAF(
        id="timecode",
        titulo="Timecode of the session",
        aaf="timecode of the composition slot",
        caminhos=(("LiveSet", "SmpteFormat"),),
    ),
    ConceitoAAF(
        id="takes",
        titulo="Takes of the same material",
        aaf="several source slots pointing to the same source",
        caminhos=(
            ("AudioClip", "TakeId"),
            ("AudioTrack", "TakeLanes"),
        ),
    ),
    ConceitoAAF(
        id="quantizacao",
        titulo="Quantisation and grid",
        aaf="no counterpart: the AAF does not carry the grid",
        caminhos=(
            ("LiveSet", "GlobalQuantisation"),
            ("LiveSet", "AutoQuantisation"),
            ("LiveSet", "Grid/FixedNumerator"),
            ("LiveSet", "Grid/FixedDenominator"),
            ("AudioClip", "LaunchQuantisation"),
        ),
        contraparte=False,
    ),
    ConceitoAAF(
        id="cor_da_faixa",
        titulo="Color of the track",
        aaf="no counterpart: the AAF does not carry track color",
        caminhos=(("AudioTrack", "Color"),),
        contraparte=False,
    ),
    ConceitoAAF(
        id="warp_e_groove",
        titulo="Warp and groove of the clip",
        aaf="no counterpart: the AAF does not carry warp nor groove",
        caminhos=(
            ("AudioClip", "IsWarped"),
            ("AudioClip", "WarpMode"),
            ("AudioClip", "GrooveSettings"),
        ),
        contraparte=False,
    ),
    ConceitoAAF(
        id="dispositivos",
        titulo="Devices of the track",
        aaf="no counterpart: the AAF does not carry plugin state",
        caminhos=(
            ("AudioTrack", "DevicesListWrapper"),
            ("AudioTrack", "DeviceChain/DeviceChain"),
        ),
        contraparte=False,
    ),
)
