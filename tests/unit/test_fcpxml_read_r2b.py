"""Testes da segunda revisao do leitor de FCPXML: storyline, corte e audio.

Os documentos foram escritos a mao a partir das sondas da revisao: uma
storyline secundaria dentro de um gap, um item de audio conectado, um audition,
um clip so de video, um corte de audio, um panner, os componentes de canal e um
conform-rate neutro. Os recursos e o documento sao os da outra revisao: sao os
mesmos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.formats.fcpxml_read import read_fcpxml
from aafton.report import Report
from aafton.report import StrictModeError
from tests.unit.test_fcpxml_read_r2 import _codigos
from tests.unit.test_fcpxml_read_r2 import _documento

TAXA = 48000


def _gap(conteudo: str) -> str:
    """O gap da storyline, onde os clips conectados ficam."""
    return f'<gap name="Gap" offset="3600s" start="3600s" duration="20s">{conteudo}</gap>'


def _gravar(tmp_path: Path, texto: str, nome: str = "caso.fcpxml") -> Path:
    """Grava um FCPXML de teste e devolve o caminho."""
    caminho = tmp_path / nome
    caminho.write_text(texto, encoding="utf-8")
    return caminho


def test_storyline_secundaria_no_gap(tmp_path):
    """A storyline do gap fica na lane dela e os clips no tempo certo."""
    spine = _gap(
        '<spine lane="-1" offset="3602s">'
        '<asset-clip name="dial02" ref="r3" offset="0s" start="36001s" duration="3s"/>'
        '<asset-clip name="dial02b" ref="r3" offset="3s" start="36010s" duration="2s"/>'
        "</spine>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    assert [t.name for t in sessao.tracks] == ["A1"]
    clips = list(sessao.all_clips())
    assert [c.name for c in clips] == ["dial02", "dial02b"]
    assert [c.timeline_start_samples for c in clips] == [3602 * TAXA, 3605 * TAXA]
    assert [c.timeline_end_samples for c in clips] == [3605 * TAXA, 3607 * TAXA]
    assert [c.source_offset_samples for c in clips] == [TAXA, 10 * TAXA]


def test_storyline_secundaria_no_asset_clip_primario(tmp_path):
    """A storyline presa ao clip primario fica no offset que ela declara."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="20s">'
        '<spine lane="-1" offset="2s">'
        '<asset-clip name="dial02" ref="r3" offset="0s" start="36001s" duration="3s"/>'
        "</spine></asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    assert [t.name for t in sessao.tracks] == ["A1", "A2"]
    clips = list(sessao.all_clips())
    assert [c.name for c in clips] == ["A001", "dial02"]
    assert [c.timeline_start_samples for c in clips] == [3600 * TAXA, 3602 * TAXA]
    assert [c.timeline_end_samples for c in clips] == [3620 * TAXA, 3605 * TAXA]


