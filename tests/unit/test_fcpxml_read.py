"""Testes do leitor de FCPXML do produto, com tempos exatos e sem o extra.

O leitor de FCPXML nao usa o OpenTimelineIO: os tempos sao exatos, o audio
que vem na storyline principal nao se perde e o que o formato nao carrega
vira erro no relatorio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import aafton.formats
from aafton.formats import ExtraAusente
from aafton.formats import otio_read
from aafton.ir import ClipIR, MarkerIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.report import StrictModeError
from aafton.timecode import TimecodeRate

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "formatos" / "cena12.fcpxml"

INICIO = 172800000
MARCADOR = 172896000
TAXA = 48000
DURACAO = 96000

RECURSOS = (
    "<resources>"
    '<format id="r1" name="FFVideoFormat1080p25" frameDuration="1/25s"/>'
    '<asset id="r2" name="dial01.wav" start="0s" duration="10s" hasAudio="1"'
    ' audioSources="1" audioChannels="1" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/media/dial01.wav"/>'
    "</asset>"
    "</resources>"
)

SEQUENCIA = 'format="r1" duration="8s" tcStart="3600s" tcFormat="NDF" audioRate="48k"'

# Spine com um clip de audio dentro de um gap da storyline.
CLIP_NO_GAP = (
    '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
    '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1"/>'
    "</gap>"
)


def _modulo(nome: str):
    """Modulo do pacote de formatos.

    O import acontece dentro da funcao para que a falta do modulo apareca
    como falha de cada teste, e nao como erro de coleta do arquivo inteiro.
    """
    from importlib import import_module

    return import_module(f"aafton.formats.{nome}")


def _codigos(report: Report) -> list[str]:
    """Codigos de todos os problemas registrados no relatorio."""
    return [issue.code for issue in report.issues]


def _fcpxml(spine: str, sequencia: str = SEQUENCIA, recursos: str = RECURSOS) -> str:
    """FCPXML de teste, com a spine, a sequencia e os recursos dados."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fcpxml version="1.9">'
        f"{recursos}"
        '<library><event name="CENA"><project name="CENA">'
        f"<sequence {sequencia}><spine>{spine}</spine></sequence>"
        "</project></event></library>"
        "</fcpxml>"
    )


def _gravar(tmp_path: Path, texto: str) -> Path:
    """Grava um FCPXML de teste e devolve o caminho."""
    caminho = tmp_path / "cena.fcpxml"
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def _cena(taxa: TimecodeRate) -> SessionIR:
    """Cena de teste: um clip de dois segundos e um marcador."""
    midia = MediaAsset(id="m0", filename="dial01.wav", channels=1, sample_rate=TAXA)
    track = TrackIR(id="t0", source_index=0, name="A1", kind="audio", channel_count=1)
    clip = ClipIR(
        id="t0c0_ch0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="dial01.wav",
        media_id="m0",
        timeline_start_samples=INICIO,
        timeline_end_samples=INICIO + DURACAO,
        source_offset_samples=0,
    )
    track.clips.append(clip)
    return SessionIR(
        name="CENA",
        sample_rate=TAXA,
        timecode_rate=taxa,
        start_timecode_samples=INICIO,
        tracks=[track],
        media=[midia],
        markers=[MarkerIR(id="k0", name="TESTE", position_samples=MARCADOR)],
    )


def test_le_o_fcpxml_da_fixture():
    """A fixture da os mesmos numeros de antes: tempos, offsets e marcador."""
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(FIXTURE, report)

    assert sessao.name == "CENA12"
    assert sessao.sample_rate == TAXA
    assert sessao.timecode_rate == TimecodeRate(25, 1)
    assert sessao.start_timecode_samples == INICIO
    assert sessao.source_application == "fcpxml"
    assert [t.id for t in sessao.tracks] == ["t0", "t1"]
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]
    assert [t.kind for t in sessao.tracks] == ["audio", "audio"]

    clips = list(sessao.all_clips())
    assert [c.id for c in clips] == ["t0c0_ch0", "t0c1_ch0", "t1c0_ch0"]
    assert [c.timeline_start_samples for c in clips] == [172800000, 172992000, 172848000]
    assert [c.timeline_end_samples for c in clips] == [172896000, 173040000, 172944000]
    assert [c.source_offset_samples for c in clips] == [48000, 240000, 0]
    assert [(m.name, m.position_samples) for m in sessao.markers] == [("TESTE", 172896000)]
    assert [m.declared_location for m in sessao.media] == [
        "C:/media/dial01.wav",
        "C:/media/dial02.wav",
    ]
    assert [m.channels for m in sessao.media] == [1, 1]
    assert report.count("error") == 0


