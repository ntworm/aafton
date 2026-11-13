"""Testes da segunda rodada da leitura de EDL e XML do FCP.

Cobrem o EDL com rotulos de drop frame misturados, o evento de quatro
digitos, o conflito entre o FCM e a taxa da sessao, o nivel de audio com
curva e as tracks e clipitem desligados do XML do FCP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats import otio_read
from aafton.formats.origem import read_origin
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate, label_to_samples

pytest.importorskip("opentimelineio")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "formatos"
EDL = FIXTURES / "cena12.edl"
XML = FIXTURES / "cena12_premiere.xml"

TAXA_DF = TimecodeRate(30000, 1001, True)
TAXA_2997 = TimecodeRate(30000, 1001)
AMOSTRAS = 48000

# Registro em drop frame de 01:00:59;28: 109690 frames a 29.97.
INICIO_DF = 175679504
# Origem em nao drop frame de 00:10:00:00: 18000 frames a 29.97.
ORIGEM_NDF = 28828800

# EDL com o registro em drop frame e a origem em nao drop frame, na mesma linha.
EDL_MISTO = """TITLE: MISTO
FCM: DROP FRAME

001  DIAL01   A     C        00:10:00:00 00:10:02:00 01:00:59;28 01:01:02;00
* FROM CLIP NAME: dial01.wav
* SOURCE FILE: dial01.wav
"""

# EDL de drop frame com o numero do evento de quatro digitos.
EDL_QUATRO_DIGITOS = """TITLE: QUATRO
FCM: DROP FRAME

0001  DIAL01   AA    C        00:00:59:28 00:01:00:04 01:00:59:28 01:01:00:04
* FROM CLIP NAME: dial01.wav
* SOURCE FILE: dial01.wav
"""

# Filtro Audio Levels com valor constante e keyframes: isso e uma curva.
NIVEL_COM_CURVA = """<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effectcategory>audiolevels</effectcategory><effecttype>audiolevels</effecttype><mediatype>audio</mediatype>
<parameter authoringApp="PremierePro"><parameterid>level</parameterid><name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>1</value>
<keyframe><when>0</when><value>1</value></keyframe><keyframe><when>25</when><value>0.1</value></keyframe></parameter></effect></filter>"""


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _wav(destino: Path, segundos: int, time_reference: int) -> Path:
    """WAV mono de 48 kHz e 24 bits, com o timecode de origem no bext."""
    return write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * AMOSTRAS * segundos,
        channels=1,
        sample_rate=AMOSTRAS,
        bits_per_sample=24,
        time_reference=time_reference,
    )


def _xml(destino: Path, texto: str) -> Path:
    """Grava o XML do FCP e devolve o caminho."""
    destino.write_text(texto, encoding="utf-8")
    return destino


def test_edl_com_rotulo_misto_guarda_a_origem_em_nao_drop_frame(tmp_path):
    """A origem com ':' de um EDL que marca o registro com ';' nao vira DF."""
    destino = tmp_path / "misto.edl"
    destino.write_text(EDL_MISTO, encoding="utf-8")
    _wav(tmp_path / "dial01.wav", 700, 0)
    report = Report(strict=False)

    sessao = read_origin(destino, report, edl_rate=TAXA_DF, media_roots=[tmp_path])

    clip = sessao.tracks[0].clips[0]
    assert clip.source_offset_samples == ORIGEM_NDF
    assert clip.timeline_start_samples == INICIO_DF


def test_edl_com_evento_de_quatro_digitos_e_lido_em_drop_frame(tmp_path):
    """O evento de quatro digitos tambem e reescrito para drop frame."""
    destino = tmp_path / "quatro.edl"
    destino.write_text(EDL_QUATRO_DIGITOS, encoding="utf-8")
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report, edl_rate=TAXA_2997)

    clip = sessao.tracks[0].clips[0]
    assert clip.timeline_start_samples == INICIO_DF
    assert sessao.timecode_rate == TAXA_DF


def test_edl_com_fcm_non_drop_em_sessao_de_drop_frame_avisa(tmp_path):
    """O FCM de non-drop em sessao de drop frame avisa e a leitura continua DF."""
    report = Report(strict=False)

    sessao = otio_read.read_timeline(EDL, report, edl_rate=TAXA_DF)

    assert "FORMAT_EDL_FCM_MISMATCH" in _codigos(report)
    assert sessao.timecode_rate == TAXA_DF
    clip = sessao.tracks[0].clips[0]
    inicio = label_to_samples("01:00:00;00", TAXA_DF, AMOSTRAS)
    assert clip.timeline_start_samples == round(inicio)


def test_xml_com_nivel_de_audio_com_curva_vira_erro(tmp_path):
    """O Audio Levels com valor e keyframe e uma curva, nao um ganho fixo."""
    texto = XML.read_text(encoding="utf-8")
    texto = texto.replace("<start>0</start>", NIVEL_COM_CURVA + "<start>0</start>", 1)
    destino = _xml(tmp_path / "curva.xml", texto)
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert "FORMAT_GAIN_UNSUPPORTED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 3
    assert sessao.tracks[0].clips[0].gain_linear == 1.0


def test_xml_com_track_desligada_deixa_os_clips_fora(tmp_path):
    """A track com enabled FALSE fica sem clips e vira erro."""
    texto = XML.read_text(encoding="utf-8")
    abertura = "<track>\n          <enabled>FALSE</enabled>"
    texto = texto.replace("<track>", abertura, 1)
    destino = _xml(tmp_path / "track_desligada.xml", texto)
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert "FORMAT_TRACK_DISABLED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 1
    assert sessao.tracks[0].clips == []
    assert len(sessao.tracks[1].clips) == 1


def test_xml_com_track_desligada_no_fim_deixa_os_clips_fora(tmp_path):
    """A track com enabled FALSE no fim do XML tambem fica sem clips."""
    texto = XML.read_text(encoding="utf-8")
    fechamento = "  <enabled>FALSE</enabled>\n          <locked>FALSE</locked>\n        </track>"
    texto = texto.replace("</track>", fechamento, 1)
    destino = _xml(tmp_path / "track_desligada_no_fim.xml", texto)
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert "FORMAT_TRACK_DISABLED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 1


def test_xml_com_clipitem_desligado_sem_id_deixa_o_clip_fora(tmp_path):
    """O clipitem desligado sem id e achado pela ordem dele na track."""
    texto = XML.read_text(encoding="utf-8")
    texto = texto.replace('<clipitem id="clipitem-001">', "<clipitem>", 1)
    texto = texto.replace("<enabled>TRUE</enabled>", "<enabled>FALSE</enabled>", 1)
    destino = _xml(tmp_path / "sem_id.xml", texto)
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 2
