"""Testes do leitor de lista de corte de imagem (EDL e XML do FCP 7)."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from aafton.cut_ir import CutEvent, CutIR, normalize_source
from aafton.cut_read import read_cut
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate

otio = pytest.importorskip("opentimelineio")

_EDL_24 = "\n".join(
    [
        "TITLE: CUT24",
        "FCM: NON-DROP FRAME",
        "",
        "001  R001     V     C        01:00:00:00 01:00:02:00 01:00:00:00 01:00:02:00",
        "* FROM CLIP NAME: shot_01.mov",
        "* SOURCE FILE: shot_01.mov",
        "",
        "002  R001     V     C        01:00:02:00 01:00:02:00 01:00:02:00 01:00:02:00",
        "002  R002     V     D    024 02:00:00:00 02:00:03:00 01:00:02:00 01:00:05:00",
        "* FROM CLIP NAME: shot_02.mov",
        "",
        "003  R003     V     C        03:00:01:00 03:00:02:12 01:00:05:00 01:00:06:12",
        "* SOURCE FILE: shot_03.mov",
        "",
        "004  AUD01    A     C        10:00:00:00 10:00:02:00 01:00:00:00 01:00:02:00",
        "* SOURCE FILE: mix.wav",
        "",
    ]
)

_EDL_25 = "\n".join(
    [
        "TITLE: CUT25",
        "FCM: NON-DROP FRAME",
        "",
        "001  A001C001 V     C        01:00:00:00 01:00:01:00 01:00:00:00 01:00:01:00",
        "* FROM CLIP NAME: clip1.mxf",
        "",
        "002  A001C001 V     C        01:00:01:00 01:00:01:00 01:00:01:00 01:00:01:00",
        "002  A001C002 V     D    012 01:00:10:00 01:00:12:00 01:00:01:00 01:00:03:00",
        "* FROM CLIP NAME: clip2.mxf",
        "",
        "003  A001C003 V     C        01:00:20:05 01:00:21:15 01:00:03:00 01:00:04:10",
        "* FROM CLIP NAME: clip3.mxf",
        "",
    ]
)


def _codigos(report: Report) -> list[str]:
    """Codigos de todas as ocorrencias registradas no relatorio."""
    return [issue.code for issue in report.issues]


def _escrever(tmp_path: Path, nome: str, conteudo: str) -> Path:
    """Grava texto sintetico em arquivo temporario UTF-8."""
    caminho = tmp_path / nome
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def _xml_rate(timebase: int, ntsc: bool) -> str:
    """Bloco <rate> do XML do FCP 7."""
    flag = "TRUE" if ntsc else "FALSE"
    return f"<rate><timebase>{timebase}</timebase><ntsc>{flag}</ntsc></rate>"


def _xml_clipitem(
    clip_id: str,
    nome_clip: str,
    arquivo: str,
    start: int,
    end: int,
    in_frame: int,
    out_frame: int,
    rate_xml: str,
    tc_frame: int = 0,
    reel: str = "",
) -> str:
    """Bloco <clipitem> do XML do FCP 7."""
    reel_xml = f"<reel><name>{reel}</name></reel>" if reel else ""
    url = f"file://localhost/C%3a/media/{arquivo.replace(' ', '%20')}"
    return (
        f'<clipitem id="{clip_id}"><name>{nome_clip}</name><duration>240</duration>{rate_xml}'
        f"<start>{start}</start><end>{end}</end><in>{in_frame}</in><out>{out_frame}</out>"
        f'<file id="file-{clip_id}"><name>{arquivo}</name><pathurl>{url}</pathurl>{reel_xml}'
        f"{rate_xml}<duration>240</duration><timecode>{rate_xml}<string>00:00:00:00</string>"
        f"<frame>{tc_frame}</frame><displayformat>NDF</displayformat></timecode></file></clipitem>"
    )


def _xml_sequencia(
    nome: str,
    duration: int,
    rate_xml: str,
    start_tc: int,
    v1_body: str,
    extra_video_track: str = "",
    audio_body: str = "",
) -> str:
    """Documento xmeml completo para testes do leitor de corte."""
    v2 = f"<track>{extra_video_track}</track>" if extra_video_track else ""
    aud = f"<audio><track>{audio_body}</track></audio>" if audio_body else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
        f'<sequence id="seq-{nome}"><name>{nome}</name><duration>{duration}</duration>{rate_xml}'
        f"<timecode>{rate_xml}<string>01:00:00:00</string><frame>{start_tc}</frame>"
        f"<displayformat>NDF</displayformat></timecode>"
        f"<media><video><track>{v1_body}</track>{v2}</video>{aud}</media>"
        "</sequence></xmeml>\n"
    )


def _xml_23976_com_gap(com_reel: bool = False) -> str:
    """XML do FCP 7 a 23.976 fps com V1 (clip, gap, clip), V2 e audio."""
    rate = _xml_rate(24, True)
    reel = "Cam A 001" if com_reel else ""
    v1_c1 = _xml_clipitem("v1-c1", "Shot A", "My Shot 01.mov", 0, 48, 24, 72, rate, 864000, reel)
    v1_c2 = _xml_clipitem("v1-c2", "Shot B", "scene 02_v1.mxf", 72, 120, 0, 48, rate, 0)
    v2_c1 = _xml_clipitem("v2-c1", "Ignored V2", "overlay.mov", 0, 24, 0, 24, rate, 0)
    a1_c1 = _xml_clipitem("a1-c1", "dial.wav", "dial.wav", 0, 48, 0, 48, rate, 0)
    return _xml_sequencia("REEL_TEST", 120, rate, 86400, v1_c1 + v1_c2, v2_c1, a1_c1)


def _xml_com_transicao() -> str:
    """XML do FCP 7 com dois clips ligados por uma transicao centrada."""
    rate = _xml_rate(24, False)
    c1 = _xml_clipitem("c1", "Shot 1", "shot_1.mov", 0, 48, 10, 58, rate, 0)
    trans = (
        f"<transitionitem><start>36</start><end>60</end><alignment>center</alignment>{rate}"
        "<effect><name>Cross Dissolve</name><effectid>Cross Dissolve</effectid>"
        "<effectcategory>Dissolve</effectcategory><effecttype>transition</effecttype>"
        "</effect></transitionitem>"
    )
    c2 = _xml_clipitem("c2", "Shot 2", "shot_2.mov", 48, 96, 20, 68, rate, 0)
    return _xml_sequencia("TRANS_TEST", 96, rate, 86400, c1 + trans + c2)


def test_edl_24fps_com_tres_cortes_e_dissolve(tmp_path: Path):
    caminho = _escrever(tmp_path, "cut24.edl", _EDL_24)
    report = Report(strict=True)
    corte = read_cut(caminho, report, frame_rate=TimecodeRate(24, 1))

    assert corte is not None
    assert corte.path == str(caminho)
    assert corte.rate == TimecodeRate(24, 1)
    assert corte.start_frames == 86400
    assert len(corte.events) == 3

    e0, e1, e2 = corte.events
    assert e0 == CutEvent(0, "clip", "R001", "R001", 86400, 86448, 86400, 86448, None, 0)
    assert e1 == CutEvent(1, "clip", "R002", "R002", 172800, 172872, 86448, 86520, 86448, 24)
    assert e2 == CutEvent(2, "clip", "R003", "R003", 259224, 259260, 86520, 86556, None, 0)


def test_edl_25fps_com_tres_cortes_e_dissolve(tmp_path: Path):
    caminho = _escrever(tmp_path, "cut25.edl", _EDL_25)
    report = Report(strict=True)
    corte = read_cut(caminho, report, frame_rate=TimecodeRate(25, 1))

    assert corte is not None
    assert corte.rate == TimecodeRate(25, 1)
    assert corte.start_frames == 90000
    assert len(corte.events) == 3

    e0, e1, e2 = corte.events
    assert (e0.record_in, e0.record_out, e0.source_in, e0.source_out) == (
        90000,
        90025,
        90000,
        90025,
    )
    assert (e0.transition_start, e0.transition_frames) == (None, 0)
    assert e1.reel == "A001C002"
    assert e1.source == "A001C002"
    assert (e1.record_in, e1.record_out, e1.source_in, e1.source_out) == (
        90025,
        90075,
        90250,
        90300,
    )
    assert (e1.transition_start, e1.transition_frames) == (90025, 12)
    assert (e2.record_in, e2.record_out, e2.source_in, e2.source_out) == (
        90075,
        90110,
        90505,
        90540,
    )


def test_edl_sem_taxa_falha_com_codigo_esperado(tmp_path: Path):
    caminho = _escrever(tmp_path, "cut24.edl", _EDL_24)
    report = Report(strict=False)
    corte = read_cut(caminho, report)

    assert corte is None
    assert "RECONFORM_EDL_RATE_REQUIRED" in _codigos(report)

    with pytest.raises(StrictModeError):
        read_cut(caminho, Report(strict=True))


def test_xml_23976_com_gap_e_dois_clips(tmp_path: Path):
    caminho = _escrever(tmp_path, "cut23976.xml", _xml_23976_com_gap(com_reel=False))
    report = Report(strict=True)
    corte = read_cut(caminho, report)

    assert corte is not None
    assert corte.rate == TimecodeRate(24000, 1001, False)
    assert corte.start_frames == 86400
    assert len(corte.events) == 3

    e0, gap, e2 = corte.events
    assert e0 == CutEvent(0, "clip", "MYSHOT01", None, 864024, 864072, 86400, 86448, None, 0)
    assert gap == CutEvent(1, "gap", None, None, None, None, 86448, 86472, None, 0)
    assert e2 == CutEvent(2, "clip", "SCENE02_V1", None, 0, 48, 86472, 86520, None, 0)


def test_normalizacao_de_chave_reel_vs_arquivo(tmp_path: Path):
    assert normalize_source(" a001 c002 ", "ignored.mov") == "A001C002"
    assert normalize_source(None, "C:/dir/My Clip 01.MXF") == "MYCLIP01"
    assert normalize_source("   ", "sub/take 03.wav") == "TAKE03"
    assert normalize_source(None, None) is None

    caminho = _escrever(tmp_path, "cut_reel.xml", _xml_23976_com_gap(com_reel=True))
    corte = read_cut(caminho, Report(strict=True))
    assert corte is not None
    primeiro = corte.events[0]
    assert primeiro.reel == "Cam A 001"
    assert primeiro.source == "CAMA001"


def test_xml_transicao_centrada_preserva_ponto_de_corte(tmp_path: Path):
    caminho = _escrever(tmp_path, "cut_trans.xml", _xml_com_transicao())
    report = Report(strict=True)
    corte = read_cut(caminho, report)

    assert corte is not None
    assert len(corte.events) == 2
    c1, c2 = corte.events
    assert (c1.record_in, c1.record_out) == (86400, 86448)
    assert (c2.record_in, c2.record_out) == (86448, 86496)
    assert c2.transition_start == 86436
    assert c2.transition_frames == 24
    assert (c2.source_in, c2.source_out) == (20, 68)


def test_edl_sem_reel_usa_source_file_ou_from_clip_name(monkeypatch, tmp_path: Path):
    from aafton.formats import otio_read

    c1 = otio.schema.Clip(
        name="clip_from_comment.mov",
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(0, 24),
            otio.opentime.RationalTime(24, 24),
        ),
        metadata={"cmx_3600": {"comments": ["SOURCE FILE: Cam B 02.MXF"]}},
    )
    c2 = otio.schema.Clip(
        name="Shot Name 02.mov",
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(24, 24),
            otio.opentime.RationalTime(24, 24),
        ),
        metadata={"cmx_3600": {"comments": []}},
    )
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.extend([c1, c2])
    timeline = otio.schema.Timeline(name="NO_REEL")
    timeline.tracks.append(track)

    monkeypatch.setattr(otio_read, "_ler_timeline", lambda *_args: timeline)
    caminho = _escrever(tmp_path, "no_reel.edl", "TITLE: NO_REEL\nFCM: NON-DROP FRAME\n")
    corte = read_cut(caminho, Report(strict=True), frame_rate=TimecodeRate(24, 1))

    assert corte is not None
    assert [e.source for e in corte.events] == ["CAMB02", "SHOTNAME02"]
    assert [e.reel for e in corte.events] == [None, None]


@pytest.mark.parametrize("nome", ["corte.fcpxml", "corte.txt"])
def test_formato_sem_suporte_reporta_erro(tmp_path: Path, nome: str):
    caminho = _escrever(tmp_path, nome, "")
    report = Report(strict=False)
    corte = read_cut(caminho, report)

    assert corte is None
    assert "RECONFORM_FORMAT_UNSUPPORTED" in _codigos(report)

    with pytest.raises(StrictModeError):
        read_cut(caminho, Report(strict=True))


def test_dataclasses_cut_ir_sao_congeladas():
    evento = CutEvent(0, "clip", "R001", "R001", 0, 24, 0, 24, None, 0)
    corte = CutIR("cut.edl", TimecodeRate(24, 1), 0, [evento])

    with pytest.raises(FrozenInstanceError):
        evento.record_in = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        corte.start_frames = 10  # type: ignore[misc]
