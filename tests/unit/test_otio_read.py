"""Testes do leitor de EDL, XML do FCP e FCPXML para o SessionIR."""

from pathlib import Path

import pytest

from aafton.report import Report
from aafton.report import StrictModeError
from aafton.timecode import TimecodeRate

otio = pytest.importorskip("opentimelineio")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "formatos"
EDL = FIXTURES / "cena12.edl"
XML = FIXTURES / "cena12_premiere.xml"
FCPXML = FIXTURES / "cena12.fcpxml"

INICIO = 172800000
TAXAS = TimecodeRate(25, 1)


def _modulo():
    """Modulo do leitor.

    O import acontece dentro da funcao para que a falta do modulo apareca
    como falha de cada teste, e nao como erro de coleta do arquivo inteiro.
    """
    from aafton.formats import otio_read

    return otio_read


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _clip(nome: str, inicio: int, duracao: int, taxa: float = 25):
    """Clip de teste, sem midia resolvida."""
    return otio.schema.Clip(
        name=nome,
        source_range=otio.opentime.TimeRange(
            otio.opentime.RationalTime(inicio, taxa),
            otio.opentime.RationalTime(duracao, taxa),
        ),
        media_reference=otio.schema.MissingReference(),
    )


def _track(nome: str = "A1", kind=otio.schema.TrackKind.Audio):
    """Track de teste."""
    return otio.schema.Track(name=nome, kind=kind)


def _timeline(*filhos):
    """Timeline de teste com as tracks dadas."""
    timeline = otio.schema.Timeline(name="CENA")
    for filho in filhos:
        timeline.tracks.append(filho)
    return timeline


def _timeline_com_transicao():
    """Timeline com uma transicao entre dois clips."""
    track = _track()
    track.append(_clip("a.wav", 0, 25))
    track.append(
        otio.schema.Transition(
            name="dissolve",
            transition_type=otio.schema.TransitionTypes.SMPTE_Dissolve,
            in_offset=otio.opentime.RationalTime(0, 25),
            out_offset=otio.opentime.RationalTime(0, 25),
        )
    )
    track.append(_clip("b.wav", 25, 25))
    return _timeline(track)


def _timeline_com_efeito():
    """Timeline com um clip que tem efeito de velocidade."""
    clip = _clip("a.wav", 0, 25)
    efeito = otio.schema.LinearTimeWarp()
    efeito.name = "velocidade"
    clip.effects.append(efeito)
    track = _track()
    track.append(clip)
    return _timeline(track)


def _timeline_com_stack():
    """Timeline com uma stack aninhada dentro de uma track."""
    stack = otio.schema.Stack(name="grupo")
    stack.append(_clip("a.wav", 0, 25))
    track = _track()
    track.append(stack)
    return _timeline(track)


def _usar_timeline(monkeypatch, timeline) -> None:
    """Faz o leitor usar a timeline do teste em vez do arquivo."""
    monkeypatch.setattr(
        _modulo(),
        "_ler_timeline",
        lambda _otio, _caminho, _adaptador, _edl_rate: timeline,
    )


def _rt(segundos: int, taxa: int):
    """RationalTime com o valor de segundos convertido para a taxa dada."""
    return otio.opentime.RationalTime(segundos * taxa, taxa)


def _midia(nome: str, inicio: int, duracao: int, taxa: int):
    """Referencia de midia de teste, com o available range do arquivo."""
    return otio.schema.ExternalReference(
        target_url=f"file://localhost/C%3a/media/{nome}",
        available_range=otio.opentime.TimeRange(_rt(inicio, taxa), _rt(duracao, taxa)),
    )


def _clip_com_midia(nome: str, fonte: int, duracao: int, taxa: int, referencia):
    """Clip de teste, com a midia que ele consome."""
    return otio.schema.Clip(
        name=nome,
        source_range=otio.opentime.TimeRange(_rt(fonte, taxa), _rt(duracao, taxa)),
        media_reference=referencia,
    )


def _gap(duracao: int, taxa: int):
    """Gap de teste, que empurra os clips seguintes da track."""
    return otio.schema.Gap(source_range=otio.opentime.TimeRange(_rt(0, taxa), _rt(duracao, taxa)))


