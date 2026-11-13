"""Testes da quarta revisao do leitor de FCPXML: efeitos, canais e volume.

Os documentos vem das sondas da revisao: o fade e o filtro de um item de
audio (primario, conectado e no gap), o asset de quatro, seis e oito canais,
o roteamento das saidas (outCh), o canal escolhido que o arquivo nao tem e o
volume que so vem no parametro do FCPXML.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.cli import main
from aafton.formats.fcpxml_read import read_fcpxml
from aafton.formats.origem import conferir_canais
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_fcpxml_read_r3 import _codigos
from tests.unit.test_fcpxml_read_r3 import _ler
from tests.unit.test_fcpxml_read_r3 import _mensagem

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000


def _documento_multicanal(nome: str, canais: int, src: str) -> str:
    """FCPXML de um asset multicanal, com o arquivo de midia dado."""
    recursos = (
        "<resources>"
        '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        f'<asset id="r8" name="{nome}" start="0s" duration="4s" hasAudio="1"'
        f' audioSources="1" audioChannels="{canais}" audioRate="48000">'
        f'<media-rep kind="original-media" src="{src}"/>'
        "</asset>"
        "</resources>"
    )
    spine = f'<asset-clip name="{nome}" ref="r8" offset="3600s" start="1s" duration="2s"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{recursos}"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" tcStart="3600s" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence></project></event></library></fcpxml>"
    )


def _ler_multicanal(tmp_path: Path, nome: str, canais: int, src: str):
    """Grava o documento multicanal, le o FCPXML e devolve o relatorio e a cena."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(_documento_multicanal(nome, canais, src), encoding="utf-8")
    report = Report(strict=False)
    return report, read_fcpxml(caminho, report)


def _gap(conteudo: str) -> str:
    """O gap da storyline, onde os clips conectados ficam."""
    return f'<gap name="Gap" offset="3600s" start="3600s" duration="30s">{conteudo}</gap>'


