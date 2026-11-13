"""Cliente OSC do Ableton Live: serializacao OSC 1.0 e conversa UDP.

O AbletonOSC responde na porta 11001, no endereco de origem de quem perguntou.
Por isso o cliente usa um so socket: ele envia de onde escuta.
"""

from __future__ import annotations

import socket
import struct
import time

TAMANHO_MAXIMO = 65535

# Quantos pacotes o cliente joga fora do socket antes de desistir.
LIMITE_DE_LEITURAS = 256

ENDERECO_SONG_TIME = "/live/song/get/current_song_time"
ENDERECO_SET_SONG_TIME = "/live/song/set/current_song_time"
ENDERECO_START_LISTEN = "/live/song/start_listen/current_song_time"
ENDERECO_STOP_LISTEN = "/live/song/stop_listen/current_song_time"
ENDERECO_TEMPO = "/live/song/get/tempo"
ENDERECO_IS_PLAYING = "/live/song/get/is_playing"


class LiveOscTimeout(TimeoutError):
    """O Live nao respondeu dentro do tempo esperado."""


def _preenche(dados: bytes) -> bytes:
    """Termina a string em NUL e preenche ate um multiplo de 4 bytes."""
    return dados + b"\x00" * (4 - len(dados) % 4)


def _alinha(posicao: int) -> int:
    """Arredonda a posicao para cima, ate o multiplo de 4 bytes."""
    return posicao + (-posicao) % 4


def _empacota(valor: int | float, formato: str) -> bytes:
    """Empacota um numero de 32 bits, big endian."""
    try:
        return struct.pack(formato, valor)
    except (OverflowError, struct.error) as exc:
        raise ValueError(f"OSC number outside the 32-bit range: {valor}") from exc


def _blob(dados: bytes) -> bytes:
    """Empacota um blob: tamanho de 32 bits, dados e preenchimento."""
    return struct.pack(">i", len(dados)) + dados + b"\x00" * (-len(dados) % 4)


def _codifica_argumento(valor: object) -> tuple[str, bytes]:
    """Devolve a tag e os dados de um argumento OSC."""
    if isinstance(valor, bool):
        return "i", _empacota(int(valor), ">i")
    if isinstance(valor, int):
        return "i", _empacota(valor, ">i")
    if isinstance(valor, float):
        return "f", _empacota(valor, ">f")
    if isinstance(valor, str):
        return "s", _preenche(valor.encode("utf-8"))
    if isinstance(valor, (bytes, bytearray)):
        return "b", _blob(bytes(valor))
    raise TypeError(f"OSC argument type not supported: {type(valor).__name__}")


def encode_message(address: str, *args: object) -> bytes:
    """Monta um pacote OSC 1.0 com endereco, tags de tipo e argumentos."""
    codificados = [_codifica_argumento(arg) for arg in args]
    tags = "".join(tag for tag, _dados in codificados)
    cabecalho = _preenche(address.encode("utf-8"))
    cabecalho += _preenche(("," + tags).encode("utf-8"))
    return cabecalho + b"".join(dados for _tag, dados in codificados)


def _troca(dados: bytes, posicao: int, tamanho: int) -> bytes:
    """Recorta bytes do pacote; erro quando o pacote esta truncado."""
    if posicao + tamanho > len(dados):
        raise ValueError("truncated OSC packet")
    return dados[posicao : posicao + tamanho]


def _le_string(dados: bytes, posicao: int) -> tuple[str, int]:
    """Le uma string OSC terminada em NUL; devolve texto e proxima posicao."""
    fim = dados.find(b"\x00", posicao)
    if fim < 0:
        raise ValueError("OSC string without a NUL terminator")
    try:
        texto = dados[posicao:fim].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("OSC string is not valid UTF-8") from exc
    proxima = _alinha(fim + 1)
    if proxima > len(dados):
        raise ValueError("truncated OSC string")
    return texto, proxima


def _le_numero(dados: bytes, posicao: int, formato: str) -> tuple[int | float, int]:
    """Le um numero de 32 bits, big endian."""
    (valor,) = struct.unpack(formato, _troca(dados, posicao, 4))
    return valor, posicao + 4


def _le_blob(dados: bytes, posicao: int) -> tuple[bytes, int]:
    """Le um blob OSC: tamanho, dados e preenchimento."""
    (tamanho,) = struct.unpack(">i", _troca(dados, posicao, 4))
    if tamanho < 0:
        raise ValueError("OSC blob with a negative size")
    fim = posicao + 4 + tamanho
    if fim > len(dados):
        raise ValueError("truncated OSC packet")
    return dados[posicao + 4 : fim], _alinha(fim)


def _le_argumento(dados: bytes, posicao: int, tag: str) -> tuple[object, int]:
    """Le um argumento OSC pela tag; devolve valor e proxima posicao."""
    if tag == "i":
        return _le_numero(dados, posicao, ">i")
    if tag == "f":
        return _le_numero(dados, posicao, ">f")
    if tag == "s":
        return _le_string(dados, posicao)
    if tag == "b":
        return _le_blob(dados, posicao)
    if tag == "T":
        return True, posicao
    if tag == "F":
        return False, posicao
    raise ValueError(f"unknown OSC tag: {tag!r}")


