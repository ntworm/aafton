"""Tests of the Live template that comes inside the package.

O template do Live e um dado do produto: o pacote instalado o carrega. Todo
caminho que procura um template usa o do pacote quando o pedido nao nomeia
nenhum, e a conversao nao depende mais da pasta do codigo.
"""

from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aafton.bundled import default_template
from aafton.cli import run_cli
from aafton.web_app import run_studio_server

REPO = Path(__file__).resolve().parents[2]
TEMPLATE_DO_REPO = REPO / "templates" / "live12.als"
FIXTURE = REPO / "tests" / "fixtures" / "aaf" / "embarcado.aaf"


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


def _post(url, payload):
    """A POST with a JSON body and the JSON it answers."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _copia_do_fixture(tmp_path):
    """The AAF with the embedded audio, alone in the folder."""
    origem = tmp_path / "embarcado.aaf"
    shutil.copyfile(FIXTURE, origem)
    return origem


def test_o_template_do_pacote_e_o_arquivo_do_repositorio():
    caminho = Path(default_template())

    assert caminho.is_file()
    assert caminho.read_bytes() == TEMPLATE_DO_REPO.read_bytes()


def test_o_pyproject_declara_o_template_como_dado_do_pacote():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")

    assert '"aafton" = ["templates/*.als"]' in pyproject


def test_a_cli_importa_sem_nomear_template(tmp_path, monkeypatch):
    """A pasta sem o template do codigo converte com o template do pacote."""
    origem = _copia_do_fixture(tmp_path)
    saida = tmp_path / "saida.als"
    monkeypatch.chdir(tmp_path)

    codigo = run_cli(["import", str(origem), str(saida)])

    assert codigo == 0
    assert saida.exists()


def test_a_rota_de_import_escreve_o_set_sem_nomear_template(studio_server, tmp_path):
    origem = _copia_do_fixture(tmp_path)
    saida = tmp_path / "saida.als"

    status, body = _post(
        f"{studio_server}/api/convert/import",
        {"input": str(origem), "output": str(saida)},
    )

    assert status == 200
    assert body["ok"] is True
    assert saida.exists()


def test_a_rota_do_app_escreve_o_set_sem_nomear_template(studio_server, tmp_path):
    origem = _copia_do_fixture(tmp_path)
    saida = tmp_path / "saida.als"

    status, body = _post(
        f"{studio_server}/api/app/convert",
        {
            "direction": "to_live",
            "input": str(origem),
            "output": str(saida),
            "options": {"mode": "strict"},
        },
    )

    assert status == 200
    assert body["ok"] is True
    assert body["written"] is True
    assert saida.exists()
