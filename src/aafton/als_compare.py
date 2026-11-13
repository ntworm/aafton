"""Comparador semantico de dois .als: o que o aafton gerou e o que o Live devolveu.

R07.6, Onda 1, E1. O Live abre o .als gerado, o operador salva como, e este
modulo compara campo a campo. A normalizacao (gzip, ordem dos filhos, ids) e a
lista de ruido estao em ``als_compare_campos``; aqui fica a classificacao.

Classes de diferenca:

- ``DROPPED_POR_LIVE``: estava no gerado e o Live tirou, num campo ou entidade
  que o aafton escreve. E defeito.
- ``AUSENTE_NO_LIVE``: estava no gerado e o Live tirou, num campo herdado do
  template. O Live nao serializa aquilo; nao e defeito do aafton.
- ``RESCRITO_POR_LIVE``: o Live mudou ou acrescentou um valor que o aafton nao
  escreve, ou mudou um valor escrito por uma regra declarada em
  ``REGRAS_DO_LIVE``. E regra a documentar: vira especificacao.
- ``VALOR_DIVERGENTE``: o Live mudou um valor que o aafton escreve, e nenhuma
  regra declarada explica. E o que se investiga (e o gatilho da fase de REA).

O endereco efetivo da midia (o tipo ativo, o ``RelativePath`` e a pasta do
``.als``) e comparado fora do texto: dois documentos com o mesmo XML, salvos em
pastas diferentes, apontam para midias diferentes. Quando essa divergencia
aparece, a linha do relatorio termina em ``#referencia-efetiva``: e um
endereco derivado, nao um campo do XML serializado.
"""

from __future__ import annotations

import gzip
import struct
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Callable

from .als_compare_campos import RUIDO_PADRAO, Projecao, Ruido, e_numerico, projetar

__all__ = [
    "AUSENTE_NO_LIVE",
    "CLASSES",
    "DROPPED_POR_LIVE",
    "REGRAS_DO_LIVE",
    "RESCRITO_POR_LIVE",
    "RUIDO_PADRAO",
    "VALOR_DIVERGENTE",
    "Diferenca",
    "Ruido",
    "carregar",
    "comparar",
    "contagem",
    "tabela_markdown",
]

RESCRITO_POR_LIVE = "RESCRITO_POR_LIVE"
DROPPED_POR_LIVE = "DROPPED_POR_LIVE"
VALOR_DIVERGENTE = "VALOR_DIVERGENTE"
AUSENTE_NO_LIVE = "AUSENTE_NO_LIVE"
CLASSES = (DROPPED_POR_LIVE, VALOR_DIVERGENTE, RESCRITO_POR_LIVE, AUSENTE_NO_LIVE)
TIPO_RELATIVO_AO_PROJETO = "1"
# Politica local de referencia: so um tipo declarado decide o endereco efetivo
# da midia. Tipo fora da lista nao prova nada.
TIPO_ABSOLUTO_DO_TEMPLATE = "5"
# Tipo 0: material antigo, lido como absoluto so por compatibilidade local.
TIPO_ABSOLUTO_LEGADO = "0"
TIPOS_ABSOLUTOS = frozenset({TIPO_ABSOLUTO_DO_TEMPLATE, TIPO_ABSOLUTO_LEGADO})
PREFIXO_DO_FILE_REF = "SampleRef/FileRef/"
# Sufixo do caminho derivado no relatorio: o endereco efetivo da midia. O "#"
# marca o que nao existe no XML serializado.
SUFIXO_DO_ENDERECO = "#referencia-efetiva"
# Campos do FileRef que carregam o endereco da midia.
CAMPOS_DE_ENDERECO = ("Path", "RelativePath")


@dataclass(frozen=True)
class Diferenca:
    caminho: str
    gerado: str | None
    devolvido: str | None
    classe: str
    escrito: bool
    regra: str | None = None

    @property
    def defeito(self) -> bool:
        """DROPPED num campo que o aafton escreve: o Live jogou fora o que foi gerado."""
        return self.classe == DROPPED_POR_LIVE and self.escrito


def _float32(texto: str) -> float | None:
    try:
        (valor,) = struct.unpack(">f", struct.pack(">f", float(texto)))
    except (ValueError, OverflowError):
        return None
    return valor


def _mesmo_float32(gerado: str, devolvido: str) -> bool:
    a, b = _float32(gerado), _float32(devolvido)
    return a is not None and a == b


