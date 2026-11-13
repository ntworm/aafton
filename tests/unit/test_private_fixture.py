"""Testes de resolucao de fixtures privadas e publicas (paths.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from _pytest.outcomes import Failed, Skipped

from tests.support import paths


def test_fixture_privada_ausente_pula_com_motivo_not_distributed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "FIXTURES_AAF", tmp_path)

    for nome in ("dialect_protools.aaf", "dialect_premiere", "dialect_resolve.aaf"):
        with pytest.raises(Skipped) as exc_info:
            paths.private_fixture(nome)
        assert exc_info.value.msg.startswith("not distributed:")


def test_fixture_publica_ausente_falha_sem_pular(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(paths, "FIXTURES_AAF", tmp_path)

    for nome in ("TESTE_AFF_PROTOOLS.aaf", "inexistente.aaf", "outro"):
        with pytest.raises(Failed):
            paths.private_fixture(nome)


def test_fixture_existente_retorna_caminho(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "FIXTURES_AAF", tmp_path)

    privada = tmp_path / "dialect_protools.aaf"
    privada.write_bytes(b"aaf")
    assert paths.private_fixture("dialect_protools.aaf") == privada
    assert paths.private_fixture("dialect_protools") == privada

    publica = tmp_path / "publica.aaf"
    publica.write_bytes(b"aaf")
    assert paths.private_fixture("publica.aaf") == publica


def test_is_private_fixture() -> None:
    for privada in paths.PRIVATE_FIXTURES:
        assert paths.is_private_fixture(privada)
        assert paths.is_private_fixture(Path(privada).stem)

    assert not paths.is_private_fixture("TESTE_AFF_PROTOOLS.aaf")
    assert not paths.is_private_fixture("TESTE_AFF_PROTOOLS")
    assert not paths.is_private_fixture("teste_aaf_protools_txt.txt")