def _marcador(nome: str, segundos: int, taxa: int):
    """Marcador de teste."""
    return otio.schema.Marker(
        name=nome,
        marked_range=otio.opentime.TimeRange(_rt(segundos, taxa), _rt(0, taxa)),
    )


def _escrever_xmeml(caminho: Path, timebase: int) -> None:
    """Grava um XML do FCP minimo, que so declara a taxa de frames."""
    caminho.write_text(
        f"<sequence><rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate></sequence>",
        encoding="utf-8",
    )


def _escrever_fcpxml(caminho: Path, frame_duration: str) -> None:
    """Grava um FCPXML minimo, com o formato que declara a taxa de frames."""
    caminho.write_text(
        f"<fcpxml version='1.9'><resources><format id='r1' frameDuration='{frame_duration}'/>"
        "</resources><library><event><project><sequence format='r1'/>"
        "</project></event></library></fcpxml>",
        encoding="utf-8",
    )


def _cena(taxa_inicio: int, taxa_clip: int, taxa_gap: int, taxa_midia: int):
    """Cena da fixture cena12_premiere.xml, montada nas taxas dadas.

    A sessao comeca em 01:00:00:00; a track 1 tem clips de 2 s e 1 s em
    01:00:00:00 e 01:00:04:00; a track 2 tem clip de 2 s em 01:00:01:00; os
    offsets sao de 1 s, 5 s e 0 s dentro de arquivos que comecam em
    10:00:00:00 e 00:00:00:00; e um marcador em 01:00:02:00.
    """
    dial01 = _midia("dial01.wav", 36000, 20, taxa_midia)
    dial02 = _midia("dial02.wav", 0, 10, taxa_midia)
    primeira = _track("")
    primeira.append(_clip_com_midia("dial01.wav", 36001, 2, taxa_clip, dial01))
    primeira.append(_gap(2, taxa_gap))
    primeira.append(_clip_com_midia("dial01.wav", 36005, 1, taxa_clip, dial01))
    segunda = _track("")
    segunda.append(_gap(1, taxa_gap))
    segunda.append(_clip_com_midia("dial02.wav", 0, 2, taxa_clip, dial02))
    timeline = _timeline(primeira, segunda)
    timeline.global_start_time = _rt(3600, taxa_inicio)
    timeline.tracks.markers.append(_marcador("TESTE", 2, taxa_inicio))
    return timeline


def test_edl_le_a_cena12():
    report = Report(strict=False)
    sessao = _modulo().read_timeline(EDL, report, edl_rate=TAXAS)

    assert sessao.name == "CENA12"
    assert sessao.sample_rate == 48000
    assert sessao.timecode_rate == TAXAS
    assert sessao.start_timecode_samples == INICIO
    assert sessao.source_application == "edl"
    assert sessao.source_path == str(EDL)
    assert [t.id for t in sessao.tracks] == ["t0", "t1"]
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]
    assert [t.kind for t in sessao.tracks] == ["audio", "audio"]
    assert [t.channel_count for t in sessao.tracks] == [1, 1]
    assert [t.source_index for t in sessao.tracks] == [0, 1]
    assert [t.original_source_indices for t in sessao.tracks] == [[0], [1]]

    clips = list(sessao.all_clips())
    assert [c.id for c in clips] == ["t0c0_ch0", "t0c1_ch0", "t1c0_ch0"]
    assert [c.event_index for c in clips] == [0, 1, 0]
    assert [c.channel_index for c in clips] == [0, 0, 0]
    assert [c.track_id for c in clips] == ["t0", "t0", "t1"]
    assert [c.timeline_start_samples for c in clips] == [172800000, 172992000, 172848000]
    assert [c.timeline_end_samples for c in clips] == [172896000, 173040000, 172944000]
    assert [c.source_offset_samples for c in clips] == [None, None, None]
    assert [c.raw["source_tc_samples"] for c in clips] == [1728048000, 1728240000, 0]
    assert sessao.markers == []
    assert [m.id for m in sessao.media] == ["m0", "m1"]
    assert [m.filename for m in sessao.media] == ["dial01.wav", "dial02.wav"]
    assert [m.declared_location for m in sessao.media] == ["dial01.wav", "dial02.wav"]
    assert [m.status for m in sessao.media] == ["missing", "missing"]
    assert "FORMAT_SAMPLE_RATE_ASSUMED" in _codigos(report)


