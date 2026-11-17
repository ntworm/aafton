"""O set escrito pelo import descreve o arquivo real de cada clip.

Tres defeitos vistos em 15/11/2026 num AAF com audio embutido e num XML do FCP7:

- Cada SampleRef herdava do template um ``LastModDate`` fixo, ``OriginalFileSize``
  0 e um ``SourceContext`` de browser. O Live compara a data com a do arquivo,
  confere o CRC 0 e abre o clip como "Sample Offline".
- O MasterMob ``X.wav`` virava ``X.wav.wav`` na extracao da essencia.
- Com XML do FCP7 e ``--video``, o import tentava ler o XML como AAF e avisava
  ``VIDEO_AAF_READ_FAILED``.
"""

from __future__ import annotations

import gzip
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_write import WriteOptions, write_session
from aafton.cli import main
from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.video import process_video

from tests.unit.test_aaf_write_embed import RATE, _asset, _sessao, _wav

TEMPLATE = Path("templates/live12.als")
# Data do template: o sample do Core Library que deu origem ao prototipo de clip.
DATA_DO_TEMPLATE = 1778592308


def _sample_refs(als: Path) -> list[ET.Element]:
    root = ET.fromstring(gzip.decompress(als.read_bytes()))
    return list(root.iter("SampleRef"))


def _confere_identidade(sample_ref: ET.Element) -> Path:
    file_ref = sample_ref.find("FileRef")
    assert file_ref is not None
    arquivo = Path(file_ref.find("Path").get("Value"))
    estado = arquivo.stat()
    assert sample_ref.find("LastModDate").get("Value") == str(int(estado.st_mtime))
    assert file_ref.find("OriginalFileSize").get("Value") == str(estado.st_size)
    contexto = sample_ref.find("SourceContext")
    assert contexto is not None and len(contexto) == 0
    return arquivo


def test_set_escrito_grava_data_tamanho_e_sem_contexto_de_browser(tmp_path):
    wav = tmp_path / "boom.wav"
    _wav(wav, frames=480)
    # Data diferente da do template e de agora, para nao passar por acaso.
    os.utime(wav, (1_700_000_000, 1_700_000_000))
    als = tmp_path / "set.als"
    write_session(
        _sessao([_asset("m", wav, 1, 480)], clips=[("m", 0, 480, 0)]),
        TEMPLATE,
        als,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )

    refs = _sample_refs(als)
    assert refs
    for ref in refs:
        assert _confere_identidade(ref) == wav
        assert ref.find("LastModDate").get("Value") == "1700000000"
    texto = gzip.decompress(als.read_bytes()).decode("utf-8")
    assert "FileId_" not in texto
    assert str(DATA_DO_TEMPLATE) not in texto


def test_sem_arquivo_no_disco_data_e_tamanho_ficam_zero(tmp_path):
    wav = tmp_path / "some.wav"
    _wav(wav, frames=480)
    sessao = _sessao([_asset("m", wav, 1, 480)], clips=[("m", 0, 480, 0)])
    wav.unlink()
    als = tmp_path / "set.als"
    write_session(
        sessao,
        TEMPLATE,
        als,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0, permissive=True),
        Report(strict=False),
    )
    for ref in _sample_refs(als):
        assert ref.find("LastModDate").get("Value") == "0"
        assert ref.find("FileRef/OriginalFileSize").get("Value") == "0"


def test_extract_de_aaf_embutido_nao_dobra_a_extensao(tmp_path):
    wav = tmp_path / "COMPOSITOR_JANTAR_BOOM_1.wav"
    _wav(wav, frames=960)
    sessao = _sessao([_asset("m", wav, 1, 960)], clips=[("m", 0, 960, 0)])
    # O XML do FCP7 que deu origem ao set chamava o clip pelo nome do arquivo,
    # e o export grava esse nome no MasterMob.
    sessao.tracks[0].clips[0].name = wav.name
    origem = tmp_path / "origem.als"
    write_session(
        sessao,
        TEMPLATE,
        origem,
        WriteOptions(sample_rate=RATE, bpm=60, live_origin_samples=0),
        Report(strict=False),
    )
    aaf = tmp_path / "jantar.aaf"
    assert main(["export", str(origem), str(aaf), "--media", "embed"]) == 0
    wav.unlink()

    importado = tmp_path / "saida" / "T.als"
    assert (
        main(
            [
                "import",
                str(aaf),
                str(importado),
                "--media",
                "extract",
                "--live-origin",
                "00:00:00:00",
            ]
        )
        == 0
    )

    extraidos = sorted(p.name for p in importado.parent.rglob("*.wav"))
    assert extraidos == ["COMPOSITOR_JANTAR_BOOM_1.wav"]
    refs = _sample_refs(importado)
    assert refs
    for ref in refs:
        assert _confere_identidade(ref).name == "COMPOSITOR_JANTAR_BOOM_1.wav"


def _xml_fcp7(path: Path) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
        '<xmeml version="4"><sequence id="seq"><name>S</name></sequence></xmeml>\n',
        encoding="utf-8",
    )


def test_video_com_xml_do_fcp7_nao_le_o_xml_como_aaf(tmp_path):
    sessao = SessionIR(
        name="S", sample_rate=48000, timecode_rate=TimecodeRate(25, 1), start_timecode_samples=0
    )
    track = TrackIR(id="t0", source_index=0, name="Audio", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="clip",
            media_id=None,
            timeline_start_samples=48000 * 3600,
            timeline_end_samples=48000 * 3610,
        )
    )
    sessao.tracks.append(track)
    xml = tmp_path / "S.xml"
    _xml_fcp7(xml)
    video = tmp_path / "ref.mp4"
    video.write_bytes(b"dummy")
    rep = Report()

    process_video(sessao, xml, str(video), "01:00:00:00", rep)

    assert not any(i.code == "VIDEO_AAF_READ_FAILED" for i in rep.issues)
    assert sessao.tracks[0].is_video is True
    assert sessao.tracks[0].clips[0].timeline_start_samples == 48000 * 3600
