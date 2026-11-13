"""Porta de resposta do AbletonOSC ocupada por outro programa.

O AbletonOSC responde sempre na porta 11001, a constante dele. Quando outro
cliente do AbletonOSC ja esta ligado nessa porta, o socket da ponte nao abre e a
pagina mostra o erro cru do Windows. A ponte traduz esse erro em portugues,
dizendo a porta e o que fazer; quando a porta e liberada, ela liga sozinha.
"""

import socket

import pytest

from aafton.cli import main
from aafton.studio.timecode import BRIDGE
from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_studio_timecode import TEMPLATE, _argv_import


@pytest.fixture
def aaf_do_studio(tmp_path):
    """AAF de audio linkado, como no fixture do test_cli.py."""
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(48000 * 10))
    return build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000), (192000, 96000, 0)], wav)


@pytest.fixture
def set_do_studio(aaf_do_studio, tmp_path):
    """O set importado, com o sidecar ao lado."""
    if not TEMPLATE.exists():
        pytest.skip("Portao B: template ainda nao autorado")
    als = tmp_path / "saida.als"
    codigo = main(_argv_import(aaf_do_studio, als))
    assert codigo == 0
    return als


@pytest.fixture
def ponte_do_studio():
    """A ponte viva do Studio, com o cliente OSC fechado no fim do teste."""
    BRIDGE.reset()
    yield BRIDGE
    BRIDGE.reset()


def test_porta_ocupada_vira_erro_em_portugues(ponte_do_studio, set_do_studio):
    """A porta de resposta ocupada vira um erro que diz a porta e o que fazer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as envio:
        envio.bind(("127.0.0.1", 0))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ocupado:
            ocupado.bind(("127.0.0.1", 0))
            porta = ocupado.getsockname()[1]
            ponte_do_studio.configure(
                send_port=envio.getsockname()[1], reply_port=porta, timeout=0.2
            )
            ponte_do_studio.load(str(set_do_studio))
            estado = ponte_do_studio.state()
            assert estado["connected"] is False
            assert estado["error"] is not None
            assert str(porta) in estado["error"]
            assert "in use" in estado["error"]
            assert "AbletonOSC" in estado["error"]
            ocupado.close()
        estado = ponte_do_studio.state()
        assert "in use" not in (estado["error"] or "")
