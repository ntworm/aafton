from pathlib import Path

import pytest
from aafton.als_paths import (
    AUDIO_CLIP_FIELDS,
    REQUIRED_PROTOTYPES,
    find_prototype,
)
from aafton.als_xml import load_als

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(
    not TEMPLATE.exists(), reason="Portao B: templates/live12.als ainda nao foi autorado"
)


@pytest.fixture(scope="module")
def root():
    return load_als(TEMPLATE)


@pytest.mark.parametrize("tag", REQUIRED_PROTOTYPES)
def test_template_contem_cada_prototipo(root, tag):
    assert find_prototype(root, tag) is not None, (
        f"o template nao contem nenhum <{tag}>; refaca o template no Live "
        f"incluindo esse elemento em vez de sintetizar o XML"
    )


@pytest.mark.parametrize("nome,caminho", sorted(AUDIO_CLIP_FIELDS.items()))
def test_cada_campo_declarado_existe_no_audio_clip(root, nome, caminho):
    clip = find_prototype(root, "AudioClip")
    assert clip.find(caminho) is not None, (
        f"o campo {nome!r} foi declarado em als_paths.AUDIO_CLIP_FIELDS como "
        f"{caminho!r}, mas nao existe no AudioClip do template. Corrija o "
        f"caminho com base em docs/ALS_XML_MAP.md."
    )


def test_o_clip_do_template_tem_fades_moldados(root):
    """Fades de 1 ms indicam que o template foi salvo sem moldar os fades."""
    clip = find_prototype(root, "AudioClip")
    entrada = clip.find(AUDIO_CLIP_FIELDS["fade_in_length"])
    assert float(entrada.get("Value")) > 0.01, (
        "o fade de entrada do clip do template esta no valor padrao; molde os "
        "fades no Live e salve de novo, senao nao ha prototipo de fade real"
    )
