"""Set do Live do som direto: locators por bloco, sidecar, monos, cor e verify."""

import json

import pytest

from aafton import som_direto
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_write_clips import CIRCLED_CLIP_COLOR, TAKE_CLIP_COLOR
from aafton.als_xml import load_als
from aafton.bundled import default_template
from aafton.bwf_meta import read_bwf
from aafton.pipeline import ImportOptions, import_session, media_dir_for
from aafton.report import Report
from aafton.sidecar import PRODUCTION_SET_MESSAGE, ProductionSetError, sidecar_path_for
from aafton.som_direto import read_production
from aafton.som_direto_tracks import dividir_em_monos
from aafton.som_direto_verify import verify_production
from aafton.studio.app_set import read_set
from tests.unit.test_som_direto_leitor import (
    HORA,
    RATE,
    SEP,
    duas_diarias,
    gravadores_no_mesmo_canal,
    meia_noite,
)


@pytest.fixture
def set_de_duas_diarias(tmp_path):
    raiz = duas_diarias(tmp_path / "som")
    saida = tmp_path / "out" / "duas.als"
    saida.parent.mkdir()
    import_session(ImportOptions(input=str(raiz), output=str(saida)), Report(strict=True))
    return raiz, saida


def _locators(root) -> list[str]:
    return [
        loc.find("Name").get("Value")
        for loc in root.findall(".//LiveSet/Locators/Locators/Locator")
    ]


def test_set_tem_um_locator_por_bloco_sem_ancora_nem_hora(set_de_duas_diarias):
    _, saida = set_de_duas_diarias
    nomes = _locators(load_als(saida))
    assert nomes == [
        f"2025-05-05 {SEP} TC 10:00:00:00",
        f"2025-06-05 {SEP} TC 09:00:00:00",
    ]
    assert not any(nome.startswith("TC ") for nome in nomes)
    assert "01:00:00:00" not in nomes


def test_sidecar_grava_layout_e_blocos(set_de_duas_diarias):
    _, saida = set_de_duas_diarias
    dados = json.loads(sidecar_path_for(saida).read_text(encoding="utf-8"))
    assert dados["layout"] == "production_blocks"
    blocos = dados["blocks"]
    assert [b["day"] for b in blocos] == ["05-05-25", "05-06-25"]
    assert [b["date"] for b in blocos] == ["2025-05-05", "2025-06-05"]
    assert blocos[0]["start_samples"] == 0
    assert [b["timecode_samples"] for b in blocos] == [10 * HORA, 9 * HORA]
    assert all(b["circled_only"] is False for b in blocos)


def test_tracks_mono_com_monos_divididos_e_metadados(set_de_duas_diarias):
    _, saida = set_de_duas_diarias
    session = read_session(saida, Report(strict=False), allow_production=True)
    assert [t.name for t in session.tracks] == [f"In 3 {SEP} NEUMANN", "In 4"]
    pasta = media_dir_for(str(saida))
    for asset in session.media:
        caminho = asset.resolved_path.replace("\\", "/")
        assert caminho.startswith(str(pasta).replace("\\", "/"))
        info = read_bwf(asset.resolved_path)
        assert info.channels == 1
        tcs = (10 * HORA, 10 * HORA + 5 * RATE, 9 * HORA, 9 * HORA + 3 * RATE)
        assert info.bext["time_reference"] in tcs
        assert len(info.ixml.track_list) == 1


def test_take_circled_ganha_a_cor_e_o_texto_do_clip(set_de_duas_diarias):
    _, saida = set_de_duas_diarias
    root = load_als(saida)
    clips = {
        clip.find("Name").get("Value"): clip
        for clip in root.iter("AudioClip")
        if clip.find("Name") is not None
    }
    circled = clips["12A/005"]
    assert circled.find("Color").get("Value") == str(CIRCLED_CLIP_COLOR)
    texto = circled.find("Annotation").get("Value")
    assert "Scene: 12A" in texto and "Take: 005" in texto and "Tape: T1" in texto
    assert "Circled: yes" in texto and "Note: nota" in texto
    nao_circled = clips["12A/006"]
    assert nao_circled.find("Color").get("Value") == str(TAKE_CLIP_COLOR)
    assert TAKE_CLIP_COLOR != CIRCLED_CLIP_COLOR
    assert "Circled: no" in nao_circled.find("Annotation").get("Value")


