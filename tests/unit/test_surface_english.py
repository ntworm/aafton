"""A superficie do aafton em ingles: nenhum texto para a pessoa em portugues.

Todo literal de texto de `src/aafton` que nao e docstring e tem mais de uma
palavra e lido da arvore de sintaxe e conferido contra uma lista de palavras
do portugues que nao sao palavras do ingles. O literal que um programa le (uma
chave, um formato, um valor comparado com o conteudo de um arquivo) entra na
lista de excecoes, com o motivo. A pagina de timecode e conferida pelo texto.
"""

from __future__ import annotations

import ast
import html
import re
from importlib import resources
from pathlib import Path

import aafton

# Palavras do portugues que nao sao palavras do ingles. Ficam de fora as que
# existem nas duas linguas (a, as, e, no, do, com, em) e os termos tecnicos.
PORTUGUESE = (
    "nao não sao são que uma dos das foi foram pelo pela pelos pelas para sem ao aos de da "
    "esta está estao estão ser tem deve devem pode podem nenhum nenhuma arquivo arquivos "
    "midia mídia sessao sessão faixa faixas duracao duração invalido inválido invalida inválida "
    "ja já cada entre mais menos apenas quando porque mas ou seu sua seus suas esse essa este "
    "isso depois antes ainda tambem também onde como muito pouco todo toda todos todas outro "
    "outra novo nova mesmo mesma erro erros aviso avisos caminho pasta leitura escrita "
    "conversao conversão relatorio relatório encontrado encontrada suportado suportada suporte "
    "automacao automação ganho taxa quadro quadros trecho lacuna sobreposicao sobreposição "
    "resultado verificacao verificação cobertura tolerancia tolerância desvio mapeamento "
    "conectado tocando parado carregar carregado servidor encerrado aguardando posicao posição "
    "aplicar repor recusou pedido resposta maior inicio início fim operacao operação cancelada "
    "usuario usuário extensao extensão reconhecida interrompida estrito permissivo rodando "
    "pressione encerrar severidade codigo código mensagem notas faltando presumida aprovado "
    "reprovado gravado gravada lido lida ignorado ignorada pulado convertido renderizado "
    "possivel possível necessario necessário precisa valor valores numero número "
    "exato exata exatos exatas ausente ausentes esperado esperados comparado comparados "
    "mapeado mapeados atraves através conteudo conteúdo origem destino canal canais "
    "silencio silêncio amostra amostras dentro fora"
).split()
WORD = re.compile(r"\b(" + "|".join(re.escape(word) for word in PORTUGUESE) + r")\b", re.IGNORECASE)

# Pares de palavras que so o portugues escreve, para o texto que nao tem palavra da lista.
PHRASES = (" no als", " no aaf", " do aaf", " do als", " do live", " no live", " na track")

# Literais de uma palavra so, em portugues, que existiam no codigo antes da
# traducao. Um literal igual a uma delas e portugues, a menos que esteja nas
# excecoes.
SINGLE = frozenset(
    "contexto desconhecido diferenca documentos ruido selecionar alvo aprovado atributo caminho "
    "candidato carregar casos causa causas comparar contagem dependencia desconhecida "
    "dispositivos elemento embarcado encontrados entrada esperados falha grupo igual indefinido "
    "marcadores origem padrao pasta preparo presumida propriedade quantizacao referencia "
    "relatorio reprovado saida soma taxas tipo aplicar repor ir resultado cobertura tolerancia "
    "desvio verificacao mapeamento erros avisos notas severidade codigo mensagem sim nao "
    "aguardando conectado parado tocando".split()
)

