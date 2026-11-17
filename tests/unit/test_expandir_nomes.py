"""Casamento por nome normalizado, ORIGINAL_FILENAME e fallback zSCENE/zTAKE."""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.bwf_meta import BwfInfo, Ixml, com_ixml_do_bext, read_bwf
from aafton.expandir import expand_production_channels, planejar, resolve_for_inspect
from aafton.expandir_casamento import NOTA_SEM_ORIGINAL, mesmo_take, original_do_arquivo
from aafton.expandir_nomes import nome_de_casamento, nomes_do_clip, nomes_do_original
from aafton.ir import ClipIR
from aafton.report import Report
from tests.aaf_factory import build_calibration_aaf
from tests.support.gravadores import bext, descricao
from tests.unit.test_expandir import FRAMES, canais_16, clip, gravar
from tests.unit.test_som_direto_leitor import RATE, ixml

SEP = "\u00b7"
TC = 10 * 3600 * RATE


def _expandir(tmp_path: Path, gravador: Path, midia: Path, nome_clip: str, copia: Path):
    tracks = [{"name": "Audio 1", "clips": [clip(copia, nome_clip, 0, 2000)]}]
    aaf = build_calibration_aaf(tmp_path / "editor.aaf", tracks, frames=20000)
    session = read_aaf(aaf, Report(strict=False))
    resolve_for_inspect(session, [midia], Report(strict=False))
    report = Report(strict=False)
    plano = expand_production_channels(session, gravador, tmp_path / "monos", report)
    return session, plano, report


def _ixml_com_original_filename(
    scene: str,
    take: str,
    entradas: list[tuple[int, str]],
    original_filename: str,
) -> bytes:
    meta = ixml(scene, take, "fam", entradas)
    bloco = f"<HISTORY><ORIGINAL_FILENAME>{original_filename}</ORIGINAL_FILENAME></HISTORY>"
    return meta.replace(b"</BWFXML>", bloco.encode("ascii") + b"</BWFXML>")


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("S01T01.L", "s01t01"),
        ("S01T01.L.wav", "s01t01"),
        ("S01T01-R.WAV", "s01t01"),
        ("name.WAV_L", "name"),
        ("@S01T01.wav", "s01t01"),
        ("S01T01A.wav", "s01t01a"),
        ("+S01T01.wav", "+s01t01"),
        ("@+S01T01.wav", "+s01t01"),
        ("@@S01T01.wav", "@s01t01"),
        ("S01T01.L_R.wav", "s01t01.l"),
    ],
)
def test_nome_de_casamento_regras_da_tabela(entrada, esperado):
    assert nome_de_casamento(entrada) == esperado


def test_nomes_do_original_e_do_clip_usam_apenas_ixml_real_e_nao_repetem(tmp_path):
    caminho = tmp_path / "S01T01.wav"
    info_real = BwfInfo(
        path=caminho,
        channels=2,
        sample_rate=RATE,
        bits=16,
        frames=FRAMES,
        ixml=Ixml(original_filename="S01T01.WAV", source="ixml"),
    )
    assert nomes_do_original(caminho, info_real) == ("s01t01",)

    renomeado = tmp_path / "renomeado.wav"
    info_renomeado = BwfInfo(
        path=renomeado,
        channels=2,
        sample_rate=RATE,
        bits=16,
        frames=FRAMES,
        ixml=Ixml(original_filename="Scene001-001.WAV", source="ixml"),
    )
    assert nomes_do_original(renomeado, info_renomeado) == ("renomeado", "scene001-001")

    info_bext = BwfInfo(
        path=caminho,
        channels=2,
        sample_rate=RATE,
        bits=16,
        frames=FRAMES,
        ixml=Ixml(original_filename="OUTRO.wav", source="bext"),
    )
    assert nomes_do_original(caminho, info_bext) == ("s01t01",)

    c = ClipIR(
        id="c1",
        track_id="t1",
        channel_index=0,
        event_index=0,
        name="corte.L",
        media_id="m1",
        timeline_start_samples=0,
        timeline_end_samples=100,
    )
    assert nomes_do_clip(c, info_real) == ("corte", "s01t01")
    assert nomes_do_clip(c, info_bext) == ("corte",)


