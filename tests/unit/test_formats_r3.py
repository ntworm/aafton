"""Testes da terceira rodada da leitura de EDL, XML do FCP e MXF.

Cobrem o XML do FCP com mais de uma sequencia, o EDL cujas linhas FCM nao
concordam entre si e o MXF so de audio com timecode de drop frame em sessao
que nao conta drop frame.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.formats import otio_read
from aafton.media_extract import parse_bext
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate, label_to_samples

pytest.importorskip("opentimelineio")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "formatos"
XML = FIXTURES / "cena12_premiere.xml"

AMOSTRAS = 48000
TAXA_25 = TimecodeRate(25, 1)
TAXA_2997 = TimecodeRate(30000, 1001)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

# Cabecalho do XML do FCP 7, como o Premiere escreve.
CABECALHO = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'

# Timecode de 01:00:00;00 contado na taxa de 25 fps da sessao.
TC_UMA_HORA_EM_25 = 172800000

# EDL com FCM de non-drop frame no topo e FCM de drop frame antes do evento 2.
EDL_FCM_MISTURADO = """TITLE: FCM POR EVENTO
FCM: NON-DROP FRAME

001  DIAL01   A     C        00:00:10:00 00:00:12:00 01:00:00:00 01:00:02:00
* SOURCE FILE: dial01.wav

