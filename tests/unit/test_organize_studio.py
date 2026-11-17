"""Testes do backend de organizacao de sessao no Studio (P5a)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aafton.organize_plan import Edits, build_proposal
from aafton.studio import app_convert, app_organize, app_routes
from tests.support.paths import corpus_dir, fixture_aaf

RAIZ = Path(__file__).parents[2]


@pytest.fixture
def pt_aaf(tmp_path: Path) -> Path:
    isolado = tmp_path / "aaf_isolado"
    isolado.mkdir()
    return Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolado))


@pytest.fixture
def audio_dir() -> Path:
    try:
        return corpus_dir("teste_aaf")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"corpus teste_aaf ausente: {exc}")


def _cli_inspect(aaf: Path, audio_dir: Path, *extra: str) -> dict:
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "aafton.cli",
            "inspect",
            str(aaf),
            "--organize",
            "type",
            "--audio-dir",
            str(audio_dir),
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(RAIZ),
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _movidas(proposta: dict) -> set[tuple[int, int]]:
    return {
        tuple(k)
        for g in proposta["groups"]
        for t in g["tracks"]
        for u in t["units"]
        if t["source_track"] is None or u["source_label"] != t["source_track"]
        for k in u["origin_keys"]
    }


def test_organize_studio_igual_ao_cli_e_resumo(pt_aaf: Path, audio_dir: Path):
    app_organize.clear_cache()
    payload = {
        "input": str(pt_aaf),
        "audio_dir": str(audio_dir),
        "certainty": "permissive",
        "signals": ["file", "meta", "name", "family", "color"],
    }
    res = app_organize.organize(payload)
    assert res["ok"] is True

    cli_out = _cli_inspect(pt_aaf, audio_dir)
    cli_prop = cli_out["proposal"]
    app_prop = res["proposal"]

    cli_groups = [g["key"] for g in cli_prop["groups"]]
    app_groups = [g["key"] for g in app_prop["groups"]]
    assert app_groups == cli_groups

    cli_moved = _movidas(cli_prop)
    app_moved = _movidas(app_prop)
    assert app_moved == cli_moved

    summary = res["summary"]
    expected_tracks = sum(len(g["tracks"]) for g in app_prop["groups"])
    expected_groups = len(app_prop["groups"])
    expected_moved = len(app_moved)
    expected_stay = sum(
        len(u["origin_keys"])
        for g in app_prop["groups"]
        for t in g["tracks"]
        for u in t["units"]
        if not u["accepted"]
    ) + sum(
        len(u["origin_keys"])
        for left in app_prop["left_tracks"]
        for u in left["units"]
        if not u["accepted"]
    )
    expected_edited = 0

    assert summary["tracks"] == expected_tracks
    assert summary["groups"] == expected_groups
    assert summary["moved"] == expected_moved
    assert summary["stay"] == expected_stay
    assert summary["edited"] == expected_edited


def test_segunda_chamada_com_edits_desmarca_grupo_e_usa_cache(
    pt_aaf: Path, audio_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    app_organize.clear_cache()
    read_count = 0
    orig_read = app_organize.read_for_proposal

    def counting_read(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        return orig_read(*args, **kwargs)

    monkeypatch.setattr(app_organize, "read_for_proposal", counting_read)

    payload1 = {"input": str(pt_aaf), "audio_dir": str(audio_dir)}
    res1 = app_organize.organize(payload1)
    assert res1["ok"] is True
    assert read_count == 1
    groups1 = res1["proposal"]["groups"]
    assert groups1

    desmarcado = groups1[0]["key"]
    desmarcado_origins = [
        tuple(k) for t in groups1[0]["tracks"] for u in t["units"] for k in u["origin_keys"]
    ]
    edits = {
        "groups": {desmarcado: False},
        "origins": [{"origin": list(k), "accepted": False} for k in desmarcado_origins],
    }
    payload2 = {
        "input": str(pt_aaf),
        "audio_dir": str(audio_dir),
        "edits": edits,
    }
    res2 = app_organize.organize(payload2)
    assert res2["ok"] is True
    assert read_count == 1

    groups2_keys = [g["key"] for g in res2["proposal"]["groups"]]
    assert desmarcado not in groups2_keys
    assert res2["summary"]["edited"] == len(desmarcado_origins)


def _origens_aceitas(track: dict) -> set[tuple[int, int]]:
    """Chaves de origem das unidades aceitas de uma track da proposta."""
    return {
        tuple(chave)
        for unidade in track["units"]
        if unidade["accepted"]
        for chave in unidade["origin_keys"]
    }


def _grupos_e_nomes(proposta: dict) -> list[tuple[str, list[tuple[str, str]]]]:
    """Chave de cada grupo com a chave e o nome de suas tracks de destino."""
    return [(g["key"], [(t["key"], t["name"]) for t in g["tracks"]]) for g in proposta["groups"]]


def _proposta_organizada(pt_aaf: Path, audio_dir: Path) -> dict:
    """Primeira proposta da fixture, com a compactacao que a pagina sempre liga."""
    app_organize.clear_cache()
    res = app_organize.organize({"input": str(pt_aaf), "audio_dir": str(audio_dir)})
    assert res["ok"] is True
    return res["proposal"]


def _edits_com_ancora(proposta: dict) -> tuple[dict, set[tuple[int, int]]]:
    """Renomeia a track A e desmarca as origens aceitas de outra track do mesmo grupo."""
    for grupo in proposta["groups"]:
        com_aceitas = [t for t in grupo["tracks"] if _origens_aceitas(t)]
        if len(com_aceitas) < 2:
            continue
        track_a, track_b = com_aceitas[0], com_aceitas[1]
        ancora = _origens_aceitas(track_a)
        origens_b = sorted(_origens_aceitas(track_b))
        edits = {
            "track_names": {track_a["key"]: "Nome ancorado"},
            "track_name_origins": {track_a["key"]: [list(k) for k in sorted(ancora)]},
            "origins": [{"origin": list(k), "accepted": False} for k in origens_b],
        }
        return edits, ancora
    raise AssertionError("a fixture precisa de duas tracks aceitas no mesmo grupo")


def test_nome_da_track_segue_as_origens_aceitas_ao_desmarcar_outra(pt_aaf: Path, audio_dir: Path):
    base = _proposta_organizada(pt_aaf, audio_dir)
    edits, ancora = _edits_com_ancora(base)

    pedido = {"input": str(pt_aaf), "audio_dir": str(audio_dir), "edits": edits}
    res = app_organize.organize(pedido)
    assert res["ok"] is True
    proposta = res["proposal"]

    portadoras = [
        t for g in proposta["groups"] for t in g["tracks"] if _origens_aceitas(t) & ancora
    ]
    assert portadoras, "a proposta ficou sem as origens aceitas da track renomeada"
    nomes = [t["name"] for g in proposta["groups"] for t in g["tracks"]]
    assert nomes.count("Nome ancorado") == 1
    assert any(t["name"] == "Nome ancorado" for t in portadoras)


def test_proposta_da_rota_igual_ao_build_proposal(pt_aaf: Path, audio_dir: Path, tmp_path: Path):
    base = _proposta_organizada(pt_aaf, audio_dir)
    edits, _ancora = _edits_com_ancora(base)

    pedido = {"input": str(pt_aaf), "audio_dir": str(audio_dir), "edits": edits}
    res = app_organize.organize(pedido)
    assert res["ok"] is True

    # Mesmas entradas da rota: sessao e tracks excluidas do proprio cache, os
    # sinais que a rota usa (com compact) e o nivel padrao.
    sessao, excluidas = app_organize._obter_sessao(pt_aaf, str(audio_dir), None, None, None)
    sinais = app_organize._sinais(None)
    assert "compact" in sinais
    plano = build_proposal(
        sessao,
        level="permissive",
        signals=sinais,
        edits=Edits.from_dict(edits),
        excluded_source_indices=excluidas,
    ).to_dict()

    assert _grupos_e_nomes(plano) == _grupos_e_nomes(res["proposal"])
    assert plano == res["proposal"]


def test_caminho_inexistente_devolve_not_found():
    res = app_organize.organize({"input": "arquivo_inexistente_123.aaf"})
    assert res["ok"] is False
    assert res["error"]["code"] == "not_found"


def test_payload_sem_input_devolve_bad_request():
    res1 = app_organize.organize({})
    assert res1["ok"] is False
    assert res1["error"]["code"] == "bad_request"

    res2 = app_organize.organize({"input": ""})
    assert res2["ok"] is False
    assert res2["error"]["code"] == "bad_request"


def test_app_convert_opcoes_do_import_organize_type():
    edits = {"groups": {"SFX": False}, "origins": []}
    payload = {
        "input": "input.aaf",
        "output": "output.als",
        "options": {
            "organize": "type",
            "certainty": "strict",
            "signals": ["file", "meta", "name"],
            "edits": edits,
        },
    }
    opts = app_convert._opcoes_do_import(payload, "input.aaf", Path("output.als"))
    assert opts.organize == "type"
    assert opts.certainty == "strict"
    assert opts.organize_signals == ("file", "meta", "name", "compact")
    assert opts.organize_edits == edits
    assert opts.groups == "none"


def test_app_convert_opcoes_do_import_groups_prefix():
    payload = {
        "input": "input.aaf",
        "output": "output.als",
        "options": {
            "groups": "prefix",
        },
    }
    opts = app_convert._opcoes_do_import(payload, "input.aaf", Path("output.als"))
    assert opts.organize == "prefix"
    assert opts.groups == "prefix"


def test_app_convert_opcoes_do_import_sem_organize():
    payload = {
        "input": "input.aaf",
        "output": "output.als",
        "options": {},
    }
    opts = app_convert._opcoes_do_import(payload, "input.aaf", Path("output.als"))
    assert opts.organize == "off"
    assert opts.groups == "none"


class _DummyHandler:
    def __init__(self) -> None:
        self.sent_data: dict | None = None
        self.status: int | None = None

    def _send_json(self, data: dict, status: int = 200) -> None:
        self.sent_data = data
        self.status = status


def test_app_routes_post_organize():
    handler = _DummyHandler()
    servida = app_routes.handle_post(handler, "/api/app/organize", {"input": ""})
    assert servida is True
    assert handler.status == 400
    assert handler.sent_data is not None
    assert handler.sent_data["ok"] is False
    assert handler.sent_data["error"]["code"] == "bad_request"
