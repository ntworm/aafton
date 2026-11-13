"""Testes da segunda rodada do leitor de EDL e XML do FCP.

Cobrem o drop frame do EDL, o clipitem desligado, os filtros de audio do
XML do FCP, o erro de leitura dos adaptadores e o codigo de saida do
comando inspect.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from aafton.cli import main
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate, label_to_samples

otio = pytest.importorskip("opentimelineio")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "formatos"
EDL = FIXTURES / "cena12.edl"
XML = FIXTURES / "cena12_premiere.xml"

TAXA_DF = TimecodeRate(30000, 1001, True)
TAXA_2997 = TimecodeRate(30000, 1001)
AMOSTRAS = 48000

# Evento de drop frame que cruza a marca de minuto: em drop frame o trecho
# de 01:00:59;28 ate 01:01:00;04 tem quatro frames, e nao seis.
EDL_DROP = """TITLE: CENA12
FCM: DROP FRAME

001  DIAL01   A     C        10:00:59:28 10:01:00:04 01:00:59:28 01:01:00:04
* FROM CLIP NAME: dial01.wav
* SOURCE FILE: dial01.wav
"""

FILTRO = """            <filter>
              <effect>
                <name>{nome}</name>
                <effectid>{effectid}</effectid>
                <effecttype>filter</effecttype>
{parametros}              </effect>
            </filter>
"""

PARAMETRO = """                <parameter authoringApp="PremierePro">
                  <parameterid>{pid}</parameterid>
                  <name>{pid}</name>
                  <valuemin>0</valuemin>
                  <valuemax>3.9810717</valuemax>
                  {valor}
                </parameter>
