"""O pertencimento de lista: medido no template, nao nomeado pelo vendor.

O schema do Live declara que um campo e uma lista; ele nao declara quais
classes podem ser itens dela. Quem declara e o template do mesmo build, medido
no XML: a segunda autoridade, com evidencia empirica. Estes testes inspecionam
o template real, nos lugares explicitos, e conferem o registro contra ele.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from aafton.als_schema import (
    BUILD_TRANSLATOR,
    LISTAS_MEDIDAS,
    SCHEMA_SNAPSHOT,
    SchemaLive,
    ler_bytes,
    schema_do_build,
)
from aafton.als_xml import load_als

TEMPLATE = Path("templates/live12.als")

# Onde o template mostra cada lista, e a chave do schema que a declara.
LOCALIZACOES: dict[tuple[str, str], str] = {
    ("AutomationEnvelopes", "Envelopes"): ".//AutomationEnvelopes/Envelopes",
    ("ListAutomation", "Events"): ".//AutomationEnvelope/Automation/Events",
    ("LiveSet", "Tracks"): ".//LiveSet/Tracks",
}

# O caminho, sob a classe raiz, que chega a cada lista medida.
CAMINHOS: dict[tuple[str, str], tuple[str, str]] = {
    ("AutomationEnvelopes", "Envelopes"): (
        "AudioTrack",
        "AutomationEnvelopes/Envelopes/AutomationEnvelope",
    ),
    ("ListAutomation", "Events"): (
        "AudioTrack",
        "AutomationEnvelopes/Envelopes/AutomationEnvelope/Automation/Events/FloatEvent",
    ),
    ("LiveSet", "Tracks"): ("LiveSet", "Tracks/AudioTrack"),
}

# Classes que o schema conhece e que nao sao item medido da lista em que
# aparecem. O nome sozinho nao autoriza: a lista e por lista.
FORA_DA_LISTA = [
    ("AudioTrack", "AutomationEnvelopes/Envelopes/LiveSet/SmpteFormat"),
    ("AudioTrack", "AutomationEnvelopes/Envelopes/AudioClip"),
    ("AudioTrack", "AutomationEnvelopes/Envelopes/GroupTrack"),
    (
        "AudioTrack",
        "AutomationEnvelopes/Envelopes/AutomationEnvelope/Automation/Events/GroupTrack",
    ),
    ("LiveSet", "Tracks/LiveSet"),
]

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture(scope="module")
def schema():
    return schema_do_build()


@pytest.fixture(scope="module")
def template():
    return load_als(TEMPLATE)


def _itens_do_template(template: ET.Element, caminho: str) -> set[str]:
    """As classes de item que o template mostra dentro da lista."""
    achados: set[str] = set()
    for container in template.findall(caminho):
        achados.update(filho.tag for filho in container)
    return achados


def _schema_de_outro_build(tmp_path: Path, translator: str) -> SchemaLive:
    """O schema do snapshot com o TranslatorNumber de outro build."""
    raiz = ET.fromstring(ler_bytes(SCHEMA_SNAPSHOT))
    raiz.set("TranslatorNumber", translator)
    destino = tmp_path / f"{translator}.txt"
    destino.write_bytes(ET.tostring(raiz, encoding="utf-8", xml_declaration=True))
    return SchemaLive.carregar(destino)


def test_o_registro_bate_com_o_template_fixado(template):
    """Cada lista do registro tem no template os itens que o registro afirma."""
    registro = LISTAS_MEDIDAS[BUILD_TRANSLATOR]
    for chave, caminho in LOCALIZACOES.items():
        medido = _itens_do_template(template, caminho)
        assert medido == set(registro[chave]), (
            f"a lista {chave} do build {BUILD_TRANSLATOR} diz {sorted(registro[chave])}, "
            f"e o template mostra {sorted(medido)} em {caminho}"
        )


def test_o_registro_e_do_build_do_schema_e_declara_os_campos(schema):
    """A medicao vale para o build do schema, e o schema declara os campos."""
    assert schema.translator == BUILD_TRANSLATOR
    assert schema.listas_medidas() == LISTAS_MEDIDAS[BUILD_TRANSLATOR]
    for classe, campo in LOCALIZACOES:
        assert schema.classe_do_campo(classe, campo) is not None, (
            f"o schema nao declara o campo {campo!r} na classe {classe!r}"
        )


@pytest.mark.parametrize("chave", sorted(CAMINHOS))
def test_o_resolvedor_aceita_o_item_medido(schema, chave):
    """O item que a medicao prova resolve no schema."""
    raiz, caminho = CAMINHOS[chave]
    resolucao = schema.resolver(caminho, raiz)

    assert resolucao is not None, f"{raiz}/{caminho} nao resolve no schema"
    assert resolucao.classe in LISTAS_MEDIDAS[BUILD_TRANSLATOR][chave]


@pytest.mark.parametrize(("raiz", "caminho"), FORA_DA_LISTA)
def test_classe_conhecida_sem_prova_nao_resolve(schema, raiz, caminho):
    """O schema conhecer o nome nao autoriza: item sem medicao nao resolve.

    None significa que o contrato schema-template nao prova o caminho. Nao
    significa que o Live recuse o caminho.
    """
    assert schema.resolver(caminho, raiz) is None


def test_o_schema_de_outro_build_nao_herda_a_prova_da_lista(schema, tmp_path):
    """A medicao e do build fixado: outro build nao a herda."""
    outro = _schema_de_outro_build(tmp_path, "99999")
    caminho = "AutomationEnvelopes/Envelopes/AutomationEnvelope"

    assert outro.translator == "99999"
    assert outro.listas_medidas() == {}
    assert outro.resolver(caminho, "AudioTrack") is None
    assert schema.resolver(caminho, "AudioTrack") is not None