@pytest.mark.parametrize("nome_clip", ["S01T01.L", "S01T01.L.wav"])
def test_clips_l_e_l_wav_casam_com_original_sem_ixml(tmp_path, nome_clip):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("s01t01", 2)
    orig = gravar(gravador / "S01T01.wav", canais)
    copia = gravar(midia / "copia.wav", [canais[0]])
    session, plano, report = _expandir(tmp_path, gravador, midia, nome_clip, copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} Ch 2"]
    assert plano.takes[orig].usadas == {("Ch", 1)}
    assert plano.takes[orig].faltando == [("Ch", 2)]
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


def test_clip_sem_arroba_casa_com_original_circulado(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("circ", 2)
    orig = gravar(gravador / "@S01T01.wav", canais)
    copia = gravar(midia / "copia.wav", [canais[0]])
    session, plano, report = _expandir(tmp_path, gravador, midia, "S01T01", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} Ch 2"]
    assert plano.takes[orig].usadas == {("Ch", 1)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


@pytest.mark.parametrize("nome_original", ["S01T01A.wav", "+S01T01.wav"])
def test_clip_nao_casa_com_duplicata_a_nem_mais(tmp_path, nome_original):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("dup", 2)
    orig_path = gravar(gravador / nome_original, canais)
    copia = gravar(midia / "copia.wav", [canais[0]])
    session, plano, report = _expandir(tmp_path, gravador, midia, "S01T01", copia)
    assert [t.name for t in session.tracks] == ["Audio 1"]
    assert plano.takes == {}
    notas = [i.message for i in report.issues if i.code == "EXPANSION_NOTE"]
    assert any(NOTA_SEM_ORIGINAL in nota for nota in notas)

    orig = original_do_arquivo(orig_path, com_ixml_do_bext(read_bwf(orig_path)))
    assert not mesmo_take(session.tracks[0].clips[0], com_ixml_do_bext(read_bwf(copia)), orig)


def test_original_renomeado_casa_pelo_original_filename(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("ren", 2)
    meta = _ixml_com_original_filename("1", "1", [(3, "BOOM"), (4, "LAP")], "S01T01.wav")
    orig = gravar(
        gravador / "tomada_renomeada.wav",
        canais,
        extras=((b"bext", bext(time_reference=TC)), (b"iXML", meta)),
    )
    copia = gravar(midia / "copia.wav", [canais[0]])
    session, plano, report = _expandir(tmp_path, gravador, midia, "S01T01.L", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} In 4 {SEP} LAP"]
    assert plano.takes[orig].usadas == {("In", 3)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


def test_midia_do_clip_com_original_filename_casa_com_original(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("clipren", 2)
    orig = gravar(gravador / "S01T01.wav", canais)
    bloco = "<HISTORY><ORIGINAL_FILENAME>S01T01.wav</ORIGINAL_FILENAME></HISTORY>"
    meta = f"<BWFXML>{bloco}</BWFXML>".encode("ascii")
    copia = gravar(midia / "copia.wav", [canais[0]], extras=((b"iXML", meta),))
    session, plano, report = _expandir(tmp_path, gravador, midia, "Dialogo_editado", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} Ch 2"]
    assert plano.takes[orig].usadas == {("Ch", 1)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


def test_original_com_original_filename_igual_ao_proprio_nome_nao_duplica(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("igual", 2)
    meta = _ixml_com_original_filename("1", "1", [(3, "BOOM"), (4, "LAP")], "S01T01.wav")
    orig = gravar(
        gravador / "S01T01.wav",
        canais,
        extras=((b"bext", bext(time_reference=TC)), (b"iXML", meta)),
    )
    copia = gravar(midia / "copia.wav", [canais[0]])
    session, plano, report = _expandir(tmp_path, gravador, midia, "S01T01.L", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} In 4 {SEP} LAP"]
    assert plano.takes[orig].usadas == {("In", 3)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


def test_candidatos_mesmo_original_por_dois_nomes_nao_duplica(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("ident", 2)
    meta_orig = _ixml_com_original_filename("1", "1", [(3, "BOOM"), (4, "LAP")], "S01T01.wav")
    orig = gravar(
        gravador / "renomeado.wav",
        canais,
        extras=((b"bext", bext(time_reference=TC)), (b"iXML", meta_orig)),
    )
    bloco = "<HISTORY><ORIGINAL_FILENAME>S01T01.wav</ORIGINAL_FILENAME></HISTORY>"
    meta_clip = f"<BWFXML>{bloco}</BWFXML>".encode("ascii")
    copia = gravar(midia / "copia.wav", [canais[0]], extras=((b"iXML", meta_clip),))
    session, plano, report = _expandir(tmp_path, gravador, midia, "renomeado.wav", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} In 4 {SEP} LAP"]
    assert plano.takes[orig].usadas == {("In", 3)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]


def test_midia_sem_ixml_com_bext_zscene_ztake_casa_por_chave(tmp_path):
    gravador, midia = tmp_path / "som", tmp_path / "midia"
    canais = canais_16("zoom_z", 2)
    desc = descricao(zSCENE="Scene001", zTAKE="003")
    orig = gravar(
        gravador / "orig_zoom.wav",
        canais,
        extras=((b"bext", bext(desc, time_reference=TC)),),
    )
    copia = gravar(
        midia / "copia.wav",
        [canais[0]],
        extras=((b"bext", bext(desc, time_reference=TC)),),
    )
    session, plano, report = _expandir(tmp_path, gravador, midia, "corte_sem_nome_igual", copia)
    assert [t.name for t in session.tracks] == ["Audio 1", f"Audio 1 {SEP} Ch 2"]
    assert plano.takes[orig].usadas == {("Ch", 1)}
    assert not [i for i in report.issues if i.code == "EXPANSION_NOTE"]

    orig_real = gravar(
        tmp_path / "som_real" / "Scene001-003.wav",
        canais,
        extras=(
            (b"bext", bext(time_reference=TC)),
            (b"iXML", ixml("Scene001", "003", "fam", [(1, "BOOM"), (2, "LAP")])),
        ),
    )
    aaf_real = build_calibration_aaf(
        tmp_path / "real.aaf",
        [{"name": "Audio 1", "clips": [clip(copia, "corte_sem_nome_igual", 0, 2000)]}],
        frames=20000,
    )
    sessao_real = read_aaf(aaf_real, Report(strict=False))
    resolve_for_inspect(sessao_real, [midia], Report(strict=False))
    plano_real = planejar(sessao_real, tmp_path / "som_real")
    assert plano_real.takes[orig_real].usadas == {("In", 1)}
    assert plano_real.takes[orig_real].faltando == [("In", 2)]
    orig_obj = original_do_arquivo(orig_real, com_ixml_do_bext(read_bwf(orig_real)))
    assert mesmo_take(sessao_real.tracks[0].clips[0], com_ixml_do_bext(read_bwf(copia)), orig_obj)

    copia_outro_tc = gravar(
        midia / "outro_tc.wav",
        [canais[0]],
        extras=((b"bext", bext(desc, time_reference=TC + 1)),),
    )
    aaf_miss = build_calibration_aaf(
        tmp_path / "miss.aaf",
        [{"name": "Audio 1", "clips": [clip(copia_outro_tc, "corte_sem_nome_igual", 0, 2000)]}],
        frames=20000,
    )
    sessao_miss = read_aaf(aaf_miss, Report(strict=False))
    resolve_for_inspect(sessao_miss, [midia], Report(strict=False))
    assert planejar(sessao_miss, gravador).takes == {}
