"""Testes da track que perde o clip de midia com mais de dois canais.

As sondas da revisao 7 montam a cena com um arquivo de quatro canais que o
documento declara com dois, ao lado de um clip mono: o clip de quatro canais
conectado ao mono (N1) e o clip de quatro canais na mesma lane (N2). A track
que perde o clip e fica sem nenhum sai da cena, e a que guarda clip ganha a
quantidade de canais dos clips que ficaram. O verify da origem roda a mesma
conferencia da midia.
"""

from __future__ import annotations

import json
from pathlib import Path

from aafton.als_read import read_session
from aafton.cli import main
from aafton.media_extract import write_wav_with_bext
from aafton.report import Report
from aafton.verify import verify_conversion

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
TAXA = 48000

# N1: o clip de quatro canais conectado ao clip mono.
SPINE_CONECTADO = (
    '<asset-clip name="dial" ref="r3" offset="3600s" start="0s" duration="6s">'
    '<asset-clip name="poly4" ref="r2" lane="-1" offset="1s" start="1s" duration="3s"/>'
    "</asset-clip>"
)
# N2: o clip de quatro canais e o clip mono na mesma lane, em sequencia.
SPINE_MESMA_LANE = (
    '<asset-clip name="poly4" ref="r2" offset="3600s" start="1s" duration="3s"/>'
    '<asset-clip name="dial" ref="r3" offset="3603s" start="0s" duration="3s"/>'
)
# N4: o arquivo stereo e o arquivo mono na mesma lane.
SPINE_CONTROLE = (
    '<asset-clip name="st" ref="r4" offset="3600s" start="1s" duration="3s"/>'
    '<asset-clip name="dial" ref="r3" offset="3603s" start="0s" duration="3s"/>'
)
# Os assets da cena: identificador, nome, canais declarados, arquivo e canais.
ASSETS = (
    ("r2", "poly4", 2, "poly4.wav", 4),
    ("r3", "dial", 1, "dial.wav", 1),
    ("r4", "st", 2, "st.wav", 2),
)


def _wav(destino: Path, canais: int) -> Path:
    """WAV de teste com o chunk bext."""
    return write_wav_with_bext(
        destino,
        b"\x00\x00\x00" * canais * TAXA * 10,
        channels=canais,
        sample_rate=TAXA,
        bits_per_sample=24,
        time_reference=0,
    )


def _documento(pasta: Path, spine: str) -> str:
    """FCPXML da cena: os assets de midia e a spine dadas."""
    recursos = "".join(
        f'<asset id="{ident}" name="{nome}" start="0s" duration="10s" hasAudio="1"'
        f' audioSources="1" audioChannels="{declarados}" audioRate="48000">'
        f'<media-rep kind="original-media" src="{(pasta / arquivo).as_uri()}"/></asset>'
        for ident, nome, declarados, arquivo, _canais in ASSETS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">'
        '<resources><format id="r1" name="FFVideoFormat1080p25" frameDuration="100/2500s"'
        ' width="1920" height="1080"/>'
        f"{recursos}</resources>"
        '<library><event name="E"><project name="Projeto">'
        '<sequence format="r1" duration="10s" tcStart="3600s" tcFormat="NDF"'
        ' audioLayout="stereo" audioRate="48k">'
        f"<spine>{spine}</spine></sequence></project></event></library></fcpxml>"
    )


def _cena(tmp_path: Path, spine: str) -> Path:
    """Pasta com a midia e o FCPXML da cena dada; devolve o FCPXML."""
    pasta = tmp_path / "cena"
    pasta.mkdir()
    for _ident, _nome, _declarados, arquivo, canais in ASSETS:
        _wav(pasta / arquivo, canais)
    origem = pasta / "cena.fcpxml"
    origem.write_text(_documento(pasta, spine), encoding="utf-8")
    return origem


def _importar(origem: Path, als: Path, permissivo: bool = False) -> tuple[int, list[dict]]:
    """Importa a cena e devolve o codigo de saida e as issues do relatorio."""
    relatorio = als.with_suffix(".json")
    argumentos = ["import", str(origem), str(als), "--template", str(TEMPLATE)]
    argumentos += ["--report", str(relatorio)]
    if permissivo:
        argumentos.append("--permissive")
    codigo = main(argumentos)
    if not relatorio.exists():
        return codigo, []
    return codigo, json.loads(relatorio.read_text(encoding="utf-8"))["issues"]


def _codigos(issues: list[dict]) -> list[str]:
    """Os codigos das issues que importam: os erros e os avisos."""
    return [issue["code"] for issue in issues if issue["severity"] != "info"]


def _tracks(als: Path) -> list[tuple[str, int, list[str]]]:
    """As tracks de audio do .als: nome, canais e nomes dos clips."""
    sessao = read_session(als, Report(strict=False))
    return [
        (track.name, track.channel_count, [clip.name for clip in track.clips])
        for track in sessao.tracks
        if not track.is_video
    ]


def test_track_conectada_sem_clip_sai_da_cena(tmp_path):
    """A track que perde o clip de quatro canais e fica vazia sai da cena."""
    origem = _cena(tmp_path, SPINE_CONECTADO)
    als = tmp_path / "conectado.als"

    codigo, issues = _importar(origem, als, permissivo=True)

    assert als.exists()
    assert codigo == 1
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(issues)
    assert "STEREO_PAIR_REJECTED" not in _codigos(issues)
    assert _tracks(als) == [("A1", 1, ["dial"])]


def test_track_na_mesma_lane_sem_clip_sai_da_cena(tmp_path):
    """A track que guarda o clip mono fica com um canal so."""
    origem = _cena(tmp_path, SPINE_MESMA_LANE)
    als = tmp_path / "mesma_lane.als"

    codigo, issues = _importar(origem, als, permissivo=True)

    assert als.exists()
    assert codigo == 1
    assert "FORMAT_MULTICHANNEL_UNSUPPORTED" in _codigos(issues)
    assert "STEREO_PAIR_REJECTED" not in _codigos(issues)
    assert _tracks(als) == [("A1", 1, ["dial"])]


def test_verify_da_origem_nao_acha_clip_ausente(tmp_path):
    """O verify roda a mesma conferencia: o clip que saiu da cena nao falta."""
    origem = _cena(tmp_path, SPINE_CONECTADO)
    als = tmp_path / "conectado.als"
    _importar(origem, als, permissivo=True)

    resultado = verify_conversion(origem, als)

    assert resultado.audio_missing == 0
    assert resultado.audio_extra == 0
    assert resultado.audio_compared == 1
    assert any("FORMAT_MULTICHANNEL_UNSUPPORTED" in erro for erro in resultado.errors)
    assert main(["verify", str(origem), str(als)]) == 1


def test_cena_de_controle_stereo_e_mono_passa(tmp_path):
    """O arquivo stereo com o mono na mesma lane nao tem erro nenhum."""
    origem = _cena(tmp_path, SPINE_CONTROLE)
    als = tmp_path / "controle.als"

    codigo, issues = _importar(origem, als)

    assert codigo == 0
    assert [issue["code"] for issue in issues if issue["severity"] == "error"] == []
    assert _tracks(als) == [("A1", 2, ["st", "dial"])]
    assert main(["verify", str(origem), str(als)]) == 0
