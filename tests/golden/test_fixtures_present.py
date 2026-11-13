import pytest

from tests.support.paths import FIXTURES_AAF, private_fixture

PRIVADOS = (
    "dialect_resolve.aaf",
    "dialect_premiere.aaf",
    "dialect_protools.aaf",
)
PUBLICOS = (
    "TESTE_AFF_PROTOOLS.aaf",
    "teste_aaf_protools_txt.txt",
)


@pytest.mark.parametrize("nome", PRIVADOS)
def test_fixture_privada_existe(nome):
    caminho = private_fixture(nome)
    assert caminho.stat().st_size > 0


@pytest.mark.parametrize("nome", PUBLICOS)
def test_fixture_publica_existe(nome):
    caminho = FIXTURES_AAF / nome
    if not caminho.is_file():
        pytest.fail(
            f"fixture publica ausente: {caminho}. Deve estar versionada em tests/fixtures/aaf/.",
            pytrace=False,
        )
    assert caminho.stat().st_size > 0
