"""Testes unitarios da tabela de dialetos, regras de partes e resolver_letras."""

from dataclasses import FrozenInstanceError, dataclass, fields
from pathlib import Path

import pytest

from aafton.bwf_meta import com_ixml_do_bext, read_bwf
from aafton.gravador_dialetos import Partes, reconhecer, resolver_letras
from tests.support.gravadores import (
    bext,
    descricao,
    sound_devices,
    sound_devices_circulado,
    sound_devices_duplicata_letra,
    sound_devices_duplicata_mais,
    sound_devices_pedacos,
    tascam_dr40x,
    tascam_dr701d_par,
    tascam_dr701d_poly,
    tascam_x8_faixa,
    tascam_x8_mix,
    tascam_x8_par,
    zoom_32fp,
    zoom_pedaco,
    zoom_poly,
    zoom_tl_r,
    zoom_tr,
    zoom_tr_par,
)
from tests.unit.test_som_direto_leitor import ixml, wav


@dataclass
class _StubArquivo:
    path: Path
    partes: Partes | None


def _ler(path: Path) -> Partes | None:
    return reconhecer(path, com_ixml_do_bext(read_bwf(path)))


def test_partes_congelada_e_campos_exatos():
    assert [f.name for f in fields(Partes)] == [
        "dialeto",
        "identidade",
        "pedaco",
        "marca_de_pedaco",
        "letra",
        "canais_do_nome",
        "circulado",
        "alternativa",
        "confirmado",
    ]
    partes = Partes("zoom", ("s1", "001"), 1, False, None, None, False, None, True)
    with pytest.raises(FrozenInstanceError):
        partes.pedaco = 2  # type: ignore[misc]


def test_tascam_dr40_mono_e_pares(tmp_path):
    mono = wav(tmp_path / "TASCAM_0112S1.wav", 1, 100)
    par12 = tascam_dr40x(tmp_path, par="12")
    par34 = tascam_dr40x(tmp_path, par="34")

    assert _ler(mono) == Partes(
        dialeto="tascam_dr40",
        identidade=("tascam", "0112"),
        pedaco=1,
        marca_de_pedaco=False,
        letra=None,
        canais_do_nome=(1,),
        circulado=False,
        alternativa=None,
        confirmado=True,
    )
    p12 = _ler(par12)
    p34 = _ler(par34)
    assert p12 is not None and p12.canais_do_nome == (1, 2)
    assert p12.identidade == ("180113", "0003")
    assert p34 is not None and p34.canais_do_nome == (3, 4)


def test_tascam_x8_e_dr701d_partes(tmp_path):
    faixa = tascam_x8_faixa(tmp_path)
    par = tascam_x8_par(tmp_path)
    mix = tascam_x8_mix(tmp_path)
    fl = tascam_x8_faixa(tmp_path, duplo_float=True)
    div_a = tascam_x8_par(tmp_path, num="0003", div="A")
    div_b = tascam_x8_par(tmp_path, num="0003", div="B")
    div_01 = tascam_x8_par(tmp_path, num="0004", div="01")
    dr701_par = tascam_dr701d_par(tmp_path, base="141001")
    dr701_poly = tascam_dr701d_poly(tmp_path)

    assert _ler(faixa) == Partes(
        "tascam_x8", ("tascam", "0001"), 1, False, None, (1,), False, None, True
    )
    assert _ler(par) == Partes(
        "tascam_x8", ("tascam", "0001"), 1, False, None, (1, 2), False, None, True
    )
    assert _ler(mix) == Partes(
        "tascam_x8", ("tascam", "0001"), 1, False, None, None, False, "mix", True
    )
    assert _ler(fl) == Partes(
        "tascam_x8", ("tascam", "0001"), 1, False, None, (1,), False, "float", True
    )
    assert _ler(div_a) == Partes(
        "tascam_x8", ("tascam", "0003"), 1, True, None, (1, 2), False, None, True
    )
    assert _ler(div_b) == Partes(
        "tascam_x8", ("tascam", "0003"), 2, True, None, (1, 2), False, None, True
    )
    assert _ler(div_01) == Partes(
        "tascam_x8", ("tascam", "0004"), 1, True, None, (1, 2), False, None, True
    )
    assert _ler(dr701_par) == Partes(
        "tascam_x8", ("141001", "0002"), 1, False, None, (1, 2), False, None, True
    )
    assert _ler(dr701_poly) == Partes(
        "tascam_x8", ("tascam", "0002"), 1, False, None, (1, 2, 3, 4, 5, 6), False, None, True
    )


