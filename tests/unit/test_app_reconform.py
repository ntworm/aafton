"""Rotas da aba Reconform do Studio: change list e copia do set."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.studio import app_convert, app_reconform
from aafton.studio.app_routes import POST_HANDLERS
from aafton.studio.page import load_page
from tests.unit.test_cli_reconform import _edls, _edls_empate
from tests.unit.test_reconform_apply import _build_spanning_set, _sha256
from tests.unit.test_review_robustness import make_set

pytest.importorskip("opentimelineio")


def test_rotas_registradas() -> None:
    assert POST_HANDLERS["/api/reconform/read"] == (app_reconform, "read")
    assert POST_HANDLERS["/api/reconform/run"] == (app_reconform, "run")


def test_pagina_tem_a_aba() -> None:
    html = load_page()
    assert 'id="tab-reconform" aria-controls="panel-reconform"' in html
    assert 'data-direction="reconform"' in html
    assert 'id="tpl-track-reconform"' in html
    assert "App.renderReconform" in html


def test_pagina_avisa_do_empate_em_vez_de_recusar() -> None:
    """A aba do Reconform avisa dos planos repetidos em vez de recusar o set."""
    html = load_page()
    aviso = "Some shots are used more than once; those parts were not matched and are listed."
    assert "A shot is used twice: the set cannot be conformed." not in html
    assert aviso in html


def test_read_devolve_a_change_list(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    res = app_reconform.read({"input": old, "new_cut": new, "edl_rate": "25"})
    assert res["ok"] and not res["ambiguous"]
    tipos = {linha["kind"] for linha in res["items"]}
    assert {"Removed", "Added"} <= tipos
    assert res["facts"][0]["k"] == "Changes"


def test_read_edl_sem_taxa_e_erro(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    res = app_reconform.read({"input": old, "new_cut": new})
    assert not res["ok"]
    assert "frame rate" in res["error"]["message"]


def test_read_sugere_a_copia_ao_lado_do_set(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    conjunto = _build_spanning_set(tmp_path)
    res = app_reconform.read({"input": old, "new_cut": new, "set": str(conjunto)})
    assert res["ok"]
    assert res["suggest"]["output"] == str(tmp_path / "review reconform.als")


def test_run_conforma_e_verifica(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    conjunto = _build_spanning_set(tmp_path)
    antes = _sha256(conjunto)
    res = app_reconform.run({"input": old, "new_cut": new, "set": str(conjunto)})
    assert res["ok"] and res["written"]
    assert res["verdict"]["word"] == "Conformed"
    assert Path(res["output"]).exists()
    assert _sha256(conjunto) == antes
    assert app_convert.LAST["reconform"]["output"] == Path(res["output"])


def test_run_so_locators(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    conjunto = _build_spanning_set(tmp_path)
    payload = {"input": old, "new_cut": new, "set": str(conjunto), "options": {"apply": False}}
    res = app_reconform.run(payload)
    assert res["written"] and res["verdict"]["word"] == "Locators written"
    assert (tmp_path / "review reconform locators.als").exists()


def test_run_empate_escreve_e_avisa(tmp_path: Path) -> None:
    """O par empatado e aplicado e o trecho sem correspondencia fica como aviso."""
    old, new = _edls_empate(tmp_path)
    conjunto = make_set(tmp_path)
    res = app_reconform.run({"input": old, "new_cut": new, "set": str(conjunto)})
    assert res["ok"] and res["written"]
    assert res["verdict"]["word"] == "Conformed"
    assert (tmp_path / "review reconform.als").exists()
    notas = res["notes"]["main"]
    avisos = [nota for nota in notas if nota["code"] == "RECONFORM_REUSE_UNRESOLVED"]
    assert avisos
    assert avisos[0]["sev"] == "warning"

    leitura = app_reconform.read({"input": old, "new_cut": new, "edl_rate": "25"})
    assert leitura["unresolved"] == 1
    linhas = [linha for linha in leitura["items"] if linha["kind"] == "Not matched"]
    assert linhas
    assert linhas[0]["state"]["st"] == "warn"


def test_run_sem_set(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    res = app_reconform.run({"input": old, "new_cut": new, "edl_rate": "25"})
    assert not res["ok"]