"""


def _modulo():
    """Modulo do leitor."""
    from aafton.formats import otio_read

    return otio_read


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _edl_com_ponto(texto: str) -> str:
    """Troca o separador dos timecodes de ':' para ';'."""
    return re.sub(r"(\d{2}:\d{2}:\d{2}):(\d{2})", r"\1;\2", texto)


def _xml_com_filtro(
    tmp_path: Path, nome: str, effectid: str, parametros: list[tuple[str, str]]
) -> Path:
    """Copia da fixture do FCP com um filtro no primeiro clipitem."""
    blocos = ""
    for pid, valor in parametros:
        if valor == "CHAVE":
            conteudo = (
                "<value><keyframe><when>00:00:00:00</when><value>1</value></keyframe></value>"
            )
        else:
            conteudo = f"<value>{valor}</value>"
        blocos += PARAMETRO.format(pid=pid, valor=conteudo)
    filtro = FILTRO.format(nome=nome, effectid=effectid, parametros=blocos)
    texto = XML.read_text(encoding="utf-8")
    texto = texto.replace("          </clipitem>", filtro + "          </clipitem>", 1)
    destino = tmp_path / f"{effectid}.xml"
    destino.write_text(texto, encoding="utf-8")
    return destino


def _ler(caminho: Path, report: Report, edl_rate=None):
    """Le a cena com o leitor de formatos."""
    return _modulo().read_timeline(caminho, report, edl_rate=edl_rate)


def test_edl_drop_frame_com_ponto_e_com_dois_pontos_dao_a_mesma_posicao(tmp_path):
    """O EDL de drop frame com ':' e com ';' le nas mesmas posicoes."""
    com_ponto = tmp_path / "com_ponto.edl"
    com_ponto.write_text(_edl_com_ponto(EDL_DROP), encoding="utf-8")
    com_dois_pontos = tmp_path / "com_dois_pontos.edl"
    com_dois_pontos.write_text(EDL_DROP, encoding="utf-8")

    sessao_ponto = _ler(com_ponto, Report(strict=False), edl_rate=TAXA_DF)
    sessao_dois = _ler(com_dois_pontos, Report(strict=False), edl_rate=TAXA_DF)

    clip_ponto = sessao_ponto.tracks[0].clips[0]
    clip_dois = sessao_dois.tracks[0].clips[0]
    esperado = label_to_samples("01:00:59;28", TAXA_DF, AMOSTRAS)

    assert clip_ponto.timeline_start_samples == esperado
    assert clip_dois.timeline_start_samples == esperado
    assert clip_ponto.timeline_end_samples == clip_dois.timeline_end_samples
    assert sessao_ponto.start_timecode_samples == esperado
    assert sessao_dois.start_timecode_samples == esperado


def test_edl_com_ponto_em_sessao_sem_drop_frame_falha(tmp_path):
    """Rotulo de drop frame em sessao sem drop frame e erro."""
    destino = tmp_path / "ponto.edl"
    texto = _edl_com_ponto(EDL_DROP).replace("FCM: DROP FRAME", "FCM: NON-DROP FRAME")
    destino.write_text(texto, encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report, edl_rate=TAXA_2997)

    assert "FORMAT_EDL_DROP_FRAME_MISMATCH" in _codigos(report)
    assert sessao.tracks == []


def test_edl_de_drop_frame_em_taxa_que_nao_conta_drop_frame_falha(tmp_path):
    """EDL que declara drop frame em taxa que nao conta drop frame e erro."""
    destino = tmp_path / "cena.edl"
    texto = EDL.read_text(encoding="utf-8").replace("FCM: NON-DROP FRAME", "FCM: DROP FRAME")
    destino.write_text(texto, encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report, edl_rate=TimecodeRate(25, 1))

    assert "FORMAT_EDL_DROP_FRAME_MISMATCH" in _codigos(report)
    assert sessao.tracks == []


def test_edl_de_drop_frame_com_fcm_em_2997_e_lido_como_drop_frame(tmp_path):
    """O FCM do arquivo manda: 29.97 sem drop frame e lido como drop frame."""
    destino = tmp_path / "cena.edl"
    destino.write_text(EDL_DROP, encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report, edl_rate=TAXA_2997)
    sessao_df = _ler(destino, Report(strict=False), edl_rate=TAXA_DF)

    clip = sessao.tracks[0].clips[0]
    clip_df = sessao_df.tracks[0].clips[0]
    assert clip.timeline_start_samples == clip_df.timeline_start_samples
    assert clip.timeline_end_samples == clip_df.timeline_end_samples
    assert sessao.start_timecode_samples == sessao_df.start_timecode_samples
    assert sessao.timecode_rate == TAXA_DF
    assert "FORMAT_EDL_DROP_FRAME_FROM_FCM" in _codigos(report)


def test_edl_de_drop_frame_com_fcm_em_taxa_de_25_falha(tmp_path):
    """FCM de drop frame em taxa que nao conta drop frame continua erro."""
    destino = tmp_path / "cena.edl"
    destino.write_text(EDL_DROP, encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report, edl_rate=TimecodeRate(25, 1))

    assert "FORMAT_EDL_DROP_FRAME_MISMATCH" in _codigos(report)
    assert sessao.tracks == []


def test_xml_com_clipitem_desligado_falha(tmp_path):
    """Clipitem com enabled FALSE sai da cena e vira erro."""
    texto = XML.read_text(encoding="utf-8").replace(
        "<enabled>TRUE</enabled>", "<enabled>FALSE</enabled>", 1
    )
    destino = tmp_path / "cena.xml"
    destino.write_text(texto, encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 2

    with pytest.raises(StrictModeError):
        _ler(destino, Report(strict=True))


@pytest.mark.parametrize(
    ("nome", "effectid", "parametros", "codigo"),
    [
        ("Audio Levels", "audiolevels", [("level", "1")], None),
        ("Audio Levels", "audiolevels", [("level", "0.5")], None),
        ("Audio Levels", "audiolevels", [("level", "CHAVE")], "FORMAT_GAIN_UNSUPPORTED"),
        ("Audio Pan", "audiopan", [("pan", "0")], None),
        ("Audio Pan", "audiopan", [("pan", "0.5")], "FORMAT_EFFECT_UNSUPPORTED"),
        ("Fade", "fade", [("level", "1")], "FORMAT_EFFECT_UNSUPPORTED"),
    ],
    ids=[
        "nivel unity",
        "nivel abaixo de unity",
        "nivel com curva",
        "pan no centro",
        "pan fora do centro",
        "efeito desconhecido",
    ],
)
def test_xml_com_filtro_de_audio(tmp_path, nome, effectid, parametros, codigo):
    """Filtro de audio do FCP: ganho do clip e clip preservado."""
    caminho = _xml_com_filtro(tmp_path, nome, effectid, parametros)

    report = Report(strict=False)
    sessao = _ler(caminho, report)

    codigos = _codigos(report)
    if codigo is None:
        assert codigos == []
    else:
        assert codigo in codigos
    assert len(list(sessao.all_clips())) == 3


def test_xml_com_audio_levels_um_muda_o_ganho_do_clip(tmp_path):
    """O nivel constante do filtro Audio Levels vira o ganho do clip."""
    caminho = _xml_com_filtro(tmp_path, "Audio Levels", "audiolevels", [("level", "0.5")])

    report = Report(strict=False)
    sessao = _ler(caminho, report)

    assert sessao.tracks[0].clips[0].gain_linear == 0.5
    assert sessao.tracks[1].clips[0].gain_linear == 1.0


def test_edl_com_duracoes_diferentes_vira_erro_de_leitura(tmp_path):
    """EDL com duracao de origem e de registro diferentes nao levanta excecao."""
    destino = tmp_path / "cena.edl"
    destino.write_text(
        "TITLE: CENA12\n"
        "FCM: NON-DROP FRAME\n"
        "\n"
        "001  DIAL01   A     C        10:00:01:00 10:00:03:00 01:00:00:00 01:00:03:00\n"
        "* FROM CLIP NAME: dial01.wav\n"
        "* SOURCE FILE: dial01.wav\n",
        encoding="utf-8",
    )

    report = Report(strict=False)
    sessao = _ler(destino, report, edl_rate=TimecodeRate(25, 1))

    assert "FORMAT_PARSE_ERROR" in _codigos(report)
    assert sessao.tracks == []


def test_xml_truncado_vira_erro_de_leitura(tmp_path):
    """XML truncado nao levanta excecao."""
    destino = tmp_path / "truncado.xml"
    destino.write_text(XML.read_text(encoding="utf-8")[:600], encoding="utf-8")

    report = Report(strict=False)
    sessao = _ler(destino, report)

    assert "FORMAT_PARSE_ERROR" in _codigos(report)
    assert sessao.tracks == []


def test_inspect_de_edl_sem_taxa_falha_com_codigo_um(capsys):
    """O inspect de uma EDL sem taxa sai com codigo 1 e mostra o erro."""
    codigo = main(["inspect", str(EDL)])

    saida = capsys.readouterr()
    assert codigo == 1
    assert json.loads(saida.out)["total_clips"] == 0
    assert "FORMAT_EDL_RATE_REQUIRED" in saida.err


def test_inspect_de_xml_sem_erro_sai_com_codigo_zero(capsys):
    """O inspect de um XML sem erro continua saindo com codigo zero."""
    codigo = main(["inspect", str(XML)])

    saida = capsys.readouterr()
    assert codigo == 0
    assert json.loads(saida.out)["total_clips"] == 3
    assert saida.err == ""
