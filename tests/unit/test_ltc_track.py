"""Testes da track de LTC gerada: ela nasce muda e a volta ao AAF continua sem ela."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aafton.aaf_read import read_aaf
from aafton.als_xml import load_als
from aafton.cli import main
from aafton.report import Report
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")

pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="o template do Live nao existe")


@pytest.fixture
def aaf(tmp_path):
    """Um AAF com um clip ligado a um WAV ao lado."""
    wav = tmp_path / "cena.wav"
    wav.write_bytes(wav_bytes(48000 * 20))
    return build_linked_aaf(tmp_path / "cena.aaf", [(0, 96000, 0)], wav)


def _cli(argumentos) -> int:
    """O codigo de saida do CLI, com o SystemExit do argparse."""
    try:
        return main(argumentos)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _importa_com_ltc(aaf, saida, relatorio) -> int:
    """O import com a track de LTC e o relatorio em JSON."""
    return _cli(
        [
            "import",
            str(aaf),
            str(saida),
            "--template",
            str(TEMPLATE),
            "--media",
            "link",
            "--ltc",
            "--report",
            str(relatorio),
        ]
    )


def _speaker_da_ltc(saida) -> str:
    """O estado do botao de som da track LTC no .als (true = soando)."""
    for track in load_als(saida).findall("./LiveSet/Tracks/AudioTrack"):
        nome = track.find("Name/EffectiveName")
        if nome is not None and nome.get("Value") == "LTC":
            return track.find("DeviceChain/Mixer/Speaker/Manual").get("Value")
    raise AssertionError("o set nao tem a track LTC")


def test_a_track_de_ltc_nasce_muda_e_o_aviso_diz(aaf, tmp_path):
    saida = tmp_path / "cena.als"
    relatorio = tmp_path / "relatorio.json"

    assert _importa_com_ltc(aaf, saida, relatorio) == 0

    assert _speaker_da_ltc(saida) == "false"
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    aviso = [i for i in dados["issues"] if i["code"] == "LTC_TRACK_WRITTEN"][0]
    assert "muted" in aviso["message"]
    assert aviso["context"]["muted"] is True


def test_o_export_continua_sem_a_track_de_ltc_muda(aaf, tmp_path):
    saida = tmp_path / "cena.als"
    assert _importa_com_ltc(aaf, saida, tmp_path / "import.json") == 0
    volta = tmp_path / "volta.aaf"

    assert _cli(["export", str(saida), str(volta)]) == 0

    sessao = read_aaf(volta, Report(strict=False))
    assert all(track.name != "LTC" for track in sessao.tracks)