def test_fade_do_audio_primario_e_erro_do_audio(tmp_path):
    """O fade do item de audio e erro do audio, com o nome dele."""
    spine = (
        '<audio name="dial02" ref="r3" offset="3600s" start="36000s" duration="10s">'
        '<adjust-volume><param name="amount"><fadeIn type="easeIn" duration="1s"/></param>'
        "</adjust-volume></audio>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert _codigos(report).count("FORMAT_FADE_UNSUPPORTED") == 1
    assert "dial02" in _mensagem(report, "FORMAT_FADE_UNSUPPORTED")
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_fade_do_audio_conectado_e_erro_do_audio(tmp_path):
    """O fade do audio conectado e erro do audio, nao do clip primario."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<audio name="dial02" ref="r3" lane="-1" offset="2s" start="36001s" duration="3s">'
        '<adjust-volume><param name="amount"><fadeIn type="easeIn" duration="1s"/></param>'
        "</adjust-volume></audio></asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert _codigos(report).count("FORMAT_FADE_UNSUPPORTED") == 1
    mensagem = _mensagem(report, "FORMAT_FADE_UNSUPPORTED")
    assert "dial02" in mensagem
    assert "A001" not in mensagem
    assert [c.name for c in sessao.all_clips()] == ["A001", "dial02"]


def test_filtro_de_audio_do_audio_conectado_e_erro(tmp_path):
    """O filtro de audio do audio conectado e erro do audio."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<audio name="dial02" ref="r3" lane="-1" offset="2s" start="36001s" duration="3s">'
        '<filter-audio ref="rF" name="Channel EQ"/></audio></asset-clip>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert _codigos(report).count("FORMAT_EFFECT_UNSUPPORTED") == 1
    mensagem = _mensagem(report, "FORMAT_EFFECT_UNSUPPORTED")
    assert "dial02" in mensagem
    assert "A001" not in mensagem


def test_fade_out_no_gap_e_erro_do_audio(tmp_path):
    """O fadeOut do audio no gap e erro do audio, e o ganho fica em -3 dB."""
    spine = _gap(
        '<audio name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s" duration="3s">'
        '<adjust-volume amount="-3dB"><param name="amount">'
        '<fadeOut type="easeIn" duration="1s"/></param></adjust-volume></audio>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert _codigos(report).count("FORMAT_FADE_UNSUPPORTED") == 1
    assert "dial02" in _mensagem(report, "FORMAT_FADE_UNSUPPORTED")
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(10 ** (-3 / 20))]


@pytest.mark.parametrize("canais", [4, 6, 8], ids=["quatro", "seis", "oito"])
def test_asset_de_mais_de_dois_canais_e_erro(tmp_path, canais):
    """O clip de um arquivo com mais de dois canais e erro de multicanal."""
    nome = f"poly{canais}.wav"

    report, sessao = _ler_multicanal(tmp_path, nome, canais, f"file:///C:/midia/{nome}")

    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_MULTICHANNEL_UNSUPPORTED")
    assert nome in mensagem
    assert f"{canais} channels" in mensagem
    # O clip fica fora da cena: o Live nao toca tres ou mais canais.
    assert [c.name for c in sessao.all_clips()] == []


def _cena_multicanal(tmp_path: Path) -> tuple[Path, Path]:
    """A pasta com o WAV de quatro canais e o FCPXML que o aponta."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    quadro = b"\x00\x00\x00" * 4
    write_wav_with_bext(
        pasta / "poly4.wav",
        quadro * TAXA * 4,
        channels=4,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )
    origem = pasta / "poly4.fcpxml"
    origem.write_text(
        _documento_multicanal("poly4.wav", 4, (pasta / "poly4.wav").as_uri()),
        encoding="utf-8",
    )
    return origem, tmp_path / "poly4.als"


def test_import_estrito_de_wav_de_quatro_canais_falha(tmp_path, capsys):
    """O import estrito de um WAV de quatro canais para no erro de multicanal."""
    origem, als = _cena_multicanal(tmp_path)

    codigo = main(["import", str(origem), str(als), "--template", str(TEMPLATE)])

    assert codigo == 1
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in capsys.readouterr().err
    assert not als.exists()


def test_import_permissivo_de_wav_de_quatro_canais_deixa_o_clip_fora(tmp_path):
    """No permissivo o .als e escrito sem o clip, e o relatorio traz o erro."""
    origem, als = _cena_multicanal(tmp_path)
    relatorio = tmp_path / "import.json"

    codigo = main(
        [
            "import",
            str(origem),
            str(als),
            "--template",
            str(TEMPLATE),
            "--report",
            str(relatorio),
            "--permissive",
        ]
    )

    assert codigo == 1
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in [i["code"] for i in dados["issues"]]
    assert als.exists()
    sessao = read_session(als, Report(strict=False))
    assert [c.name for t in sessao.tracks for c in t.clips] == []


def _sessao_com_canais(canais: int, aplicacao: str) -> SessionIR:
    """Cena de teste com um clip de midia com a quantidade de canais dada."""
    midia = MediaAsset(id="m0", filename="poly4.wav", channels=canais)
    track = TrackIR(id="t0", source_index=0, name="A1", kind="audio", channel_count=canais)
    track.clips.append(
        ClipIR(
            id="t0c0_ch0",
            track_id="t0",
            channel_index=0,
            event_index=0,
            name="poly4",
            media_id="m0",
            timeline_start_samples=0,
            timeline_end_samples=TAXA,
        )
    )
    return SessionIR(
        name="CENA",
        sample_rate=TAXA,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[midia],
        source_application=aplicacao,
    )


def test_conferir_canais_recusa_o_arquivo_de_quatro_canais_no_fcpxml():
    """O FCPXML recusa o arquivo de mais de dois canais; a EDL recusa o par."""
    multicanal = Report(strict=False)
    par = Report(strict=False)
    edl = Report(strict=False)

    conferir_canais(_sessao_com_canais(4, "fcpxml"), multicanal)
    conferir_canais(_sessao_com_canais(2, "fcpxml"), par)
    conferir_canais(_sessao_com_canais(2, "edl"), edl)

    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(multicanal)
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" not in _codigos(par)
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(edl)


@pytest.mark.parametrize("saida", ["L", "C", "L, R"], ids=["so_l", "centro", "par"])
def test_saida_de_canal_so_e_erro(tmp_path, saida):
    """O audio de um canal so: a saida L e erro, o centro e o par passam."""
    spine = (
        '<asset-clip name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s"'
        ' duration="3s">'
        f'<audio-channel-source srcCh="1" outCh="{saida}" role="dialogue.dialogue-1"/>'
        "</asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    erro = saida == "L"
    assert ("FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)) is erro
    assert [c.channel_index for c in sessao.all_clips()] == [0]


def test_saida_trocada_de_um_par_e_erro(tmp_path):
    """O par stereo com as saidas trocadas e erro de canal."""
    spine = (
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1, 2" outCh="R, L" role="music.music-1"/></asset-clip>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert "musica" in _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert [t.channel_count for t in sessao.tracks] == [2]


def test_saida_no_item_de_audio_e_erro(tmp_path):
    """O item de audio que leva o audio para uma saida so e erro de canal."""
    spine = _gap(
        '<audio name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s"'
        ' duration="3s" srcCh="1" outCh="L"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert "dial02" in _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert [c.channel_index for c in sessao.all_clips()] == [0]


@pytest.mark.parametrize("erro", [False, True], ids=["existe", "nao_existe"])
def test_src_ch_escolhe_o_canal_do_arquivo(tmp_path, erro):
    """O canal escolhido que o arquivo nao tem e erro: o clip fica no zero."""
    srcch = "2" if erro else "1"
    spine = _gap(
        '<audio name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s"'
        f' duration="3s" srcCh="{srcch}"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert ("FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)) is erro
    assert [c.channel_index for c in sessao.all_clips()] == [0]
    assert [t.channel_count for t in sessao.tracks] == [1]


def test_volume_so_no_parametro_vira_o_ganho(tmp_path):
    """O volume que so vem no parametro amount e o nivel do clip."""
    spine = (
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<adjust-volume><param name="amount" value="-6dB"/></adjust-volume></asset-clip>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(10 ** (-6 / 20))]


def test_volume_no_parametro_com_keyframe_e_erro(tmp_path):
    """O volume do parametro amount com keyframe e erro de ganho."""
    spine = (
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<adjust-volume><param name="amount"><keyframeAnimation>'
        '<keyframe time="3602s" value="-6dB"/><keyframe time="3603s" value="0dB"/>'
        "</keyframeAnimation></param></adjust-volume></asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_GAIN_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(1.0)]


def test_papel_de_audio_com_volume_neutro_passa(tmp_path):
    """O audio-role-source com volume de 0 dB nao muda o som e passa."""
    spine = (
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s"'
        ' duration="3s" audioRole="music">'
        '<audio-role-source role="music.music-1">'
        '<adjust-volume amount="0dB"/></audio-role-source></asset-clip>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    clips = list(sessao.all_clips())
    assert [c.gain_linear for c in clips] == [pytest.approx(1.0)]


def test_saida_numerada_do_par_estereo_passa(tmp_path):
    """outCh "1, 2" e o par L, R da saida estereo: o clip estereo passa."""
    spine = (
        '<asset-clip name="musica" ref="r4" offset="3600s" start="0s" duration="5s">'
        '<audio-channel-source srcCh="1, 2" outCh="1, 2"/>'
        "</asset-clip>"
    )
    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" not in _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["musica"]


def test_componente_com_a_faixa_do_video_num_split_edit_passa(tmp_path):
    """Num split edit, o componente que repete a faixa do video nao corta nada."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="10s" duration="10s"'
        ' audioStart="8s" audioDuration="14s">'
        '<audio-channel-source srcCh="1, 2" start="10s" duration="10s"/>'
        "</asset-clip>"
    )
    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" not in _codigos(report)
