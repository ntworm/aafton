"""Testes da classificacao de diferenca de taxa de amostragem do import."""

from __future__ import annotations


import json


import gzip


import xml.etree.ElementTree as ET


from pathlib import Path


import pytest


from aafton.aaf_read_rates import classificar_diferenca_de_taxa, mesma_duracao

from aafton.aaf_read_session import check_pulldown

from aafton.cli import main

from aafton.ir import MediaAsset, SessionIR

from aafton.media_extract import _disambiguate

from aafton.report import Report, StrictModeError

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


def _sessao_com_taxa(taxa_arquivo: int) -> SessionIR:
    """Sessao de 48 kHz com um unico arquivo de midia na taxa pedida."""
    return SessionIR(
        name="Teste",
        sample_rate=48000,
        timecode_rate=None,
        start_timecode_samples=0,
        tracks=[],
        media=[
            MediaAsset(
                id="m1",
                filename="audio.wav",
                duration_samples=1000,
                channels=1,
                sample_rate=taxa_arquivo,
            )
        ],
        markers=[],
        source_path=None,
        source_application=None,
    )


@pytest.mark.parametrize(
    ("taxa_arquivo", "taxa_sessao"),
    [(44100, 48000), (48000, 96000), (22050, 48000)],
)
def test_taxa_padrao_diferente_da_sessao_e_padrao(taxa_arquivo, taxa_sessao):
    assert classificar_diferenca_de_taxa(taxa_arquivo, taxa_sessao) == "padrao"


@pytest.mark.parametrize(
    ("taxa_arquivo", "taxa_sessao"),
    [
        (48048, 48000),
        (47952, 48000),
        (44144, 44100),
        (44056, 44100),
        (50000, 48000),
    ],
)
def test_pulldown_e_taxa_fora_do_padrao(taxa_arquivo, taxa_sessao):
    assert classificar_diferenca_de_taxa(taxa_arquivo, taxa_sessao) == "pulldown"


@pytest.mark.parametrize("taxa", [48000, 44100, 96000])
def test_taxa_igual(taxa):
    assert classificar_diferenca_de_taxa(taxa, taxa) == "igual"


def test_check_pulldown_com_taxa_padrao_diferente_avisa_sem_levantar():
    """Taxa padrao diferente da sessao passa com aviso, mesmo em modo estrito."""
    sessao = _sessao_com_taxa(44100)
    relatorio = Report(strict=True)

    check_pulldown(sessao, relatorio)

    avisos = [issue for issue in relatorio.issues if issue.code == "MEDIA_SAMPLE_RATE_DIFFERS"]
    assert len(avisos) == 1
    assert avisos[0].context == {
        "filename": "audio.wav",
        "file_sample_rate": 44100,
        "session_sample_rate": 48000,
    }
    assert relatorio.count("error") == 0


def test_check_pulldown_com_pulldown_levanta_em_modo_estrito():
    """Pull-down continua erro: em modo estrito o check levanta."""
    sessao = _sessao_com_taxa(48048)
    relatorio = Report(strict=True)

    with pytest.raises(StrictModeError):
        check_pulldown(sessao, relatorio)


def test_import_com_taxa_padrao_diferente_gera_als_com_aviso(tmp_path):
    """Import estrito de AAF a 48 kHz com WAV a 44,1 kHz: passa com aviso."""
    wav_path = tmp_path / "audio44100.wav"
    wav_path.write_bytes(wav_bytes(frames=44100, channels=1, sample_rate=44100))
    aaf_path = build_linked_aaf(
        tmp_path / "origem.aaf",
        [(0, 44100, 0)],
        wav_path,
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
        ],
    )

    assert codigo == 0
    assert als.exists()

    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    codigos = [issue["code"] for issue in dados["issues"]]
    assert "MEDIA_SAMPLE_RATE_DIFFERS" in codigos
    assert "PULLDOWN_MISMATCH" not in codigos

    raiz = _raiz_do_als(als)
    ref = next(raiz.iter("SampleRef"))
    taxa = ref.find("DefaultSampleRate")
    assert taxa is not None
    assert taxa.get("Value") == "44100"


def test_mesma_duracao_com_taxas_diferentes_compara_em_tempo():
    """144000 samples a 48 kHz e 132300 samples a 44,1 kHz sao o mesmo tempo."""
    assert mesma_duracao(144000, 48000, 132300, 44100)
    assert not mesma_duracao(144000, 48000, 88200, 44100)


def test_mesma_duracao_com_taxas_iguais_exige_igualdade_exata():
    """Com a mesma taxa, um sample de diferenca ja e outra duracao."""
    assert mesma_duracao(144000, 48000, 144000, 48000)
    assert not mesma_duracao(144000, 48000, 144001, 48000)


def test_mesma_duracao_sem_taxa_exige_igualdade_exata():
    """Sem a taxa nao da para converter os samples, entao a igualdade e exata."""
    assert mesma_duracao(144000, None, 144000, 48000)
    assert not mesma_duracao(144000, None, 144001, 48000)


def test_mesma_duracao_aceita_um_sample_de_diferenca_na_taxa_maior():
    """A conversao entre taxas arredonda, entao 1 sample na taxa maior passa."""
    assert mesma_duracao(48001, 48000, 44100, 44100)
    assert not mesma_duracao(48002, 48000, 44100, 44100)


def test_disambiguate_escolhe_o_candidato_com_a_mesma_duracao_em_tempo(tmp_path):
    """Dois arquivos com o mesmo nome: vence o de mesma duracao em tempo."""
    pasta_3s = tmp_path / "tres_segundos"
    pasta_2s = tmp_path / "dois_segundos"
    pasta_3s.mkdir()
    pasta_2s.mkdir()
    (pasta_3s / "audio.wav").write_bytes(wav_bytes(frames=132300, channels=1, sample_rate=44100))
    (pasta_2s / "audio.wav").write_bytes(wav_bytes(frames=88200, channels=1, sample_rate=44100))

    asset = MediaAsset(
        id="m1",
        filename="audio.wav",
        duration_samples=144000,
        channels=1,
        sample_rate=48000,
    )

    escolhido = _disambiguate(asset, [pasta_3s / "audio.wav", pasta_2s / "audio.wav"])

    assert escolhido == pasta_3s / "audio.wav"
