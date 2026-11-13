from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from aafton.als_xml import DocumentInvalid, load_als, validate_document


def _doc(tracks_xml: str, next_pointee: int = 200000, extra: str = "") -> ET.Element:
    return ET.fromstring(
        f"""
        <Ableton>
          <LiveSet>
            <NextPointeeId Value="{next_pointee}" />
            <Tracks>{tracks_xml}</Tracks>
            <MainTrack><TrackGroupId Value="-1" />{extra}</MainTrack>
          </LiveSet>
        </Ableton>
        """
    )


AUDIO = '<AudioTrack Id="8"><TrackGroupId Value="-1" /></AudioTrack>'

POS = frozenset(["ClipSlot", "AudioClip", "WarpMarker", "FileRef"])


def test_documento_coerente_passa():
    validate_document(
        _doc(
            '<GroupTrack Id="17"><TrackGroupId Value="-1" /></GroupTrack>'
            '<AudioTrack Id="8"><TrackGroupId Value="17" /></AudioTrack>'
        ),
        POS,
    )


def test_track_group_id_apontando_para_grupo_inexistente_falha():
    with pytest.raises(DocumentInvalid, match="TrackGroupId"):
        validate_document(_doc('<AudioTrack Id="8"><TrackGroupId Value="17" /></AudioTrack>'), POS)


def test_track_group_id_menos_um_e_valido():
    validate_document(_doc(AUDIO), POS)


def test_pointee_id_pendente_falha():
    with pytest.raises(DocumentInvalid, match="PointeeId"):
        validate_document(
            _doc(
                AUDIO,
                extra='<AutomationEnvelopes><Envelopes><AutomationEnvelope Id="0">'
                '<EnvelopeTarget><PointeeId Value="99999" /></EnvelopeTarget>'
                "</AutomationEnvelope></Envelopes></AutomationEnvelopes>",
            ),
            POS,
        )


def test_id_global_duplicado_falha():
    with pytest.raises(DocumentInvalid, match="duplicated"):
        validate_document(
            _doc(
                '<AudioTrack Id="8"><TrackGroupId Value="-1" />'
                '<A Id="150000" /><B Id="150000" /></AudioTrack>'
            ),
            POS,
        )


def test_id_pequeno_repetido_e_permitido():
    """Abaixo de 100 o Id e escopado e se repete de proposito no proprio Live."""
    validate_document(
        _doc('<AudioTrack Id="8"><TrackGroupId Value="-1" /><A Id="1" /><B Id="1" /></AudioTrack>'),
        POS,
    )


def test_indice_posicional_inflado_falha():
    """E este o erro que derruba o Live: um indice de lista virou numero enorme."""
    with pytest.raises(DocumentInvalid, match="positional"):
        validate_document(
            _doc(
                '<AudioTrack Id="8"><TrackGroupId Value="-1" /><ClipSlotList>'
                '<ClipSlot Id="0" /><ClipSlot Id="1" /></ClipSlotList></AudioTrack>'
                '<AudioTrack Id="9"><TrackGroupId Value="-1" /><ClipSlotList>'
                '<ClipSlot Id="100017" /></ClipSlotList></AudioTrack>'
            ),
            POS,
        )


def test_next_pointee_id_menor_que_um_id_existente_falha():
    with pytest.raises(DocumentInvalid, match="NextPointeeId"):
        validate_document(
            _doc(
                '<AudioTrack Id="8"><TrackGroupId Value="-1" /><A Id="300000" /></AudioTrack>',
                next_pointee=1000,
            ),
            POS,
        )


def test_o_template_de_verdade_passa_na_validacao():
    caminho = Path("templates/live12.als")
    if not caminho.exists():
        pytest.skip("Portao B: template ainda nao autorado")
    validate_document(load_als(caminho), POS)


def test_duas_tracks_com_mesmo_id_reprova():
    xml = """
    <Ableton>
      <LiveSet>
        <Tracks>
          <AudioTrack Id="2"/>
          <ReturnTrack Id="2"/>
        </Tracks>
      </LiveSet>
    </Ableton>
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)

    from aafton.als_xml import DocumentInvalid

    with pytest.raises(DocumentInvalid, match="Non-unique list Ids"):
        validate_document(root, frozenset())


def test_validate_document_aceita_blocos_contiguos():
    validate_document(
        _doc("""
        <GroupTrack Id="10" />
        <AudioTrack Id="11"><TrackGroupId Value="10" /></AudioTrack>
        <AudioTrack Id="12"><TrackGroupId Value="10" /></AudioTrack>
        <GroupTrack Id="20" />
        <AudioTrack Id="21"><TrackGroupId Value="20" /></AudioTrack>
        """),
        POS,
    )


def test_validate_document_reprova_track_solta_interrompendo_bloco():
    with pytest.raises(DocumentInvalid, match="after the group block ended"):
        validate_document(
            _doc("""
            <GroupTrack Id="10" />
            <AudioTrack Id="11"><TrackGroupId Value="10" /></AudioTrack>
            <AudioTrack Id="12"><TrackGroupId Value="-1" /></AudioTrack>
            <AudioTrack Id="13"><TrackGroupId Value="10" /></AudioTrack>
            """),
            POS,
        )


def test_validate_document_reprova_membro_antes_do_grupo():
    with pytest.raises(DocumentInvalid, match="before the matching GroupTrack"):
        validate_document(
            _doc("""
            <AudioTrack Id="11"><TrackGroupId Value="10" /></AudioTrack>
            <GroupTrack Id="10" />
            """),
            POS,
        )
