"""Achado F28: os sockets OSC que ficavam para o coletor de lixo.

O cliente OSC criava o socket e so depois bindava a porta de resposta: quando
a porta ja estava ocupada, o socket ficava aberto e o erro subia sem fechar
nada. A ponte do Studio abre um cliente por consulta, e ele so fecha no
reset.

Este arquivo cobre as duas pontas: o cliente que falha no bind e a ponte que
precisa fechar o cliente depois de uma consulta de estado.
"""

import json
import socket

import pytest

from aafton.live_osc import LiveOscClient
from aafton.sidecar import sidecar_path_for
from aafton.studio.timecode import TimecodeBridge
from tests.unit.test_studio_timecode_r1 import ServidorLento

HOST = "127.0.0.1"

# O socket de verdade, guardado antes de qualquer monkeypatch.
SOCKET_REAL = socket.socket


class SocketQueAnota:
    """Socket de verdade por dentro, que anota quando foi fechado."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        self._interno = SOCKET_REAL(*args, **kwargs)
        self.fechado = False

    def close(self) -> None:
        """Anota o fechamento e fecha o socket de verdade."""
        self.fechado = True
        self._interno.close()

    def __getattr__(self, nome: str) -> object:
        """Manda o resto das chamadas para o socket de dentro."""
        return getattr(self._interno, nome)


def _set_de_teste(tmp_path) -> str:
    """Cria um .als de mentira com o sidecar de teste ao lado."""
    als = tmp_path / "set.als"
    als.write_text("isto nao e um set do Live", encoding="utf-8")
    carga = {
        "format_version": 2,
        "sample_rate": 48000,
        "bpm": 120.0,
        "timecode_rate": {"num": 30, "den": 1, "drop_frame": False},
        "live_origin_samples": 0,
        "session_start_samples": 0,
    }
    sidecar_path_for(als).write_text(json.dumps(carga), encoding="utf-8")
    return str(als)


def test_cliente_com_a_porta_ocupada_fecha_o_socket(monkeypatch):
    """Com a porta de resposta ocupada, o cliente falha e nao deixa socket."""
    ocupado = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ocupado.bind((HOST, 0))
    porta = ocupado.getsockname()[1]
    criados: list[SocketQueAnota] = []

    def cria(*args: object, **kwargs: object) -> SocketQueAnota:
        """Cria um socket que anota o fechamento."""
        novo = SocketQueAnota(*args, **kwargs)
        criados.append(novo)
        return novo

    # O live_osc usa o modulo socket: o patch pega o socket do cliente.
    monkeypatch.setattr(socket, "socket", cria)
    try:
        with pytest.raises(OSError):
            LiveOscClient(reply_port=porta)
    finally:
        ocupado.close()
    assert criados, "o cliente nem chegou a criar o socket"
    for criado in criados:
        assert criado.fechado, "o socket do cliente ficou aberto"
        assert criado._interno.fileno() == -1, "o socket do cliente nao foi fechado"


def test_reset_fecha_o_cliente_depois_da_consulta(tmp_path):
    """Depois de uma consulta de estado, o reset fecha o cliente da ponte."""
    servidor = ServidorLento()
    ponte = TimecodeBridge(send_port=servidor.porta, reply_port=0, timeout=0.5)
    try:
        ponte.load(_set_de_teste(tmp_path))
        estado = ponte.state()
        assert estado["connected"] is True
        cliente = ponte._osc
        assert cliente is not None, "a consulta de estado nao abriu cliente"
        sock = cliente._sock
        assert sock.fileno() != -1, "o cliente ja nasceu fechado"
        ponte.reset()
        assert ponte._osc is None, "o reset nao largou o cliente"
        assert sock.fileno() == -1, "o cliente ficou aberto depois do reset"
    finally:
        ponte.reset()
        servidor.close()
