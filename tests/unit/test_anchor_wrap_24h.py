"""A origem negativa da pre-roll volta a caber no timecode: 24 h de volta.

O pipeline deixa dez segundos de folga antes do primeiro clip; quando a
timeline comeca em zero essa folga joga a origem para antes de 00:00:00:00,
que nao existe como timecode. Quem escreve embrulha a origem em 24 h e quem
le desembrulha.

A volta e contada em frames de rotulo: 24 h de samples nao sao 24 h de
timecode, e a 29.97 a diferenca ja passa de um segundo.
"""

import gzip
import math
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

from aafton.als_ancora import ancora_sem_sidecar, start_da_linha, start_padrao
from aafton.als_read import read_session
from aafton.als_write import WriteOptions, write_session
from aafton.als_write_locators import fps_do_nome
from aafton.als_xml import load_als
from aafton.ir import SessionIR
from aafton.reconform_anchor import _start_without_sidecar, resolve_reconform_anchor
from aafton.report import Report
from aafton.sidecar import (
    anchor_label_samples,
    frames_per_day,
    parse_anchor_locator_name,
    preroll_frames,
    sidecar_path_for,
)
from aafton.timecode import (
    TimecodeRate,
    frames_to_label,
    frames_to_samples_exact,
    label_to_samples,
    samples_to_label,
)

SR = 48000
DEZ_SEGUNDOS = 10 * SR
UMA_HORA = 3600 * SR
VINTE_QUATRO_HORAS = 24 * 3600 * SR

# As taxas em que a volta de 24 h e testada: 24 e 25 sao inteiras, 23.976 e
# 29.97 tem frames que nao cabem em sample inteiro.
TAXAS = [
    TimecodeRate(24000, 1001),
    TimecodeRate(25, 1),
    TimecodeRate(30000, 1001),
    TimecodeRate(30000, 1001, drop_frame=True),
]
IDS = ["23.976", "25", "29.97", "29.97df"]


def _escrever(tmp_path, live_origin_samples: int, rate: TimecodeRate = TimecodeRate(24, 1)) -> Path:
    sessao = SessionIR(
        name="teste_ancoragem_24h",
        sample_rate=SR,
        timecode_rate=rate,
        start_timecode_samples=live_origin_samples,
        tracks=[],
        markers=[],
        source_path="fonte.aaf",
    )
    destino = tmp_path / "teste.als"
    write_session(
        sessao,
        Path("templates/live12.als"),
        destino,
        WriteOptions(sample_rate=SR, bpm=60.0, live_origin_samples=live_origin_samples),
        Report(),
    )
    return destino


def _nome_do_locator_ancora(destino: Path) -> str:
    with gzip.open(destino, "rb") as f:
        tree = ET.parse(f)
    locator = tree.getroot().find(".//LiveSet/Locators/Locators/Locator")
    return locator.find("Name").get("Value")


