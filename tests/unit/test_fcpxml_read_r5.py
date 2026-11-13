"""Testes da quinta revisao do leitor de FCPXML: melhorias, canais e mensagens.

Os documentos vem das sondas da revisao: as seis melhorias de audio do FCP
(Inspector > Audio Enhancements) no clip, no item de audio e no componente de
audio, o arquivo de tres e de seis canais, a escolha dos canais fora de ordem,
o item sem nome e o asset que nao declara a quantidade de canais.
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
from tests.unit.test_fcpxml_read_r3 import _codigos
from tests.unit.test_fcpxml_read_r3 import _documento
from tests.unit.test_fcpxml_read_r3 import _ler
from tests.unit.test_fcpxml_read_r3 import _mensagem
from tests.unit.test_fcpxml_read_r4 import _documento_multicanal

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000

# As seis melhorias de audio do FCP, com o markup que cada uma usa.
MELHORIAS = {
    "adjust-EQ": '<adjust-EQ mode="voice_enhance"/>',
    "adjust-matchEQ": '<adjust-matchEQ><data key="effectData">AAAA</data></adjust-matchEQ>',
    "adjust-loudness": '<adjust-loudness amount="40" uniformity="20"/>',
    "adjust-noiseReduction": '<adjust-noiseReduction amount="60"/>',
    "adjust-humReduction": '<adjust-humReduction frequency="60"/>',
    "adjust-voiceIsolation": '<adjust-voiceIsolation amount="50"/>',
}
# Os ajustes de video do FCPXML: eles nao mudam o audio e ficam de fora.
AJUSTES_DE_VIDEO = ("adjust-transform", "adjust-crop", "adjust-blend")
# O momento do dial02 e o da musica, na lane menos um.
QUANDO_MONO = 'offset="3602s" start="36001s" duration="3s"'
QUANDO_STEREO = 'offset="3602s" start="1s" duration="3s"'
# Os itens de teste: o audio e o clip, com nome e sem nome.
AUDIO_COM_NOME = f'<audio name="dial02" ref="r3" lane="-1" {QUANDO_MONO}>'
AUDIO_SEM_NOME = f'<audio ref="r3" lane="-1" {QUANDO_MONO}>'
CLIP_COM_NOME = f'<asset-clip name="dial02" ref="r3" lane="-1" {QUANDO_MONO}>'
CLIP_SEM_NOME = f'<asset-clip ref="r3" lane="-1" {QUANDO_MONO}>'
CLIP_STEREO = f'<asset-clip name="musica" ref="r4" lane="-1" {QUANDO_STEREO}>'
CLIP_SEM_CANAIS = f'<asset-clip name="semcanais" ref="r9" lane="-1" {QUANDO_STEREO}>'
AUDIO_STEREO = f'<audio name="musica" ref="r4" lane="-1" {QUANDO_STEREO} srcCh="2, 1"/>'
# Os componentes de audio: o do par stereo fora de ordem e o do mono.
COMPONENTE_STEREO = '<audio-channel-source srcCh="2, 1" role="music.music-1"/>'
COMPONENTE_MONO = '<audio-channel-source srcCh="1">'
# O fade do item de teste.
FADE = '<adjust-volume><param name="amount"><fadeIn type="easeIn" duration="1s"/>'
FADE_FIM = "</param></adjust-volume>"


def _spine_com_melhoria(lugar: str, markup: str) -> str:
    """A spine com a melhoria no lugar pedido: clip, audio ou componente."""
    if lugar == "audio":
        return f"{AUDIO_COM_NOME}{markup}</audio>"
    if lugar == "componente":
        canal = f"{COMPONENTE_MONO}{markup}</audio-channel-source>"
        return f"{CLIP_COM_NOME}{canal}</asset-clip>"
    return f"{CLIP_COM_NOME}{markup}</asset-clip>"


def _spine_com_fade(lugar: str) -> str:
    """A spine com um fade no item pedido: clip ou audio."""
    fade = f"{FADE}{FADE_FIM}"
    if lugar == "audio":
        return f"{AUDIO_SEM_NOME}{fade}</audio>"
    return f"{CLIP_SEM_NOME}{fade}</asset-clip>"


def _spine_com_canais_fora_de_ordem(lugar: str) -> str:
    """A spine que escolhe os canais do par stereo fora de ordem."""
    if lugar == "audio":
        return AUDIO_STEREO
    return f"{CLIP_STEREO}{COMPONENTE_STEREO}</asset-clip>"


def _documento_sem_canais(spine: str) -> str:
    """FCPXML de teste com um asset que nao declara a quantidade de canais."""
    recursos = (
        "<resources>"
        '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        '<asset id="r9" name="semcanais" start="0s" duration="60s" hasAudio="1"'
        ' audioRate="48000">'
        '<media-rep kind="original-media" src="file:///C:/midia/semcanais.wav"/>'
        "</asset>"
        "</resources>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"{recursos}"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" duration="30s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence></project></event></library></fcpxml>"
    )


@pytest.mark.parametrize("lugar", ["clip", "audio", "componente"])
@pytest.mark.parametrize("elemento", sorted(MELHORIAS))
def test_melhoria_de_audio_e_erro_do_clip(tmp_path, elemento, lugar):
    """As seis melhorias de audio sao erro do clip que as carrega."""
    report, sessao = _ler(tmp_path, _spine_com_melhoria(lugar, MELHORIAS[elemento]))

    assert _codigos(report).count("FORMAT_EFFECT_UNSUPPORTED") == 1
    mensagem = _mensagem(report, "FORMAT_EFFECT_UNSUPPORTED")
    assert "dial02" in mensagem
    assert elemento in mensagem
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_import_estrito_de_melhoria_de_audio_falha(tmp_path, capsys):
    """O import estrito de um clip com melhoria de audio para no erro."""
    origem = tmp_path / "melhoria.fcpxml"
    origem.write_text(
        _documento(_spine_com_melhoria("clip", MELHORIAS["adjust-EQ"])),
        encoding="utf-8",
    )
    als = tmp_path / "melhoria.als"

    codigo = main(["import", str(origem), str(als), "--template", str(TEMPLATE)])

    assert codigo == 1
    assert "FORMAT_EFFECT_UNSUPPORTED" in capsys.readouterr().err
    assert not als.exists()


@pytest.mark.parametrize("elemento", AJUSTES_DE_VIDEO)
def test_ajuste_de_video_continua_ignorado(tmp_path, elemento):
    """O ajuste de video nao muda o audio e nao vira erro."""
    spine = f"{CLIP_COM_NOME}<{elemento}/></asset-clip>"

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


@pytest.mark.parametrize("canais", [3, 6], ids=["tres", "seis"])
def test_import_permissivo_de_wav_multicanal_deixa_o_clip_fora(tmp_path, canais):
    """No permissivo o .als e escrito sem o clip de um arquivo multicanal."""
    nome = f"poly{canais}.wav"
    pasta = tmp_path / "cena"
    pasta.mkdir()
    write_wav_with_bext(
        pasta / nome,
        b"\x00\x00\x00" * canais * TAXA * 4,
        channels=canais,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )
    origem = pasta / "poly.fcpxml"
    origem.write_text(
        _documento_multicanal(nome, canais, (pasta / nome).as_uri()),
        encoding="utf-8",
    )
    als = tmp_path / "poly.als"
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


@pytest.mark.parametrize("lugar", ["componente", "audio"])
def test_canais_escolhidos_fora_de_ordem_e_erro(tmp_path, lugar):
    """A escolha dos canais fora de ordem e erro: o Live toca na ordem dele."""
    report, sessao = _ler(tmp_path, _spine_com_canais_fora_de_ordem(lugar))

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert "musica" in mensagem
    assert [c.name for c in sessao.all_clips()] == ["musica"]


@pytest.mark.parametrize("lugar", ["clip", "audio"])
def test_item_sem_nome_usa_o_nome_do_asset(tmp_path, lugar):
    """O item sem nome pega o nome do asset na mensagem do fade."""
    report, sessao = _ler(tmp_path, _spine_com_fade(lugar))

    assert _codigos(report).count("FORMAT_FADE_UNSUPPORTED") == 1
    mensagem = _mensagem(report, "FORMAT_FADE_UNSUPPORTED")
    assert "dial02" in mensagem
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_asset_sem_canais_declarados_diz_que_o_leitor_assumiu(tmp_path):
    """O asset sem audioChannels diz que a quantidade foi assumida."""
    spine = f"{CLIP_SEM_CANAIS}{COMPONENTE_STEREO}</asset-clip>"
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(_documento_sem_canais(spine), encoding="utf-8")
    report = Report(strict=False)

    read_fcpxml(caminho, report)

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert "has 1 channel" not in mensagem
    assert "does not declare" in mensagem
    assert "assumed" in mensagem


def test_melhoria_de_audio_desligada_nao_e_erro(tmp_path):
    """Uma melhoria com enabled="0" nao muda o som: o clip passa sem erro."""
    spine = _spine_com_melhoria("clip", '<adjust-noiseReduction enabled="0" amount="50"/>')

    report, sessao = _ler(tmp_path, spine)

    assert "FORMAT_EFFECT_UNSUPPORTED" not in _codigos(report)
    assert len(list(sessao.all_clips())) == 1