def _mesmo_numero(gerado: str, devolvido: str) -> bool:
    """Dois textos sao o mesmo numero: exato, sem passar por float.

    ``float`` arredonda: ``9007199254740993`` e ``9007199254740992`` viram o
    mesmo numero e a diferencia sumiria.
    """
    try:
        return Decimal(gerado) == Decimal(devolvido)
    except (InvalidOperation, ValueError):
        return False


# Regras do Live, declaradas: (nome, predicado). Uma diferenca num campo
# escrito que uma regra explica e RESCRITO_POR_LIVE, nao VALOR_DIVERGENTE.
# Regra nova so entra com o caso do Live que a produziu.
REGRA_FLOAT32 = "float32: the value is the same 32-bit float (field SampleVolume)"
REGRAS_DO_LIVE: tuple[tuple[str, Callable[[str, str], bool]], ...] = (
    (REGRA_FLOAT32, _mesmo_float32),
)

# Escopo de cada regra: so o campo declarado e explicado por ela. A
# compatibilidade de float32 vale para o volume do clip; ela nao explica tempo,
# posicao nem painel, e nao prova comportamento nativo do Live.
ESCOPO_DAS_REGRAS: dict[str, frozenset[str]] = {
    REGRA_FLOAT32: frozenset({"SampleVolume"}),
}


def _regra_explica(nome: str, campo: str) -> bool:
    """A regra so explica o campo do seu escopo; sem escopo, nao explica nada.

    O padrao e fechado: regra nova sem escopo declarado nao vale para todo
    campo, so para o campo que o escopo nomeia.
    """
    escopo = ESCOPO_DAS_REGRAS.get(nome)
    return escopo is not None and campo in escopo


def carregar(origem: str | Path | ET.Element) -> ET.Element:
    """Raiz do .als, gzipado ou em XML puro."""
    if isinstance(origem, ET.Element):
        return origem
    bruto = Path(origem).read_bytes()
    if bruto[:2] == b"\x1f\x8b":
        bruto = gzip.decompress(bruto)
    return ET.fromstring(bruto)


def _diretorio(origem: str | Path | ET.Element) -> str | None:
    """Pasta do .als: contexto para resolver o ``RelativePath`` da midia.

    A pasta e a do .als absoluto: argumento relativo resolvido contra o
    diretorio de trabalho, sem tocar no disco. Raiz ET nao tem pasta: sem ela
    o comparador fica conservador.
    """
    if isinstance(origem, ET.Element):
        return None
    return str(Path(origem).absolute().parent)


def _lexico(texto: str) -> PurePath:
    """Caminho lexical: opera no texto, nunca no disco.

    Windows quando o texto traz barra invertida ou letra de drive, POSIX no
    resto. A raiz fica preservada: ``//servidor/compartimento`` continua UNC e
    ``/local`` continua caminho local enraizado.
    """
    windows = "\\" in texto or (len(texto) > 1 and texto[1] == ":")
    return PureWindowsPath(texto) if windows else PurePosixPath(texto)


def _tem_raiz(texto: str) -> bool:
    """O caminho tem raiz: drive (``C:``), UNC (``//servidor``) ou barra.

    ``C:cena.wav`` e drive-relativo: tem drive e nao tem raiz, e por isso
    tambem nao e um caminho relativo ao projeto.
    """
    caminho = _lexico(texto)
    return bool(caminho.drive or caminho.root)


def _tem_raiz_real(texto: str) -> bool:
    """Raiz de verdade: ``C:cena.wav`` tem drive e nao tem raiz."""
    return bool(_lexico(texto).root)


def _normalizar_caminho(texto: str) -> str:
    """Caminho lexical normalizado, com a raiz preservada.

    A comparacao e de texto: letra maiuscula e barra nao sao a mesma coisa.
    Espaco e aspas nao se removem: um nome de arquivo valido continua valido.
    """
    if not texto:
        return ""
    normalizado = _lexico(texto).as_posix()
    unc = texto.startswith("//") or texto.startswith("\\\\")
    if unc and not normalizado.startswith("//"):
        normalizado = "/" + normalizado
    return normalizado


def _juntar(diretorio: str, relativo: str) -> str:
    """Caminho relativo resolvido contra a pasta do .als."""
    return _normalizar_caminho(f"{_normalizar_caminho(diretorio)}/{relativo}")


def _referencia_relativa(projecao: Projecao, prefixo: str) -> str | None:
    """Endereco do ``RelativePath``: relativo a pasta do .als."""
    relativo = projecao.campos.get(prefixo + "RelativePath")
    if relativo is None or not relativo[1]:
        return None
    if projecao.diretorio is None or _tem_raiz(relativo[1]):
        return None
    return _juntar(projecao.diretorio, relativo[1])


