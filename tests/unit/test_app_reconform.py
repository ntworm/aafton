"""Rotas da aba Reconform do Studio: change list e copia do set."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.studio import app_convert, app_reconform
from aafton.studio.app_routes import POST_HANDLERS
from aafton.studio.page import load_page
from tests.unit.test_cli_reconform import _AMBIGUO, _edls
from tests.unit.test_reconform_apply import _build_spanning_set, _sha256

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


def test_run_ambiguo_nao_escreve(tmp_path: Path) -> None:
    old, new = _edls(tmp_path, _AMBIGUO)
    conjunto = _build_spanning_set(tmp_path)
    res = app_reconform.run({"input": old, "new_cut": new, "set": str(conjunto)})
    assert not res["written"]
    assert res["verdict"]["state"] == "err"
    assert not (tmp_path / "review reconform.als").exists()


def test_run_sem_set(tmp_path: Path) -> None:
    old, new = _edls(tmp_path)
    res = app_reconform.run({"input": old, "new_cut": new, "edl_rate": "25"})
    assert not res["ok"]
