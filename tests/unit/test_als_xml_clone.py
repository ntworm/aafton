import xml.etree.ElementTree as ET

import pytest
from aafton.als_xml import (
    GLOBAL_ID_FLOOR,
    IdAllocator,
    classify_id_tags,
    clone,
    collect_ids,
    renumber_siblings,
    set_value,
    unique_ids,
)


def _arvore():
    return ET.fromstring(
        """
        <Ableton>
          <LiveSet>
            <NextPointeeId Value="20000" />
            <Tracks>
              <AudioTrack Id="8">
                <TrackGroupId Value="17" />
                <Name><EffectiveName Value="Track 1" /></Name>
                <DeviceChain>
                  <Mixer>
                    <Sends>
                      <TrackSendHolder Id="0"><Send Id="8642" /></TrackSendHolder>
                    </Sends>
                    <Volume Id="8643" />
                    <Pan Id="1" />
                  </Mixer>
                  <MainSequencer>
                    <ClipSlotList>
                      <ClipSlot Id="0" /><ClipSlot Id="1" /><ClipSlot Id="2" />
                    </ClipSlotList>
                    <Sample><ArrangerAutomation><Events>
                      <AudioClip Id="0"><Name Value="a" /></AudioClip>
                    </Events></ArrangerAutomation></Sample>
                  </MainSequencer>
                </DeviceChain>
              </AudioTrack>
              <AudioTrack Id="15">
                <TrackGroupId Value="-1" />
                <DeviceChain><Mixer><Pan Id="1" /></Mixer></DeviceChain>
              </AudioTrack>
            </Tracks>
          </LiveSet>
        </Ableton>
        """
    )


def _clone(root, allocator=None):
    posicionais, _ = classify_id_tags(root)
    return clone(
        root.find(".//AudioTrack"),
        allocator or IdAllocator.from_root(root),
        posicionais,
        unique_ids(root),
    )


def test_collect_ids_acha_todos_os_atributos_id():
    assert collect_ids(_arvore()) == {0, 1, 2, 8, 15, 8642, 8643}


def test_classify_separa_posicional_de_ponteiro():
    posicionais, ponteiros = classify_id_tags(_arvore())
    assert {"ClipSlot", "TrackSendHolder", "AudioClip"} <= posicionais
    assert {"Send", "Volume"} <= ponteiros


def test_unique_ids_ignora_valor_que_se_repete():
    """Pan Id=1 aparece nas duas tracks: e escopado, nao identidade global."""
    unicos = unique_ids(_arvore())
    assert 1 not in unicos
    assert 8642 in unicos and 8643 in unicos


def test_clone_remapeia_ponteiro_unico():
    root = _arvore()
    novos = collect_ids(_clone(root))
    assert 8642 not in novos and 8643 not in novos


def test_clone_preserva_id_escopado_que_ja_se_repetia():
    copia = _clone(_arvore())
    assert copia.find(".//Pan").get("Id") == "1"


def test_clip_slot_nunca_e_renumerado_pelo_clone():
    """Um ClipSlot Id e o indice da cena. Joga-lo para 100000 derruba o Live."""
    copia = _clone(_arvore())
    assert [int(n.get("Id")) for n in copia.iter("ClipSlot")] == [0, 1, 2]


def test_track_send_holder_nunca_e_renumerado_pelo_clone():
    copia = _clone(_arvore())
    assert [n.get("Id") for n in copia.iter("TrackSendHolder")] == ["0"]


def test_dois_clones_nao_compartilham_ponteiro():
    root = _arvore()
    allocator = IdAllocator.from_root(root)
    a, b = _clone(root, allocator), _clone(root, allocator)
    posicionais, _ = classify_id_tags(root)

    def ponteiros(node):
        return {
            int(n.get("Id"))
            for n in node.iter()
            if (n.get("Id") or "").isdigit()
            and n.tag not in posicionais
            and int(n.get("Id")) >= GLOBAL_ID_FLOOR
        }

    assert ponteiros(a) and ponteiros(b)
    assert ponteiros(a).isdisjoint(ponteiros(b))


def test_clone_nao_altera_o_prototipo():
    root = _arvore()
    prototipo = root.find(".//AudioTrack")
    antes = collect_ids(prototipo)
    _clone(root)
    assert collect_ids(prototipo) == antes


def test_allocator_comeca_acima_do_maior_id_e_do_next_pointee():
    assert IdAllocator.from_root(_arvore()).next() >= 20000


def test_allocator_nunca_repete():
    allocator = IdAllocator(start=100000)
    assert len({allocator.next() for _ in range(1000)}) == 1000


def test_allocator_grava_next_pointee_id_maior_que_todos():
    root = _arvore()
    allocator = IdAllocator.from_root(root)
    ultimo = 0
    for _ in range(5):
        ultimo = allocator.next()
    allocator.write_next_pointee_id(root)
    assert int(root.find(".//NextPointeeId").get("Value")) > ultimo


def test_renumber_siblings_poe_a_lista_em_zero_a_n_menos_um():
    container = ET.fromstring(
        '<Events><AudioClip Id="0"/><AudioClip Id="0"/><AudioClip Id="0"/></Events>'
    )
    renumber_siblings(container, "AudioClip")
    assert [n.get("Id") for n in container] == ["0", "1", "2"]


def test_renumber_siblings_ignora_outras_tags():
    container = ET.fromstring('<Events><AudioClip Id="7"/><MidiClip Id="9"/></Events>')
    renumber_siblings(container, "AudioClip")
    assert container.find("AudioClip").get("Id") == "0"
    assert container.find("MidiClip").get("Id") == "9"


def test_set_value_escreve_no_atributo_value():
    root = _arvore()
    set_value(root.find(".//AudioTrack"), "Name/EffectiveName", "DIALOGO 1")
    assert root.find(".//EffectiveName").get("Value") == "DIALOGO 1"


def test_set_value_falha_em_caminho_inexistente():
    with pytest.raises(KeyError, match="Nao/Existe"):
        set_value(_arvore().find(".//AudioTrack"), "Nao/Existe", "x")


def test_set_value_formata_float_com_precisao_total():
    node = ET.fromstring('<AudioClip><CurrentStart Value="0" /></AudioClip>')
    set_value(node, "CurrentStart", 3600.0)
    assert node.find("CurrentStart").get("Value") == "3600"
    set_value(node, "CurrentStart", 1.0001458333333333)
    assert node.find("CurrentStart").get("Value") == "1.0001458333333333"


def test_set_value_escreve_booleano_em_minusculas():
    node = ET.fromstring('<AudioClip><IsWarped Value="true" /></AudioClip>')
    set_value(node, "IsWarped", False)
    assert node.find("IsWarped").get("Value") == "false"