def _referencia_absoluta(projecao: Projecao, prefixo: str) -> str | None:
    """Endereco do ``Path``: caminho absoluto (drive-relativo nao e absoluto)."""
    absoluto = projecao.campos.get(prefixo + "Path")
    if absoluto is None or not absoluto[1] or not _tem_raiz_real(absoluto[1]):
        return None
    return _normalizar_caminho(absoluto[1])


def _referencia_efetiva(projecao: Projecao, entidade: str) -> str | None:
    """Endereco da midia que o documento aponta, ja resolvido.

    So um tipo com politica declarada decide: ``1`` e relativo a pasta do
    .als; ``5`` (o do template) e ``0`` (legado) usam o ``Path`` absoluto.
    Tipo ausente ou desconhecido devolve ``None``: nao prova nada, e a
    diferenca crua do tipo continua aparecendo.
    """
    prefixo = f"{entidade}/{PREFIXO_DO_FILE_REF}"
    tipo = projecao.campos.get(prefixo + "RelativePathType")
    if tipo is None:
        return None
    if tipo[1] == TIPO_RELATIVO_AO_PROJETO:
        return _referencia_relativa(projecao, prefixo)
    if tipo[1] in TIPOS_ABSOLUTOS:
        return _referencia_absoluta(projecao, prefixo)
    return None


def _mesma_referencia(entidade: str, a: Projecao, b: Projecao) -> bool:
    """Os dois lados apontam para o mesmo arquivo (mesmo caminho efetivo)."""
    ref_a = _referencia_efetiva(a, entidade)
    ref_b = _referencia_efetiva(b, entidade)
    return ref_a is not None and ref_a == ref_b


def _entidades_com_midia(projecao: Projecao) -> set[str]:
    """Entidades que tem referencia de midia (o clip e o seu FileRef)."""
    entidades = set()
    for chave, (entidade, _, _) in projecao.campos.items():
        if chave.startswith(f"{entidade}/{PREFIXO_DO_FILE_REF}"):
            entidades.add(entidade)
    return entidades


def _endereco_ja_reportado(entidade: str, diferencas: list[Diferenca]) -> bool:
    """A diferenca crua de Path ou RelativePath ja conta a troca de endereco."""
    return any(
        d.caminho in {f"{entidade}/{PREFIXO_DO_FILE_REF}{campo}" for campo in CAMPOS_DE_ENDERECO}
        for d in diferencas
    )


def _diferencas_de_endereco(
    a: Projecao, b: Projecao, diferencas: list[Diferenca], comuns: set[str]
) -> list[Diferenca]:
    """Endereco efetivo diferente: o XML pode ser igual e a midia nao.

    O endereco depende do tipo ativo, do ``RelativePath`` e da pasta do .als.
    A linha e derivada: o caminho termina em ``#referencia-efetiva`` e nao e
    um campo do XML serializado.
    """
    saida = []
    entidades = _entidades_com_midia(a) & _entidades_com_midia(b) & comuns
    for entidade in sorted(entidades):
        ref_a = _referencia_efetiva(a, entidade)
        ref_b = _referencia_efetiva(b, entidade)
        if ref_a is None or ref_b is None or ref_a == ref_b:
            continue
        if _endereco_ja_reportado(entidade, diferencas):
            continue
        caminho = f"{entidade}/{PREFIXO_DO_FILE_REF}{SUFIXO_DO_ENDERECO}"
        saida.append(Diferenca(caminho, ref_a, ref_b, VALOR_DIVERGENTE, True))
    return saida


def _relocado(chave: str, a: Projecao, b: Projecao, ruido: Ruido) -> bool:
    """True quando a diferenca e so a forma de escrever a mesma midia.

    O Live relativiza o caminho da midia (``RelativePathType``=1) e reescreve o
    ``RelativePath``. Isso so e ruido se os dois lados apontarem para o mesmo
    arquivo: o caminho efetivo de cada lado e resolvido com o tipo ativo, a
    pasta do .als e o ``RelativePath``. Tipo 1 sozinho nao prova nada, e um
    caminho absoluto igual e obsoleto nao esconde uma midia trocada.
    """
    sufixo = next((s for s in ruido.relocacao if chave.endswith("/" + s)), None)
    if sufixo is None:
        return False
    entrada = a.campos.get(chave) or b.campos.get(chave)
    if entrada is None:
        return False
    entidade = entrada[0]
    tipo = b.campos.get(f"{entidade}/{PREFIXO_DO_FILE_REF}RelativePathType")
    if tipo is None or tipo[1] != TIPO_RELATIVO_AO_PROJETO:
        return False
    return _mesma_referencia(entidade, a, b)


