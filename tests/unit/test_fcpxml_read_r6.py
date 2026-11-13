"""Testes da sexta revisao do leitor de FCPXML: texto, pan, canais e efeitos.

Os documentos vem das sondas da revisao: o title e o caption com o texto que o
FCP grava, o elemento desconhecido dentro do clip, o panner com o nivel so no
parametro, a melhoria de audio dentro de um componente desligado, o EQ plano,
o filtro desligado, o asset que nao declara a quantidade de canais e o arquivo
de quatro canais que so a conferencia da midia encontra.
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
from tests.unit.test_fcpxml_read_r3 import _mensagem

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000

# Os recursos do FCPXML de teste: os assets e os efeitos que as sondas usam.
RECURSOS = (
    '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
    ' width="1920" height="1080"/>'
    '<asset id="r2" name="A001" start="0s" duration="60s" hasVideo="1" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000" format="r1">'
    '<media-rep kind="original-media" src="file:///C:/midia/A001.mov"/>'
    "</asset>"
    '<asset id="r3" name="dial02" start="36000s" duration="60s" hasAudio="1"'
    ' audioSources="1" audioChannels="1" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/dial02.wav"/>'
    "</asset>"
    '<asset id="r4" name="musica" start="0s" duration="120s" hasAudio="1"'
    ' audioSources="1" audioChannels="2" audioRate="48000">'
    '<media-rep kind="original-media" src="file:///C:/midia/musica.wav"/>'
    "</asset>"
    '<effect id="rT" name="Basic Title"'
    ' uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.moti"/>'
    '<effect id="rF" name="Channel EQ" uid="com.apple.audio.ChannelEQ"/>'
)
# O texto que o FCP grava no title e no caption, como nas sondas.
TEXTO = (
    '<text><text-style ref="ts1">CENA 12</text-style></text>'
    '<text-style-def id="ts1">'
    '<text-style font="Helvetica" fontSize="63" fontColor="1 1 1 1"/>'
    "</text-style-def>"
)
# O clip de teste: o dial02 e o canal menos um.
CLIP_MONO = (
    '<asset-clip name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s" duration="3s">'
)
# O clip de teste: a musica e o canal menos um.
CLIP_STEREO = (
    '<asset-clip name="musica" ref="r4" lane="-1" offset="3602s" start="1s" duration="3s">'
)


def _asset_sem_canais(src: str) -> str:
    """Asset que nao declara a quantidade de canais, como nas sondas."""
    return (
        '<asset id="r9" name="estereo" start="0s" duration="10s" hasAudio="1"'
        ' audioSources="1" audioRate="48000">'
        f'<media-rep kind="original-media" src="{src}"/>'
        "</asset>"
    )


def _documento(spine: str, recursos: str = "") -> str:
    """FCPXML de teste: os recursos, o projeto e a spine dados."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<!DOCTYPE fcpxml>\n"
        '<fcpxml version="1.9">'
        f"<resources>{RECURSOS}{recursos}</resources>"
        '<library><event name="E"><project name="P">'
        '<sequence format="r1" duration="30s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine>"
        "</sequence></project></event></library></fcpxml>"
    )


def _ler(tmp_path: Path, spine: str, recursos: str = ""):
    """Grava o documento, le o FCPXML e devolve o relatorio e a cena."""
    caminho = tmp_path / "caso.fcpxml"
    caminho.write_text(_documento(spine, recursos), encoding="utf-8")
    report = Report(strict=False)
    return report, read_fcpxml(caminho, report)


def _wav(destino: Path, amostras: bytes, canais: int) -> Path:
    """WAV de teste com o chunk bext."""
    return write_wav_with_bext(
        destino,
        amostras,
        channels=canais,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )


def _documento_multicanal(nome: str, declarados: int, src: str) -> str:
    """FCPXML de um asset que declara menos canais do que o arquivo tem."""
    asset = (
        f'<asset id="r8" name="{nome}" start="0s" duration="4s" hasAudio="1"'
        f' audioSources="1" audioChannels="{declarados}" audioRate="48000">'
        f'<media-rep kind="original-media" src="{src}"/>'
        "</asset>"
    )
    spine = f'<asset-clip name="{nome}" ref="r8" offset="3600s" start="1s" duration="2s"/>'
    return _documento(spine, asset)


