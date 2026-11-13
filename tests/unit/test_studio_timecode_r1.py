"""Achados da revisao da fase 2 do relogio de timecode do Studio.

Cada teste aponta um achado: as tres perguntas ao Live em uma ida so (F3), o
video em blocos de 4 MiB e o servidor em threads (F2), o Range com sufixo e
a unidade em qualquer caixa (F15), a origem do video que nao muda com o
start (F7) e a taxa fora do RATES_LTC (F11).
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

import aafton.web_app as web
from aafton.live_clock import label_to_beats
from aafton.live_osc import (
    ENDERECO_IS_PLAYING,
    ENDERECO_SONG_TIME,
    ENDERECO_TEMPO,
    LiveOscClient,
    LiveOscTimeout,
)
from aafton.sidecar import Anchor, sidecar_path_for
from aafton.studio.page import load_timecode_page
from aafton.studio.timecode import BRIDGE, TimecodeBridge
from aafton.timecode import TimecodeRate
from tests.unit.test_live_osc import ServidorFalso

HOST = "127.0.0.1"
# O AbletonOSC responde uma vez a cada 100 ms.
PERIODO = 0.1
TAMANHO_VIDEO = 10 * 1024 * 1024
LIMITE_RANGE = 4 * 1024 * 1024


class ServidorLento(ServidorFalso):
    """O servidor falso do Live, que segura as respostas e as manda de uma vez.

    O AbletonOSC real responde no seu tique de 100 ms: as tres perguntas do
    estado sao respondidas no mesmo tique, e nao em tres.
    """

    def __init__(self, periodo: float = PERIODO) -> None:
        self.periodo = periodo
        self._fila: list[tuple[str, list, tuple]] = []
        self._ultimo = 0.0
        super().__init__()
        self._tique = threading.Thread(target=self._responde_tudo, daemon=True)
        self._tique.start()

    def _responde(self, address, args, origem) -> None:
        """Guarda a resposta para o proximo tique."""
        self._fila.append((address, args, origem))
        self._ultimo = time.monotonic()

    def _responde_tudo(self) -> None:
        while not self._parar.is_set():
            time.sleep(0.01)
            if not self._fila or time.monotonic() - self._ultimo < self.periodo:
                continue
            fila, self._fila = self._fila, []
            for address, args, origem in fila:
                self._envia(address, args, origem)

    def _envia(self, address, args, origem) -> None:
        try:
            super()._responde(address, args, origem)
        except OSError:
            pass


def _ancora_de_teste() -> Anchor:
    """A ancora que o sidecar de teste descreve."""
    return Anchor(
        live_origin_samples=0,
        session_start_samples=0,
        sample_rate=48000,
        timecode_rate=TimecodeRate(30, 1),
        bpm=120.0,
    )


def _set_de_teste(tmp_path, num: int = 30, den: int = 1, drop: bool = False) -> str:
    """Cria um .als de mentira com o sidecar de teste ao lado."""
    als = tmp_path / "set.als"
    als.write_text("isto nao e um set do Live", encoding="utf-8")
    carga = {
        "format_version": 2,
        "sample_rate": 48000,
        "bpm": 120.0,
        "timecode_rate": {"num": num, "den": den, "drop_frame": drop},
        "live_origin_samples": 0,
        "session_start_samples": 0,
    }
    sidecar_path_for(als).write_text(json.dumps(carga), encoding="utf-8")
    return str(als)


def _chama(url, caminho, payload=None, headers=None, timeout=10.0):
    """Chama a rota e devolve status, cabecalhos e corpo."""
    dados = json.dumps(payload).encode("utf-8") if payload is not None else None
    envio = dict(headers or {})
    if dados is not None:
        envio["Content-Type"] = "application/json"
    request = urllib.request.Request(url + caminho, data=dados, headers=envio)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resposta:
            cabecalhos = {k.lower(): v for k, v in resposta.headers.items()}
            return resposta.status, cabecalhos, resposta.read()
    except urllib.error.HTTPError as erro:
        cabecalhos = {k.lower(): v for k, v in erro.headers.items()}
        return erro.code, cabecalhos, erro.read()


@pytest.fixture
def servidor_web():
    """O Studio rodando numa porta livre, em threads."""
    server = web.run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", server.server_port
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def ponte():
    """A ponte viva do Studio, limpa antes e depois do teste."""
    BRIDGE.reset()
    BRIDGE.configure(reply_port=0, timeout=0.05)
    yield BRIDGE
    BRIDGE.reset()


@pytest.fixture
def ponte_com_video(ponte, tmp_path):
    """A ponte com um set carregado e um video de 10 MiB."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * TAMANHO_VIDEO)
    ponte.load(_set_de_teste(tmp_path))
    # O video vem do set importado; aqui ele e colocado direto no lugar.
    ponte._video = (video, 48000, 0)
    return ponte