def _caminho_do_campo(chave: str, entidade: str) -> str:
    """Caminho do campo dentro da entidade (sem a chave da entidade)."""
    return chave[len(entidade) + 1 :]


def _classe_dos_valores(
    gerado: str, devolvido: str, escrito: bool, numerico: bool, campo: str
) -> tuple[str | None, str | None]:
    if gerado == devolvido:
        return None, None
    if numerico and _mesmo_numero(gerado, devolvido):
        return None, None
    if not escrito:
        return RESCRITO_POR_LIVE, None
    for nome, explica in REGRAS_DO_LIVE:
        if _regra_explica(nome, campo) and explica(gerado, devolvido):
            return RESCRITO_POR_LIVE, nome
    return VALOR_DIVERGENTE, None


def _diferencas_de_entidade(a: Projecao, b: Projecao) -> list[Diferenca]:
    saida = [
        Diferenca(chave, "exists", None, DROPPED_POR_LIVE, True)
        for chave in sorted(a.entidades - b.entidades)
    ]
    saida += [
        Diferenca(chave, None, "exists", VALOR_DIVERGENTE, True)
        for chave in sorted(b.entidades - a.entidades)
    ]
    return saida


def _diferenca_de_campo(chave: str, a: Projecao, b: Projecao, ruido: Ruido) -> Diferenca | None:
    lado_a, lado_b = a.campos.get(chave), b.campos.get(chave)
    if _relocado(chave, a, b, ruido):
        return None
    if lado_b is None:
        _, valor, escrito = lado_a
        classe = DROPPED_POR_LIVE if escrito else AUSENTE_NO_LIVE
        return Diferenca(chave, valor, None, classe, escrito)
    if lado_a is None:
        _, valor, escrito = lado_b
        return Diferenca(chave, None, valor, RESCRITO_POR_LIVE, escrito)
    escrito = lado_a[2]
    campo = _caminho_do_campo(chave, lado_a[0])
    classe, regra = _classe_dos_valores(lado_a[1], lado_b[1], escrito, e_numerico(campo), campo)
    if classe is None:
        return None
    return Diferenca(chave, lado_a[1], lado_b[1], classe, escrito, regra)


def comparar(
    gerado: str | Path | ET.Element,
    devolvido: str | Path | ET.Element,
    *,
    ruido: Ruido = RUIDO_PADRAO,
) -> list[Diferenca]:
    """Diferencas semanticas entre o .als gerado e o que o Live devolveu."""
    a = projetar(carregar(gerado), ruido, diretorio=_diretorio(gerado))
    b = projetar(carregar(devolvido), ruido, diretorio=_diretorio(devolvido))
    diferencas = _diferencas_de_entidade(a, b)
    comuns = a.entidades & b.entidades
    chaves = {
        chave
        for chave, (entidade, _, _) in (*a.campos.items(), *b.campos.items())
        if entidade in comuns
    }
    for chave in sorted(chaves):
        diferenca = _diferenca_de_campo(chave, a, b, ruido)
        if diferenca is not None:
            diferencas.append(diferenca)
    diferencas += _diferencas_de_endereco(a, b, diferencas, comuns)
    return diferencas


def identidade(origem: str | Path | ET.Element) -> dict[str, str]:
    """Build que gravou o documento: MinorVersion, SchemaChangeCount e Creator."""
    raiz = carregar(origem)
    return {nome: raiz.get(nome, "") for nome in ("MinorVersion", "SchemaChangeCount", "Creator")}


def contagem(diferencas: list[Diferenca]) -> dict[str, int]:
    """Diferencas por classe, com as quatro classes sempre presentes."""
    contadas = Counter(d.classe for d in diferencas)
    return {classe: contadas.get(classe, 0) for classe in CLASSES}


def _celula(valor: str | None) -> str:
    if valor is None:
        return "—"
    texto = valor.replace("|", "\\|")
    return texto if len(texto) <= 60 else texto[:57] + "..."


def tabela_markdown(diferencas: list[Diferenca], caso: str = "") -> str:
    """Uma linha por diferenca: caso, caminho, gerado, devolvido, classe e regra."""
    linhas = [
        "| case | path | generated | returned by Live | class | rule |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in diferencas:
        linhas.append(
            f"| {caso} | `{d.caminho}` | {_celula(d.gerado)} | {_celula(d.devolvido)} "
            f"| {d.classe} | {d.regra or ''} |"
        )
    return "\n".join(linhas)
