import pytest
from aafton.als_write import AnchorError, to_beats
from aafton.als_write import write_session, WriteOptions
from aafton.als_write_locators import fps_do_nome
from aafton.als_read import read_session
from aafton.ir import SessionIR
from aafton.timecode import TimecodeRate
from aafton.report import Report
from aafton.sidecar import parse_anchor_locator_name, sidecar_path_for
import xml.etree.ElementTree as ET
from pathlib import Path

SR = 48000
UMA_HORA = 172800000
CINCO_SEGUNDOS = 240000


def test_ancorado_no_inicio_da_sessao_poe_o_clip_em_cinco_segundos():
    beats = to_beats(
        UMA_HORA + CINCO_SEGUNDOS, live_origin_samples=UMA_HORA, sample_rate=SR, bpm=60
    )
    assert beats == pytest.approx(5.0)


def test_ancorado_em_zero_poe_o_clip_em_3605_segundos():
    beats = to_beats(UMA_HORA + CINCO_SEGUNDOS, live_origin_samples=0, sample_rate=SR, bpm=60)
    assert beats == pytest.approx(3605.0)


def test_ancorado_dez_segundos_antes_da_sessao_da_pre_roll():
    dez_segundos = 480000
    beats = to_beats(
        UMA_HORA + CINCO_SEGUNDOS,
        live_origin_samples=UMA_HORA - dez_segundos,
        sample_rate=SR,
        bpm=60,
    )
    assert beats == pytest.approx(15.0)


def test_bpm_60_faz_beat_igual_a_segundo():
    assert to_beats(SR, live_origin_samples=0, sample_rate=SR, bpm=60) == pytest.approx(1.0)


def test_bpm_120_dobra_a_contagem_de_beats():
    assert to_beats(SR, live_origin_samples=0, sample_rate=SR, bpm=120) == pytest.approx(2.0)


def test_posicao_antes_da_origem_falha_com_mensagem_acionavel():
    with pytest.raises(AnchorError, match="live-origin"):
        to_beats(0, live_origin_samples=UMA_HORA, sample_rate=SR, bpm=60)


def test_posicao_de_24_horas_sobrevive_a_ida_e_volta_no_sample_exato():
    from aafton.als_xml import format_value

    vinte_quatro_horas = 24 * 60 * 60 * SR
    beats = to_beats(vinte_quatro_horas, live_origin_samples=0, sample_rate=SR, bpm=60)
    texto = format_value(beats)
    de_volta = float(texto) * 60 / 60 * SR
    assert de_volta == vinte_quatro_horas


def test_write_session_cria_locator_de_ancoragem_no_beat_zero(tmp_path):
    sessao = SessionIR(
        name="teste_ancoragem",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[],
        markers=[],
        source_path="fonte.aaf",
    )
    destino = tmp_path / "teste.als"
    template = Path("templates/live12.als")

    write_session(
        sessao,
        template,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        Report(),
    )

    import gzip

    with gzip.open(destino, "rb") as f:
        tree = ET.parse(f)
    root = tree.getroot()

    locators = root.findall(".//LiveSet/Locators/Locators/Locator")
    assert len(locators) == 2

    anchor = locators[0]
    nome_anchor = anchor.find("Name").get("Value")
    assert nome_anchor.startswith("TC ")
    assert float(anchor.find("Time").get("Value")) == 0.0

    hour_loc = locators[1]
    assert hour_loc.find("Name").get("Value") == "01:00:00:00"
    assert float(hour_loc.find("Time").get("Value")) == 0.0

    # parse_anchor_locator_name pode extrair isso de volta
    samples = parse_anchor_locator_name(nome_anchor, TimecodeRate(24, 1))
    assert samples == UMA_HORA


def test_read_session_recupera_ancoragem_do_locator_quando_sidecar_sumir(tmp_path):
    sessao = SessionIR(
        name="teste_recuperacao",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=UMA_HORA,
        tracks=[],
        markers=[],
        source_path="fonte.aaf",
    )
    destino = tmp_path / "teste2.als"
    template = Path("templates/live12.als")

    write_session(
        sessao,
        template,
        destino,
        WriteOptions(sample_rate=48000, bpm=60.0, live_origin_samples=UMA_HORA),
        Report(),
    )

    # Apaga o sidecar para forcar o comportamento
    sidecar = sidecar_path_for(destino)
    assert sidecar.exists()
    sidecar.unlink()

    # read_session de volta
    report = Report()
    sessao_lida = read_session(destino, report)

    assert sessao_lida.start_timecode_samples == UMA_HORA

    # Checa o aviso
    encontrado = any(issue.code == "SIDECAR_RECOVERED" for issue in report.issues)
    assert encontrado, "O aviso de recuperacao do locator nao foi registrado"


def test_fps_do_nome_do_locator():
    """O nome leva o fps que a extensao le: fracao 1000/1001 vira `23.976`."""
    assert fps_do_nome(TimecodeRate(24000, 1001)) == "23.976"
    assert fps_do_nome(TimecodeRate(30000, 1001, drop_frame=True)) == "29.97"
    assert fps_do_nome(TimecodeRate(25, 1)) == "25"
