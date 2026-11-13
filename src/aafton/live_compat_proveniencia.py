"""Proveniencia do pacote do oraculo diferencial do Live (R07.6, E3).

O manifesto amarra cada evidencia local (a entrada do operador, o .als gerado,
o sidecar, o AAF de origem e o relatorio) ao hash do seu conteudo. O hash liga
a evidencia aos insumos: mostra que os arquivos que a medicao leu sao os que o
manifesto gravou. Ele nao autentica o Save As de um humano nem certifica que o
Live rodou: so diz que os bytes nao mudaram.

Um manifesto sem hash (o formato anterior a E3) e recusado: a evidencia nao
pode ser reconstituida, e a saida e refazer o pacote em um diretorio novo, sem
apagar o que o operador devolveu.

O hash confere integridade, nao confia no manifesto: por isso os caminhos
tambem sao conferidos, e nenhum caminho do manifesto pode sair do pacote.

O modulo tem uma unica escrita: a troca do sidecar da saida do operador. Ela
e atomica, para que um destino que seja link de arquivo para a evidencia do
manifesto nunca seja truncado. Todo o resto e leitura.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from .sidecar import sidecar_path_for

CAMPOS = ("entrada", "als_gerado", "sidecar", "aaf", "relatorio")
CHAVE_HASHES = "hashes"
CHAVE_CAMINHOS = "caminhos_proveniente"
CHAVE_MIDIAS = "midias_proveniente"
SAIDA_ESPERADA = "saida_do_live.als"
PASTA_DO_LIVE = "saida_do_live Project"
ALIASES = (("entrada", "als_de_entrada"), ("als_gerado", "als_gerado"), ("aaf", "aaf"))
MANIFESTO_SEM_HASH = (
    "case {id}: the manifest has no provenance hash (format before E3). "
    "The evidence cannot be checked: generate the package again in a new directory "
    "and measure from there, without deleting what the operator returned."
)


class ProvenienciaInvalida(RuntimeError):
    """A evidencia local nao bate com o manifesto: nada e medido."""


def sha256_arquivo(caminho: Path) -> str:
    """Hash sha256 do conteudo do arquivo, em hexadecimal."""
    digest = hashlib.sha256()
    with Path(caminho).open("rb") as handle:
        for bloco in iter(lambda: handle.read(1 << 16), b""):
            digest.update(bloco)
    return digest.hexdigest()


def caminhos_do_caso(
    pacote: Path, *, entrada: Path, gerado: Path, aaf: Path, relatorio: Path
) -> dict[str, str]:
    """Caminhos, relativos ao pacote, dos cinco arquivos de proveniencia."""
    brutos = {
        "entrada": entrada,
        "als_gerado": gerado,
        "sidecar": sidecar_path_for(gerado),
        "aaf": aaf,
        "relatorio": relatorio,
    }
    return {
        campo: Path(caminho).relative_to(pacote).as_posix() for campo, caminho in brutos.items()
    }


def hashes_do_caso(pacote: Path, caminhos: dict[str, str]) -> dict[str, str]:
    """Hash do conteudo de cada arquivo de proveniencia do caso."""
    return {campo: sha256_arquivo(pacote / caminhos[campo]) for campo in CAMPOS}


def substituir_sidecar_atomico(origem: Path, destino: Path) -> None:
    """Grava o sidecar gerado no destino sem abrir o arquivo que estava la.

    A copia vai para um temporario unico na mesma pasta do destino e so depois
    troca a entrada do destino. Trocar o nome, e nao abrir o arquivo, e o que
    impede a destruicao de um destino que seja link de arquivo para o AAF de
    origem ou para a volta do operador: o conteudo do alvo ligado nao e
    truncado, e o destino passa a ser um arquivo novo.

    A pasta do destino e a do operador, que ja existe. Se a copia ou a troca
    falhar, o temporario e apagado e o erro sobe: nada e medido como sucesso.
    """
    descritor, nome_temporario = tempfile.mkstemp(
        dir=destino.parent, prefix=destino.name + ".", suffix=".tmp"
    )
    os.close(descritor)
    temporario = Path(nome_temporario)
    try:
        shutil.copy(origem, temporario)
        os.replace(temporario, destino)
    finally:
        temporario.unlink(missing_ok=True)


def validar_pacote(pacote: Path, manifesto: dict) -> list[dict[str, Path]]:
    """Confere a proveniencia de todos os casos do manifesto.

    Levanta ``ProvenienciaInvalida`` na primeira evidencia que nao bate: um
    arquivo que sumiu, um arquivo que mudou, uma entrada que nao e mais o .als
    gerado, um caminho que sai do pacote, um manifesto sem hash, um inventario
    de midias que nao e lista ou uma referencia de midia que o .als gerado nao
    sabe ler. Nao escreve nada.

    Devolve, na ordem do manifesto, os caminhos ja resolvidos e conferidos de
    cada caso: e deles que a medicao consome. Nenhum caminho e recalculado pelo
    texto cru do manifesto.
    """
    return [_validar_caso(pacote, item) for item in manifesto.get("casos", [])]


def _validar_caso(pacote: Path, item: dict) -> dict[str, Path]:
    """Confere os hashes do caso e os caminhos que a medicao vai consumir.

    Devolve os caminhos conferidos: os cinco de proveniencia e a saida
    esperada do operador, ja resolvidos dentro do pacote.
    """
    id_caso = str(item.get("id", "?"))
    hashes = item.get(CHAVE_HASHES)
    caminhos = item.get(CHAVE_CAMINHOS)
    if not isinstance(hashes, dict) or not isinstance(caminhos, dict):
        raise ProvenienciaInvalida(MANIFESTO_SEM_HASH.format(id=id_caso))
    resolvidos: dict[str, Path] = {}
    for campo in CAMPOS:
        relativo = caminhos.get(campo)
        esperado = hashes.get(campo)
        if not relativo or not esperado:
            raise ProvenienciaInvalida(MANIFESTO_SEM_HASH.format(id=id_caso))
        caminho = _resolver_no_pacote(pacote, id_caso, campo, relativo)
        if not caminho.is_file():
            raise ProvenienciaInvalida(f"case {id_caso}: missing {campo} ({relativo})")
        if sha256_arquivo(caminho) != esperado:
            raise ProvenienciaInvalida(f"case {id_caso}: {campo} ({relativo}) changed")
        resolvidos[campo] = caminho
    if hashes["entrada"] != hashes["als_gerado"]:
        raise ProvenienciaInvalida(f"case {id_caso}: the operator input is not the generated .als")
    _conferir_consumo(pacote, id_caso, item, resolvidos)
    _conferir_midias(pacote, id_caso, item, resolvidos)
    saida = _conferir_saida(pacote, id_caso, item, resolvidos)
    return resolvidos | {"saida": saida}


def _conferir_consumo(pacote: Path, id_caso: str, item: dict, resolvidos: dict[str, Path]) -> None:
    """O caminho que a medicao consome tem de ser o que o manifesto hashou."""
    for campo, alias in ALIASES:
        if _resolver_no_pacote(pacote, id_caso, alias, item.get(alias)) != resolvidos[campo]:
            raise _recusa(id_caso, f"{alias} is not the validated path")
    if resolvidos["als_gerado"].parent / "relatorio.md" != resolvidos["relatorio"]:
        raise _recusa(id_caso, "the report read (key relatorio) is not the validated path")
    if sidecar_path_for(resolvidos["als_gerado"]) != resolvidos["sidecar"]:
        raise _recusa(id_caso, "the sidecar read is not the validated path")


def _conferir_saida(pacote: Path, id_caso: str, item: dict, resolvidos: dict[str, Path]) -> Path:
    """A saida do operador: o arquivo de volta, plano ou na pasta do Live.

    O Live salva por padrao em uma pasta nova, ``saida_do_live Project``, com o
    ``saida_do_live.als`` dentro. As duas formas sao aceitas; as duas juntas nao
    sao: com as duas presentes nao ha prova de qual e a volta, e o caso fica
    recusado ate o operador escolher uma.

    A volta escolhida e conferida contra a entrada depois da escolha: um
    caminho canonico que seja a propria entrada do operador nao e devolucao,
    e nao importa se o link estava no caminho plano ou no aninhado.

    Devolve o caminho conferido da saida, para a medicao consumir.
    """
    saida = _resolver_no_pacote(pacote, id_caso, "saida_esperada", item.get("saida_esperada"))
    if saida.name != SAIDA_ESPERADA or saida.parent != resolvidos["entrada"].parent:
        raise _recusa(id_caso, f"the output has to be {SAIDA_ESPERADA} in the case folder")
    if saida == resolvidos["entrada"]:
        raise _recusa(id_caso, "the output cannot be the operator input")
    pasta = _resolver_no_pacote(pacote, id_caso, "pasta", item.get("pasta"))
    if pasta != resolvidos["entrada"].parent:
        raise _recusa(id_caso, "the case folder is not the input folder")
    _conferir_dentro(pacote, id_caso, pasta, "the case folder")
    escolhida = _escolher_a_saida(pacote, id_caso, pasta, saida)
    if escolhida == resolvidos["entrada"]:
        raise _recusa(id_caso, "the output cannot be the operator input")
    return escolhida


def _escolher_a_saida(pacote: Path, id_caso: str, pasta: Path, saida: Path) -> Path:
    """A volta do operador: a aninhada na pasta do Live, ou a plana."""
    aninhada = _saida_aninhada(pasta)
    if aninhada.is_file() and saida.is_file():
        plana_rel = saida.relative_to(pacote.resolve()).as_posix()
        aninhada_rel = aninhada.relative_to(pacote.resolve()).as_posix()
        raise _recusa(
            id_caso,
            f"the output is in two places ({plana_rel} and {aninhada_rel}): "
            "the operator keeps one and renames the other (e.g. saida_do_live-discarded.als)",
        )
    escolhida = aninhada.resolve() if aninhada.is_file() else saida
    _conferir_dentro(pacote, id_caso, escolhida.parent, "the output folder")
    if pasta.resolve() not in escolhida.resolve().parents:
        raise _recusa(id_caso, "the output leaves the case folder")
    _conferir_sidecar(pacote, id_caso, pasta, escolhida)
    return escolhida


def _conferir_sidecar(pacote: Path, id_caso: str, pasta: Path, saida: Path) -> None:
    """O sidecar da saida mora dentro do pacote e dentro da pasta do caso."""
    sidecar = sidecar_path_for(saida).resolve()
    if pacote.resolve() not in sidecar.parents:
        raise _recusa(id_caso, "the sidecar of the output leaves the package")
    if pasta.resolve() not in sidecar.parents:
        raise _recusa(id_caso, "the sidecar of the output leaves the case folder")


def _saida_aninhada(pasta: Path) -> Path:
    """A volta que o Live grava por padrao: a pasta do projeto com o arquivo."""
    return pasta / PASTA_DO_LIVE / SAIDA_ESPERADA


def _conferir_dentro(pacote: Path, id_caso: str, caminho: Path, descricao: str) -> None:
    """O caminho tem de morar fisicamente dentro do pacote.

    O texto do manifesto ja confere o caminho; aqui confere o destino real, que
    e o que um link ou uma junction apontariam para fora do pacote.
    """
    if pacote.resolve() not in caminho.resolve().parents:
        raise _recusa(id_caso, f"{descricao} sai do pacote")


def _conferir_midias(pacote: Path, id_caso: str, item: dict, resolvidos: dict[str, Path]) -> None:
    """O inventario de midias, quando o manifesto traz, tem de bater com o .als.

    O manifesto sem o campo continua valido: e o formato anterior, com os cinco
    hashes. O campo que existe com valor nulo nao e a ausencia do campo: a
    lista tem de ser uma lista, ou o caso e recusado. Com a lista, cada midia
    listada tem de existir dentro do pacote, com o hash que o manifesto
    gravou, e a lista tem de cobrir as referencias de audio do .als gerado.
    """
    if CHAVE_MIDIAS not in item:
        return
    inventario = item[CHAVE_MIDIAS]
    if not isinstance(inventario, list):
        raise _recusa(id_caso, "the media inventory is not a list")
    conferidas = []
    for entrada in inventario:
        if not isinstance(entrada, dict):
            raise _recusa(id_caso, "the media inventory has an invalid entry")
        relativo = entrada.get("caminho")
        esperado = entrada.get("sha256")
        caminho = _resolver_no_pacote(pacote, id_caso, "midia", relativo)
        if not caminho.is_file():
            raise _recusa(id_caso, f"missing the media ({relativo})")
        if sha256_arquivo(caminho) != esperado:
            raise _recusa(id_caso, f"the media ({relativo}) changed")
        conferidas.append(caminho)
    geradas = _midias_do_als_gerado(id_caso, resolvidos["als_gerado"])
    if geradas != {str(caminho).casefold() for caminho in conferidas}:
        raise _recusa(id_caso, "the media inventory does not cover the generated .als")


def _midias_do_als_gerado(id_caso: str, als: Path) -> set[str]:
    """As referencias de audio do .als gerado, como o inventario as lista."""
    from .live_compat_media import MidiaLocalInvalida, midias_efetivas

    try:
        return {str(caminho).casefold() for caminho in midias_efetivas(als)}
    except MidiaLocalInvalida as erro:
        raise _recusa(id_caso, f"invalid media reference in the generated .als ({erro})") from erro
    except (OSError, ValueError) as erro:
        raise _recusa(id_caso, f"the generated .als cannot be read ({erro})") from erro


def _resolver_no_pacote(pacote: Path, id_caso: str, descricao: str, relativo: object) -> Path:
    """Resolve um caminho do manifesto e recusa o que nao mora dentro do pacote.

    O texto e lido como caminho do Windows mesmo em host POSIX: assim uma
    unidade, uma barra inicial ou um ".." nao passam como nome de arquivo.
    O texto tambem e usado como veio: espaco no comeco ou no fim e recusado,
    porque a medicao consome o texto cru. Espaco no meio do nome e valido.
    """
    if not isinstance(relativo, str) or not relativo.strip():
        raise _recusa(id_caso, f"{descricao} has no path in the manifest")
    if relativo != relativo.strip():
        raise _recusa(id_caso, f"{descricao} ({relativo!r}) has a leading or trailing space")
    texto = relativo
    win = PureWindowsPath(texto)
    if PurePosixPath(texto).is_absolute() or win.drive or win.root:
        raise _recusa(id_caso, f"{descricao} ({texto}) is not a relative path of the package")
    if ".." in win.parts:
        raise _recusa(id_caso, f"{descricao} ({texto}) leaves the package")
    raiz = pacote.resolve()
    destino = (raiz / texto).resolve()
    if destino == raiz or raiz not in destino.parents:
        raise _recusa(id_caso, f"{descricao} ({texto}) leaves the package")
    return destino


def _recusa(id_caso: str, texto: str) -> ProvenienciaInvalida:
    """Erro de proveniencia com o caso nomeado."""
    return ProvenienciaInvalida(f"case {id_caso}: {texto}")