def test_origem_negativa_e_escrita_com_24_horas_de_volta(tmp_path):
    """Dez segundos antes de zero nao cabem: o locator leva a volta do dia."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    nome = _nome_do_locator_ancora(destino)
    assert nome == "TC 23:59:50:00 @ 48000 @ 24"


def test_origem_negativa_volta_a_zerar_com_o_leitor(tmp_path):
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    nome = _nome_do_locator_ancora(destino)
    assert parse_anchor_locator_name(nome, TimecodeRate(24, 1)) == -DEZ_SEGUNDOS


def test_origem_positiva_continua_como_esta(tmp_path):
    """Uma origem que cabe no tempo nao muda de forma."""
    destino = _escrever(tmp_path, UMA_HORA)
    nome = _nome_do_locator_ancora(destino)
    assert nome == "TC 01:00:00:00 @ 48000 @ 24"
    assert parse_anchor_locator_name(nome, TimecodeRate(24, 1)) == UMA_HORA


def test_nome_fora_da_janela_continua_positivo():
    """Vinte segundos antes da meia-noite nao e a volta: fica como estava."""
    nome = "TC 23:59:40:00 @ 48000 @ 24"
    assert parse_anchor_locator_name(nome, TimecodeRate(24, 1)) == VINTE_QUATRO_HORAS - 20 * SR


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_origem_negativa_e_escrita_no_fim_do_dia(tmp_path, rate):
    """Dez segundos antes de zero: o rotulo cai no fim do dia, em qualquer taxa."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS, rate)
    nome = _nome_do_locator_ancora(destino)
    assert nome.startswith("TC 23:59:5")


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_origem_negativa_volta_a_zerar_em_frames_de_rotulo(tmp_path, rate):
    """O leitor devolve a origem alinhada no frame, nao em samples."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS, rate)
    nome = _nome_do_locator_ancora(destino)
    frame = round(Fraction(-DEZ_SEGUNDOS) * Fraction(rate.num, SR * rate.den))
    assert parse_anchor_locator_name(nome, rate) == frames_to_samples_exact(frame, rate, SR)


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_a_volta_usa_o_dia_de_frames_do_rotulo(tmp_path, rate):
    """O dia da volta sao frames de rotulo, nao 24 h de samples."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS, rate)
    nome = _nome_do_locator_ancora(destino)
    frame = round(Fraction(-DEZ_SEGUNDOS) * Fraction(rate.num, SR * rate.den))
    esperado = frames_to_label(frames_per_day(rate) + frame, rate)
    assert nome == f"TC {esperado} @ {SR} @ {fps_do_nome(rate)}"


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_origem_positiva_continua_como_esta_em_todas_as_taxas(tmp_path, rate):
    """Uma origem que cabe no tempo nao muda de forma nem de rotulo."""
    destino = _escrever(tmp_path, UMA_HORA, rate)
    nome = _nome_do_locator_ancora(destino)
    rotulo = samples_to_label(UMA_HORA, rate, SR)
    assert nome == f"TC {rotulo} @ {SR} @ {fps_do_nome(rate)}"
    assert parse_anchor_locator_name(nome, rate) == int(label_to_samples(rotulo, rate, SR))


def test_read_session_sem_sidecar_desembrulha_origem_negativa(tmp_path):
    """Sem sidecar, read_session e ancora_sem_sidecar recuperam -10 s a 24 fps."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    sidecar_path_for(destino).unlink()

    report_read = Report()
    sessao = read_session(destino, report_read)
    assert sessao.start_timecode_samples == -DEZ_SEGUNDOS
    assert any(issue.code == "SIDECAR_RECOVERED" for issue in report_read.issues)

    report_ancora = Report()
    anchor = ancora_sem_sidecar(destino, load_als(destino), report_ancora, None, 60.0)
    assert anchor.live_origin_samples == -DEZ_SEGUNDOS
    assert anchor.session_start_samples == -DEZ_SEGUNDOS


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_ancora_sem_sidecar_desembrulha_em_todas_as_taxas(tmp_path, rate):
    """Sem sidecar, ancora_sem_sidecar desembrulha em qualquer taxa de frames."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS, rate)
    sidecar_path_for(destino).unlink()
    start = start_padrao(Fraction(rate.num, rate.den), rate.drop_frame)
    anchor = ancora_sem_sidecar(destino, load_als(destino), Report(), start, 60.0)
    frame = round(Fraction(-DEZ_SEGUNDOS) * Fraction(rate.num, SR * rate.den))
    assert anchor.live_origin_samples == round(frames_to_samples_exact(frame, rate, SR))
    assert anchor.live_origin_samples < 0


