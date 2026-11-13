"""Textos do LTC: origem de menos de meio frame e descricao do BWF.

A origem do Live um sample antes de zero produzia "0 s antes de 00:00:00:00" e
o aviso MTC_CHASE_ORIGIN_NEGATIVE, que nao ajuda: para o Live a origem e zero.
Agora uma origem de menos de meio frame antes de zero sai com o chase de MTC de
sempre, e o aviso de origem negativa aponta o intervalo entre 00:00:00:00 e o
primeiro clip. O BWF do `aafton ltc` tambem descreve o primeiro frame de
verdade, e nao o rotulo digitado.
"""

import pytest

from aafton.cli import main
from aafton.ltc import write_ltc_wav
from aafton.ltc_track import add_ltc_track
from aafton.media_extract import parse_bext
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import wav_bytes
from tests.unit.test_melhorias_ltc import SR, _issue, _sessao

TAXA = TimecodeRate(25, 1)


@pytest.fixture
def wav(tmp_path):
    """WAV de origem com 30 s de audio."""
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(SR * 30))
    return caminho


def test_origem_de_um_sample_antes_de_zero_sai_com_o_chase_de_mtc(tmp_path, wav):
    """Uma origem de menos de meio frame antes de zero e o offset 00:00:00:00."""
    session = _sessao(tmp_path, wav, 5 * SR, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(session, -1, tmp_path / "midia", relatorio)

    assert not [i for i in relatorio.issues if i.code == "MTC_CHASE_ORIGIN_NEGATIVE"]
    assert "antes de 00:00:00:00" not in " ".join(i.message for i in relatorio.issues)
    assert _issue(relatorio, "MTC_CHASE_SETTINGS").context["offset"] == "00:00:00:00"


def test_origem_dez_segundos_antes_de_zero_aponta_o_primeiro_clip(tmp_path, wav):
    """O aviso de origem negativa diz o intervalo entre zero e o primeiro clip."""
    session = _sessao(tmp_path, wav, 5 * SR, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(session, -10 * SR, tmp_path / "midia", relatorio)

    aviso = _issue(relatorio, "MTC_CHASE_ORIGIN_NEGATIVE")
    assert "00:00:00:00" in aviso.message
    assert "00:00:05:00" in aviso.message
    assert not [i for i in relatorio.issues if i.code == "MTC_CHASE_SETTINGS"]


def test_ltc_com_inicio_negativo_descreve_o_frame_zero(tmp_path):
    """O LTC comecado em -00:00:00:00 descreve o frame zero no BWF."""
    caminho = tmp_path / "ltc.wav"

    write_ltc_wav(caminho, "-00:00:00:00", TAXA, 25, SR)

    assert parse_bext(caminho)["description"] == "LTC 00:00:00:00 25/1"


def test_comando_ltc_com_inicio_negativo_descreve_o_frame_zero(tmp_path):
    """O `aafton ltc --start=-00:00:00:00` descreve o frame zero no BWF."""
    saida = tmp_path / "cli.wav"

    codigo = main(
        [
            "ltc",
            "--start=-00:00:00:00",
            "--rate",
            "25",
            "--duration",
            "00:00:01:00",
            str(saida),
        ]
    )

    assert codigo == 0
    assert parse_bext(saida)["description"] == "LTC 00:00:00:00 25/1"
