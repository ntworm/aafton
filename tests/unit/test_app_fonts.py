"""Tests of the fonts the app page bundles: the files, the rules and the server."""

from __future__ import annotations

import hashlib
import re
import threading
import urllib.request
from importlib import resources
from pathlib import Path

import pytest

from aafton.studio import app_routes
from aafton.web_app import run_studio_server


def _pasta(*partes: str) -> Path:
    """A path inside the package of the studio."""
    return Path(str(resources.files("aafton.studio").joinpath(*partes)))


# The folder of the fonts inside the package and the stylesheet that names them.
FONTE_PACOTE = _pasta("static", "fonts")
CSS = _pasta("static", "studio.css")

# The rule of the stylesheet: the block of a font and the url inside it.
REGRA_FONTE = re.compile(r"@font-face\s*\{([^}]*)\}")
URL_FONTE = re.compile(r'url\(\s*"([^"]*)"\s*\)')

# The size and the sha256 of every file that ships: the bytes cannot change.
CONTEUDO = {
    "IBMPlexSans-Regular.woff2": (
        22588,
        "3b646991d30055a93a4ecc499713d4347953a74a947ecab435ab72070cbdab0e",
    ),
    "IBMPlexSans-Medium.woff2": (
        24184,
        "0717336fb31fcdcde4b8deb3675bb4a0f7f6d484864afcd6751ac29975962203",
    ),
    "IBMPlexMono-Regular.woff2": (
        14708,
        "08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7",
    ),
    "IBMPlexMono-Medium.woff2": (
        14888,
        "01d285447409c8a588692162439a038b8cbd7871309ee20267b0d2d91c6e8e22",
    ),
    "IBMPlexSerif-MediumItalic.woff2": (
        21696,
        "42fde68f485b8d3096a22711a16702013d437830f7293ecc4cc75554d479a363",
    ),
}

# The families that ship, each one with its licence text beside it.
FAMILIAS = ("IBMPlexSans", "IBMPlexMono", "IBMPlexSerif")

# The section of the pyproject that holds the files of the package.
SECAO_DADOS = "[tool.setuptools.package-data]"
DADOS_PACOTE = re.compile(r'"aafton\.studio"\s*=\s*\[([^\]]*)\]')


@pytest.fixture(scope="module")
def studio_server():
    """The studio server on a free port, without opening a browser."""
    server = run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def _regras() -> list[str]:
    """The block of every @font-face rule of the stylesheet."""
    return REGRA_FONTE.findall(CSS.read_text(encoding="utf-8"))


def _nomes_do_css() -> list[str]:
    """The name of every font file that a rule of the stylesheet names."""
    nomes = []
    for regra in _regras():
        urls = URL_FONTE.findall(regra)
        assert len(urls) == 1, regra
        assert urls[0].startswith(app_routes.FONT_PREFIX), urls[0]
        nomes.append(urls[0].removeprefix(app_routes.FONT_PREFIX))
    return nomes


def _dados_do_pacote() -> list[str]:
    """The files that the package data of aafton.studio declares."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    linhas = pyproject.read_text(encoding="utf-8").splitlines()
    if SECAO_DADOS not in linhas:
        return []
    bloco = []
    for linha in linhas[linhas.index(SECAO_DADOS) + 1 :]:
        if linha.startswith("["):
            break
        bloco.append(linha)
    achado = DADOS_PACOTE.search("\n".join(bloco))
    if achado is None:
        return []
    return re.findall(r'"([^"]+)"', achado.group(1))


def _get(url: str):
    """A GET and its status, headers and body."""
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.headers, resp.read()


def test_o_css_tem_cinco_regras_e_nenhuma_usa_local():
    regras = _regras()

    assert len(regras) == 5
    for regra in regras:
        assert "local(" not in regra, regra


def test_o_css_nomeia_exatamente_as_fontes_que_ship():
    assert sorted(_nomes_do_css()) == sorted(CONTEUDO)


@pytest.mark.parametrize("nome", sorted(CONTEUDO))
def test_a_fonte_nomeada_existe_no_pacote_e_e_um_woff2(nome):
    arquivo = FONTE_PACOTE / nome

    assert arquivo.is_file(), nome
    assert app_routes.FONT_NAME.fullmatch(nome), nome
    assert arquivo.read_bytes()[:4] == b"wOF2", nome


@pytest.mark.parametrize("nome", sorted(CONTEUDO))
def test_o_conteudo_de_cada_fonte_e_o_esperado(nome):
    tamanho, resumo = CONTEUDO[nome]
    dados = (FONTE_PACOTE / nome).read_bytes()

    assert len(dados) == tamanho, nome
    assert hashlib.sha256(dados).hexdigest() == resumo, nome


@pytest.mark.parametrize("nome", sorted(CONTEUDO))
def test_o_servidor_responde_a_fonte_com_o_conteudo_do_arquivo(studio_server, nome):
    status, headers, corpo = _get(f"{studio_server}{app_routes.FONT_PREFIX}{nome}")

    assert status == 200
    assert headers["Content-Type"] == "font/woff2"
    assert corpo == (FONTE_PACOTE / nome).read_bytes()


def test_nenhuma_fonte_fica_de_fora_do_css():
    shipped = sorted(item.name for item in FONTE_PACOTE.iterdir() if item.name.endswith(".woff2"))

    assert shipped == sorted(_nomes_do_css())


def test_cada_familia_tem_a_sua_licenca_ao_lado():
    familias = {nome.split("-", 1)[0] for nome in CONTEUDO}

    assert familias == set(FAMILIAS)
    for familia in FAMILIAS:
        licenca = FONTE_PACOTE / f"{familia}-LICENSE.txt"
        assert licenca.is_file(), familia
        assert "SIL OPEN FONT LICENSE" in licenca.read_text(encoding="utf-8"), familia


def test_o_pyproject_embala_as_fontes_no_pacote():
    assert "static/fonts/*" in _dados_do_pacote()