def test_start_tc_explicito_nao_desembrulha_23_59_50(tmp_path):
    """Um --start-tc explicito perto da meia-noite continua positivo."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    sidecar_path_for(destino).unlink()
    report = Report()
    start = start_da_linha("23:59:50:00", TimecodeRate(24, 1))
    anchor = ancora_sem_sidecar(destino, load_als(destino), report, start, 60.0)
    assert anchor.live_origin_samples == VINTE_QUATRO_HORAS - DEZ_SEGUNDOS
    assert any(issue.code == "ALS_START_TC_OVERRIDES_LOCATOR" for issue in report.issues)


def test_reconform_anchor_sem_sidecar_nao_produz_24h(tmp_path):
    """Sem sidecar, o reconform desembrulha o locator sem devolver +24 h."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    sidecar_path_for(destino).unlink()
    root = load_als(destino)
    rate = TimecodeRate(24, 1)

    report_start = Report()
    start = _start_without_sidecar(destino, root, report_start, rate, None)
    assert start is not None
    assert start.start_tc == "-00:00:10:00"
    assert label_to_samples(start.start_tc, start.timecode_rate, SR) == -DEZ_SEGUNDOS

    report_anchor = Report()
    anchor = resolve_reconform_anchor(destino, root, report_anchor, rate)
    assert anchor is not None
    assert not report_anchor.incomplete
    assert anchor.live_origin_samples == -DEZ_SEGUNDOS
    assert anchor.session_start_samples == -DEZ_SEGUNDOS


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_reconform_anchor_sem_sidecar_desembrulha_em_todas_as_taxas(tmp_path, rate):
    """No reconform, o locator embrulhado volta negativo em todas as taxas."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS, rate)
    sidecar_path_for(destino).unlink()
    report = Report()
    anchor = resolve_reconform_anchor(destino, load_als(destino), report, rate)
    assert anchor is not None
    frame = round(Fraction(-DEZ_SEGUNDOS) * Fraction(rate.num, SR * rate.den))
    assert anchor.live_origin_samples == round(frames_to_samples_exact(frame, rate, SR))
    assert anchor.live_origin_samples < 0


def test_reconform_anchor_recusa_locator_invalido_para_taxa(tmp_path):
    """Um locator fora da taxa do corte falha com RECONFORM_ANCHOR_MISSING."""
    destino = _escrever(tmp_path, -DEZ_SEGUNDOS)
    sidecar_path_for(destino).unlink()
    root = load_als(destino)
    locator = root.find(".//LiveSet/Locators/Locators/Locator/Name")
    locator.set("Value", "TC 23:59:50:29 @ 48000 @ 30")

    report = Report(strict=False)
    anchor = resolve_reconform_anchor(destino, root, report, TimecodeRate(24, 1))
    assert anchor is None
    assert any(issue.code == "RECONFORM_ANCHOR_MISSING" for issue in report.issues)


def test_escritor_nao_embrulha_origem_fora_da_janela_de_preroll(tmp_path):
    """Uma origem mais negativa que o pre-roll sai como antes, sem volta 24 h."""
    origem = -int(10.05 * SR)
    rate = TimecodeRate(24, 1)
    destino = _escrever(tmp_path, origem, rate)
    nome = _nome_do_locator_ancora(destino)
    assert nome == f"TC {samples_to_label(origem, rate, SR)} @ {SR} @ 24"
    assert not nome.startswith("TC 23:59:")
    assert parse_anchor_locator_name(nome, rate) is None


@pytest.mark.parametrize("rate", TAXAS, ids=IDS)
def test_escritor_e_leitor_usam_mesma_janela_preroll_frames(tmp_path, rate):
    """Na borda de preroll_frames: dentro embrulha e volta; fora fica negativo."""
    limite = preroll_frames(rate)
    (tmp_path / "dentro").mkdir()
    dentro = int(frames_to_samples_exact(-limite, rate, SR))
    destino_dentro = _escrever(tmp_path / "dentro", dentro, rate)
    nome_dentro = _nome_do_locator_ancora(destino_dentro)
    assert nome_dentro.startswith("TC 23:59:")
    assert parse_anchor_locator_name(nome_dentro, rate) == frames_to_samples_exact(-limite, rate, SR)

    (tmp_path / "fora").mkdir()
    fora = math.floor(frames_to_samples_exact(-limite - 1, rate, SR))
    destino_fora = _escrever(tmp_path / "fora", fora, rate)
    nome_fora = _nome_do_locator_ancora(destino_fora)
    assert nome_fora == f"TC {samples_to_label(fora, rate, SR)} @ {SR} @ {fps_do_nome(rate)}"
    assert not nome_fora.startswith("TC 23:59:")


def test_anchor_label_samples_respeita_janela_de_24h():
    """anchor_label_samples desembrulha na ultima janela e mantem fora dela."""
    rate = TimecodeRate(24, 1)
    assert anchor_label_samples("23:59:50:00", rate, SR) == -DEZ_SEGUNDOS
    assert anchor_label_samples("23:59:49:23", rate, SR) == VINTE_QUATRO_HORAS - DEZ_SEGUNDOS - 2000
    rate_2997 = TimecodeRate(30000, 1001)
    assert anchor_label_samples("00:00:00:01", rate_2997, SR) == Fraction(8008, 5)
    assert anchor_label_samples("23:59:59:29", rate_2997, SR) == Fraction(-8008, 5)