def decode_message(dados: bytes) -> tuple[str, list]:
    """Le um pacote OSC 1.0 e devolve o endereco e os argumentos."""
    if dados.startswith(b"#bundle"):
        raise ValueError("OSC bundle not supported")
    address, posicao = _le_string(dados, 0)
    if not address.startswith("/"):
        raise ValueError(f"invalid OSC address: {address!r}")
    tags, posicao = _le_string(dados, posicao)
    if not tags.startswith(","):
        raise ValueError(f"invalid OSC type tags: {tags!r}")
    args: list = []
    for tag in tags[1:]:
        valor, posicao = _le_argumento(dados, posicao, tag)
        args.append(valor)
    return address, args


class LiveOscClient:
    """Cliente OSC do Live: um socket que envia e escuta no mesmo par."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        send_port: int = 11000,
        reply_port: int = 11001,
        timeout: float = 0.5,
    ) -> None:
        self.host = host
        self.send_port = send_port
        self.timeout = timeout
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind((host, reply_port))
        except OSError:
            # A porta de resposta ocupada nao pode deixar o socket para o
            # coletor de lixo: o erro sobe depois do fechamento.
            self._sock.close()
            raise
        self.reply_port = int(self._sock.getsockname()[1])
        self._fechado = False

    def close(self) -> None:
        """Fecha o socket do cliente; chamar duas vezes nao falha."""
        if self._fechado:
            return
        self._fechado = True
        self._sock.close()

    def __enter__(self) -> LiveOscClient:
        """Usa o cliente como gerenciador de contexto."""
        return self

    def __exit__(self, *_excecao: object) -> None:
        """Fecha o cliente quando sai do contexto."""
        self.close()

    def send(self, address: str, *args: object) -> None:
        """Envia uma mensagem OSC, sem esperar resposta."""
        self._sock.sendto(encode_message(address, *args), (self.host, self.send_port))

    def _recebe_ate(self, prazo: float) -> tuple[str, list] | None:
        """Le um pacote ate o prazo; None quando o prazo passa."""
        while True:
            restante = prazo - time.monotonic()
            if restante <= 0:
                return None
            self._sock.settimeout(restante)
            try:
                dados, _origem = self._sock.recvfrom(TAMANHO_MAXIMO)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # No Windows o ICMP de porta fechada chega como erro de conexao.
                continue
            try:
                return decode_message(dados)
            except ValueError:
                continue

    def _esvazia(self) -> None:
        """Joga fora as respostas que ficaram no socket de uma leitura antiga.

        O Live responde depois do prazo quando a thread principal dele pausa
        para salvar ou abrir um plugin. Sem esta limpeza a leitura seguinte
        pega a resposta velha e a propria resposta nova fica na fila.
        """
        timeout_anterior = self._sock.gettimeout()
        self._sock.setblocking(False)
        try:
            for _ in range(LIMITE_DE_LEITURAS):
                try:
                    self._sock.recvfrom(TAMANHO_MAXIMO)
                except (BlockingIOError, socket.timeout):
                    return
                except ConnectionResetError:
                    # No Windows o ICMP de porta fechada chega como erro de conexao.
                    continue
        finally:
            self._sock.settimeout(timeout_anterior)

    def query(self, address: str, *args: object) -> list:
        """Envia uma mensagem e espera a resposta do mesmo endereco."""
        self._esvazia()
        self.send(address, *args)
        prazo = time.monotonic() + self.timeout
        while True:
            pacote = self._recebe_ate(prazo)
            if pacote is None:
                raise LiveOscTimeout(f"no answer from Live for {address}")
            endereco, lidos = pacote
            if endereco == address:
                return lidos

    def query_many(self, addresses: list[str]) -> dict[str, list]:
        """Envia varias perguntas e espera a resposta de cada endereco.

        O AbletonOSC responde uma vez a cada 100 ms: perguntar um por vez
        gasta tres tiques, e a posicao da song chega velha. Aqui todas as
        perguntas vao juntas e as respostas vem no mesmo tique.
        """
        esperadas = list(dict.fromkeys(addresses))
        self._esvazia()
        for address in esperadas:
            self.send(address)
        prazo = time.monotonic() + self.timeout
        respostas: dict[str, list] = {}
        while len(respostas) < len(esperadas):
            pacote = self._recebe_ate(prazo)
            if pacote is None:
                faltando = [endereco for endereco in esperadas if endereco not in respostas]
                raise LiveOscTimeout(f"no answer from Live for {', '.join(faltando)}")
            endereco, lidos = pacote
            if endereco in esperadas and endereco not in respostas:
                respostas[endereco] = lidos
        return respostas

    def poll(self, timeout: float | None = None) -> tuple[str, list] | None:
        """Le um pacote que ja chegou; None quando nada chegou a tempo."""
        if timeout is None:
            timeout = self.timeout
        return self._recebe_ate(time.monotonic() + timeout)

    def song_time(self) -> float:
        """Posicao da song em batidas."""
        return float(self.query(ENDERECO_SONG_TIME)[0])

    def set_song_time(self, beats: float) -> None:
        """Move a song para a posicao dada em batidas."""
        self.send(ENDERECO_SET_SONG_TIME, float(beats))

    def tempo(self) -> float:
        """Tempo do Live em BPM."""
        return float(self.query(ENDERECO_TEMPO)[0])

    def is_playing(self) -> bool:
        """Diz se o Live esta tocando."""
        return bool(self.query(ENDERECO_IS_PLAYING)[0])

    def listen_song_time(self) -> None:
        """Pede ao Live que avise a cada mudanca da posicao da song."""
        self.send(ENDERECO_START_LISTEN)

    def stop_listen_song_time(self) -> None:
        """Pede ao Live que pare de avisar a posicao da song."""
        self.send(ENDERECO_STOP_LISTEN)