def test_as_tres_perguntas_vao_juntas_e_o_estado_e_rapido(tmp_path):
    """As tres perguntas do estado sao respondidas no mesmo tique do Live."""
    servidor = ServidorLento()
    ponte = TimecodeBridge(send_port=servidor.porta, reply_port=0, timeout=0.5)
    try:
        ponte.load(_set_de_teste(tmp_path))
        inicio = time.monotonic()
        estado = ponte.state()
        duracao = time.monotonic() - inicio
    finally:
        ponte.reset()
        servidor.close()
    assert duracao < 0.16, duracao
    assert estado["connected"] is True
    assert estado["beats"] == pytest.approx(4.0)
    # A posicao da song e a ultima pergunta: ela e a mais nova.
    assert servidor.recebidos[-3:] == [ENDERECO_TEMPO, ENDERECO_IS_PLAYING, ENDERECO_SONG_TIME]


def test_query_many_levanta_timeout_nomeando_os_faltantes():
    """Sem resposta, o erro diz quais enderecos faltaram."""
    mudo = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mudo.bind((HOST, 0))
    porta = mudo.getsockname()[1]
    try:
        with LiveOscClient(send_port=porta, reply_port=0, timeout=0.1) as cliente:
            with pytest.raises(LiveOscTimeout) as erro:
                cliente.query_many([ENDERECO_TEMPO, ENDERECO_IS_PLAYING])
    finally:
        mudo.close()
    assert ENDERECO_TEMPO in str(erro.value)
    assert ENDERECO_IS_PLAYING in str(erro.value)


def test_video_em_blocos_de_4_mib(servidor_web, ponte_com_video):
    """Um Range grande e servido em 4 MiB, com o fim real no Content-Range."""
    url, _porta = servidor_web
    status, cabecalhos, corpo = _chama(url, "/api/timecode/video", headers={"Range": "bytes=0-"})
    assert status == 206
    assert cabecalhos["content-range"] == f"bytes 0-{LIMITE_RANGE - 1}/{TAMANHO_VIDEO}"
    assert len(corpo) == LIMITE_RANGE


def _termina_a_escrita(cliente: socket.socket) -> None:
    """Le o resto do video, para o servidor terminar a escrita sem erro."""
    cliente.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    cliente.settimeout(5)
    try:
        while cliente.recv(65536):
            pass
    except OSError:
        pass