def test_zoom_todos_os_formatos(tmp_path):
    poly = zoom_poly(tmp_path)
    circ = zoom_poly(tmp_path, take="002", circulado=True)
    tr1 = zoom_tr(tmp_path, com_z_bext=False)
    tr_par = zoom_tr_par(tmp_path)
    tl_r = zoom_tl_r(tmp_path)
    fp = zoom_32fp(tmp_path)
    ped2 = zoom_pedaco(tmp_path, pedaco=2)

    assert _ler(poly) == Partes(
        "zoom", ("scene001", "001"), 1, False, None, None, False, None, True
    )
    assert _ler(circ) == Partes("zoom", ("scene001", "002"), 1, False, None, None, True, None, True)
    assert _ler(tr1) == Partes("zoom", ("scene001", "001"), 1, False, None, (1,), False, None, True)
    assert _ler(tr_par) == Partes(
        "zoom", ("scene001", "001"), 1, False, None, (1, 2), False, None, True
    )
    assert _ler(tl_r) == Partes(
        "zoom", ("scene001", "001"), 1, False, None, None, False, "mix", True
    )
    assert _ler(fp) == Partes(
        "zoom", ("scene001", "001"), 1, False, None, None, False, "float", True
    )
    assert _ler(ped2) == Partes("zoom", ("scene001", "001"), 2, True, None, None, False, None, True)


def test_sound_devices_com_ixml_e_regras_de_identidade(tmp_path):
    base = sound_devices(tmp_path)
    circ = sound_devices_circulado(tmp_path)
    mais = sound_devices_duplicata_mais(tmp_path)
    letra_a = sound_devices_duplicata_letra(tmp_path, letra="A")
    proj = sound_devices(tmp_path, projeto="Doc", designador="-")
    hifen = sound_devices(tmp_path, cena="12", take="001", designador="-")

    assert _ler(base) == Partes(
        "sound_devices", ("", "", "s01", "t", "01", ""), 1, False, None, None, False, None, True
    )
    assert _ler(circ) == Partes(
        "sound_devices", ("", "", "s01", "t", "01", ""), 1, False, None, None, True, None, True
    )
    assert _ler(mais) == Partes(
        "sound_devices", ("+", "", "s01", "t", "01", ""), 1, False, None, None, False, None, True
    )
    assert _ler(letra_a) == Partes(
        "sound_devices", ("", "", "s01", "t", "01", "a"), 1, False, "A", None, False, None, True
    )
    assert _ler(proj) == Partes(
        "sound_devices", ("", "doc", "s01", "-", "01", ""), 1, False, None, None, False, None, True
    )
    # 12-001 sem z-bext e fora de .TAKE vai para sound_devices, nao zoom.
    assert _ler(hifen) == Partes(
        "sound_devices", ("", "", "12", "-", "001", ""), 1, False, None, None, False, None, True
    )


def test_sound_devices_bext_s_e_igualdade_numerica_ou_casefold(tmp_path):
    com_s = sound_devices(tmp_path, com_ixml=False, bext_s=True)
    partes_s = _ler(com_s)
    assert partes_s == Partes(
        "sound_devices", ("", "", "s01", "t", "01", ""), 1, False, None, None, False, None, False
    )

    desc_num = descricao(sSCENE="012", sTAKE="1")
    num_eq = wav(tmp_path / "12-001.wav", 1, 100, extras=((b"bext", bext(desc_num)),))
    partes_num = _ler(num_eq)
    assert partes_num is not None
    assert partes_num.dialeto == "sound_devices"
    assert partes_num.confirmado is False

    meta = ixml("s01", "1", "fam", [(1, "BOOM")])
    caixa = wav(tmp_path / "S01T001.wav", 1, 100, extras=((b"iXML", meta),))
    partes_caixa = _ler(caixa)
    assert partes_caixa is not None
    assert partes_caixa.confirmado is True


