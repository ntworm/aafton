"""Testes do asset que declara menos canais do que o arquivo tem.

O documento declara audioChannels="1" e o arquivo e estereo: o clip escolhe o
canal 1 (S5) e o Live tocaria o arquivo inteiro, com os dois canais. Isso e
FORMAT_CHANNEL_SELECTION_UNSUPPORTED, com a quantidade que o documento declara
e a quantidade que o arquivo tem. Sem escolha de canal (S6) e com a escolha
dos canais que o arquivo tem, nao ha erro.

O WAV de teste e a importacao vem do teste da track multicanal.
"""

from __future__ import annotations

from pathlib import Path

from aafton.als_read import read_session
from aafton.cli import main
from aafton.report import Report
from tests.unit.test_acomp_track_multicanal import TEMPLATE, _importar, _wav

# O componente que escolhe o canal 1 do arquivo.
COMPONENTE_CANAL_1 = '<audio-channel-source srcCh="1" role="dialogue.dialogue-1"/>'
# O componente que escolhe os dois canais do arquivo.
COMPONENTE_CANAIS_1_2 = '<audio-channel-source srcCh="1, 2" role="dialogue.dialogue-1"/>'


def _documento(pasta: Path, declarados: int, filhos: str, arquivo: str) -> str:
    """FCPXML da cena: o asset com a declaracao de canais e o clip dados."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">'
        '<resources><format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        '<asset id="r2" name="estereo" start="0s" duration="10s" hasAudio="1"'
        f' audioSources="1" audioChannels="{declarados}" audioRate="48000">'
        f'<media-rep kind="original-media" src="{(pasta / arquivo).as_uri()}"/></asset>'
        "</resources>"
        '<library><event name="E"><project name="Projeto">'
        '<sequence format="r1" duration="10s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        '<spine><asset-clip name="estereo" ref="r2" offset="3600s" start="1s" duration="3s">'
        f"{filhos}</asset-clip></spine></sequence></project></event></library></fcpxml>"
    )


def _cena(tmp_path: Path, declarados: int, filhos: str, arquivo: str = "estereo.wav") -> Path:
    """Pasta com a midia e o FCPXML da cena dada; devolve o FCPXML."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    _wav(pasta / "estereo.wav", 2)
    _wav(pasta / "mono.wav", 1)
    origem = pasta / "cena.fcpxml"
    origem.write_text(_documento(pasta, declarados, filhos, arquivo), encoding="utf-8")
    return origem


def _erros(issues: list[dict]) -> list[str]:
    """Os codigos das issues que sao erro."""
    return [issue["code"] for issue in issues if issue["severity"] == "error"]


def test_asset_de_um_canal_com_arquivo_estereo_e_erro(tmp_path, capsys):
    """O asset que declara um canal e o arquivo stereo: o clip escolhe o canal 1."""
    origem = _cena(tmp_path, 1, COMPONENTE_CANAL_1)
    als = tmp_path / "cena.als"

    codigo = main(["import", str(origem), str(als), "--template", str(TEMPLATE)])

    assert codigo == 1
    erro = capsys.readouterr().err
    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in erro
    assert "2 channels" in erro
    assert "declares 1" in erro


def test_asset_de_um_canal_com_arquivo_estereo_no_modo_permissivo(tmp_path):
    """No modo permissivo o clip fica na cena, e o erro continua no relatorio."""
    origem = _cena(tmp_path, 1, COMPONENTE_CANAL_1)
    als = tmp_path / "cena.als"

    codigo, issues = _importar(origem, als, permissivo=True)

    assert codigo == 1
    assert "FORMAT_CHANNEL_SELECTION_UNSUPPORTED" in _erros(issues)
    sessao = read_session(als, Report(strict=False))
    assert [clip.name for track in sessao.tracks for clip in track.clips] == ["estereo"]


def test_asset_de_um_canal_sem_componente_passa(tmp_path):
    """Sem componente o clip toca o arquivo inteiro: nao ha erro."""
    origem = _cena(tmp_path, 1, "")
    als = tmp_path / "cena.als"

    codigo, issues = _importar(origem, als)

    assert codigo == 0
    assert _erros(issues) == []


def test_asset_de_dois_canais_com_os_dois_escolhidos_passa(tmp_path):
    """O asset que declara dois canais e escolhe os dois: nao ha erro."""
    origem = _cena(tmp_path, 2, COMPONENTE_CANAIS_1_2)
    als = tmp_path / "cena.als"

    codigo, issues = _importar(origem, als)

    assert codigo == 0
    assert _erros(issues) == []