def test_leitor_lento_do_video_nao_trava_o_estado(servidor_web, ponte_com_video):
    """Um cliente que nao le o video nao segura o estado do relogio."""
    url, porta = servidor_web
    cliente = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Um buffer de recepcao minimo: o servidor trava escrevendo o video.
    cliente.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    cliente.settimeout(5)
    cliente.connect((HOST, porta))
    try:
        # Host valido e sem Range: o servidor manda o video inteiro (200) e a
        # escrita trava, porque o cliente nao le nada.
        pedido = f"GET /api/timecode/video HTTP/1.1\r\nHost: {HOST}:{porta}\r\n\r\n"
        cliente.sendall(pedido.encode("ascii"))
        # A resposta comeca pelo status: o pedido passou pela rota do video.
        cabecalho = cliente.recv(256)
        assert b" 200 " in cabecalho, cabecalho
        time.sleep(0.3)
        inicio = time.monotonic()
        try:
            status, _cabecalhos, _corpo = _chama(url, "/api/timecode/state", timeout=3.0)
        except (urllib.error.URLError, OSError) as erro:
            pytest.fail(f"o estado nao respondeu enquanto o video esperava: {erro}")
        duracao = time.monotonic() - inicio
    finally:
        _termina_a_escrita(cliente)
        cliente.close()
    assert status == 200
    assert duracao < 1.0, duracao


@pytest.mark.parametrize(
    "pedido,inicio,fim",
    [
        ("bytes=0-99", 0, 99),
        ("BYTES=0-99", 0, 99),
        ("bytes=-1024", TAMANHO_VIDEO - 1024, TAMANHO_VIDEO - 1),
    ],
)
def test_range_responde_a_faixa_pedida(servidor_web, ponte_com_video, pedido, inicio, fim):
    """O Range responde a faixa pedida, com a unidade em qualquer caixa."""
    url, _porta = servidor_web
    status, cabecalhos, corpo = _chama(url, "/api/timecode/video", headers={"Range": pedido})
    assert status == 206
    assert cabecalhos["content-range"] == f"bytes {inicio}-{fim}/{TAMANHO_VIDEO}"
    assert len(corpo) == fim - inicio + 1


def test_range_comecando_depois_do_fim_da_416(servidor_web, ponte_com_video):
    """Um inicio fora do arquivo e um 416."""
    url, _porta = servidor_web
    status, cabecalhos, _corpo = _chama(
        url,
        "/api/timecode/video",
        headers={"Range": "bytes=99999999-"},
    )
    assert status == 416
    assert cabecalhos["content-range"] == f"bytes */{TAMANHO_VIDEO}"


def test_o_start_nao_move_o_video_do_set(ponte_com_video):
    """O start renomeia o TC do beat zero, mas o clip de video fica no lugar."""
    antes = ponte_com_video.state()["video"]["start_seconds"]
    depois = ponte_com_video.start("02:00:00:00")["video"]["start_seconds"]
    assert antes == depois


@pytest.mark.parametrize(
    "num,den,drop,esperado",
    [(50, 1, False, "50/1"), (60000, 1001, True, "60000/1001df")],
)
def test_taxa_fora_do_rates_ltc_vem_numerada(tmp_path, ponte, num, den, drop, esperado):
    """Uma taxa que o LTC nao conhece vem como NUM/DEN, e nao como erro."""
    estado = ponte.load(_set_de_teste(tmp_path, num=num, den=den, drop=drop))
    assert estado["rate"] == esperado


def test_a_pagina_calcula_o_fps_da_taxa_numerada():
    """O JS da pagina divide o numerador pelo denominador da taxa."""
    pagina = load_timecode_page()
    inicio = pagina.index("function fpsDaTaxa(")
    corpo = pagina[inicio : pagina.index("\n  }", inicio)]
    assert '"/"' in corpo
    assert "num / den" in corpo


def test_o_goto_manda_o_menor_float32_acima_do_comeco(tmp_path):
    """O Live recebe um float32 que nao cai antes do comeco do frame."""
    servidor = ServidorLento()
    ponte = TimecodeBridge(send_port=servidor.porta, reply_port=0, timeout=0.5)
    try:
        ponte.load(_set_de_teste(tmp_path))
        beats = label_to_beats("01:00:00:02", _ancora_de_teste())
        ponte.goto("01:00:00:02")
        for _ in range(150):
            if servidor.song_time != 4.0:
                break
            time.sleep(0.02)
        enviado = servidor.song_time
    finally:
        ponte.reset()
        servidor.close()
    assert enviado >= beats