def test_nomes_e_condicoes_que_nao_podem_bater(tmp_path):
    assert _ler(wav(tmp_path / "A.wav", 1, 100)) is None
    assert _ler(wav(tmp_path / "241127_0001.wav", 2, 100)) is None

    tascam_ixml = wav(
        tmp_path / "TASCAM_0112S1.wav",
        1,
        100,
        extras=((b"iXML", ixml("1", "1", "fam", [(1, "M1")])),),
    )
    assert _ler(tascam_ixml) is None

    sd_sem_meta = wav(tmp_path / "S01T01.wav", 1, 100)
    assert _ler(sd_sem_meta) is None

    boom_diff = wav(
        tmp_path / "BOOM-01.wav",
        1,
        100,
        extras=((b"bext", bext(descricao(sSCENE="OUTRA", sTAKE="01"))),),
    )
    assert _ler(boom_diff) is None

    boom_take_diff = wav(
        tmp_path / "take_diff" / "BOOM-01.wav",
        1,
        100,
        extras=((b"bext", bext(descricao(sSCENE="BOOM", sTAKE="02"))),),
    )
    assert _ler(boom_take_diff) is None

    sd_ixml_diverge_com_s = wav(
        tmp_path / "mismatch" / "S01T01.wav",
        1,
        100,
        extras=(
            (b"bext", bext(descricao(sSCENE="S01", sTAKE="01"))),
            (b"iXML", ixml("OUTRA", "99", "fam", [(1, "M1")])),
        ),
    )
    assert _ler(sd_ixml_diverge_com_s) is None

    sd_zero_nao_digito = wav(
        tmp_path / "naodigito" / "S01T01.wav",
        1,
        100,
        extras=((b"iXML", ixml("S1", "01", "fam", [(1, "M1")])),),
    )
    assert _ler(sd_zero_nao_digito) is None

    tascam_com_bext_z = wav(
        tmp_path / "TASCAM_0001_1.WAV",
        1,
        100,
        extras=((b"bext", bext(descricao(zSCENE="1", zTAKE="1"))),),
    )
    assert _ler(tascam_com_bext_z) is None

    sd_com_bext_z = wav(
        tmp_path / "zbext" / "S01T01.wav",
        1,
        100,
        extras=(
            (b"bext", bext(descricao(zSCENE="S01", zTAKE="01"))),
            (b"iXML", ixml("S01", "01", "fam", [(1, "M1")])),
        ),
    )
    assert _ler(sd_com_bext_z) is None


def test_resolver_letras_junta_pedacos_e_preserva_duplicatas(tmp_path):
    pasta = tmp_path / "dia"
    peds = sound_devices_pedacos(pasta, cena="S02", take="01", letras=("A", "B", "C"))
    sozinha = sound_devices_duplicata_letra(pasta, cena="S01", take="01", letra="A")
    s03 = [
        sound_devices(pasta, cena="S03", take="01"),
        sound_devices_duplicata_letra(pasta, cena="S03", take="01", letra="A"),
        sound_devices_duplicata_letra(pasta, cena="S03", take="01", letra="B"),
    ]
    outra_a = sound_devices_duplicata_letra(tmp_path / "p1", cena="S04", take="01", letra="A")
    outra_b = sound_devices_duplicata_letra(tmp_path / "p2", cena="S04", take="01", letra="B")
    zoom_arq = zoom_poly(pasta)
    nulo = _StubArquivo(pasta / "A.wav", None)

    caminhos = [*peds, sozinha, *s03, outra_a, outra_b, zoom_arq]
    stubs = [_StubArquivo(p, _ler(p)) for p in caminhos] + [nulo]
    resolver_letras(stubs)

    a2, b2, c2, s1_a, s3_base, s3_a, s3_b, p1_a, p2_b, z_stub, n_stub = stubs
    assert a2.partes is not None and b2.partes is not None and c2.partes is not None
    assert a2.partes.identidade == ("", "", "s02", "t", "01", "")
    assert b2.partes.identidade == a2.partes.identidade
    assert c2.partes.identidade == a2.partes.identidade
    assert (a2.partes.pedaco, a2.partes.marca_de_pedaco) == (1, False)
    assert (b2.partes.pedaco, b2.partes.marca_de_pedaco) == (2, True)
    assert (c2.partes.pedaco, c2.partes.marca_de_pedaco) == (3, True)

    assert s1_a.partes is not None
    assert s1_a.partes.identidade == ("", "", "s01", "t", "01", "a")
    assert (s1_a.partes.pedaco, s1_a.partes.marca_de_pedaco) == (1, False)

    assert s3_base.partes is not None and s3_a.partes is not None and s3_b.partes is not None
    assert s3_base.partes.identidade[-1] == ""
    assert s3_a.partes.identidade[-1] == "a"
    assert s3_b.partes.identidade[-1] == "b"
    assert (s3_b.partes.pedaco, s3_b.partes.marca_de_pedaco) == (1, False)

    assert p1_a.partes is not None and p1_a.partes.identidade[-1] == "a"
    assert p2_b.partes is not None and p2_b.partes.identidade[-1] == "b"
    assert z_stub.partes is not None and z_stub.partes.dialeto == "zoom"
    assert n_stub.partes is None