def test_xml_le_a_cena12():
    report = Report(strict=False)
    sessao = _modulo().read_timeline(XML, report)

    assert sessao.name == "CENA12"
    assert sessao.sample_rate == 48000
    assert sessao.timecode_rate == TAXAS
    assert sessao.start_timecode_samples == INICIO
    assert sessao.source_application == "fcp_xml"
    assert [t.id for t in sessao.tracks] == ["t0", "t1"]
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]

    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [172800000, 172992000, 172848000]
    assert [c.timeline_end_samples for c in clips] == [172896000, 173040000, 172944000]
    assert [c.source_offset_samples for c in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in sessao.markers] == [("TESTE", 172896000)]
    assert [m.id for m in sessao.markers] == ["k0"]
    assert [m.filename for m in sessao.media] == ["dial01.wav", "dial02.wav"]
    assert [m.declared_location for m in sessao.media] == [
        "C:/media/dial01.wav",
        "C:/media/dial02.wav",
    ]
    assert "FORMAT_SAMPLE_RATE_ASSUMED" not in _codigos(report)


def test_fcpxml_le_a_cena12():
    report = Report(strict=False)
    sessao = _modulo().read_timeline(FCPXML, report)

    assert sessao.name == "CENA12"
    assert sessao.sample_rate == 48000
    assert sessao.timecode_rate == TAXAS
    assert sessao.start_timecode_samples == INICIO
    assert sessao.source_application == "fcpxml"
    assert [t.id for t in sessao.tracks] == ["t0", "t1"]
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]

    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [172800000, 172992000, 172848000]
    assert [c.timeline_end_samples for c in clips] == [172896000, 173040000, 172944000]
    assert [c.source_offset_samples for c in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in sessao.markers] == [("TESTE", 172896000)]
    assert [m.declared_location for m in sessao.media] == [
        "C:/media/dial01.wav",
        "C:/media/dial02.wav",
    ]


def test_edl_sem_taxa_falha_fechado():
    report = Report(strict=False)
    sessao = _modulo().read_timeline(EDL, report)

    assert "FORMAT_EDL_RATE_REQUIRED" in _codigos(report)
    assert sessao.tracks == []
    assert sessao.media == []
    assert sessao.markers == []

    with pytest.raises(StrictModeError):
        _modulo().read_timeline(EDL, Report(strict=True))


def test_track_de_video_e_pulada(monkeypatch, tmp_path):
    video = _track("V1", otio.schema.TrackKind.Video)
    video.append(_clip("video.mov", 0, 25))
    audio = _track()
    audio.append(_clip("a.wav", 0, 25))
    _usar_timeline(monkeypatch, _timeline(video, audio))

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert [t.id for t in sessao.tracks] == ["t0"]
    assert "FORMAT_VIDEO_TRACK_SKIPPED" in _codigos(report)


@pytest.mark.parametrize(
    ("monta", "codigo", "clips"),
    [
        (_timeline_com_transicao, "FORMAT_TRANSITION_UNSUPPORTED", 2),
        (_timeline_com_efeito, "FORMAT_EFFECT_UNSUPPORTED", 0),
        (_timeline_com_stack, "FORMAT_NESTED_UNSUPPORTED", 0),
    ],
)
def test_item_sem_suporte_falha_fechado(monkeypatch, tmp_path, monta, codigo, clips):
    _usar_timeline(monkeypatch, monta())

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert codigo in _codigos(report)
    assert len(list(sessao.all_clips())) == clips

    with pytest.raises(StrictModeError):
        _modulo().read_timeline(tmp_path / "cena.xml", Report(strict=True))


def test_edl_com_drop_frame(tmp_path):
    texto = EDL.read_text(encoding="utf-8").replace("FCM: NON-DROP FRAME", "FCM: DROP FRAME")
    destino = tmp_path / "cena12.edl"
    destino.write_text(texto, encoding="utf-8")

    report = Report(strict=False)
    sessao = _modulo().read_timeline(destino, report, edl_rate=TimecodeRate(30000, 1001, True))

    assert sessao.timecode_rate.drop_frame is True
    assert sessao.timecode_rate.num == 30000
    assert sessao.timecode_rate.den == 1001