def test_item_audio_conectado_vira_clip(tmp_path):
    """O item <audio> com ref de asset e lido como o audio de um clip."""
    spine = _gap(
        '<audio name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s"'
        ' duration="3s" role="dialogue"/>'
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    assert [t.name for t in sessao.tracks] == ["A1"]
    clips = list(sessao.all_clips())
    assert [c.name for c in clips] == ["dial02"]
    assert [c.timeline_start_samples for c in clips] == [3602 * TAXA]
    assert [c.timeline_end_samples for c in clips] == [3605 * TAXA]
    assert [c.source_offset_samples for c in clips] == [TAXA]


def test_audition_e_erro_de_aninhamento(tmp_path):
    """O audition e erro: o leitor nao sabe comparar duas trilhas."""
    spine = _gap(
        '<audition lane="-1" offset="3602s">'
        '<asset-clip name="dial02" ref="r3" offset="3602s" start="36001s" duration="3s"/>'
        "</audition>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_NESTED_UNSUPPORTED" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_NESTED_UNSUPPORTED" in str(erro.value)


def test_clip_so_de_video_nao_vira_audio(tmp_path):
    """Um clip so de video nao vira audio, mas os filhos dele andam."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s"'
        ' srcEnable="video" tcFormat="NDF">'
        '<asset-clip name="A001" ref="r2" lane="-1" offset="0s" start="0s" duration="10s"'
        ' srcEnable="audio"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    assert [t.name for t in sessao.tracks] == ["A1"]
    clips = list(sessao.all_clips())
    assert [c.name for c in clips] == ["A001"]
    assert [c.timeline_start_samples for c in clips] == [3600 * TAXA]
    assert [c.timeline_end_samples for c in clips] == [3610 * TAXA]


def test_corte_de_audio_comeca_antes_do_video(tmp_path):
    """O audioStart corta o audio antes do video e dura o audioDuration."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="10s" duration="10s"'
        ' audioStart="8s" audioDuration="14s" tcFormat="NDF"/>'
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    clips = list(sessao.all_clips())
    assert len(clips) == 1
    assert clips[0].timeline_start_samples == 3598 * TAXA
    assert clips[0].timeline_end_samples == 3612 * TAXA
    assert clips[0].source_offset_samples == 8 * TAXA


def test_panner_que_mexe_no_balance_e_erro(tmp_path):
    """O panner que nao e neutro e erro; o neutro passa."""
    spine = _gap(
        '<asset-clip name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s"'
        ' duration="3s"><adjust-panner amount="-50"/></asset-clip>'
        '<asset-clip name="dial02b" ref="r3" lane="-1" offset="3606s" start="36001s"'
        ' duration="3s"><adjust-panner amount="0"/></asset-clip>'
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert _codigos(report).count("FORMAT_PAN_UNSUPPORTED") == 1
    assert len(list(sessao.all_clips())) == 2
    with pytest.raises(StrictModeError):
        read_fcpxml(caminho, Report(strict=True))


def test_ganho_do_componente_multiplica_o_ganho_do_clip(tmp_path):
    """O adjust-volume do componente ligado multiplica o ganho do clip."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1, 2" role="music.music-1">'
        '<adjust-volume amount="-20dB"/></audio-channel-source>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(10 ** (-20 / 20))]
    assert [t.channel_count for t in sessao.tracks] == [2]


def test_ganho_do_componente_com_keyframe_e_erro(tmp_path):
    """O ganho do componente com keyframe e erro e o clip fica sem ganho."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1, 2" role="music.music-1">'
        '<adjust-volume amount="-20dB"><param name="volume">'
        '<keyframe time="0s" value="-20dB"/></param></adjust-volume>'
        "</audio-channel-source></asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_GAIN_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert len(clips) == 1
    assert clips[0].gain_linear == pytest.approx(1.0)
    with pytest.raises(StrictModeError):
        read_fcpxml(caminho, Report(strict=True))


def test_componente_desligado_deixa_o_clip_fora(tmp_path):
    """Um componente de canal desligado deixa o clip sem audio."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1, 2" role="music.music-1" active="0"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_CLIP_DISABLED" in str(erro.value)


def test_mute_com_faixa_e_erro_de_ganho(tmp_path):
    """Um mute com faixa e erro de ganho e o clip continua no lugar."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1, 2" role="music.music-1">'
        '<mute start="1s" duration="1s"/></audio-channel-source>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_GAIN_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert len(clips) == 1
    assert clips[0].timeline_start_samples == 3602 * TAXA
    with pytest.raises(StrictModeError):
        read_fcpxml(caminho, Report(strict=True))


def test_mute_do_clip_inteiro_deixa_o_clip_fora(tmp_path):
    """Um mute sem faixa apaga o clip inteiro."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        "<mute/>"
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_CLIP_DISABLED" in _codigos(report)
    assert list(sessao.all_clips()) == []
    with pytest.raises(StrictModeError) as erro:
        read_fcpxml(caminho, Report(strict=True))
    assert "FORMAT_CLIP_DISABLED" in str(erro.value)


def test_dual_mono_com_um_componente_ligado_e_mono(tmp_path):
    """Com um componente ligado, o clip escolhe so um canal do arquivo.

    O produto nao extrai canal: o Live tocaria o arquivo inteiro. O clip fica
    com todos os canais do asset, e a escolha parcial e erro.
    """
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
        '<audio-channel-source srcCh="2" role="dialogue.dialogue-2" enabled="0"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert [t.channel_count for t in sessao.tracks] == [2]
    clips = list(sessao.all_clips())
    assert [c.channel_index for c in clips] == [0]


def test_dual_mono_com_dois_componentes_ligados_e_erro(tmp_path):
    """Dois componentes ligados nao cabem num clip so: e erro."""
    spine = _gap(
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
        '<audio-channel-source srcCh="2" role="dialogue.dialogue-2"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 1
    with pytest.raises(StrictModeError):
        read_fcpxml(caminho, Report(strict=True))


def test_conform_rate_neutro_passa_e_o_que_escala_e_erro(tmp_path):
    """Um conform-rate que nao escala nao muda a velocidade do clip."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<conform-rate scaleEnabled="0" srcFrameRate="29.97"/>'
        "</asset-clip>"
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0
    clips = list(sessao.all_clips())
    assert len(clips) == 1
    assert clips[0].timeline_start_samples == 3600 * TAXA
    assert clips[0].timeline_end_samples == 3610 * TAXA
    assert clips[0].source_offset_samples == 0

    escalado = spine.replace('scaleEnabled="0"', 'scaleEnabled="1"')
    outro = Report(strict=False)

    read_fcpxml(_gravar(tmp_path, _documento(escalado), "escalado.fcpxml"), outro)

    assert "FORMAT_EFFECT_UNSUPPORTED" in _codigos(outro)


def test_marcadores_e_metadados_do_fcp_nao_viram_erro(tmp_path):
    """Chapter marker vira marcador; analysis marker, role source e caption nao sao erro."""
    spine = _gap(
        '<asset-clip name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s" duration="3s">'
        '<chapter-marker start="36002s" duration="1/25s" value="Capitulo"/>'
        '<analysis-marker start="36001s" duration="1s"/>'
        '<audio-role-source role="dialogue"/>'
        "</asset-clip>"
        '<caption name="Legenda" lane="1" offset="3604s" duration="2s"/>'
    )
    caminho = _gravar(tmp_path, _documento(spine))
    report = Report(strict=False)

    sessao = read_fcpxml(caminho, report)

    assert report.count("error") == 0, _codigos(report)
    assert [m.name for m in sessao.markers] == ["Capitulo"]
    assert sessao.markers[0].position_samples == 3603 * TAXA
    assert [c.name for c in sessao.all_clips()] == ["dial02"]
