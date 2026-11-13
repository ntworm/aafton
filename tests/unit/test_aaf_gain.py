import math
from unittest.mock import MagicMock
import pytest

from aafton.aaf_read import _constant_gain, read_aaf
from aafton.report import Report
from tests.aaf_factory import (
    build_aaf_with_gain,
    build_aaf_with_varying_gain,
    wav_bytes,
)


@pytest.fixture
def wav(tmp_path):
    caminho = tmp_path / "cena12_tk3.wav"
    caminho.write_bytes(wav_bytes(48000 * 60))
    return caminho


def test_ganho_constante_de_meia_amplitude_vira_gain_linear(tmp_path, wav):
    aaf = build_aaf_with_gain(tmp_path / "ganho.aaf", wav, gain=(1, 2))
    clip = read_aaf(aaf, Report(strict=False)).tracks[0].clips[0]
    assert clip.gain_linear == pytest.approx(0.5)


def test_ganho_unitario_fica_em_um(tmp_path, wav):
    aaf = build_aaf_with_gain(tmp_path / "ganho.aaf", wav, gain=(1, 1))
    clip = read_aaf(aaf, Report(strict=False)).tracks[0].clips[0]
    assert clip.gain_linear == pytest.approx(1.0)


def test_o_source_clip_dentro_do_operation_group_e_encontrado(tmp_path, wav):
    aaf = build_aaf_with_gain(tmp_path / "ganho.aaf", wav, duration=96000)
    clip = read_aaf(aaf, Report(strict=False)).tracks[0].clips[0]
    assert clip.duration_samples == 96000
    assert clip.timeline_start_samples == 0


def test_operacao_desconhecida_gera_aviso_mas_preserva_o_clip(tmp_path, wav):
    aaf = build_aaf_with_gain(tmp_path / "ganho.aaf", wav)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    assert len(session.tracks[0].clips) == 1


# --- Testes sinteticos de VaryingValue e _constant_gain ---


class MockControlPoint:
    def __init__(self, value, time=0.0):
        self.value = value
        self.time = time


class MockVaryingValue:
    def __init__(self, point_list=None, name="Amplitude"):
        self.name = name
        self.point_list = point_list


class MockConstantValue:
    def __init__(self, value, name="Amplitude"):
        self.name = name
        self.value = value


def test_varying_value_com_pontos_identicos_retorna_ganho_linear_sem_aviso():
    report = Report(strict=True)
    param = MockVaryingValue(
        point_list=[
            MockControlPoint(0.288, 0.0),
            MockControlPoint(0.288, 0.5),
            MockControlPoint(0.288, 1.0),
        ]
    )
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert math.isclose(gain, 0.288, rel_tol=1e-9)
    assert len(report.issues) == 0


def test_varying_value_com_um_unico_ponto_retorna_valor_sem_aviso():
    report = Report(strict=True)
    param = MockVaryingValue(point_list=[MockControlPoint(0.707, 0.0)])
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert math.isclose(gain, 0.707, rel_tol=1e-9)
    assert len(report.issues) == 0


def test_varying_value_com_point_list_vazia_retorna_um_sem_aviso():
    report = Report(strict=True)
    param = MockVaryingValue(point_list=[])
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert gain == 1.0
    assert len(report.issues) == 0


def test_varying_value_com_point_list_none_retorna_um_sem_aviso():
    report = Report(strict=True)
    param = MockVaryingValue(point_list=None)
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert gain == 1.0
    assert len(report.issues) == 0


def test_varying_value_com_pontos_variaveis_emite_aviso_com_clip_e_rampa():
    report = Report(strict=False)
    param = MockVaryingValue(
        point_list=[
            MockControlPoint(0.112, 0.0),
            MockControlPoint(0.537, 1.0),
        ]
    )
    gain = _constant_gain([param], report, "Audio 1", "clip_dialogo")
    assert gain == 1.0
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code == "AAF_GAIN_AUTOMATION"
    assert issue.severity == "warning"
    assert "clip_dialogo" in issue.message
    assert "Audio 1" in issue.message
    assert "0.112" in issue.message
    assert "0.537" in issue.message
    assert issue.context["gain_start"] == pytest.approx(0.112)
    assert issue.context["gain_end"] == pytest.approx(0.537)
    assert issue.context["clip"] == "clip_dialogo"
    assert issue.context["track"] == "Audio 1"


def test_varying_value_com_pontos_variaveis_sem_clip_name():
    report = Report(strict=False)
    param = MockVaryingValue(
        point_list=[
            MockControlPoint(0.347, 0.0),
            MockControlPoint(0.288, 1.0),
        ]
    )
    gain = _constant_gain([param], report, "Track Stereo")
    assert gain == 1.0
    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.code == "AAF_GAIN_AUTOMATION"
    assert "Track Stereo" in issue.message
    assert "0.347" in issue.message
    assert "0.288" in issue.message


def test_varying_value_com_tuplas_de_tempo_e_valor():
    report = Report(strict=True)
    param = MockVaryingValue(point_list=[(0.0, 0.537), (1.0, 0.537)])
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert math.isclose(gain, 0.537, rel_tol=1e-9)
    assert len(report.issues) == 0


def test_varying_value_com_dicionario_value():
    report = Report(strict=True)
    val_mock = MagicMock()
    val_mock.value = 0.42
    cp = {"Value": val_mock}
    param = {"PointList": [cp], "name": "Amplitude"}
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert math.isclose(gain, 0.42, rel_tol=1e-9)
    assert len(report.issues) == 0


def test_constant_value_com_float():
    report = Report(strict=True)
    param = MockConstantValue(0.75)
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert gain == 0.75
    assert len(report.issues) == 0


def test_constant_value_com_racional():
    report = Report(strict=True)

    class RationalMock:
        numerator = 3
        denominator = 4

    param = MockConstantValue(RationalMock())
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert gain == 0.75
    assert len(report.issues) == 0


def test_parametro_com_nome_ignorado():
    report = Report(strict=True)
    param = MockConstantValue(0.5, name="Pan")
    gain = _constant_gain([param], report, "Audio 1", "clip1")
    assert gain == 1.0
    assert len(report.issues) == 0


def test_lista_de_parametros_vazia():
    report = Report(strict=True)
    gain = _constant_gain([], report, "Audio 1", "clip1")
    assert gain == 1.0
    assert len(report.issues) == 0


# --- Testes de integracao com arquivos AAF sinteticos via pyaaf2 ---


def test_aaf_com_varying_value_constante(tmp_path, wav):
    aaf = build_aaf_with_varying_gain(
        tmp_path / "ganho_vv_constante.aaf",
        wav,
        points=[((0, 1), (288, 1000)), ((1, 2), (288, 1000)), ((1, 1), (288, 1000))],
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    clip = session.tracks[0].clips[0]
    assert clip.gain_linear == pytest.approx(0.288)
    assert not [i for i in report.issues if i.code == "AAF_GAIN_AUTOMATION"]


def test_aaf_com_varying_value_com_automacao_gera_aviso(tmp_path, wav):
    aaf = build_aaf_with_varying_gain(
        tmp_path / "ganho_vv_automacao.aaf",
        wav,
        points=[((0, 1), (100, 1000)), ((1, 1), (500, 1000))],
    )
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    clip = session.tracks[0].clips[0]
    assert clip.gain_linear == pytest.approx(1.0)
    envelope_issues = [i for i in report.issues if i.code == "GAIN_ENVELOPE_WRITTEN"]
    assert len(envelope_issues) == 1
