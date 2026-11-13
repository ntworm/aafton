import json

import pytest
from aafton.report import ConversionIssue, Report, StrictModeError


def test_issue_guarda_severidade_codigo_e_contexto():
    issue = ConversionIssue("warning", "MEDIA_AMBIGUOUS", "dois candidatos", {"n": 2})
    assert issue.severity == "warning"
    assert issue.context["n"] == 2


def test_severidade_invalida_recusada():
    with pytest.raises(ValueError, match="unknown severity"):
        ConversionIssue("catastrofico", "X", "y")


def test_report_acumula_e_conta_por_severidade():
    report = Report(strict=False)
    report.info("ROUNDING", "residuo de -0.4 sample")
    report.warning("NO_TIMECODE", "AAF sem TimecodeMobSlot")
    report.error("MEDIA_MISSING", "arquivo nao encontrado")
    assert report.count("error") == 1
    assert report.count("warning") == 1
    assert len(report.issues) == 3


def test_modo_estrito_levanta_no_primeiro_erro():
    report = Report(strict=True)
    report.warning("X", "isso passa")
    with pytest.raises(StrictModeError, match="MEDIA_MISSING"):
        report.error("MEDIA_MISSING", "arquivo nao encontrado")


def test_modo_permissivo_marca_a_conversao_como_incompleta():
    report = Report(strict=False)
    report.error("MEDIA_MISSING", "x")
    assert report.incomplete is True


def test_report_sem_erro_nao_e_incompleto():
    report = Report(strict=False)
    report.info("X", "y")
    assert report.incomplete is False


def test_serializa_para_json_com_contagens():
    report = Report(strict=False)
    report.error("MEDIA_MISSING", "arquivo nao encontrado", {"filename": "a.wav"})
    data = json.loads(report.to_json())
    assert data["counts"]["error"] == 1
    assert data["incomplete"] is True
    assert data["issues"][0]["context"]["filename"] == "a.wav"


def test_serializa_para_markdown_com_uma_linha_por_issue():
    report = Report(strict=False)
    report.warning("NO_TIMECODE", "AAF sem TimecodeMobSlot")
    texto = report.to_markdown()
    assert "NO_TIMECODE" in texto
    assert "AAF sem TimecodeMobSlot" in texto


def test_barra_vertical_na_mensagem_nao_quebra_a_tabela():
    report = Report(strict=False)
    report.error("MEDIA_AMBIGUOUS", "dois candidatos: a|b.wav e c.wav")
    linha = next(item for item in report.to_markdown().splitlines() if "MEDIA_AMBIGUOUS" in item)
    assert r"a\|b.wav" in linha
    # o pipe escapado nao conta como separador de coluna
    assert linha.replace(r"\|", "").count("|") == 4


def test_quebra_de_linha_na_mensagem_vira_espaco():
    report = Report(strict=False)
    report.error("X", "primeira linha\nsegunda linha")
    linhas = [item for item in report.to_markdown().splitlines() if item.startswith("| error")]
    assert len(linhas) == 1
    assert "primeira linha segunda linha" in linhas[0]


def test_toda_linha_da_tabela_tem_o_mesmo_numero_de_colunas():
    report = Report(strict=False)
    report.info("A", "sem nada de especial")
    report.warning("B", "com | barra")
    report.error("C", "com\nquebra")
    linhas = [item for item in report.to_markdown().splitlines() if item.startswith("|")]
    colunas = {item.replace(r"\|", "").count("|") for item in linhas}
    assert colunas == {4}
