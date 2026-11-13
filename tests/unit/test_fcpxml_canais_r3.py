"""Testes dos canais do leitor de FCPXML: selecao parcial, papel e faixa.

Os documentos vem das sondas da terceira revisao: um dual mono com o
componente do boom desligado, um item de audio que escolhe o canal 2 de um
asset estereo, um audio-role-source desligado, com volume ou com mudo, e um
componente de canal cortado ou roteado so para uma saida. O produto nao
extrai canal de arquivo de troca: o .als aponta para o arquivo inteiro e o
Live toca todos os canais dele.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.cli import main
from aafton.formats.fcpxml_read import read_fcpxml
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from tests.unit.test_fcpxml_read_r2 import _codigos
from tests.unit.test_fcpxml_read_r2 import _documento

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000


def _gap(conteudo: str) -> str:
    """O gap da storyline, onde os clips conectados ficam."""
    return f'<gap name="Gap" offset="3600s" start="3600s" duration="20s">{conteudo}</gap>'


def _componente(conteudo: str) -> str:
    """Um clip de asset estereo com o conteudo dado dentro dele."""
    return _gap(
        '<asset-clip name="dialogo" ref="r4" lane="-1" offset="3602s" start="1s"'
        f' duration="3s">{conteudo}</asset-clip>'
    )


def _ler(tmp_path: Path, spine: str):
    """Grava o documento, le o FCPXML e devolve o relatorio e a cena."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(_documento(spine), encoding="utf-8")
    report = Report(strict=False)
    return report, read_fcpxml(caminho, report)


def _mensagem(report: Report, codigo: str) -> str:
    """Mensagem do primeiro problema com o codigo dado."""
    for issue in report.issues:
        if issue.code == codigo:
            return issue.message
    return ""


# Os audio-role-source que mudam o som: cada um deles e erro de efeito.
PAPEIS = [
    '<audio-role-source role="music" enabled="0"/>',
    '<audio-role-source role="music" active="0"/>',
    '<audio-role-source role="music"><adjust-volume amount="-20dB"/></audio-role-source>',
    '<audio-role-source role="music"><mute/></audio-role-source>',
]


def test_dual_mono_com_o_boom_desligado_e_erro_de_canal(tmp_path):
    """O clip que escolhe so um canal do arquivo estereo e erro de canal."""
    spine = _componente(
        '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
        '<audio-channel-source srcCh="2" role="dialogue.dialogue-2" enabled="0"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert "dialogo" in mensagem
    assert "musica.wav" in mensagem
    assert "whole file" in mensagem
    assert [t.channel_count for t in sessao.tracks] == [2]
    assert [c.channel_index for c in sessao.all_clips()] == [0]


@pytest.mark.parametrize("papel", PAPEIS, ids=["desligado", "inativo", "volume", "mudo"])
def test_audio_role_source_que_muda_o_som_e_erro(tmp_path, papel):
    """O audio-role-source que mexe no som e erro: o leitor nao sabe aplica-lo."""
    spine = _componente(papel)

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_EFFECT_UNSUPPORTED" in _codigos(report)
    assert len(list(sessao.all_clips())) == 1


def test_audio_role_source_simples_continua_ignorado(tmp_path):
    """O audio-role-source que so tem papel continua ignorado."""
    spine = _componente('<audio-role-source role="music.music-1"/>')

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert len(list(sessao.all_clips())) == 1


@pytest.mark.parametrize("atributo", ['srcCh="2"', 'srcID="2"'], ids=["srcCh", "srcID"])
def test_item_de_audio_que_escolhe_parte_do_asset_e_erro(tmp_path, atributo):
    """O item <audio> que escolhe parte do asset estereo e erro."""
    spine = _gap(
        '<audio name="musica R" ref="r4" lane="-1" offset="3602s" start="1s"'
        f' duration="3s" {atributo} role="music"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert "musica R" in mensagem
    assert "musica.wav" in mensagem
    assert "whole file" in mensagem
    assert [t.channel_count for t in sessao.tracks] == [2]


def test_componente_de_canal_com_faixa_propria_e_erro(tmp_path):
    """O componente cortado em outra faixa e erro: o corte nao cabe no clip."""
    spine = _componente(
        '<audio-channel-source srcCh="1, 2" role="music.music-1" start="2s" duration="1s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    clips = list(sessao.all_clips())
    assert len(clips) == 1
    assert clips[0].timeline_start_samples == 3602 * TAXA
    assert clips[0].timeline_end_samples == 3605 * TAXA


def test_componente_de_canal_com_uma_saida_so_e_erro(tmp_path):
    """O componente que leva o audio so para uma saida e erro."""
    spine = _componente('<audio-channel-source srcCh="1" outCh="L" role="music.music-1"/>')

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert [t.channel_count for t in sessao.tracks] == [2]


def _wav_estereo(destino: Path) -> Path:
    """WAV estereo de 48 kHz e 24 bits, com o chunk bext."""
    quadro = (0x100000).to_bytes(3, "little", signed=True) + (0x200000).to_bytes(
        3, "little", signed=True
    )
    return write_wav_with_bext(
        destino,
        quadro * TAXA * 20,
        channels=2,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )


def _documento_dual_mono(pasta: Path) -> str:
    """FCPXML do dual mono: o arquivo e estereo e o boom esta desligado."""
    extra = (
        '<asset id="r5" name="dualmono.wav" start="0s" duration="20s" hasAudio="1"'
        ' audioSources="1" audioChannels="2" audioRate="48000">'
        f'<media-rep kind="original-media" src="{(pasta / "dualmono.wav").as_uri()}"/>'
        "</asset>"
    )
    spine = (
        '<asset-clip name="dualmono" ref="r5" offset="3600s" start="1s" duration="3s">'
        '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
        '<audio-channel-source srcCh="2" role="dialogue.dialogue-2" enabled="0"/>'
        "</asset-clip>"
    )
    return _documento(spine, extra)


def test_import_estrito_do_dual_mono_falha_com_erro_de_canal(tmp_path, capsys):
    """O import estrito do dual mono com o boom desligado para no erro."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    _wav_estereo(pasta / "dualmono.wav")
    origem = pasta / "dualmono.fcpxml"
    origem.write_text(_documento_dual_mono(pasta), encoding="utf-8")

    als = tmp_path / "estrito.als"
    codigo = main(["import", str(origem), str(als), "--template", str(TEMPLATE)])

    assert codigo == 1
    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in capsys.readouterr().err


def test_import_permissivo_do_dual_mono_escreve_track_esterea(tmp_path):
    """O import permissivo escreve a track com os canais do arquivo de audio."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    _wav_estereo(pasta / "dualmono.wav")
    origem = pasta / "dualmono.fcpxml"
    origem.write_text(_documento_dual_mono(pasta), encoding="utf-8")
    als = tmp_path / "dualmono.als"
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
    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in [i["code"] for i in dados["issues"]]
    assert als.exists()
    sessao = read_session(als, Report(strict=False))
    assert [t.channel_count for t in sessao.tracks] == [2]