def test_verify_passa_no_set_novo(set_de_duas_diarias):
    raiz, saida = set_de_duas_diarias
    resultado = verify_production(raiz, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.compared == resultado.expected == 7
    assert resultado.max_delta_samples == 0
    assert resultado.samples_equal == 7
    tabela = resultado.to_table()
    assert "7 of 7 audio clips compared" in tabela
    assert "largest deviation 0 samples" in tabela


def test_verify_falha_com_clip_movido_um_sample(set_de_duas_diarias):
    raiz, saida = set_de_duas_diarias
    report = Report(strict=False)
    session = read_production(raiz, report)
    dividir_em_monos(session, media_dir_for(str(saida)), report)
    clip = session.tracks[0].clips[1]
    clip.timeline_start_samples += 1
    clip.timeline_end_samples += 1
    opcoes = WriteOptions(sample_rate=RATE, live_origin_samples=0)
    write_session(session, default_template(), saida, opcoes, report)
    resultado = verify_production(raiz, saida)
    assert not resultado.ok
    assert resultado.max_delta_samples == 1
    assert [r.status for r in resultado.rows].count("position differs") == 1
    # A conferencia pelo bext do proprio mono tambem acusa, sem a regra dos blocos.
    erros = resultado.errors
    assert any("1 clips are not at the distance of their TimeReference" in e for e in erros)


def _regravar(raiz, saida, mudar):
    """Regrava o set a partir da pasta, com uma mudanca no IR antes de escrever."""
    report = Report(strict=False)
    session = read_production(raiz, report)
    dividir_em_monos(session, media_dir_for(str(saida)), report)
    mudar(session)
    opcoes = WriteOptions(sample_rate=RATE, live_origin_samples=0)
    write_session(session, default_template(), saida, opcoes, report)


def test_verify_falha_com_locator_movido(set_de_duas_diarias):
    raiz, saida = set_de_duas_diarias

    def mover(session):
        session.markers[1].position_samples += RATE

    _regravar(raiz, saida, mover)
    resultado = verify_production(raiz, saida)
    assert not resultado.ok
    assert "the locators of the set differ from the blocks of the sidecar" in resultado.errors
    assert resultado.max_delta_samples == 0


def test_verify_acusa_erro_da_regra_dos_blocos_pelo_bext(tmp_path, monkeypatch):
    """Erro na regra dos blocos, repetido no import e no verify: o bext do mono acusa."""
    original = som_direto.montar_blocos

    def com_erro(takes, sample_rate, report):
        blocos = original(takes, sample_rate, report)
        blocos[0].takes[1].start += 1
        return blocos

    monkeypatch.setattr(som_direto, "montar_blocos", com_erro)
    raiz = duas_diarias(tmp_path / "som")
    saida = tmp_path / "out" / "x.als"
    saida.parent.mkdir()
    import_session(ImportOptions(input=str(raiz), output=str(saida)), Report(strict=True))
    resultado = verify_production(raiz, saida)
    assert resultado.max_delta_samples == 0
    assert not resultado.ok
    assert any("2 clips are not at the distance" in erro for erro in resultado.errors)


@pytest.mark.parametrize(
    "montar",
    [
        lambda raiz: meia_noite(raiz, com_datas=True),
        lambda raiz: meia_noite(raiz, com_datas=False),
        gravadores_no_mesmo_canal,
    ],
    ids=["midnight_dates", "midnight_timecode", "overlap"],
)
def test_verify_passa_na_meia_noite_e_nas_sobreposicoes(tmp_path, montar):
    raiz = montar(tmp_path / "som")
    saida = tmp_path / "out" / "x.als"
    saida.parent.mkdir()
    import_session(ImportOptions(input=str(raiz), output=str(saida)), Report(strict=True))
    resultado = verify_production(raiz, saida)
    assert resultado.ok, resultado.to_table()
    assert resultado.max_delta_samples == 0


def test_verify_falha_quando_as_amostras_mudam(set_de_duas_diarias):
    raiz, saida = set_de_duas_diarias
    session = read_session(saida, Report(strict=False), allow_production=True)
    mono = session.media[0].resolved_path
    bruto = bytearray(open(mono, "rb").read())
    bruto[-1] ^= 0xFF
    with open(mono, "wb") as destino:
        destino.write(bruto)
    resultado = verify_production(raiz, saida)
    assert not resultado.ok
    assert "samples differ" in [r.status for r in resultado.rows]


def test_set_de_som_direto_e_recusado_na_leitura(set_de_duas_diarias):
    _, saida = set_de_duas_diarias
    with pytest.raises(ProductionSetError, match=PRODUCTION_SET_MESSAGE):
        read_session(saida, Report(strict=False))
    resposta = read_set({"input": str(saida)})
    assert resposta["ok"] is False
    assert resposta["error"]["code"] == "production_set"
    assert resposta["error"]["message"] == PRODUCTION_SET_MESSAGE
