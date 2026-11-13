"""Caminhos de fixtures versionados e do corpus externo pinado.

Falta de fixture ou de corpus e falha, nunca pulo: skips por midia externa ja
esconderam perda de clips do Resolve. Os AAF de teste
estao versionados em tests/fixtures/aaf/. O audio (silencio estrutural ou real) fica fora do Git e e
conferido contra o manifesto em tests/corpus/<nome>.json, gerado por
scripts/corpus_manifest.py.

Variaveis de ambiente:
- AAFTON_CORPUS_DIR: pasta local com os WAV do corpus (padrao: a pasta do
  Desktop onde o corpus foi medido).
- AAFTON_CORPUS_VERIFY=hash: alem de presenca e tamanho, confere o sha256 de
  cada arquivo (mais lento; use quando trocar de maquina ou de copia).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES_AAF = REPO / "tests" / "fixtures" / "aaf"
CORPUS_MANIFESTS = REPO / "tests" / "corpus"

CORPUS_ENV = "AAFTON_CORPUS_DIR"
CORPUS_VERIFY_ENV = "AAFTON_CORPUS_VERIFY"
REEXPORT_ENV = "AAFTON_REEXPORT_DIR"
LOCAL_MEDIA_ENV = "AAFTON_LOCAL_MEDIA_DIR"

# Onde cada corpus foi medido nesta maquina; AAFTON_CORPUS_DIR tem precedencia.
DEFAULT_CORPUS_DIRS = {
    "teste_aaf": REPO / ".local" / "corpus" / "teste_aaf",
}

_PROBLEMAS_LISTADOS = 5

PRIVATE_FIXTURES = frozenset(
    {
        "dialect_protools.aaf",
        "dialect_premiere.aaf",
        "dialect_resolve.aaf",
    }
)
NOT_DISTRIBUTED_PREFIX = "not distributed:"

REAL_MEDIA_CORPORA = frozenset(
    {
        "teste_novo_reexport",
        "teste_novo_reexport_20261013",
        "teste_novo_tracks",
        "teste_novo_tracks_20261013",
    }
)


def is_private_fixture(nome: str) -> bool:
    nome_arquivo = f"{nome}.aaf" if not Path(nome).suffix else nome
    return nome_arquivo in PRIVATE_FIXTURES


def fixture_aaf(nome: str) -> Path:
    """AAF versionado em tests/fixtures/aaf; falha se nao estiver la."""
    caminho = FIXTURES_AAF / nome
    if not caminho.is_file():
        pytest.fail(
            f"fixture AAF ausente: {caminho}. Ele deve estar versionado em tests/fixtures/aaf/.",
            pytrace=False,
        )
    return caminho


def private_fixture(nome: str) -> Path:
    """Fixture privada em tests/fixtures/aaf; pula se ausente e publica falha."""
    nome_arquivo = f"{nome}.aaf" if not Path(nome).suffix else nome
    caminho = FIXTURES_AAF / nome_arquivo
    if not caminho.is_file():
        if is_private_fixture(nome_arquivo):
            pytest.skip(f"{NOT_DISTRIBUTED_PREFIX} {nome} (private fixture)")
        return fixture_aaf(nome_arquivo)
    return caminho


def local_media(nome: str) -> Path:
    """Midia local fora do repositorio; pula se ausente ou nao configurada."""
    if LOCAL_MEDIA_ENV in os.environ and os.environ[LOCAL_MEDIA_ENV].strip():
        caminho = Path(os.environ[LOCAL_MEDIA_ENV]) / nome
        if caminho.exists():
            return caminho
    pytest.skip(f"{NOT_DISTRIBUTED_PREFIX} {nome} (local media); set {LOCAL_MEDIA_ENV}")


def corpus_manifest_path(nome: str) -> Path:
    return CORPUS_MANIFESTS / f"{nome}.json"


def load_corpus_manifest(nome: str, manifest_path: Path | None = None) -> dict:
    """Le o manifesto pinado; manifesto ausente ou vazio e falha."""
    caminho = Path(manifest_path) if manifest_path is not None else corpus_manifest_path(nome)
    if not caminho.is_file():
        pytest.fail(
            f"manifesto do corpus {nome!r} ausente: {caminho}. "
            f"Gere com scripts/corpus_manifest.py.",
            pytrace=False,
        )
    with open(caminho, encoding="utf-8") as f:
        manifesto = json.load(f)
    if not manifesto.get("files"):
        pytest.fail(f"manifesto do corpus {nome!r} sem arquivos: {caminho}", pytrace=False)
    return manifesto


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for pedaco in iter(lambda: f.read(1 << 20), b""):
            h.update(pedaco)
    return h.hexdigest()


def _gerar_corpus_de_silencio(manifesto_caminho: Path, pasta: Path) -> int:
    """Gera o corpus de silencio do manifesto na pasta; devolve o codigo do gerador."""
    caminho = REPO / "scripts" / "make_silence_corpus.py"
    spec = importlib.util.spec_from_file_location("aafton_make_silence_corpus", caminho)
    if spec is None or spec.loader is None:
        pytest.fail(f"gerador de silencio ausente: {caminho}", pytrace=False)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.make_silence_corpus(manifesto_caminho, pasta)


def corpus_dir(
    nome: str = "teste_aaf",
    *,
    manifest_path: Path | None = None,
    env_var: str = CORPUS_ENV,
    default_dir: Path | None = None,
    verify_hash: bool = False,
) -> Path:
    """Pasta local do corpus, conferida contra o manifesto pinado.

    Resolve AAFTON_CORPUS_DIR (ou a pasta padrao do corpus) e confere presenca e
    tamanho de cada arquivo do manifesto; com AAFTON_CORPUS_VERIFY=hash confere
    tambem o sha256. Qualquer divergencia chama pytest.fail citando
    AAFTON_CORPUS_DIR. Sem AAFTON_CORPUS_DIR e sem a pasta padrao de teste_aaf,
    o corpus de silencio e gerado a partir do manifesto antes da conferida.
    """
    manifesto_caminho = (
        Path(manifest_path) if manifest_path is not None else corpus_manifest_path(nome)
    )
    manifesto = load_corpus_manifest(nome, manifesto_caminho)

    configurado = os.environ.get(env_var, "").strip()
    if configurado:
        pasta = Path(configurado)
        origem = f"{env_var}={configurado}"
    elif default_dir is not None or nome in DEFAULT_CORPUS_DIRS:
        pasta = default_dir if default_dir is not None else DEFAULT_CORPUS_DIRS[nome]
        origem = f"pasta padrao, {env_var} nao definido"
    else:
        if nome in REAL_MEDIA_CORPORA:
            pytest.skip(f"{NOT_DISTRIBUTED_PREFIX} {nome} (real media corpus); set {env_var}")
        pytest.fail(
            f"corpus {nome!r} sem pasta padrao: defina {env_var} com a pasta dos "
            f"arquivos listados em {manifesto_caminho}",
            pytrace=False,
        )

    instrucao = (
        f"Defina {env_var} com a pasta que contem os {len(manifesto['files'])} "
        f"arquivos listados em {manifesto_caminho}."
    )
    if nome == "teste_aaf":
        instrucao += (
            " Crie com: python scripts/make_silence_corpus.py "
            "tests/corpus/teste_aaf.json .local/corpus/teste_aaf"
        )
    if nome == "teste_aaf" and not configurado:
        if not pasta.is_dir():
            codigo = _gerar_corpus_de_silencio(manifesto_caminho, pasta)
            if codigo != 0:
                pytest.fail(
                    f"corpus {nome!r} gerado em {pasta} nao corresponde ao manifesto "
                    f"(codigo de saida {codigo}). {instrucao}",
                    pytrace=False,
                )
    if not pasta.is_dir():
        if nome in REAL_MEDIA_CORPORA:
            pytest.skip(f"{NOT_DISTRIBUTED_PREFIX} {nome} (real media corpus); set {env_var}")
        pytest.fail(
            f"corpus {nome!r} nao encontrado em {pasta} ({origem}). {instrucao}",
            pytrace=False,
        )

    conferir_hash = verify_hash or os.environ.get(CORPUS_VERIFY_ENV, "").strip().lower() == "hash"
    problemas = []
    for entrada in manifesto["files"]:
        arquivo = pasta / entrada["name"]
        if not arquivo.is_file():
            problemas.append(f"ausente: {entrada['name']}")
            continue
        tamanho = arquivo.stat().st_size
        if tamanho != entrada["bytes"]:
            problemas.append(
                f"tamanho difere: {entrada['name']} tem {tamanho} bytes, manifesto diz {entrada['bytes']}"
            )
            continue
        if conferir_hash and _sha256(arquivo) != entrada["sha256"]:
            problemas.append(f"sha256 difere: {entrada['name']}")

    if problemas:
        if nome in REAL_MEDIA_CORPORA:
            pytest.skip(f"{NOT_DISTRIBUTED_PREFIX} {nome} (real media corpus); set {env_var}")
        listados = "; ".join(problemas[:_PROBLEMAS_LISTADOS])
        resto = len(problemas) - _PROBLEMAS_LISTADOS
        if resto > 0:
            listados += f"; e mais {resto}"
        pytest.fail(
            f"corpus {nome!r} em {pasta} ({origem}) diverge do manifesto: "
            f"{len(problemas)} de {len(manifesto['files'])} arquivos com problema ({listados}). "
            f"{instrucao}",
            pytrace=False,
        )
    return pasta


def reexport_corpus(*, manifest_path: Path | None = None) -> tuple[Path, Path]:
    """AAF e fades reais TESTE_NOVO; ausencia da midia pula com motivo declarado."""
    nome = "teste_novo_reexport"
    manifesto = Path(manifest_path) if manifest_path is not None else corpus_manifest_path(nome)
    if REEXPORT_ENV in os.environ and not os.environ[REEXPORT_ENV].strip():
        pytest.fail(f"{REEXPORT_ENV} definido mas vazio", pytrace=False)
    if not manifesto.is_file():
        pytest.fail(
            f"{REEXPORT_ENV}: manifesto ausente: {manifesto}; gere com scripts/corpus_manifest.py",
            pytrace=False,
        )
    pasta = corpus_dir(
        nome,
        manifest_path=manifesto,
        env_var=REEXPORT_ENV,
        default_dir=REPO / "out/teste-novo/g01-float32-20261014/reexport-final24",
        verify_hash=True,
    )
    aafs = [
        pasta / e["name"]
        for e in load_corpus_manifest(nome, manifesto)["files"]
        if Path(e["name"]).suffix.lower() == ".aaf"
    ]
    if len(aafs) != 1:
        pytest.fail(
            f"{REEXPORT_ENV}: manifesto deve pinar exatamente um AAF; achou {len(aafs)}",
            pytrace=False,
        )
    return aafs[0], pasta


def corpus_file(nome_arquivo: str, nome: str = "teste_aaf") -> tuple[Path, dict]:
    """Arquivo do corpus conferido e sua entrada no manifesto."""
    pasta = corpus_dir(nome)
    manifesto = load_corpus_manifest(nome)
    for entrada in manifesto["files"]:
        if entrada["name"] == nome_arquivo:
            return pasta / nome_arquivo, entrada
    pytest.fail(
        f"{nome_arquivo} nao esta no manifesto {corpus_manifest_path(nome)}",
        pytrace=False,
    )