def test_title_com_texto_nao_e_erro(tmp_path):
    """O texto do title e metadado: a musica e o marcador entram na cena."""
    spine = (
        '<title name="Abertura" ref="rT" offset="3600s" start="3600s" duration="10s">'
        '<param name="Position" key="9999/999166631/1/100/101" value="0 0"/>'
        f"{TEXTO}"
        '<asset-clip name="musica" ref="r4" lane="-1" offset="3600s" start="0s"'
        ' duration="10s"/>'
        '<marker start="3601s" duration="100/2500s" value="M title"/>'
        "</title>"
        '<asset-clip name="A001" ref="r2" offset="3610s" start="0s" duration="10s"/>'
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["A001", "musica"]
    assert [(m.name, m.position_samples // TAXA) for m in sessao.markers] == [("M title", 3601)]


def test_caption_com_texto_nao_e_erro(tmp_path):
    """O texto do caption e metadado: o caption nao vira erro do clip."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<caption name="Legenda" lane="1" offset="2s" duration="2s"'
        ' role="iTT?captionFormat=ITT.en">'
        f"{TEXTO}"
        "</caption></asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["A001"]


@pytest.mark.parametrize(
    ("markup", "tag"),
    [
        ("<coisa-nova/>", "coisa-nova"),
        ("<outra-coisa/>", "outra-coisa"),
    ],
    ids=["desconhecido", "outro-desconhecido"],
)
def test_elemento_desconhecido_diz_o_tag(tmp_path, markup, tag):
    """O item que o leitor nao sabe ler diz qual e o elemento."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        f"{markup}</asset-clip>"
    )

    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_NESTED_UNSUPPORTED" in _codigos(report)
    assert tag in _mensagem(report, "FORMAT_NESTED_UNSUPPORTED")


def test_pan_com_nivel_no_parametro_e_erro(tmp_path):
    """O panner com o nivel so no parametro amount e erro de pan."""
    spine = (
        f'{CLIP_MONO}<adjust-panner mode="stereo_left_right">'
        '<param name="amount" value="-100"/></adjust-panner>'
        "</asset-clip>"
    )

    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_PAN_UNSUPPORTED" in _codigos(report)
    assert "dial02" in _mensagem(report, "FORMAT_PAN_UNSUPPORTED")


def test_pan_no_componente_com_nivel_no_parametro_e_erro(tmp_path):
    """O panner com o nivel no parametro, dentro do componente, e erro de pan."""
    spine = (
        f'{CLIP_STEREO}<audio-channel-source srcCh="1, 2" role="music.music-1">'
        '<adjust-panner><param name="amount" value="60"/></adjust-panner>'
        "</audio-channel-source></asset-clip>"
    )

    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_PAN_UNSUPPORTED" in _codigos(report)
    assert "musica" in _mensagem(report, "FORMAT_PAN_UNSUPPORTED")


def test_pan_no_atributo_continua_erro(tmp_path):
    """O panner com o nivel no atributo amount continua erro de pan."""
    spine = f'{CLIP_MONO}<adjust-panner mode="stereo_left_right" amount="-50"/></asset-clip>'

    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_PAN_UNSUPPORTED" in _codigos(report)


def test_eq_plano_nao_e_erro(tmp_path):
    """O adjust-EQ com mode flat e sem parametro nao mexe no som."""
    spine = f'{CLIP_MONO}<adjust-EQ mode="flat"/></asset-clip>'

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


@pytest.mark.parametrize("atributo", ["enabled", "active"], ids=["enabled", "active"])
def test_melhoria_em_componente_desligado_nao_e_erro(tmp_path, atributo):
    """A melhoria dentro de um componente desligado nao mexe no som."""
    spine = (
        f'{CLIP_STEREO}<audio-channel-source srcCh="1, 2" role="music.music-1"/>'
        f'<audio-channel-source srcCh="1, 2" role="music.music-1" {atributo}="0">'
        '<adjust-loudness amount="40" uniformity="20"/>'
        "</audio-channel-source></asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["musica"]


def test_melhoria_ligada_continua_erro(tmp_path):
    """A melhoria de audio ligada continua erro do clip."""
    spine = f'{CLIP_MONO}<adjust-humReduction frequency="50" enabled="1"/></asset-clip>'

    report, _sessao = _ler(tmp_path, spine)

    assert "FORMAT_EFFECT_UNSUPPORTED" in _codigos(report)
    assert "dial02" in _mensagem(report, "FORMAT_EFFECT_UNSUPPORTED")


def test_filtro_de_audio_desligado_nao_e_erro(tmp_path):
    """O filter-audio desligado nao mexe no som: o clip passa."""
    spine = f'{CLIP_MONO}<filter-audio ref="rF" name="Channel EQ" enabled="0"/></asset-clip>'

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_asset_sem_canais_com_escolha_de_canal_e_erro(tmp_path):
    """O asset que nao declara os canais nao serve para escolher um canal.

    O leitor assumiu um canal so e nao sabe que o arquivo e estereo: a escolha
    do canal 1 e parcial, e por isso e erro.
    """
    spine = (
        '<asset-clip name="estereo" ref="r9" lane="-1" offset="3602s" start="1s"'
        ' duration="3s">'
        '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
        "</asset-clip>"
    )

    report, _sessao = _ler(tmp_path, spine, _asset_sem_canais("file:///C:/midia/estereo.wav"))

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    mensagem = _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")
    assert "does not declare" in mensagem
    assert "assumed" in mensagem


def test_asset_sem_canais_no_item_de_audio_e_erro(tmp_path):
    """O item de audio que escolhe o canal de um asset sem declaracao e erro."""
    spine = (
        '<audio name="estereo" ref="r9" lane="-1" offset="3602s" start="1s"'
        ' duration="3s" srcCh="2"/>'
    )

    report, _sessao = _ler(tmp_path, spine, _asset_sem_canais("file:///C:/midia/estereo.wav"))

    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _codigos(report)
    assert "does not declare" in _mensagem(report, "FORMAT_CHANNEL_SELECTION_UNSUPPORTED")


def test_import_permissivo_de_wav_de_quatro_canais_deixa_o_clip_fora(tmp_path):
    """A conferencia da midia tambem tira da cena o clip de quatro canais."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    nome = "poly4.wav"
    _wav(pasta / nome, b"\x00\x00\x00" * 4 * TAXA * 4, 4)
    origem = pasta / "poly.fcpxml"
    origem.write_text(
        _documento_multicanal(nome, 2, (pasta / nome).as_uri()),
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


def test_rastreador_de_objeto_e_video_e_passa(tmp_path):
    """O object-tracker guarda o rastreio de video: nao muda o som e passa."""
    spine = (
        '<asset-clip name="A001" ref="r2" offset="3600s" start="0s" duration="10s">'
        '<object-tracker><tracking-shape id="ts1" name="Obj" offsetEnabled="1"/>'
        "</object-tracker></asset-clip>"
    )

    report, sessao = _ler(tmp_path, spine)

    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["A001"]