@pytest.mark.parametrize(
    "taxa",
    [TimecodeRate(30000, 1001, True), TimecodeRate(24000, 1001)],
    ids=["29.97 drop frame", "23.976"],
)
def test_cena_escrita_volta_com_os_mesmos_samples(tmp_path, taxa):
    """A cena escrita pelo produto volta com os mesmos samples e o drop frame."""
    destino = tmp_path / "cena.fcpxml"
    escrita = Report(strict=False)
    _modulo("fcpxml_write").write_fcpxml(_cena(taxa), destino, escrita)
    assert escrita.count("error") == 0

    sessao = _modulo("fcpxml_read").read_fcpxml(destino, Report(strict=False))

    assert sessao.timecode_rate == taxa
    assert sessao.start_timecode_samples == INICIO
    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [INICIO]
    assert [c.timeline_end_samples for c in clips] == [INICIO + DURACAO]
    assert [c.source_offset_samples for c in clips] == [0]
    assert [(m.name, m.position_samples) for m in sessao.markers] == [("TESTE", MARCADOR)]


def test_clip_fora_do_frame_volta_no_mesmo_sample(tmp_path):
    """Um clip 101 samples fora do frame volta no mesmo sample, sem aviso."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="172800101/48000s" start="0s"'
        ' duration="2s" lane="-1"/>'
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [INICIO + 101]
    assert [c.timeline_end_samples for c in clips] == [INICIO + 101 + DURACAO]
    assert "FORMAT_SUBSAMPLE_POSITION" not in _codigos(report)


def test_adjust_volume_da_o_ganho_linear(tmp_path):
    """Um adjust-volume de -6 dB vira o ganho linear do clip."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1">'
        '<adjust-volume amount="-6dB"/>'
        "</asset-clip>"
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=False))

    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(10 ** (-6 / 20))]


@pytest.mark.parametrize(
    ("dentro", "codigo"),
    [
        (
            '<adjust-volume amount="-6dB"><param name="volume">'
            '<keyframe time="0s" value="-6dB"/></param></adjust-volume>',
            "FORMAT_GAIN_UNSUPPORTED",
        ),
        ('<fadeIn duration="1s"/>', "FORMAT_FADE_UNSUPPORTED"),
        ('<filter-audio kind="lowpass"/>', "FORMAT_EFFECT_UNSUPPORTED"),
    ],
    ids=["ganho com keyframe", "fade", "filtro de audio"],
)
def test_item_sem_suporte_falha_fechado(tmp_path, dentro, codigo):
    """Fade, filtro e ganho com keyframe sao erro e paramam a conversao."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1">'
        f"{dentro}"
        "</asset-clip>"
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert codigo in _codigos(report)
    assert len(list(sessao.all_clips())) == 1
    with pytest.raises(StrictModeError):
        _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=True))


def test_fade_dentro_do_volume_falha_fechado(tmp_path):
    """Um fade dentro do adjust-volume e erro e o ganho constante fica."""
    spine = (
        '<asset-clip name="CENA" ref="r2" offset="3600s" start="0s" duration="2s"'
        ' hasVideo="1" hasAudio="1">'
        '<adjust-volume amount="-3dB"><param name="amount">'
        '<fadeIn type="easeIn" duration="1001/30000s"/></param></adjust-volume>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert "FORMAT_FADE_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(10 ** (-3 / 20))]
    with pytest.raises(StrictModeError):
        _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=True))


def test_clip_desativado_falha_fechado(tmp_path):
    """Um clip com enabled="0" e erro e nao vira clip."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s"'
        ' lane="-1" enabled="0"/>'
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError):
        _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=True))


def test_clip_da_storyline_principal_vira_audio(tmp_path):
    """Um asset-clip da storyline principal com audio vira clip na A1."""
    spine = (
        '<asset-clip name="CENA" ref="r2" offset="3600s" start="0s" duration="2s"'
        ' hasVideo="1" hasAudio="1"/>'
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=False))

    assert [t.id for t in sessao.tracks] == ["t0"]
    assert [t.name for t in sessao.tracks] == ["A1"]
    clips = list(sessao.all_clips())
    assert [c.timeline_start_samples for c in clips] == [INICIO]
    assert [c.timeline_end_samples for c in clips] == [INICIO + DURACAO]
    assert [c.channel_index for c in clips] == [0]