# Literais que um programa le e que por isso ficam como estao, com o motivo.
# A chave e "modulo.py: texto exato".
_VALOR = "valor comparado dentro do codigo, nunca impresso nem gravado"
_NOME = "nome de classe ou de funcao do modulo, nao e texto"
_CATALOGO = "identificador de uma linha do catalogo do schema, lido pelos scripts de auditoria"
_PRIVADO = "chave ou pasta dos arquivos das ferramentas privadas de compatibilidade"
EXCEPTIONS: dict[str, str] = {
    "aaf_read_rates.py: igual": _VALOR,
    "aaf_read_rates.py: padrao": _VALOR,
    "als_schema.py: atributo": _VALOR,
    "als_schema.py: elemento": _VALOR,
    "als_schema.py: marcadores": _CATALOGO,
    "als_schema.py: quantizacao": _CATALOGO,
    "als_schema.py: dispositivos": _CATALOGO,
    "als_compare.py: Diferenca": _NOME,
    "als_compare.py: Ruido": _NOME,
    "als_compare.py: carregar": _NOME,
    "als_compare.py: comparar": _NOME,
    "als_compare.py: contagem": _NOME,
    "formats/fcpxml_audio.py: Contexto": _NOME,
    "live_compat.py: embarcado": _PRIVADO,
    "live_compat.py: origem": _PRIVADO,
    "live_compat_media.py: preparo": _PRIVADO,
    "live_compat_media.py: caminho": _PRIVADO,
    "live_compat_proveniencia.py: entrada": _PRIVADO,
    "live_compat_proveniencia.py: relatorio": _PRIVADO,
    "live_compat_proveniencia.py: pasta": _PRIVADO,
    "live_compat_proveniencia.py: saida": _PRIVADO,
    "live_compat_proveniencia.py: casos": _PRIVADO,
    "live_compat_proveniencia.py: caminho": _PRIVADO,
    "live_compat_proveniencia.py: the report read (key relatorio) is not the validated path": (
        "a mensagem cita a chave do manifesto privado, que o operador le no arquivo"
    ),
}

SRC = Path(aafton.__file__).resolve().parent


def _docstrings(tree: ast.AST) -> set[int]:
    """Os nos de texto que sao docstring de modulo, classe ou funcao."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def _texts(tree: ast.AST) -> list[str]:
    """O texto de cada literal que nao e docstring; o f-string inteiro, com {} nos campos."""
    skip = _docstrings(tree)
    inside: set[int] = set()
    texts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                inside.add(id(value))
                parts.append(value.value if isinstance(value, ast.Constant) else "{}")
            texts.append("".join(str(part) for part in parts))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip and id(node) not in inside:
                texts.append(node.value)
    return texts


def _portuguese(text: str) -> list[str]:
    """As palavras em portugues que o texto tem.

    Um texto de uma palavra so e conferido contra a lista das palavras soltas;
    um texto com mais de uma, contra a lista das palavras do portugues.
    """
    text = re.sub(r"\{[^{}]*\}", "{}", text)
    stripped = text.strip()
    if " " not in stripped:
        return [stripped.lower()] if stripped.lower() in SINGLE else []
    words = {match.group(1).lower() for match in WORD.finditer(text)}
    words.update(phrase.strip() for phrase in PHRASES if phrase in f" {text.lower()}")
    return sorted(words)


def test_no_text_of_the_modules_is_in_portuguese():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for text in _texts(tree):
            words = _portuguese(text)
            if words and f"{name}: {text}" not in EXCEPTIONS:
                found.append(f"{name}: {text!r} ({', '.join(words)})")
    assert not found, f"{len(found)} textos em portugues:\n" + "\n".join(found[:40])


def test_every_exception_names_a_text_that_exists():
    present = set()
    for path in sorted(SRC.rglob("*.py")):
        name = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        present.update(f"{name}: {text}" for text in _texts(tree))
    assert sorted(set(EXCEPTIONS) - present) == []
    assert all(reason.strip() for reason in EXCEPTIONS.values())


def test_the_timecode_page_is_in_english():
    page = (resources.files("aafton.studio") / "static" / "timecode.html").read_text(
        encoding="utf-8"
    )
    assert '<html lang="en">' in page
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", page, flags=re.DOTALL)
    markup = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.DOTALL)
    chunks = [html.unescape(chunk).strip() for chunk in re.findall(r">([^<>]+)<", markup)]
    chunks += [
        html.unescape(text)
        for text in re.findall(r'(?:title|placeholder|aria-label)="([^"]*)"', markup)
    ]
    literals = []
    for script in scripts:
        literals += re.findall(r'"((?:[^"\\\n]|\\.)*)"', script)
        literals += re.findall(r"'((?:[^'\\\n]|\\.)*)'", script)
        literals += re.findall(r"`((?:[^`\\]|\\.)*)`", script)
    found = []
    for chunk in chunks:
        found += [f"markup {chunk!r}: {word}" for word in _portuguese(chunk)]
    for literal in literals:
        found += [f"script {literal!r}: {word}" for word in _portuguese(literal)]
    assert found == []
