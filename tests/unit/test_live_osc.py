"""Testes do cliente OSC do Live: serializacao OSC 1.0 e conversa com um servidor falso.

Nenhum Live real e necessario. O servidor falso deste arquivo imita o
AbletonOSC: responde em UDP, na porta que o cliente escolheu, e manda o valor
como unico argumento da resposta.
"""

import socket
import threading
import time

import pytest

from aafton.live_osc import (
    LiveOscClient,
    LiveOscTimeout,
    decode_message,
    encode_message,
)

HOST = "127.0.0.1"
GET_SONG_TIME = "/live/song/get/current_song_time"
SET_SONG_TIME = "/live/song/set/current_song_time"
START_LISTEN = "/live/song/start_listen/current_song_time"
STOP_LISTEN = "/live/song/stop_listen/current_song_time"
GET_TEMPO = "/live/song/get/tempo"
GET_IS_PLAYING = "/live/song/get/is_playing"


def test_bytes_exatos_do_endereco_so():
    """Endereco e string de tipos: NUL e preenchimento ate 4 bytes."""
    esperado = b"/live/song/get/tempo\x00\x00\x00\x00" + b",\x00\x00\x00"
    assert encode_message(GET_TEMPO) == esperado


def test_bytes_exatos_de_um_inteiro():
    inteiro = b"/a\x00\x00" + b",i\x00\x00" + b"\x00\x00\x00\x01"
    assert encode_message("/a", 1) == inteiro


def test_bytes_exatos_de_um_float():
    real = b"/a\x00\x00" + b",f\x00\x00" + b"\x3f\x00\x00\x00"
    assert encode_message("/a", 0.5) == real


def test_preenchimento_de_string():
    """A string e terminada em NUL e preenchida ate um multiplo de 4 bytes."""
    assert encode_message("/a", "abc") == b"/a\x00\x00" + b",s\x00\x00" + b"abc\x00"
    assert encode_message("/a", "abcd") == b"/a\x00\x00" + b",s\x00\x00" + b"abcd\x00\x00\x00\x00"
    assert encode_message("/a", "abcde") == b"/a\x00\x00" + b",s\x00\x00" + b"abcde\x00\x00\x00"


@pytest.mark.parametrize(
    "valor",
    [
        pytest.param(1, id="int"),
        pytest.param(-7, id="int_negativo"),
        pytest.param(0.5, id="float"),
        pytest.param(-2.5, id="float_negativo"),
        pytest.param("texto", id="str"),
        pytest.param(b"\x01\x02\x03", id="bytes"),
    ],
)
def test_volta_de_cada_tipo(valor):
    address, lidos = decode_message(encode_message("/live/teste", valor))
    assert address == "/live/teste"
    assert lidos == [valor]


def test_mensagem_sem_argumentos():
    assert decode_message(encode_message(GET_TEMPO)) == (GET_TEMPO, [])


def test_booleano_via_int():
    """O booleano vai como int 1 ou 0, com a tag i."""
    assert encode_message("/a", True) == b"/a\x00\x00" + b",i\x00\x00" + b"\x00\x00\x00\x01"
    assert encode_message("/a", False) == b"/a\x00\x00" + b",i\x00\x00" + b"\x00\x00\x00\x00"
    assert decode_message(encode_message("/a", True)) == ("/a", [1])


def test_tags_de_booleano_na_decodificacao():
    """As tags T e F nao carregam dados e viram booleano."""
    assert decode_message(b"/a\x00\x00" + b",T\x00\x00") == ("/a", [True])
    assert decode_message(b"/a\x00\x00" + b",F\x00\x00") == ("/a", [False])


def test_tipo_desconhecido_levanta_type_error():
    with pytest.raises(TypeError):
        encode_message("/a", {"chave": 1})


def test_bundle_levanta_value_error():
    with pytest.raises(ValueError):
        decode_message(b"#bundle\x00\x00\x00\x00")


def test_pacote_truncado_levanta_value_error():
    with pytest.raises(ValueError):
        decode_message(b"/live/song/get/tempo")
    with pytest.raises(ValueError):
        decode_message(b"/a\x00\x00" + b",i\x00\x00" + b"\x00\x00")


class ServidorFalso:
    """Servidor OSC que imita o AbletonOSC, sempre numa porta livre."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((HOST, 0))
        self.porta = self.sock.getsockname()[1]
        self.song_time = 4.0
        self.tempo = 120.0
        self.tocando = True
        self.recebidos = []
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def close(self):
        self._parar.set()
        self._thread.join(timeout=2.0)
        self.sock.close()

    def _loop(self):
        self.sock.settimeout(0.1)
        while not self._parar.is_set():
            try:
                dados, origem = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                address, args = decode_message(dados)
            except ValueError:
                continue
            self.recebidos.append(address)
            self._responde(address, args, origem)

    def _responde(self, address, args, origem):
        if address == SET_SONG_TIME:
            self.song_time = float(args[0])
        elif address == GET_SONG_TIME:
            self._manda(origem, GET_SONG_TIME, self.song_time)
        elif address == GET_TEMPO:
            # Um pacote de outro endereco antes da resposta certa: o cliente
            # tem de pular esse e esperar o do endereco consultado.
            self._manda(origem, "/live/outro/endereco", 1.0)
            self._manda(origem, GET_TEMPO, self.tempo)
        elif address == GET_IS_PLAYING:
            self._manda(origem, GET_IS_PLAYING, self.tocando)
        elif address == START_LISTEN:
            for valor in (10.0, 11.0, 12.0):
                self._manda(origem, GET_SONG_TIME, valor)

    def _manda(self, origem, address, valor):
        self.sock.sendto(encode_message(address, valor), origem)


def test_conversa_com_o_servidor_falso():
    servidor = ServidorFalso()
    try:
        with LiveOscClient(send_port=servidor.porta, reply_port=0, timeout=0.3) as cliente:
            assert cliente.reply_port > 0
            assert cliente.song_time() == 4.0
            cliente.set_song_time(8.0)
            assert cliente.song_time() == 8.0
            # O servidor manda um pacote de outro endereco antes do tempo.
            assert cliente.tempo() == 120.0
            assert cliente.is_playing() is True
            cliente.listen_song_time()
            valores = []
            for _ in range(3):
                pacote = cliente.poll(timeout=0.3)
                assert pacote is not None
                address, args = pacote
                assert address == GET_SONG_TIME
                valores.append(args[0])
            assert valores == [10.0, 11.0, 12.0]
            cliente.stop_listen_song_time()
    finally:
        servidor.close()
    assert GET_SONG_TIME in servidor.recebidos
    assert START_LISTEN in servidor.recebidos
    assert STOP_LISTEN in servidor.recebidos


def test_timeout_sem_resposta():
    """Ninguem responde na porta de envio: a consulta tem de expirar."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as mudo:
        # O socket fica aberto de proposito, para o sistema nao responder ICMP
        # de porta fechada, que no Windows vira erro de conexao e nao timeout.
        mudo.bind((HOST, 0))
        porta = mudo.getsockname()[1]
        with LiveOscClient(send_port=porta, reply_port=0, timeout=0.3) as cliente:
            inicio = time.monotonic()
            with pytest.raises(LiveOscTimeout):
                cliente.song_time()
            assert time.monotonic() - inicio < 1.0
            assert cliente.poll(timeout=0.1) is None