# As taxas de cada papel do tempo: inicio da sessao, clip, gap e midia.
@pytest.mark.parametrize(
    ("taxas", "timebase"),
    [
        ((25, 25, 25, 25), None),
        ((2500, 2500, 2500, 2500), 25),
        ((2500, 48000, 48000, 25), None),
    ],
    ids=["tudo em 25", "tudo em 2500", "taxas misturadas"],
)
def test_xml_le_o_tempo_na_taxa_de_cada_tempo(monkeypatch, tmp_path, taxas, timebase):
    """Cada tempo entra na sua propria taxa e a cena sai igual a da fixture."""
    _usar_timeline(monkeypatch, _cena(*taxas))
    if timebase is not None:
        _escrever_xmeml(tmp_path / "cena.xml", timebase)

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert sessao.timecode_rate == TAXAS
    assert "FORMAT_RATE_ASSUMED" not in _codigos(report)
    assert sessao.start_timecode_samples == INICIO
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]
    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [172800000, 172992000, 172848000]
    assert [c.timeline_end_samples for c in clips] == [172896000, 173040000, 172944000]
    assert [c.source_offset_samples for c in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in sessao.markers] == [("TESTE", 172896000)]
    assert [m.filename for m in sessao.media] == ["dial01.wav", "dial02.wav"]


def test_xml_sem_taxa_de_frames_falha_fechado(monkeypatch, tmp_path):
    """Sem tempo de frames e sem taxa no arquivo, a taxa nao e suportada."""
    _usar_timeline(monkeypatch, _cena(2500, 2500, 2500, 2500))

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert "FORMAT_RATE_UNSUPPORTED" in _codigos(report)
    assert sessao.tracks == []
    assert sessao.media == []


def test_xml_usa_a_taxa_declarada_no_arquivo(monkeypatch, tmp_path):
    """A taxa do XML do FCP entra quando os tempos nao sao de frames."""
    _usar_timeline(monkeypatch, _cena(2500, 2500, 2500, 2500))
    _escrever_xmeml(tmp_path / "cena.xml", 24)

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert sessao.timecode_rate == TimecodeRate(24, 1)


def test_xml_usa_o_available_range_quando_o_clip_nao_e_de_frames(monkeypatch, tmp_path):
    """O available range em 25 fps da a taxa quando o clip vem em 48000."""
    _usar_timeline(monkeypatch, _cena(2500, 48000, 48000, 25))

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    assert sessao.timecode_rate == TimecodeRate(25, 1)
    assert "FORMAT_RATE_ASSUMED" not in _codigos(report)


def test_fcpxml_usa_o_frame_duration_do_formato(tmp_path):
    """O frameDuration do FCPXML da a taxa de frames da cena.

    O FCPXML nao passa mais pelo adaptador do OpenTimelineIO: o leitor
    proprio do produto le o frameDuration do formato da sequencia.
    """
    _escrever_fcpxml(tmp_path / "cena.fcpxml", "1001/30000s")

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.fcpxml", report)

    assert sessao.timecode_rate == TimecodeRate(30000, 1001)


def test_xml_le_valor_decimal_sem_fracao_binaria(monkeypatch, tmp_path):
    """Um tempo de 0.1 s entra como decimo, e nao como fracao binaria."""
    comeco = otio.opentime.RationalTime(0.1, 1)
    duracao = otio.opentime.RationalTime(1, 1)
    clip = otio.schema.Clip(
        name="dial01.wav",
        source_range=otio.opentime.TimeRange(comeco, duracao),
        media_reference=_midia("dial01.wav", 0, 10, 25),
    )
    track = _track("")
    track.append(clip)
    timeline = _timeline(track)
    timeline.global_start_time = _rt(3600, 25)
    _usar_timeline(monkeypatch, timeline)

    report = Report(strict=False)
    sessao = _modulo().read_timeline(tmp_path / "cena.xml", report)

    lido = list(sessao.all_clips())[0]
    assert lido.source_offset_samples == 4800
    assert lido.timeline_start_samples == INICIO
    assert lido.timeline_end_samples == 172848000