FCM: DROP FRAME
002  DIAL01   A     C        00:10:00:00 00:10:02:00 01:10:00:00 01:10:02:00
* SOURCE FILE: dial01.wav
"""


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _mensagem(report: Report, code: str) -> str:
    """A mensagem do primeiro problema com o codigo dado."""
    for issue in report.issues:
        if issue.code == code:
            return issue.message
    return ""


def _sequencia(original: str, nome: str, ident: str) -> str:
    """A sequencia da fixture, com nome, id e clipitem proprios."""
    inicio = original.index("<sequence")
    fim = original.index("</sequence>") + len("</sequence>")
    corpo = original[inicio:fim]
    texto = corpo.replace('id="sequence-cena12"', f'id="{ident}"', 1)
    texto = texto.replace("<name>CENA12</name>", f"<name>{nome}</name>", 1)
    return re.sub(
        r'clipitem id="clipitem-(\d+)"',
        lambda m: f'clipitem id="{ident}-{m.group(1)}"',
        texto,
    )


def _track_desligada(sequencia: str) -> str:
    """A sequencia com a primeira track de audio desligada."""
    return sequencia.replace("<track>", "<track>\n          <enabled>FALSE</enabled>", 1)


def _opatom_drop_frame(destino: Path) -> Path:
    """Grava um MXF OP-Atom so de audio com timecode de drop frame."""
    argumentos = ["-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate={AMOSTRAS}:duration=2"]
    argumentos += ["-c:a", "pcm_s24le", "-mxf_audio_edit_rate", "30000/1001"]
    argumentos += ["-timecode", "01:00:00;00", "-f", "mxf_opatom"]
    subprocess.run(["ffmpeg", "-y", *argumentos, str(destino)], check=True, capture_output=True)
    return destino


def test_xml_com_duas_sequencias_no_topo_avisa_o_nome_das_duas(tmp_path):
    """Duas sequencias no XML viram o erro FORMAT_XML_MULTIPLE_SEQUENCES."""
    original = XML.read_text(encoding="utf-8")
    partes = [
        CABECALHO,
        '<xmeml version="4">',
        _sequencia(original, "PRIMEIRA", "seqA"),
        _sequencia(original, "SEGUNDA", "seqB"),
        "</xmeml>",
    ]
    destino = tmp_path / "duas_sequencias.xml"
    destino.write_text("".join(partes), encoding="utf-8")
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert sessao.name == "PRIMEIRA"
    assert "FORMAT_XML_MULTIPLE_SEQUENCES" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_XML_MULTIPLE_SEQUENCES")
    assert "PRIMEIRA" in mensagem
    assert "SEGUNDA" in mensagem


def test_xml_de_projeto_com_sequencia_no_bin_nao_marca_track_desligada(tmp_path):
    """O estado das tracks vem da sequencia lida, e nao da sequencia do bin."""
    original = XML.read_text(encoding="utf-8")
    partes = [
        CABECALHO,
        '<xmeml version="5"><project><name>P</name><children>',
        "<bin><name>Bin</name><children>",
        _track_desligada(_sequencia(original, "SEGUNDA", "seqB")),
        "</children></bin>",
        _sequencia(original, "PRIMEIRA", "seqA"),
        "</children></project></xmeml>",
    ]
    destino = tmp_path / "projeto_com_bin.xml"
    destino.write_text("".join(partes), encoding="utf-8")
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report)

    assert sessao.name == "PRIMEIRA"
    assert "FORMAT_XML_MULTIPLE_SEQUENCES" in _codigos(report)
    assert "FORMAT_TRACK_DISABLED" not in _codigos(report)


def test_xml_com_duas_sequencias_no_topo_para_no_modo_estrito(tmp_path):
    """No modo estrito, o erro das duas sequencias interrompe a leitura."""
    original = XML.read_text(encoding="utf-8")
    partes = [
        CABECALHO,
        '<xmeml version="4">',
        _sequencia(original, "PRIMEIRA", "seqA"),
        _sequencia(original, "SEGUNDA", "seqB"),
        "</xmeml>",
    ]
    destino = tmp_path / "duas_sequencias_estrito.xml"
    destino.write_text("".join(partes), encoding="utf-8")
    report = Report(strict=True)

    with pytest.raises(StrictModeError):
        otio_read.read_timeline(destino, report)


def test_xml_de_projeto_com_track_desligada_na_cena_marca_o_erro(tmp_path):
    """A track desligada na sequencia lida vira o erro FORMAT_TRACK_DISABLED."""
    original = XML.read_text(encoding="utf-8")
    partes = [
        CABECALHO,
        '<xmeml version="5"><project><name>P</name><children>',
        "<bin><name>Bin</name><children>",
        _sequencia(original, "SEGUNDA", "seqB"),
        "</children></bin>",
        _track_desligada(_sequencia(original, "PRIMEIRA", "seqA")),
        "</children></project></xmeml>",
    ]
    destino = tmp_path / "projeto_com_track_desligada.xml"
    destino.write_text("".join(partes), encoding="utf-8")
    report = Report(strict=False)

    otio_read.read_timeline(destino, report)

    assert "FORMAT_TRACK_DISABLED" in _codigos(report)


def test_edl_com_fcm_que_discorda_avisa_o_modo_da_leitura(tmp_path):
    """FCM de drop e de non-drop no mesmo arquivo viram aviso."""
    destino = tmp_path / "fcm_misturado.edl"
    destino.write_text(EDL_FCM_MISTURADO, encoding="utf-8")
    report = Report(strict=False)

    sessao = otio_read.read_timeline(destino, report, edl_rate=TAXA_2997)

    assert "FORMAT_EDL_FCM_MIXED" in _codigos(report)
    assert "non-drop frame" in _mensagem(report, "FORMAT_EDL_FCM_MIXED")
    clip = sessao.tracks[0].clips[1]
    esperado = label_to_samples("01:10:00:00", TAXA_2997, AMOSTRAS)
    assert clip.timeline_start_samples == round(esperado)


@pytest.mark.skipif(
    FFMPEG is None or FFPROBE is None,
    reason="ffmpeg e ffprobe nao estao no PATH",
)
def test_mxf_de_audio_com_timecode_drop_frame_em_sessao_de_25_avisa(tmp_path):
    """Rotulo de drop frame em sessao que nao conta drop frame avisa."""
    from aafton.mxf import extrair_mxf

    origem = _opatom_drop_frame(tmp_path / "df.mxf")
    report = Report(strict=False)

    wav = extrair_mxf(origem, tmp_path / "importados", TAXA_25, report)

    assert wav is not None
    assert parse_bext(wav)["time_reference"] == TC_UMA_HORA_EM_25
    assert "MEDIA_MXF_RATE_MISMATCH" in _codigos(report)
    mensagem = _mensagem(report, "MEDIA_MXF_RATE_MISMATCH")
    assert "drop frame" in mensagem
