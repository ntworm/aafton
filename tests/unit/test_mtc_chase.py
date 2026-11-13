"""Testes do aviso MTC_CHASE_SETTINGS do import com --ltc (fase 2, P06).

O aviso diz como o Live segue o MTC da picture: sync MTC na entrada MIDI que
carrega o MTC, offset SMPTE igual ao TC do beat zero e a taxa da sessao.
O aviso so aparece quando o import cria a track de LTC, e o offset tem de
ser o mesmo valor que o sidecar grava em live_origin_timecode.
"""

import json

import pytest

from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_import_ltc import TEMPLATE, _importar, _sidecar

SR = 48000


@pytest.fixture
def aaf(tmp_path):
    """AAF de origem com dois clips linkados, como nos testes de LTC."""
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(SR * 120))
    destino = tmp_path / "cena12.aaf"
    return build_linked_aaf(destino, [(0, 96000, 24000), (192000, 96000, 0)], wav)


def _entradas(relatorio, codigo):
    """Entradas do relatorio JSON com o codigo dado."""
    dados = json.loads(relatorio.read_text(encoding="utf-8"))
    return [entrada for entrada in dados["issues"] if entrada["code"] == codigo]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_com_ltc_avisa_o_chase_de_mtc(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"
    assert _importar(aaf, saida, relatorio=relatorio, opcoes=["--ltc"]) == 0

    entradas = _entradas(relatorio, "MTC_CHASE_SETTINGS")
    assert len(entradas) == 1
    assert entradas[0]["severity"] == "info"

    contexto = entradas[0]["context"]
    sidecar = _sidecar(saida)
    assert contexto["offset"] == sidecar["live_origin_timecode"]
    assert contexto["rate"] == "25"
    assert contexto["origin_samples"] == sidecar["live_origin_samples"]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="Portao B: template ainda nao autorado")
def test_import_sem_ltc_nao_fala_de_chase_de_mtc(aaf, tmp_path):
    saida = tmp_path / "saida.als"
    relatorio = tmp_path / "relatorio.json"
    assert _importar(aaf, saida, relatorio=relatorio) == 0

    assert _entradas(relatorio, "MTC_CHASE_SETTINGS") == []
