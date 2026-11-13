"""Achado F19: o cliente OSC tem de jogar fora as respostas atrasadas.

O Live pausa a thread principal enquanto salva ou abre um plugin: as
respostas chegam depois do prazo do cliente e ficam na fila do socket. A
leitura seguinte pega a resposta velha, a propria resposta nova fica na
fila, e o atraso cresce a cada pausa.
"""

import socket
import threading
import time

import pytest

from aafton.live_osc import (
    ENDERECO_SONG_TIME,
    LiveOscClient,
    LiveOscTimeout,
    decode_message,
    encode_message,
)

HOST = "127.0.0.1"

# O AbletonOSC responde uma vez a cada 100 ms.
PERIODO = 0.1

# Quantas batidas a song anda por segundo.
VELOCIDADE = 10.0

# O prazo do cliente, menor que a pausa do Live.
PRAZO = 0.3

# Quanto tempo o Live fica pausado, mais que o prazo do cliente.
PAUSA = 0.6

# Folga na comparacao das posicoes, em batidas.
FOLGA = 0.5

# O OSC manda a posicao em float32: o valor vem um fio abaixo do numero exato.
TOLERANCIA = 0.001


class AbletonFalso:
    """AbletonOSC falso: guarda as perguntas e as responde no tique de 100 ms.

    A thread principal do Live pode pausar para salvar ou abrir um plugin:
    o tique para e as perguntas ficam na fila. Quando a pausa acaba, tudo e
    respondido de uma vez.
    """

    def __init__(self, periodo: float = PERIODO) -> None:
        self.periodo = periodo
        self.enviados = 0
        self.recebidos: list[str] = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((HOST, 0))
        self.porta = self._sock.getsockname()[1]
        self._fila: list[tuple[str, object, tuple]] = []
        self._pausa_ate = 0.0
        self._inicio = time.monotonic()
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Fecha o socket falso e para a thread do tique."""
        self._parar.set()
        self._thread.join(timeout=2.0)
        self._sock.close()

    @property
    def posicao(self) -> float:
        """A posicao da song agora, como o Live a contaria."""
        return 100.0 + VELOCIDADE * (time.monotonic() - self._inicio)

    def pausar(self, duracao: float) -> None:
        """Suspende o tique por um tempo; as perguntas ficam na fila."""
        self._pausa_ate = time.monotonic() + duracao

    def _loop(self) -> None:
        """Recebe as perguntas e as responde no tique."""
        self._sock.settimeout(0.01)
        proximo = time.monotonic()
        while not self._parar.is_set():
            try:
                dados, origem = self._sock.recvfrom(65535)
            except socket.timeout:
                dados = None
            except OSError:
                break
            if dados is not None:
                self._guarda(dados, origem)
            agora = time.monotonic()
            if agora < self._pausa_ate or agora < proximo or not self._fila:
                continue
            fila, self._fila = self._fila, []
            for address, _args, origem in fila:
                self._manda(address, origem)
            proximo = agora + self.periodo

    def _guarda(self, dados: bytes, origem: tuple) -> None:
        """Guarda a pergunta para o proximo tique."""
        try:
            address, args = decode_message(dados)
        except ValueError:
            return
        self.recebidos.append(address)
        self._fila.append((address, args, origem))

    def _manda(self, address: str, origem: tuple) -> None:
        """Manda a resposta com a posicao da song agora."""
        try:
            self._sock.sendto(encode_message(address, self.posicao), origem)
            self.enviados += 1
        except OSError:
            pass


def _espera(predicado, limite: float = 3.0) -> None:
    """Espera a condicao virar verdadeira, sem passar do limite."""
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if predicado():
            return
        time.sleep(0.01)
    raise AssertionError("a condicao nao virou verdadeira a tempo")


def _cliente(servidor: AbletonFalso) -> LiveOscClient:
    """Cliente OSC que fala com o AbletonOSC falso."""
    return LiveOscClient(send_port=servidor.porta, reply_port=0, timeout=PRAZO)


def _pausa_do_live(servidor: AbletonFalso, cliente: LiveOscClient) -> None:
    """Faz o Live pausar mais que o prazo e espera a resposta atrasada.

    A resposta chega depois do prazo do cliente e fica no socket: e ela que
    a leitura seguinte vai pegar se o cliente nao limpar a fila.
    """
    antes = servidor.enviados
    servidor.pausar(PAUSA)
    with pytest.raises(LiveOscTimeout):
        cliente.query_many([ENDERECO_SONG_TIME])
    _espera(lambda: servidor.enviados > antes)


def _confere(obtido: float, verdadeiro: float) -> None:
    """A posicao lida e a de agora, com a folga de um tique."""
    assert obtido >= verdadeiro - TOLERANCIA, (obtido, verdadeiro)
    assert obtido <= verdadeiro + VELOCIDADE * PERIODO + FOLGA, (obtido, verdadeiro)


def test_query_many_descarta_a_resposta_atrasada():
    """Apos uma pausa do Live, a posicao lida e a nova, nao a velha."""
    servidor = AbletonFalso()
    try:
        with _cliente(servidor) as cliente:
            _pausa_do_live(servidor, cliente)
            time.sleep(0.2)
            verdadeiro = servidor.posicao
            respostas = cliente.query_many([ENDERECO_SONG_TIME])
            obtido = float(respostas[ENDERECO_SONG_TIME][0])
    finally:
        servidor.close()
    _confere(obtido, verdadeiro)


def test_query_descarta_a_resposta_atrasada():
    """O mesmo vale para uma pergunta so."""
    servidor = AbletonFalso()
    try:
        with _cliente(servidor) as cliente:
            _pausa_do_live(servidor, cliente)
            time.sleep(0.2)
            verdadeiro = servidor.posicao
            obtido = float(cliente.query(ENDERECO_SONG_TIME)[0])
    finally:
        servidor.close()
    _confere(obtido, verdadeiro)


def test_query_many_aguenta_duas_pausas_do_live():
    """O atraso nao se acumula: depois de duas pausas a posicao e a nova."""
    servidor = AbletonFalso()
    try:
        with _cliente(servidor) as cliente:
            for _ in range(2):
                _pausa_do_live(servidor, cliente)
                time.sleep(0.2)
                verdadeiro = servidor.posicao
                respostas = cliente.query_many([ENDERECO_SONG_TIME])
                _confere(float(respostas[ENDERECO_SONG_TIME][0]), verdadeiro)
    finally:
        servidor.close()
