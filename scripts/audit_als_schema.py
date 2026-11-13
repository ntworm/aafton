"""Auditoria do .als contra o schema do Live.

Compara, conceito AAF a conceito AAF, o que o schema do Live oferece, o que o
escritor declara e o que o .als realmente gerado carrega. Cada caminho sai
como:

  escrito  - a medicao provou que o escritor grava esse caminho: o valor
             medido no .als gerado bate com o valor previsto a partir da
             entrada, em duas entradas controladas, e difere do template
  presente - o caminho existe no .als gerado, mas nenhuma medicao provou a
             escrita: o valor pode ter vindo da clonagem do template
  ausente  - o caminho nao existe no .als gerado

A declaracao do escritor em `als_paths` e uma coluna a parte: ela diz o que o
escritor pretende escrever, nao o que ele escreve.

O .als gerado vem de duas sessoes minimas escritas pelo proprio aafton, com o
template do repositorio: e a saida real, nao uma suposicao.

Uso:
    python scripts/audit_als_schema.py
    python scripts/audit_als_schema.py --instalado
    python scripts/audit_als_schema.py --markdown docs/ALS_SCHEMA.md
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aafton.als_paths import (  # noqa: E402
    AUDIO_CLIP_FIELDS,
    LIVESET_FIELDS,
    LOCATOR_FIELDS,
    TRACK_FIELDS,
)
from aafton.als_schema import (  # noqa: E402
    CONCEITOS_AAF,
    SCHEMA_BUILD,
    ConceitoAAF,
    SchemaLive,
    conferir_snapshot,
    destino_xml,
    schema_do_build,
    schema_instalado,
)
from aafton.als_xml import load_als  # noqa: E402

from scripts.audit_als_schema_measure import (  # noqa: E402
    AMOSTRAS,
    caminhos_provados,
    gerar_documentos,
    medir,
)

TEMPLATE = Path("templates/live12.als")

# Raiz do .als -> tag que carrega o no correspondente.
RAIZES = ("AudioClip", "AudioTrack", "Locator", "LiveSet")

ESCRITO = "escrito"
PRESENTE = "presente"
AUSENTE = "ausente"

CABECALHO = (
    "| conceito | contraparte AAF | caminho no modelo | classe do valor "
    "| serializacao | declarado | situacao |"
)
SEPARADOR = "| --- | --- | --- | --- | --- | --- | --- |"


@dataclass(frozen=True)
class Presenca:
    """O que o .als gerado traz sob um no raiz: elementos e atributos."""

    elementos: frozenset[str]
    atributos: frozenset[str]


@dataclass(frozen=True)
class Contexto:
    """O que a tabela mede: o schema, o gerado, o declarado e o provado."""

    schema: SchemaLive
    do_gerado: dict[str, Presenca]
    do_escritor: dict[str, set[str]]
    provados: set[str]


def coletar(root: ET.Element, tag: str) -> Presenca:
    """Os caminhos de elemento e os nomes de atributo sob o no `tag`.

    Os atributos sao os do proprio no raiz: e onde o Live serializa alguns
    campos do modelo, como o `@Time` do <AudioClip>.
    """
    no = root.find(f".//{tag}")
    if no is None:
        return Presenca(frozenset(), frozenset())
    achados: set[str] = set()

    def percorre(atual: ET.Element, prefixo: str) -> None:
        for filho in atual:
            caminho = f"{prefixo}/{filho.tag}" if prefixo else filho.tag
            achados.add(caminho)
            percorre(filho, caminho)

    percorre(no, "")
    return Presenca(frozenset(achados), frozenset(no.attrib))


def declarados() -> dict[str, set[str]]:
    """Caminhos que o escritor declara, por classe raiz."""
    return {
        "AudioClip": set(AUDIO_CLIP_FIELDS.values()),
        "Locator": set(LOCATOR_FIELDS.values()),
        "AudioTrack": set(TRACK_FIELDS.values()),
        "LiveSet": set(LIVESET_FIELDS.values()),
    }


def classifica(
    caminho: str,
    raiz: str,
    do_gerado: dict[str, Presenca],
    provados: set[str],
) -> str:
    """Classifica o caminho pelo .als gerado e pela medicao.

    A ausencia e decidida pelo XML gerado: um caminho que o escritor declara
    e que nao aparece no .als gerado e `ausente`. Declaracao sozinha nao
    prova escrita. Presenca sozinha tambem nao: quem nao foi provado por
    medicao e `presente`, porque herdar do template nao se mede.
    """
    presenca = do_gerado.get(raiz)
    if presenca is None:
        return AUSENTE
    tipo, nome = destino_xml(raiz, caminho)
    if tipo == "atributo":
        achado = nome in presenca.atributos
    else:
        achado = nome in presenca.elementos
    if not achado:
        return AUSENTE
    if f"{raiz}/{caminho}" in provados:
        return ESCRITO
    return PRESENTE


def classe_do_valor(schema: SchemaLive, caminho: str, raiz: str) -> str:
    """A classe do valor do caminho no schema, ou o aviso de que nao resolve."""
    resolucao = schema.resolver(caminho, raiz)
    if resolucao is None:
        return "nao resolve no schema"
    return resolucao.classe


def linha_de_conceito(
    conceito: ConceitoAAF,
    raiz: str,
    caminho: str,
    ctx: Contexto,
) -> str:
    """A linha Markdown de um caminho de conceito."""
    tipo, nome = destino_xml(raiz, caminho)
    serializacao = f"atributo @{nome}" if tipo == "atributo" else f"elemento {nome}"
    declarado = "sim" if caminho in ctx.do_escritor.get(raiz, set()) else "nao"
    contraparte = "sim" if conceito.contraparte else "nao"
    situacao = classifica(caminho, raiz, ctx.do_gerado, ctx.provados)
    classe = classe_do_valor(ctx.schema, caminho, raiz)
    return (
        f"| {conceito.id} | {contraparte} | {raiz}/{caminho} | {classe} | "
        f"{serializacao} | {declarado} | {situacao} |"
    )


def tabela(ctx: Contexto) -> list[str]:
    """Monta a tabela Markdown da cobertura, conceito a conceito."""
    linhas = [CABECALHO, SEPARADOR]
    for conceito in CONCEITOS_AAF:
        for raiz, caminho in conceito.caminhos:
            linhas.append(linha_de_conceito(conceito, raiz, caminho, ctx))
    return linhas


def pendentes(ctx: Contexto) -> list[str]:
    """Conceitos com contraparte AAF em que nenhum caminho foi provado escrito."""
    linhas: list[str] = []
    for conceito in CONCEITOS_AAF:
        if not conceito.contraparte:
            continue
        situacoes = [
            classifica(caminho, raiz, ctx.do_gerado, ctx.provados)
            for raiz, caminho in conceito.caminhos
        ]
        if ESCRITO not in situacoes:
            linhas.append(f"- {conceito.id}: {conceito.titulo} (AAF: {conceito.aaf})")
    return linhas


def _divergem(nome_template: str, valor_template: str, valor_schema: str) -> bool:
    """Se o valor declarado no template e o declarado no schema sao o mesmo."""
    if nome_template == "MinorVersion":
        return valor_template.rsplit("_", 1)[-1] != valor_schema
    return valor_template != valor_schema


def confere_build(
    schema: SchemaLive,
    raiz: ET.Element,
    minor_esperado: str | None = None,
) -> list[str]:
    """Confere o template contra o schema selecionado.

    O casamento e conferido por dentro: o `TranslatorNumber` do schema tem de
    ser o sufixo do `MinorVersion` do template, e a `Version` do schema tem de
    ser o `MajorVersion` do template. O `SchemaChangeCount` fecha o
    casamento. Metadado interno que falta tambem e recusa, e o nome do
    arquivo, sozinho, nao prova nada: um schema renomeado continua
    descrevendo o build que o conteudo diz.
    """
    comparacoes = (
        ("MinorVersion", raiz.get("MinorVersion"), "TranslatorNumber", schema.translator),
        ("MajorVersion", raiz.get("MajorVersion"), "Version", schema.versao),
        ("SchemaChangeCount", raiz.get("SchemaChangeCount"), "ChangeCount", schema.change_count),
    )
    problemas: list[str] = []
    for nome_template, valor_template, nome_schema, valor_schema in comparacoes:
        if not valor_template:
            problemas.append(f"o template nao declara {nome_template}")
            continue
        if not valor_schema:
            problemas.append(f"o schema nao declara {nome_schema}")
            continue
        if _divergem(nome_template, valor_template, valor_schema):
            problemas.append(
                f"{nome_template} {valor_template!r} do template nao casa com "
                f"{nome_schema} {valor_schema!r} do schema"
            )
    minor = raiz.get("MinorVersion")
    if minor_esperado is not None and minor is not None and minor != minor_esperado:
        problemas.append(
            f"MinorVersion {minor!r} do template != {minor_esperado!r} do arquivo do schema"
        )
    return problemas


def _schema_selecionado(instalado: bool) -> tuple[SchemaLive | None, str, str, str]:
    """Escolhe o schema: o do Live instalado ou o snapshot versionado.

    Devolve (schema, origem, MinorVersion esperado, erro). O erro nao vazio
    significa que a auditoria nao pode seguir.
    """
    if instalado:
        caminho = schema_instalado()
        if caminho is None:
            return (None, "", "", "Live instalado sem Resources/Schema")
        schema = schema_do_build(caminho)
        return (schema, str(caminho), caminho.stem, "")
    if not conferir_snapshot():
        return (None, "", "", "snapshot fora do digest do vendor")
    schema = schema_do_build()
    return (schema, f"snapshot {SCHEMA_BUILD}", SCHEMA_BUILD, "")


def _contexto(schema, documentos, medicoes) -> Contexto:
    """Junta o schema, o que foi gerado, o que foi declarado e o que foi provado."""
    do_gerado = {raiz: coletar(documentos[AMOSTRAS[0].rotulo], raiz) for raiz in RAIZES}
    provados = caminhos_provados(medicoes)
    return Contexto(
        schema=schema,
        do_gerado=do_gerado,
        do_escritor=declarados(),
        provados=provados,
    )


def _imprime_cabecalho(schema, origem, template, raiz_template) -> None:
    """O resumo do que foi medido."""
    print(f"schema: {origem}")
    print(f"build {schema.translator} | schema versao {schema.versao}")
    print(f"change_count {schema.change_count} | classes {len(schema.classes)}")
    print(f"nomes de campo {len(schema.elementos)}")
    print(f"declaracoes classe-campo {schema.declaracoes()}")
    print(f"template: {template}")
    print(f"MinorVersion {raiz_template.get('MinorVersion')}")
    print(f"SchemaChangeCount {raiz_template.get('SchemaChangeCount')}")
    rotulos = ", ".join(amostra.rotulo for amostra in AMOSTRAS)
    print(f"amostras medidas: {rotulos}")


def _imprime_provas(medicoes) -> None:
    """As provas medidas, uma por linha."""
    print()
    print("PROVAS MEDIDAS")
    for medicao in medicoes:
        marca = "ok" if medicao.ok else "FALHOU"
        print(
            f"- {medicao.chave}: {marca} | {medicao.tipo} | medido {medicao.medido} "
            f"| previsto {medicao.previsto} | {medicao.detalhe}"
        )


def _imprime_pendentes(ctx: Contexto) -> None:
    """Os conceitos com contraparte AAF em que nenhum caminho foi provado."""
    print()
    print("CONCEITOS COM CONTRAPARTE AAF E SEM CAMINHO PROVADO")
    faltando = pendentes(ctx)
    if faltando:
        for linha in faltando:
            print(linha)
    else:
        print("- nenhum")


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada da linha de comando."""
    parser = argparse.ArgumentParser(prog="audit_als_schema", description=__doc__)
    parser.add_argument("--instalado", action="store_true", help="usa o schema do Live instalado")
    parser.add_argument("--markdown", metavar="ARQUIVO", help="grava a tabela em Markdown")
    parser.add_argument("--template", default=str(TEMPLATE), help="template .als de referencia")
    args = parser.parse_args(argv)

    schema, origem, minor_esperado, erro = _schema_selecionado(args.instalado)
    if erro:
        print(f"audit_als_schema: {erro}", file=sys.stderr)
        return 2

    template = Path(args.template)
    if not template.is_file():
        print(f"audit_als_schema: template ausente: {template}", file=sys.stderr)
        return 2

    raiz_template = load_als(template)
    problemas = confere_build(schema, raiz_template, minor_esperado)
    if problemas:
        print("audit_als_schema: o template nao casa com o schema:", file=sys.stderr)
        for problema in problemas:
            print(f"  - {problema}", file=sys.stderr)
        return 3

    with tempfile.TemporaryDirectory() as pasta:
        documentos = gerar_documentos(Path(pasta), template)

    medicoes = medir(documentos, raiz_template)
    ctx = _contexto(schema, documentos, medicoes)
    linhas = tabela(ctx)

    _imprime_cabecalho(schema, origem, template, raiz_template)
    print()
    for linha in linhas:
        print(linha)
    _imprime_provas(medicoes)
    _imprime_pendentes(ctx)

    if args.markdown:
        destino = Path(args.markdown)
        destino.write_text("\n".join(linhas) + "\n", encoding="utf-8", newline="\n")
        print(f"tabela gravada em {destino}")

    if any(not medicao.ok for medicao in medicoes):
        print("audit_als_schema: alguma prova medida nao fechou", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
