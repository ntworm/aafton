"""Oraculo de refatoracao: compara as saidas de duas arvores de codigo."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT_ROOT = REPO / "out" / "refactor-oracle"
TEMPLATE = REPO / "templates" / "live12.als"
FIXTURES = REPO / "tests" / "fixtures" / "aaf"
# Com esta variavel, os AAF do corpus preservado (com midia) entram como casos.
CORPUS_ENV = "AAFTON_REEXPORT_DIR"
# Com esta variavel, o corpus preservado fica de fora dos casos do oraculo.
SEM_CORPUS_ENV = "AAFTON_SEM_CORPUS"
# A pasta que roda o ciclo estrito, sem --permissive, em cada caso.
NOME_ESTRITO = "pair-absorb-strict"

STEREOS = ("pair", "split")
FADES = ("absorb", "keep")
VARIANTES = tuple((s, f, "none") for s in STEREOS for f in FADES) + (("pair", "absorb", "prefix"),)
LIMITE_PROFUNDIDADE = 200
MARCA_PROFUNDIDADE = "<PROFUNDIDADE MAXIMA>"
PADRAO_MOBID = re.compile(r"urn:smpte:umid:[0-9a-fA-F.-]+")
PADRAO_FPS_LOCATOR = re.compile(r'(<Name Value="TC [^"]* @ \d+) @ [\d.]+(" />)')

# Regras de normalizacao aplicadas na comparacao:
NORMALIZACOES: list[dict[str, str]] = [
    {"campo": "MobID e SourceID", "regra": "cada valor distinto vira MOB-<n>"},
    {"campo": "CreationTime, LastModified e Date", "regra": "valor substituido por <DATA>"},
    {"campo": "GenerationAUID", "regra": "valor substituido por <AUID>"},
    {"campo": "cabecalho HTTP Date", "regra": "cabecalho omitido"},
    {"campo": "MobID urn:smpte:umid nos JSON e no commands.json", "regra": "vira MOB-<n>"},
    {"campo": "binarios session.als e back.aaf", "regra": "sha256 do par em texto"},
    {"campo": "stderr do comando no commands.json", "regra": "caminho do caso vira <CAMINHO>"},
    {"campo": "nome do locator TC no session.als.xml", "regra": "sufixo @ <fps> removido"},
]


def normalize_dump_lines(lines: list[str]) -> list[str]:
    """Normaliza as linhas de um dump de AAF (MobID, datas e GenerationAUID)."""
    mapa_mob: dict[str, str] = {}
    saida: list[str] = []
    for linha in lines:
        caminho, sep, valor = linha.partition("=")
        if not sep or valor == MARCA_PROFUNDIDADE:
            saida.append(linha)
            continue
        nome = caminho.rsplit(".", 1)[-1]
        if nome in ("MobID", "SourceID"):
            if valor not in mapa_mob:
                mapa_mob[valor] = f"MOB-{len(mapa_mob) + 1}"
            saida.append(f"{caminho}={mapa_mob[valor]}")
        elif nome in ("CreationTime", "LastModified", "Date"):
            saida.append(f"{caminho}=<DATA>")
        elif nome == "GenerationAUID":
            saida.append(f"{caminho}=<AUID>")
        else:
            saida.append(linha)
    return saida


def normalize_mobids(texto: str) -> str:
    """Substitui cada mob id distinto por um token MOB-<n> (regra do MobID)."""
    mapa: dict[str, str] = {}

    def troca(matches: re.Match[str]) -> str:
        """Devolve o token estavel de um mob id encontrado."""
        return mapa.setdefault(matches.group(0), f"MOB-{len(mapa) + 1}")

    return PADRAO_MOBID.sub(troca, texto)


def normalize_locator_tc(xml: str) -> str:
    """Tira o fps do nome do locator TC, que so a regua da extensao usa."""
    return PADRAO_FPS_LOCATOR.sub(r"\1\2", xml)


def aaf_dump(path: Path) -> str:
    """Gera um dump textual e normalizado do conteudo de um arquivo AAF."""
    import aaf2

    coletor = _Coletor()
    arquivo = aaf2.open(str(path), "r")
    try:
        coletor.dump(arquivo.header, "header")
    finally:
        fechar = getattr(arquivo, "close", None)
        if callable(fechar):
            fechar()
    return "\n".join(normalize_dump_lines(coletor.linhas)) + "\n"


class _Coletor:
    """Acumula as linhas textuais de um dump de AAF."""

    def __init__(self) -> None:
        """Cria um coletor sem linhas e sem objetos visitados."""
        self.linhas: list[str] = []
        self.vistos: set[int] = set()

    def dump(self, obj: Any, caminho: str, nivel: int = 0) -> None:
        """Acumula as propriedades de um objeto AAF."""
        if id(obj) in self.vistos:
            return
        self.vistos.add(id(obj))
        try:
            propriedades = list(obj.properties())
        except Exception:  # noqa: BLE001 - objeto sem propriedades utilizaveis
            propriedades = []
        for propriedade in propriedades:
            nome, valor = _nome_e_valor(propriedade)
            self.valor(valor, f"{caminho}.{nome}", nivel + 1)
        self.vistos.discard(id(obj))

    def valor(self, valor: Any, caminho: str, nivel: int) -> None:
        """Acumula um valor de propriedade, recursando em objetos e sequencias."""
        if nivel > LIMITE_PROFUNDIDADE:
            self.linhas.append(f"{caminho}={MARCA_PROFUNDIDADE}")
            return
        if isinstance(valor, (set, frozenset, list, tuple)):
            if isinstance(valor, (set, frozenset)):
                valor = sorted(valor, key=self.assinatura)
            for indice, item in enumerate(valor):
                self.valor(item, f"{caminho}[{indice}]", nivel + 1)
            return
        if isinstance(valor, dict):
            for chave, item in valor.items():
                self.valor(item, f"{caminho}.{chave}", nivel + 1)
            return
        if hasattr(valor, "properties") and callable(valor.properties):
            self.dump(valor, caminho, nivel + 1)
            return
        self.linhas.append(f"{caminho}={valor!r}")

    def assinatura(self, valor: Any) -> str:
        """Gera a assinatura textual de um valor, para ordenar conjuntos."""
        outro = _Coletor()
        outro.valor(valor, "x", 0)
        return "\n".join(outro.linhas)


def _nome_e_valor(propriedade: Any) -> tuple[str, Any]:
    """Extrai o nome e o valor de uma propriedade devolvida pelo aaf2."""
    nome = getattr(propriedade, "name", None)
    if nome is None:
        return str(propriedade), None
    return str(nome), getattr(propriedade, "value", None)


def cmd_worker(args: list[str]) -> int:
    """Executa o trabalho de um instantaneo dentro do processo filho."""
    opts, _ = parse_opts(args)
    src = Path(opts.get("--src", "")).resolve()
    work = Path(opts.get("--work", "")).resolve()
    template = Path(opts.get("--template", "")).resolve()
    nome = "aafton" if Path(src, "src", "aafton").is_dir() else "ableton_aaf"
    pacote = importlib.import_module(nome)
    esperado = os.path.normcase(str(Path(src, "src").resolve()))
    obtido = os.path.normcase(str(pacote.__file__))
    if not obtido.startswith(esperado):
        print(obtido)
        return 3
    cli_main = importlib.import_module(f"{nome}.cli").main

    env = {**os.environ, "PYTHONPATH": str(Path(src, "src"))}
    worker_casos(work, cli_main, template)
    studio = Path(__file__).with_name("refactor_oracle_studio.py")
    subprocess.run([sys.executable, str(studio), str(work)], env=env, check=True)
    return 0


def entradas() -> list[tuple[str, Path]]:
    """Fixtures AAF versionadas e, com CORPUS_ENV, os AAF do corpus preservado."""
    lista = [(f.stem, f) for f in sorted(FIXTURES.glob("*.aaf"))]
    corpus = os.environ.get(CORPUS_ENV, "").strip()
    if corpus and not os.environ.get(SEM_CORPUS_ENV) and Path(corpus).is_dir():
        lista += [(f"corpus-{f.stem}", f) for f in sorted(Path(corpus).glob("*.aaf"))]
    return lista


def _entrada_do_caso(caso: Path, origem: Path) -> str:
    """Copia a fixture para o caso; o AAF do corpus fica no lugar, junto da midia."""
    if origem.parent != FIXTURES:
        return str(origem)
    shutil.copy2(origem, caso / "input.aaf")
    extra = origem.with_suffix(".txt")
    if extra.exists():
        shutil.copy2(extra, caso / extra.name)
    return str(caso / "input.aaf")


def worker_casos(work: Path, cli_main: Any, template: Path) -> None:
    """Roda o inspect e o ciclo de conversao de cada AAF de entrada."""
    for nome, origem in entradas():
        caso = work / nome
        caso.mkdir(parents=True, exist_ok=True)
        entrada = _entrada_do_caso(caso, origem)
        comandos = [run_command(cli_main, ["inspect", entrada], caso)]
        _escreve_json(caso, "inspect-aaf.json", comandos[0]["stdout"])
        _grava_comandos(caso, comandos)
        for stereo, fades, groups in VARIANTES:
            sub = caso / "-".join(v for v in (stereo, fades, groups) if v != "none")
            _ciclo(cli_main, sub, template, (stereo, fades, groups, entrada))
        estrito = caso / NOME_ESTRITO
        _ciclo(cli_main, estrito, template, ("pair", "absorb", "none", entrada))


def _ciclo(cli_main: Any, caso: Path, template: Path, variante: tuple[str, ...]) -> None:
    """Executa import, inspect, export e inspect; a pasta NOME_ESTRITO roda estrito."""
    stereo, fades, groups, entrada = variante
    caso.mkdir(parents=True, exist_ok=True)
    marca = [] if caso.name == NOME_ESTRITO else ["--permissive"]
    opcoes = marca + ["--stereo", stereo, "--fades", fades, "--groups", groups]
    importacao = ["import", entrada, "session.als", "--template", str(template)] + opcoes
    importacao += ["--report", "import-report.json"]
    exportacao = ["export", "session.als", "back.aaf"] + marca
    exportacao += ["--report", "export-report.json"]
    comandos = [run_command(cli_main, importacao, caso)]
    comandos.append(run_command(cli_main, ["inspect", "session.als"], caso))
    _escreve_json(caso, "inspect-als.json", comandos[1]["stdout"])
    comandos.append(run_command(cli_main, exportacao, caso))
    comandos.append(run_command(cli_main, ["inspect", "back.aaf"], caso))
    _escreve_json(caso, "inspect-back.json", comandos[3]["stdout"])
    _artefatos(caso)
    _grava_comandos(caso, comandos)


def _artefatos(caso: Path) -> None:
    """Grava o dump do AAF exportado e o XML descompactado do ALS."""
    aaf = caso / "back.aaf"
    try:
        dump = aaf_dump(aaf) if aaf.is_file() else "ausente\n"
    except Exception as erro:  # noqa: BLE001 - o dump nao pode derrubar o worker
        dump = f"exception: {type(erro).__name__}: {erro}\n"
    (caso / "back-aaf-dump.txt").write_text(dump, encoding="utf-8")
    als = caso / "session.als"
    xml = "ausente\n"
    if als.is_file():
        dados = als.read_bytes()
        try:
            dados = gzip.decompress(dados)
        except OSError:
            pass
        xml = dados.decode("utf-8", errors="replace")
    (caso / "session.als.xml").write_text(normalize_locator_tc(xml), encoding="utf-8")


def _escreve_json(caso: Path, nome: str, texto: str) -> None:
    """Grava um relatorio JSON normalizado dentro do caso."""
    (caso / nome).write_text(normalize_mobids(texto), encoding="utf-8")


def run_command(cli_main: Any, argv: list[str], cwd: Path) -> dict[str, Any]:
    """Executa um comando da CLI capturando saida, erro, codigo de saida e excecao."""
    buffer = io.StringIO()
    erros = io.StringIO()
    codigo = 0
    excecao = ""
    anterior = Path.cwd()
    os.chdir(cwd)
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(erros):
            try:
                resultado = cli_main(argv)
                codigo = 0 if resultado is None else int(resultado)
            except SystemExit as fim:
                codigo = fim.code if isinstance(fim.code, int) else int(fim.code is not None)
    except Exception as erro:  # noqa: BLE001 - a falha de um comando nao para o caso
        excecao = f"exception: {type(erro).__name__}: {erro}"
        codigo = 1
    finally:
        os.chdir(anterior)
    saida = buffer.getvalue()
    mensagem = erros.getvalue()
    for raiz in (cwd, cwd.parent):
        marca = os.path.normcase(str(raiz))
        mensagem = re.sub(re.escape(marca), "<CAMINHO>", mensagem, flags=re.IGNORECASE)
    return {
        "argv": list(argv),
        "exit_code": codigo,
        "stdout": saida,
        "stderr": mensagem,
        "exception": excecao,
    }


def _grava_comandos(caso: Path, comandos: list[dict[str, Any]]) -> None:
    """Grava o registro dos comandos executados em um caso."""
    texto = json.dumps(comandos, indent=2, sort_keys=True) + "\n"
    (caso / "commands.json").write_text(normalize_mobids(texto), encoding="utf-8")


def compare(a: Path, b: Path) -> list[str]:
    """Compara dois instantaneos e devolve a lista das diferencas."""
    manifesto_a = read_manifest(a)
    manifesto_b = read_manifest(b)
    diferencas: list[str] = []
    for caminho in sorted(set(manifesto_a) | set(manifesto_b)):
        if caminho not in manifesto_a or caminho not in manifesto_b:
            lado = a.name if caminho not in manifesto_a else b.name
            diferencas.append(f"faltando em {lado}: {caminho}")
        elif manifesto_a[caminho] != manifesto_b[caminho]:
            diferencas.append(_difereca_texto(a, b, caminho))
    return diferencas


def _difereca_texto(a: Path, b: Path, caminho: str) -> str:
    """Descreve a primeira linha diferente de um arquivo de texto."""
    if not caminho.endswith((".json", ".txt", ".xml", ".md")):
        return f"{caminho}: sha256 diferente"
    linhas_a = (a / caminho).read_text(encoding="utf-8", errors="replace").splitlines()
    linhas_b = (b / caminho).read_text(encoding="utf-8", errors="replace").splitlines()
    for indice in range(max(len(linhas_a), len(linhas_b))):
        texto_a = linhas_a[indice] if indice < len(linhas_a) else "<sem linha>"
        texto_b = linhas_b[indice] if indice < len(linhas_b) else "<sem linha>"
        if texto_a != texto_b:
            return f"{caminho}:{indice + 1}: {texto_a[:200]} != {texto_b[:200]}"
    return f"{caminho}: sha256 diferente"


def write_manifest(root: Path) -> None:
    """Grava o manifesto sha256 de todos os arquivos de um instantaneo."""
    entradas: dict[str, str] = {}
    for caminho in sorted(root.rglob("*")):
        relativo = caminho.relative_to(root).as_posix()
        if caminho.is_file() and relativo != "manifest.json":
            entradas[relativo] = hashlib.sha256(conteudo_comparavel(caminho)).hexdigest()
    texto = json.dumps(entradas, indent=2, sort_keys=True) + "\n"
    (root / "manifest.json").write_text(texto, encoding="utf-8")


def conteudo_comparavel(path: Path) -> bytes:
    """Devolve os bytes comparados de um arquivo do instantaneo."""
    # O ALS e o AAF exportado sao comparados pela forma normalizada deles.
    pares = {"session.als": "session.als.xml", "back.aaf": "back-aaf-dump.txt"}
    par = path.with_name(pares.get(path.name, path.name))
    return par.read_bytes() if par.is_file() else path.read_bytes()


def read_manifest(root: Path) -> dict[str, str]:
    """Le o manifesto sha256 de um instantaneo."""
    caminho = root / "manifest.json"
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file() else {}


def snapshot(src: Path, label: str, out: Path) -> None:
    """Gera o instantaneo 'label' da arvore 'src' dentro de 'out'."""
    out = Path(out)
    work = out / "work"
    alvo = out / label
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(src) / "src")
    comando = [sys.executable, str(Path(__file__).resolve()), "_worker"]
    comando += ["--src", str(src), "--work", str(work), "--template", str(TEMPLATE)]
    processo = subprocess.run(comando, env=env, cwd=str(work), capture_output=True, text=True)
    if processo.returncode != 0:
        sys.stderr.write(processo.stdout)
        sys.stderr.write(processo.stderr)
        raise SystemExit(f"worker falhou com codigo {processo.returncode}")
    if alvo.exists():
        shutil.rmtree(alvo)
    shutil.move(str(work), str(alvo))
    write_manifest(alvo)


def parse_opts(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    """Separa opcoes '--chave valor' de argumentos posicionais."""
    opts: dict[str, str] = {}
    posicionais: list[str] = []
    resto = list(tokens)
    while resto:
        token = resto.pop(0)
        if not token.startswith("--"):
            posicionais.append(token)
        else:
            opts[token] = resto.pop(0) if resto else ""
    return opts, posicionais


def _prepara_corpus(args: list[str]) -> int:
    """Exige o corpus preservado; com --sem-corpus, desliga o corpus e avisa."""
    if "--sem-corpus" in args:
        os.environ[SEM_CORPUS_ENV] = "1"
        print("aviso: corpus preservado desligado; o oraculo roda so com as fixtures")
        return 0
    bruto = os.environ.get(CORPUS_ENV, "").strip()
    if not bruto or not Path(bruto).is_dir():
        print(f"erro: {CORPUS_ENV} nao esta definida ou nao e uma pasta; use --sem-corpus")
        return 2
    if not any(Path(bruto).glob("*.aaf")):
        print(f"erro: {CORPUS_ENV} nao tem nenhum arquivo *.aaf; use --sem-corpus")
        return 2
    # So o --sem-corpus liga o flag; sem ele o flag que sobrou no ambiente
    # tiraria o corpus dos casos sem ninguem pedir.
    os.environ.pop(SEM_CORPUS_ENV, None)
    return 0


def cmd_snapshot(args: list[str]) -> int:
    """Executa o subcomando snapshot."""
    opts, _ = parse_opts([a for a in args if a != "--sem-corpus"])
    src = opts.get("--src", "")
    label = opts.get("--label", "")
    if not src or not label:
        print("uso: snapshot --src DIR --label NOME [--out DIR] [--sem-corpus]")
        return 2
    if _prepara_corpus(args):
        return 2
    out = Path(opts.get("--out") or OUT_ROOT).resolve()
    snapshot(Path(src).resolve(), label, out)
    print(f"instantaneo gravado em {out / label}")
    return 0


def cmd_compare(args: list[str]) -> int:
    """Executa o subcomando compare."""
    if len(args) < 2:
        print("uso: compare DIR_A DIR_B")
        return 2
    diferencas = compare(Path(args[0]), Path(args[1]))
    for diferenca in diferencas:
        print(diferenca)
    return 1 if diferencas else 0


def cmd_against(args: list[str]) -> int:
    """Executa o subcomando --against."""
    opts, _ = parse_opts([a for a in args if a != "--sem-corpus"])
    sha = opts.get("--against", "")
    if not sha:
        print("uso: --against SHA [--out DIR] [--sem-corpus]")
        return 2
    out = Path(opts.get("--out") or OUT_ROOT).resolve()
    base = out / "base-src"
    _git_worktree(["remove", "--force", str(base)])
    if _git_worktree(["add", "--force", "--detach", str(base), sha]) != 0:
        print("git worktree add falhou")
        return 2
    try:
        if _prepara_corpus(args):
            return 2
        snapshot(base, "base", out)
        snapshot(REPO, "tree", out)
    finally:
        _git_worktree(["remove", "--force", str(base)])
    diferencas = compare(out / "base", out / "tree")
    if diferencas:
        for diferenca in diferencas:
            print(diferenca)
        return 1
    arquivos = len(read_manifest(out / "base"))
    print(f"refactor_oracle: igual ({arquivos} arquivos)")
    return 0


def _git_worktree(args: list[str]) -> int:
    """Executa um comando git de worktree e devolve o codigo de saida."""
    comando = ["git", "worktree"] + list(args)
    return subprocess.run(comando, cwd=str(REPO), check=False).returncode


def main(argv: list[str]) -> int:
    """Ponto de entrada da linha de comando do oraculo."""
    args = list(argv)
    if not args:
        print("uso: refactor_oracle.py {snapshot|compare|--against}")
        return 2
    comando, resto = args[0], args[1:]
    if comando == "_worker":
        return cmd_worker(resto)
    if comando == "snapshot":
        return cmd_snapshot(resto)
    if comando == "compare":
        return cmd_compare(resto)
    if comando == "--against":
        return cmd_against(args)
    print(f"subcomando desconhecido: {comando}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
