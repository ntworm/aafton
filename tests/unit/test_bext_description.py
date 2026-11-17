"""Leitura de bext Description e fallback do bwf_meta quando falta iXML."""

import pytest

from aafton.bext_description import campos
from aafton.bwf_meta import com_ixml_do_bext, read_bwf
from tests.support.gravadores import bext, zoom_poly
from tests.unit.test_som_direto_leitor import wav


@pytest.mark.parametrize("sep", ["\r\n", "\n", "\r"])
def test_campos_separa_linhas_com_prefixo_z(sep):
    texto = sep.join(["zSCENE=12A", "zTAKE=003", "zNote=boom=alto", "zTRK1=Boom"])
    prefixo, mapa = campos(texto)
    assert prefixo == "z"
    assert mapa == {"SCENE": "12A", "TAKE": "003", "NOTE": "boom=alto", "TRK1": "Boom"}


def test_campos_prefixo_s_e_chave_so_com_take():
    prefixo, mapa = campos("sTAKE=007\r\nsTAPE=250506\r\nSPEED=24\r\n")
    assert prefixo == "s"
    assert mapa == {"TAKE": "007", "TAPE": "250506", "SPEED": "24"}


def test_campos_sem_prefixo_ignora_linhas_sem_igual():
    assert campos(None) == (None, {})
    assert campos("") == (None, {})
    assert campos("MixPre") == (None, {})
    assert campos("SCENE=1\n=ignorar\nzNOTE= ") == (None, {"SCENE": "1", "ZNOTE": ""})


def test_bwf_meta_le_original_filename_e_nota_opcional(tmp_path):
    com_orig = zoom_poly(tmp_path / "a", original_filename="ZOOM0001.WAV")
    info_com = read_bwf(com_orig)
    assert info_com.ixml is not None
    assert info_com.ixml.source == "ixml"
    assert info_com.ixml.original_filename == "ZOOM0001.WAV"
    assert "iXML without HISTORY/ORIGINAL_FILENAME" not in info_com.notes

    sem_orig = zoom_poly(tmp_path / "b")
    info_sem = read_bwf(sem_orig)
    assert info_sem.ixml is not None
    assert info_sem.ixml.source == "ixml"
    assert info_sem.ixml.original_filename is None
    assert "iXML without HISTORY/ORIGINAL_FILENAME" not in info_sem.notes


def test_bwf_meta_fallback_bext_z_sem_ixml(tmp_path):
    desc = "zSCENE=Scene001\r\nzTAKE=004\r\nzTAPE=R1\r\nzCIRCLED=TRUE\r\nzNOTE=vento\r\n"
    arq = wav(tmp_path / "z.wav", 2, 100, extras=((b"bext", bext(desc)),))
    info = com_ixml_do_bext(read_bwf(arq))
    assert info.ixml is not None
    assert info.ixml.source == "bext"
    assert (info.ixml.scene, info.ixml.take, info.ixml.tape) == ("Scene001", "004", "R1")
    assert info.ixml.circled is True
    assert info.ixml.note == "vento"
    assert info.ixml.family_uid is None
    assert info.ixml.track_list is None
    assert info.ixml.timecode_rate is None
    assert info.ixml.master_speed is None
    assert info.ixml.original_filename is None
    assert "no iXML chunk" in info.notes
    assert "iXML read from the bext Description" in info.notes
    assert "unconfirmed Sound Devices bext keys" not in info.notes


def test_bwf_meta_fallback_bext_s_emite_duas_notas(tmp_path):
    desc = "sSCENE=S01\r\nsTAKE=02\r\nsTAPE=D1\r\nsCIRCLED=FALSE\r\nsNOTE=ok\r\n"
    arq = wav(tmp_path / "s.wav", 1, 100, extras=((b"bext", bext(desc)),))
    info = com_ixml_do_bext(read_bwf(arq))
    assert info.ixml is not None
    assert info.ixml.source == "bext"
    assert (info.ixml.scene, info.ixml.take, info.ixml.tape) == ("S01", "02", "D1")
    assert info.ixml.circled is False
    assert info.ixml.note == "ok"
    assert "iXML read from the bext Description" in info.notes
    assert "unconfirmed Sound Devices bext keys" in info.notes


@pytest.mark.parametrize(
    ("payload", "nota_recusa"),
    [
        (b"<BWFXML><SCENE>12", "malformed iXML"),
        (b'<!DOCTYPE x [<!ENTITY a "b">]><BWFXML/>', "iXML with a DOCTYPE ignored"),
        (b"<OUTRO><SCENE>1</SCENE></OUTRO>", "iXML root is 'OUTRO', not BWFXML"),
    ],
)
def test_bwf_meta_fallback_com_ixml_ilegivel(tmp_path, payload, nota_recusa):
    desc = "zSCENE=10\nzTAKE=001\n"
    arq = wav(tmp_path / "bad.wav", 1, 100, extras=((b"bext", bext(desc)), (b"iXML", payload)))
    info = com_ixml_do_bext(read_bwf(arq))
    assert info.ixml is not None
    assert info.ixml.source == "bext"
    assert (info.ixml.scene, info.ixml.take) == ("10", "001")
    assert any(nota_recusa in nota for nota in info.notes)
    assert "iXML read from the bext Description" in info.notes


def test_bwf_meta_sem_prefixo_nao_ativa_fallback(tmp_path):
    arq = wav(tmp_path / "plain.wav", 1, 100, extras=((b"bext", bext("MixPre")),))
    info = com_ixml_do_bext(read_bwf(arq))
    assert info.ixml is None
    assert "iXML read from the bext Description" not in info.notes
