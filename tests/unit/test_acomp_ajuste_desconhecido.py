"""Testes do ajuste que o leitor de FCPXML nao conhece.

O leitor conhece os ajustes de video do FCP, o volume, o pan e as seis
melhorias de audio: um ajuste de audio que ele nao conhece nao pode passar
em silencio. O aviso `FORMAT_EFFECT_UNKNOWN` diz o clip e o elemento, e o
clip continua na cena.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.als_read import read_session
from aafton.cli import main
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from tests.unit.test_fcpxml_read_r3 import _codigos
from tests.unit.test_fcpxml_read_r3 import _ler
from tests.unit.test_fcpxml_read_r3 import _mensagem

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000

# O clip de teste e o item de audio que aponta para o mesmo asset.
CLIP = '<asset-clip name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s" duration="3s">'
AUDIO = '<audio name="dial02" ref="r3" lane="-1" offset="3602s" start="36001s" duration="3s">'
COMPONENTE = '<audio-channel-source srcCh="1" role="dialogue.dialogue-1">'
# O ajuste de audio que o leitor nao conhece.
DESCONHECIDO = "<adjust-foo/>"


def _spine_com_ajuste(lugar: str) -> str:
    """A spine com o ajuste desconhecido no lugar pedido: clip, audio ou componente."""
    if lugar == "audio":
        return f"{AUDIO}{DESCONHECIDO}</audio>"
    if lugar == "componente":
        return f"{CLIP}{COMPONENTE}{DESCONHECIDO}</audio-channel-source></asset-clip>"
    return f"{CLIP}{DESCONHECIDO}</asset-clip>"


def _documento_com_midia(src: str, ajuste: str) -> str:
    """FCPXML de teste com o arquivo de midia real e o ajuste dado."""
    recursos = (
        "<resources>"
        '<format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        '<asset id="r3" name="dial02" start="0s" duration="4s" hasAudio="1"'
        ' audioSources="1" audioChannels="1" audioRate="48000">'
        f'<media-rep kind="original-media" src="{src}"/>'
        "</asset>"
        "</resources>"
    )
    spine = (
        '<asset-clip name="dial02" ref="r3" offset="3600s" start="0s" duration="2s">'
        f"{ajuste}</asset-clip>"
    )
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


@pytest.mark.parametrize("lugar", ["clip", "audio", "componente"])
def test_ajuste_desconhecido_e_aviso(tmp_path, lugar):
    """O ajuste que o leitor nao conhece vira aviso, com o clip e o elemento."""
    report, sessao = _ler(tmp_path, _spine_com_ajuste(lugar))

    assert _codigos(report).count("FORMAT_EFFECT_UNKNOWN") == 1
    mensagem = _mensagem(report, "FORMAT_EFFECT_UNKNOWN")
    assert "dial02" in mensagem
    assert "adjust-foo" in mensagem
    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def _origem_com_ajuste(pasta: Path) -> Path:
    """Grava o FCPXML com o arquivo de midia real e o ajuste desconhecido."""
    pasta.mkdir()
    wav = write_wav_with_bext(
        pasta / "dial02.wav",
        b"\x00\x00\x00" * TAXA * 4,
        channels=1,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )
    origem = pasta / "ajuste.fcpxml"
    origem.write_text(_documento_com_midia(wav.as_uri(), DESCONHECIDO), encoding="utf-8")
    return origem


@pytest.mark.parametrize("permissivo", [False, True], ids=["estrito", "permissivo"])
def test_import_de_ajuste_desconhecido_passa(tmp_path, permissivo):
    """O import passa nos dois modos: o aviso do ajuste desconhecido nao e erro."""
    origem = _origem_com_ajuste(tmp_path / "cena")
    als = tmp_path / "ajuste.als"
    relatorio = tmp_path / "import.json"
    argumentos = ["import", str(origem), str(als), "--template", str(TEMPLATE)]
    argumentos += ["--report", str(relatorio)]
    if permissivo:
        argumentos.append("--permissive")

    codigo = main(argumentos)

    assert codigo == 0
    assert als.exists()
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    assert "FORMAT_EFFECT_UNKNOWN" in [issue["code"] for issue in dados["issues"]]
    assert not [issue for issue in dados["issues"] if issue["severity"] == "error"]
    sessao = read_session(als, Report(strict=False))
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


@pytest.mark.parametrize("elemento", ["adjust-transform", "adjust-blend"])
def test_ajuste_de_video_conhecido_nao_da_aviso(tmp_path, elemento):
    """O ajuste de video que o leitor conhece continua passando em silencio."""
    report, sessao = _ler(tmp_path, f"{CLIP}<{elemento}/></asset-clip>")

    assert report.count("warning") == 0, _codigos(report)
    assert report.count("error") == 0, _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]


def test_melhoria_de_audio_continua_unsupported(tmp_path):
    """A melhoria de audio continua sendo erro de efeito, e nao aviso."""
    report, sessao = _ler(tmp_path, f'{CLIP}<adjust-EQ mode="voice_enhance"/></asset-clip>')

    assert "FORMAT_EFFECT_UNSUPPORTED" in _codigos(report)
    assert "FORMAT_EFFECT_UNKNOWN" not in _codigos(report)
    assert [c.name for c in sessao.all_clips()] == ["dial02"]