def test_clip_conectado_dentro_do_asset_clip_da_spine(tmp_path):
    """O audio conectado e o marcador do asset-clip da spine nao se perdem."""
    recursos = RECURSOS.replace(
        "</resources>",
        '<asset id="r3" name="cena01.mov" start="0s" duration="10s" hasVideo="1"'
        ' hasAudio="0"/></resources>',
    )
    spine = (
        '<asset-clip name="CENA" ref="r3" offset="3600s" start="10s" duration="4s"'
        ' hasVideo="1" hasAudio="0">'
        '<asset-clip name="dial01.wav" ref="r2" offset="12s" start="0s" duration="2s" lane="-1"/>'
        '<marker value="TESTE" start="13s"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine, recursos=recursos))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert report.count("error") == 0
    assert [t.name for t in sessao.tracks] == ["A1"]
    clips = list(sessao.all_clips())
    assert [c.name for c in clips] == ["dial01.wav"]
    assert [c.timeline_start_samples for c in clips] == [(3600 + 2) * TAXA]
    assert [c.timeline_end_samples for c in clips] == [(3600 + 4) * TAXA]
    assert [c.source_offset_samples for c in clips] == [0]
    marcadores = [(m.name, m.position_samples) for m in sessao.markers]
    assert marcadores == [("TESTE", (3600 + 3) * TAXA)]


def test_asset_estereo_vira_uma_track_de_dois_canais(tmp_path):
    """Um asset de dois canais vira um clip numa track de dois canais."""
    recursos = RECURSOS.replace('audioChannels="1"', 'audioChannels="2"')
    caminho = _gravar(tmp_path, _fcpxml(CLIP_NO_GAP, recursos=recursos))

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=False))

    assert [t.channel_count for t in sessao.tracks] == [2]
    clips = list(sessao.all_clips())
    assert [c.channel_index for c in clips] == [0]
    assert [m.channels for m in sessao.media] == [2]


def test_audio_channel_source_escolhe_o_canal(tmp_path):
    """Um srcCh que o arquivo nao tem e erro: o clip fica no canal zero."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1">'
        '<audio-channel-source srcCh="2"/>'
        "</asset-clip>"
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.channel_index for c in clips] == [0]
    assert [t.channel_count for t in sessao.tracks] == [1]


def test_item_de_video_e_pulado_uma_vez(tmp_path):
    """Um item so de video e pulado, com um aviso so no relatorio."""
    spine = (
        '<video ref="r2" offset="3600s" start="0s" duration="2s"/>'
        '<title name="CENA" offset="3600s" start="0s" duration="2s"/>'
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert list(sessao.all_clips()) == []
    assert _codigos(report).count("FORMAT_VIDEO_TRACK_SKIPPED") == 1


def test_transicao_falha_fechado(tmp_path):
    """Uma transicao na spine e erro e paramam a conversao."""
    spine = (
        '<gap name="CENA" offset="3600s" start="3600s" duration="2s">'
        '<asset-clip name="dial01.wav" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1"/>'
        '<transition name="dissolve" offset="3600s" start="3600s" duration="1s"/>'
        "</gap>"
    )
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert "FORMAT_TRANSITION_UNSUPPORTED" in _codigos(report)
    with pytest.raises(StrictModeError):
        _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=True))


def test_ref_clip_falha_fechado(tmp_path):
    """Um ref-clip e erro de aninhamento e nao vira clip."""
    spine = '<ref-clip name="OUTRO" ref="r2" offset="3600s" start="0s" duration="2s" lane="-1"/>'
    caminho = _gravar(tmp_path, _fcpxml(spine))
    report = Report(strict=False)

    sessao = _modulo("fcpxml_read").read_fcpxml(caminho, report)

    assert "FORMAT_NESTED_UNSUPPORTED" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError):
        _modulo("fcpxml_read").read_fcpxml(caminho, Report(strict=True))


def test_le_sem_o_extra_formats(monkeypatch):
    """O FCPXML e lido sem o extra formats, que nao e preciso."""

    def sem_extra():
        raise ExtraAusente("the formats extra is not installed")

    monkeypatch.setattr(aafton.formats, "carregar_otio", sem_extra)
    monkeypatch.setattr(otio_read, "carregar_otio", sem_extra)
    report = Report(strict=False)

    sessao = otio_read.read_timeline(FIXTURE, report)

    assert sessao.start_timecode_samples == INICIO
    assert len(list(sessao.all_clips())) == 3
    assert report.count("error") == 0
