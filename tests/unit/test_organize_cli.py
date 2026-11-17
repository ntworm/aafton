"""Opcoes de organizacao na linha de comando (P4 da organizacao da sessao)."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aafton.sidecar import sidecar_path_for
from tests.support.paths import corpus_dir, fixture_aaf

RAIZ = Path(__file__).parents[2]


@pytest.fixture
def pt_aaf(tmp_path: Path) -> Path:
    isolado = tmp_path / "aaf_isolado"
    isolado.mkdir()
    return Path(shutil.copy2(fixture_aaf("TESTE_AFF_PROTOOLS.aaf"), isolado))


@pytest.fixture
def audio_dir() -> Path:
    return corpus_dir("teste_aaf")


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "aafton.cli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(RAIZ),
    )


def _importar(aaf: Path, saida: Path, audio_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return _cli(
        "import", str(aaf), str(saida), "--media", "link", "--audio-dir", str(audio_dir), *extra
    )


def _inspect(aaf: Path, audio_dir: Path, *extra: str) -> dict:
    res = _cli("inspect", str(aaf), "--organize", "type", "--audio-dir", str(audio_dir), *extra)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _movidas(proposta: dict) -> set[tuple[int, int]]:
    chaves = set()
    for grupo in proposta["groups"]:
        for track in grupo["tracks"]:
            for unidade in track["units"]:
                if unidade["accepted"]:
                    chaves.update(tuple(k) for k in unidade["origin_keys"])
    return chaves


def test_import_organize_type_escreve_clip_origins_e_verify_passa(pt_aaf, audio_dir, tmp_path):
    saida = tmp_path / "org.als"
    res = _importar(pt_aaf, saida, audio_dir, "--organize", "type", "--certainty", "permissive")
    assert res.returncode == 0, res.stderr

    sidecar = json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))
    assert sidecar.get("clip_origins")
    verify = _cli("verify", str(pt_aaf), str(saida))
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_groups_prefix_e_organize_prefix_escrevem_o_mesmo_set(pt_aaf, audio_dir, tmp_path):
    # O mesmo caminho de saida nas duas escritas: o XML guarda caminhos relativos ao set.
    saida = tmp_path / "set.als"
    assert _importar(pt_aaf, saida, audio_dir, "--groups", "prefix").returncode == 0
    por_groups = gzip.decompress(saida.read_bytes())
    saida.unlink()
    assert _importar(pt_aaf, saida, audio_dir, "--organize", "prefix").returncode == 0

    assert gzip.decompress(saida.read_bytes()) == por_groups
    assert "clip_origins" not in json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))


def test_groups_prefix_com_organize_type_e_recusado(pt_aaf, audio_dir, tmp_path):
    res = _importar(
        pt_aaf, tmp_path / "x.als", audio_dir, "--groups", "prefix", "--organize", "type"
    )
    assert res.returncode == 2
    assert "cannot be used together" in res.stderr


def test_inspect_organize_imprime_proposta_e_edicoes(pt_aaf, audio_dir):
    permissive = _inspect(pt_aaf, audio_dir)
    balanced = _inspect(pt_aaf, audio_dir, "--certainty", "balanced")
    strict = _inspect(pt_aaf, audio_dir, "--certainty", "strict")

    assert set(permissive) == {"proposal", "edits"}
    assert permissive["proposal"]["level"] == "permissive"
    assert balanced["proposal"]["level"] == "balanced"
    assert _movidas(strict["proposal"]) <= _movidas(balanced["proposal"])
    assert _movidas(balanced["proposal"]) <= _movidas(permissive["proposal"])


def test_organize_plan_aplica_grupo_desmarcado(pt_aaf, audio_dir, tmp_path):
    plano = _inspect(pt_aaf, audio_dir, "--certainty", "permissive")
    grupos = [g["key"] for g in plano["proposal"]["groups"]]
    assert grupos, "the fixture should give at least one group in permissive"
    desmarcado = grupos[0]
    plano["edits"]["groups"] = {desmarcado: False}
    arquivo = tmp_path / "plano.json"
    arquivo.write_text(json.dumps(plano), encoding="utf-8")

    saida = tmp_path / "plano.als"
    res = _importar(pt_aaf, saida, audio_dir, "--organize-plan", str(arquivo))
    assert res.returncode == 0, res.stderr

    rotulo = next(g["label"] for g in plano["proposal"]["groups"] if g["key"] == desmarcado)
    xml = gzip.decompress(saida.read_bytes()).decode("utf-8")
    assert f'<EffectiveName Value="{rotulo}" />' not in xml
    verify = _cli("verify", str(pt_aaf), str(saida))
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_organize_plan_ilegivel_sai_com_2(pt_aaf, audio_dir, tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{", encoding="utf-8")
    res = _importar(pt_aaf, tmp_path / "x.als", audio_dir, "--organize-plan", str(ruim))
    assert res.returncode == 2
    assert "cannot read the organize plan" in res.stderr


def test_inspect_organize_le_fcpxml():
    fcpxml = RAIZ / "tests" / "fixtures" / "formatos" / "cena12.fcpxml"
    res = _cli("inspect", str(fcpxml), "--organize", "type")
    assert res.returncode == 0, res.stderr
    assert set(json.loads(res.stdout)) == {"proposal", "edits"}
