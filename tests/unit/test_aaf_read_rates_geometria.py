"""Geometria do clip no .als quando a midia esta em taxa padrao diferente.

O AAF da sessao e o arquivo de midia podem ter taxas diferentes: o Live
reamostra o arquivo ao tocar e as posicoes do clip seguem a taxa da sessao.
A geometria escrita no .als (CurrentStart, CurrentEnd, LoopStart, LoopEnd) nao
pode depender da taxa do arquivo.

A fabrica de AAFs (tests/aaf_factory.py) monta a sessao sempre em 48 kHz e
nao expoe a taxa da sessao, entao o caso inverso (sessao em 44,1 kHz com
arquivo em 48 kHz) fica registrado como lacuna no teste ignorado no fim do
arquivo.
"""

from __future__ import annotations


import gzip


import json


import xml.etree.ElementTree as ET


from pathlib import Path


import pytest


from aafton.cli import main


from tests.aaf_factory import build_linked_aaf, wav_bytes


RAIZ_DO_REPO = Path(__file__).resolve().parents[2]
TEMPLATE = RAIZ_DO_REPO / "templates" / "live12.als"


def _raiz_do_als(caminho: Path) -> ET.Element:
    """Le a raiz do .als, que o Live guarda compactado em gzip."""
    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(2)
    if inicio == b"\x1f\x8b":
        with gzip.open(caminho, "rb") as arquivo:
            return ET.parse(arquivo).getroot()
    return ET.parse(caminho).getroot()


def _geometria(als: Path) -> dict:
    """Le os tempos e as duracoes do clip do .als."""
    raiz = _raiz_do_als(als)
    clip = next(raiz.iter("AudioClip"))
    ref = clip.find("SampleRef")
    loop = clip.find("Loop")
    dados = {}
    for nome in ("CurrentStart", "CurrentEnd"):
        elemento = clip.find(nome)
        dados[nome] = None if elemento is None else elemento.get("Value")
    for nome in ("LoopStart", "LoopEnd"):
        elemento = None if loop is None else loop.find(nome)
        dados[nome] = None if elemento is None else elemento.get("Value")
    for nome in ("DefaultDuration", "DefaultSampleRate"):
        elemento = ref.find(nome)
        dados[nome] = None if elemento is None else elemento.get("Value")
    return dados


def _converte(
    tmp_path: Path, nome: str, frames: int, taxa: int, duracao_declarada: int
) -> tuple[int, dict, list]:
    """Converte um AAF de 48 kHz que aponta para um arquivo na taxa pedida.

    A fabrica liga a midia pelo caminho, entao o mesmo AAF de 48 kHz e
    remontado para cada arquivo: a declaracao do clip e a mesma.
    """
    wav_path = tmp_path / f"{nome}.wav"
    wav_path.write_bytes(wav_bytes(frames=frames, channels=1, sample_rate=taxa))
    aaf_path = build_linked_aaf(
        tmp_path / f"{nome}.aaf",
        [(0, duracao_declarada, 0)],
        wav_path,
    )
    als = tmp_path / f"{nome}.als"
    relatorio = tmp_path / f"{nome}.json"
    codigo = main(
        [
            "import",
            str(aaf_path),
            str(als),
            "--template",
            str(TEMPLATE),
            "--report",
            str(relatorio),
        ]
    )
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    codigos = [issue["code"] for issue in dados["issues"]]
    return codigo, _geometria(als), codigos


def test_geometria_do_clip_e_a_mesma_com_arquivo_em_taxa_diferente(tmp_path):
    """O mesmo AAF de 48 kHz com arquivo de 48 kHz e de 44,1 kHz da a mesma geometria."""
    codigo48, geometria48, codigos48 = _converte(tmp_path, "a48", 144000, 48000, 144000)
    codigo44, geometria44, codigos44 = _converte(tmp_path, "a44", 132300, 44100, 144000)

    assert codigo48 == 0
    assert codigo44 == 0
    assert "MEDIA_SAMPLE_RATE_DIFFERS" not in codigos48
    assert "MEDIA_SAMPLE_RATE_DIFFERS" in codigos44

    for campo in ("CurrentStart", "CurrentEnd", "LoopStart", "LoopEnd"):
        assert geometria48[campo] == geometria44[campo], campo

    assert geometria48["DefaultDuration"] == "144000"
    assert geometria44["DefaultDuration"] == "132300"


@pytest.mark.skip(
    reason="a fabrica de AAFs monta a sessao sempre em 48 kHz e nao expoe a taxa da sessao"
)
def test_sessao_em_44100_com_arquivo_em_48000(tmp_path):
    """Sessao em 44,1 kHz com arquivo em 48 kHz: aviso e DefaultSampleRate 48000."""
    wav_path = tmp_path / "audio.wav"
    wav_path.write_bytes(wav_bytes(frames=132300, channels=1, sample_rate=48000))
    aaf_path = build_linked_aaf(
        tmp_path / "origem.aaf",
        [(0, 132300, 0)],
        wav_path,
        sample_rate=44100,
    )
    als = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"

    codigo = main(
        [
            "import",
            str(aaf_path),
            str(als),
            "--template",
            str(TEMPLATE),
            "--report",
            str(relatorio),
        ]
    )

    assert codigo == 0
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    codigos = [issue["code"] for issue in dados["issues"]]
    assert "MEDIA_SAMPLE_RATE_DIFFERS" in codigos

    raiz = _raiz_do_als(als)
    ref = next(raiz.iter("SampleRef"))
    taxa = ref.find("DefaultSampleRate")
    assert taxa is not None
    assert taxa.get("Value") == "48000"
