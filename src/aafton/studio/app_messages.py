"""As mensagens de erro de arquivo da pagina do app.

Um arquivo movido, apagado ou sem permissao vira uma frase em ingles simples,
sem o texto da excecao do sistema. So a conversao guarda esse texto, no
relatorio dela; a leitura, o Open e o Show in folder o descartam.
"""

from __future__ import annotations

from pathlib import Path

from ..report import ConversionIssue, Report

# O que fazer depois, por acao, quando o arquivo nao esta mais la.
PROXIMO_PASSO = {
    "read": "Choose the file again.",
    "open": "Convert again to write it.",
    "show": "Convert again to write it.",
}

# O nome de cada acao na frase de uma falha que nao e de arquivo sumido.
ROTULOS = {"read": "Reading", "open": "Opening", "show": "Showing", "write": "Writing"}

# A frase do destino que nao pode ser escrito.
OUTRO_DESTINO = "Choose another destination."

# A frase de uma falha de arquivo que nao diz qual arquivo.
SEM_CAMINHO = "A file could not be reached. It may have been moved, renamed or deleted."

# O codigo da nota do relatorio que guarda o texto do sistema.
CODIGO_DO_RELATORIO = "FILE_ERROR"


def mensagem_de_arquivo(exc: BaseException, caminho, acao: str) -> str:
    """A frase de uma falha de arquivo, sem o texto da excecao.

    ``acao`` e read, open, show ou write: diz o verbo da permissao e o que
    fazer depois quando o arquivo ou a pasta nao estao mais la.
    """
    if not caminho:
        if isinstance(exc, PermissionError):
            verbo = "write" if acao == "write" else "read"
            return (
                f"aafton is not allowed to {verbo} a file it needs. Check the folder's "
                "permissions or choose another place."
            )
        return SEM_CAMINHO
    alvo = Path(caminho)
    if isinstance(exc, PermissionError):
        return _sem_permissao(alvo, acao)
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        if acao == "write":
            return (
                f"{alvo.name} cannot be written in {alvo.parent}: the folder cannot be "
                f"created there. {OUTRO_DESTINO}"
            )
        passo = PROXIMO_PASSO.get(acao, PROXIMO_PASSO["read"])
        return (
            f"{alvo.name} is no longer at {alvo.parent}. It may have been moved, "
            f"renamed or deleted. {passo}"
        )
    return f"{ROTULOS.get(acao, ROTULOS['read'])} failed for {alvo}."


def _sem_permissao(alvo: Path, acao: str) -> str:
    """A frase de um arquivo que o sistema nao deixa ler ou escrever."""
    if acao == "write":
        return (
            f"aafton is not allowed to write {alvo}. Check the folder's permissions. "
            f"{OUTRO_DESTINO}"
        )
    return (
        f"aafton is not allowed to read {alvo}. Check the folder's permissions or "
        "choose another place."
    )


def e_de_arquivo(exc: BaseException) -> bool:
    """True quando a excecao veio do sistema de arquivos, com o codigo do sistema.

    Um OSError sem codigo e uma mensagem do motor e continua com ela.
    """
    if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError)):
        return True
    return isinstance(exc, OSError) and exc.errno is not None


def falha_do_motor(exc: BaseException, caminho, acao: str) -> str:
    """A mensagem de uma excecao: a de arquivo para o sistema, a do motor para as outras."""
    if not e_de_arquivo(exc):
        return str(exc)
    return mensagem_de_arquivo(exc, getattr(exc, "filename", None) or caminho, acao)


def falha_da_conversao(
    exc: BaseException, destino: Path, report: Report, origem: Path | None = None
) -> str:
    """A mensagem de uma conversao que falhou; o texto do sistema vai so ao relatorio.

    A falha na fonte e de leitura; a falha num caminho do destino e de
    escrita; qualquer outra e de leitura da midia.
    """
    if not e_de_arquivo(exc):
        return str(exc)
    nome = getattr(exc, "filename", None)
    # A nota entra direto na lista: o relatorio estrito levantaria de novo.
    contexto = {"path": str(nome) if nome else None}
    report.issues.append(ConversionIssue("error", CODIGO_DO_RELATORIO, str(exc), contexto))
    alvo = Path(str(nome)).resolve() if nome else destino
    escrita = _do_destino(alvo, destino, exc) and not _da_fonte(alvo, origem)
    return mensagem_de_arquivo(exc, alvo, "write" if escrita else "read")


def _do_destino(alvo: Path, destino: Path, exc: BaseException | None = None) -> bool:
    """True quando o caminho e o destino, uma pasta acima dele ou algo ao lado dele.

    Um arquivo que falta numa pasta que existe ao lado do destino e uma fonte
    que sumiu (escrever numa pasta que existe nao da FileNotFoundError): por
    exemplo um WAV da sessao quando o set e salvo na pasta do AAF.
    """
    if alvo == destino or alvo in destino.parents:
        return True
    if destino.parent not in alvo.parents:
        return False
    return not (isinstance(exc, FileNotFoundError) and alvo.parent.is_dir())


def _da_fonte(alvo: Path, origem: Path | None) -> bool:
    """True quando o caminho e a fonte ou fica dentro dela (a pasta do gravador)."""
    if origem is None:
        return False
    fonte = origem.resolve()
    return alvo == fonte or fonte in alvo.parents


def pasta_impossivel(destino: Path) -> str | None:
    """A mensagem do destino cuja pasta nao pode ser criada; None quando pode.

    O escritor recria a pasta que sumiu; o que ele nao cria e uma pasta cujo
    caminho passa por um arquivo.
    """
    for pasta in destino.parents:
        if pasta.exists():
            if pasta.is_dir():
                return None
            return mensagem_de_arquivo(NotADirectoryError(), destino, "write")
    return None
