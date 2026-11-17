"""Ponte entre o Studio e o Live: posicao, origem e video do timecode.

A ponte guarda o set, pergunta a posicao ao Live por OSC e traz a cola HTTP.
"""

from __future__ import annotations

import errno
import functools
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..als_read import read_session
from ..live_clock import beats_float32, beats_to_label, label_to_beats, tempo_warning
from ..live_osc import (
    ENDERECO_IS_PLAYING,
    ENDERECO_SONG_TIME,
    ENDERECO_TEMPO,
    LiveOscClient,
    LiveOscTimeout,
)
from ..ltc import RATES_LTC
from ..report import Report
from ..sidecar import Anchor, SidecarMissingError, read_anchor, resolve_video_path
from ..timecode import label_to_samples, samples_to_label
from .page import load_timecode_page

# Tamanho do bloco lido do video a cada vez que ele vai para a rede.
TAMANHO_CHUNK = 1024 * 1024
# Limite de bytes por resposta de Range: o navegador pede o resto depois.
LIMITE_RANGE = 4 * 1024 * 1024
# Porta em que o Live escuta o AbletonOSC e porta em que a ponte escuta.
PORTA_ENVIO = 11000
PORTA_RESPOSTA = 11001
TEMPO_LIMITE = 0.2
# Mensagem de erro quando o Studio ainda nao tem set carregado.
ERRO_SEM_SET = "no set loaded"
# Codigo do Windows para "endereco ja em uso" em socket.
WINERROR_PORTA_EM_USO = 10048
# Mensagem que explica que a porta de resposta do AbletonOSC esta ocupada.
ERRO_PORTA_EM_USO = (
    "the UDP port {porta} is already in use by another program; AbletonOSC only "
    "answers on that port, so close the other AbletonOSC client (for example "
    "an Ableton MCP server) and the clock connects on its own"
)


class SemSetCarregado(Exception):
    """Erro de uso quando ainda nao ha set carregado no Studio."""


# Erros que viram resposta 400 com o motivo na resposta.
ERROS_DE_ENTRADA = (SemSetCarregado, ValueError, OSError, SidecarMissingError)


def _porta_ocupada(exc: OSError) -> bool:
    """Diz se o erro e de endereco UDP ja em uso (EADDRINUSE ou winerror 10048)."""
    winerror = getattr(exc, "winerror", None)
    return exc.errno == errno.EADDRINUSE or winerror == WINERROR_PORTA_EM_USO


def _travado(metodo: Any) -> Any:
    """Roda o metodo da ponte com a trava da ponte.

    Uma trava so para a ponte: as perguntas ao Live nunca se cruzam, e o
    video servido nao segura o estado.
    """

    @functools.wraps(metodo)
    def envolvido(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._trava:
            return metodo(self, *args, **kwargs)

    return envolvido


class TimecodeBridge:
    """Ponte do Studio para o Live: set carregado, origem e video."""

    def __init__(
        self,
        send_port: int = PORTA_ENVIO,
        reply_port: int = PORTA_RESPOSTA,
        timeout: float = TEMPO_LIMITE,
    ) -> None:
        """Abre a ponte com as portas e o tempo de espera do Live."""
        self._send_port = send_port
        self._reply_port = reply_port
        self._timeout = timeout
        self._osc: LiveOscClient | None = None
        self._ancora: Anchor | None = None
        self._ancora_sidecar: Anchor | None = None
        self._video: tuple[Path, int, int] | None = None
        self._sobreposto = False
        # Uma trava so para a ponte: as perguntas ao Live nunca se cruzam.
        self._trava = threading.Lock()

    @_travado
    def configure(
        self,
        send_port: int | None = None,
        reply_port: int | None = None,
        timeout: float | None = None,
    ) -> None:
        """Aponta a ponte para outro Live; None mantem o valor atual."""
        if send_port is not None:
            self._send_port = send_port
        if reply_port is not None:
            self._reply_port = reply_port
        if timeout is not None:
            self._timeout = timeout
        self._fecha_cliente()

    @_travado
    def reset(self) -> None:
        """Esquece o set carregado e fecha o cliente OSC."""
        self._fecha_cliente()
        self._ancora = None
        self._ancora_sidecar = None
        self._video = None
        self._sobreposto = False

    def _fecha_cliente(self) -> None:
        """Fecha o cliente OSC, quando houver um aberto."""
        if self._osc is not None:
            self._osc.close()
            self._osc = None

    def _cliente(self) -> LiveOscClient:
        """Cliente OSC aberto sob demanda e reaproveitado entre as chamadas."""
        if self._osc is None:
            self._osc = self._abre_cliente()
        return self._osc

    def _abre_cliente(self) -> LiveOscClient:
        """Abre o cliente OSC; traduz o erro de porta de resposta ocupada."""
        try:
            return LiveOscClient(
                send_port=self._send_port,
                reply_port=self._reply_port,
                timeout=self._timeout,
            )
        except OSError as exc:
            if not _porta_ocupada(exc):
                raise
            raise OSError(ERRO_PORTA_EM_USO.format(porta=self._reply_port)) from exc

    @_travado
    def load(self, als_path: str) -> dict[str, Any]:
        """Carrega o set: ancora do sidecar e informacoes do video."""
        if not als_path:
            raise ValueError("set path is missing")
        self._ancora = self._ancora_sidecar = read_anchor(als_path)
        self._sobreposto = False
        self._video = self._video_do_set(als_path, self._ancora)
        return self._estado()

    def _video_do_set(self, als_path: str, anchor: Anchor) -> tuple[Path, int, int] | None:
        """Achata o video do set: caminho, inicio na timeline e offset na fonte."""
        if not anchor.video_path:
            return None
        try:
            sessao = read_session(als_path, Report(strict=False))
        except Exception:  # o video e opcional: o relogio funciona sem ele
            return None
        for track in sessao.tracks:
            for clip in track.clips:
                if track.is_video:
                    offset = clip.source_offset_samples or 0
                    caminho = resolve_video_path(anchor.video_path, Path(als_path))
                    return (caminho, clip.timeline_start_samples, offset)
        return None

    @_travado
    def state(self) -> dict[str, Any]:
        """Estado do relogio: posicao do Live, origem e video do set."""
        return self._estado()

    def _estado(self) -> dict[str, Any]:
        """Estado do relogio, sem a trava: os metodos publicos a chamam."""
        anchor = self._ancora_atual()
        dados: dict[str, Any] = {
            "connected": False,
            "playing": False,
            "beats": None,
            "label": None,
            "origin_label": self._rotulo_de_origem(anchor),
            "origin_from_sidecar": not self._sobreposto,
            "rate": self._chave_da_taxa(anchor),
            "bpm": anchor.bpm,
            "tempo_warning": None,
            "error": None,
            "video": self._video_atual(anchor),
        }
        try:
            cliente = self._cliente()
            respostas = cliente.query_many(
                [ENDERECO_TEMPO, ENDERECO_IS_PLAYING, ENDERECO_SONG_TIME]
            )
        except (LiveOscTimeout, OSError) as exc:
            dados["error"] = str(exc)
            return dados
        tempo = float(respostas[ENDERECO_TEMPO][0])
        tocando = bool(respostas[ENDERECO_IS_PLAYING][0])
        posicao = float(respostas[ENDERECO_SONG_TIME][0])
        dados["connected"] = True
        dados["playing"] = bool(tocando)
        dados["beats"] = posicao
        dados["label"] = beats_to_label(posicao, anchor)
        dados["tempo_warning"] = tempo_warning(tempo, anchor)
        return dados

    @_travado
    def goto(self, label: str) -> dict[str, Any]:
        """Move o Live para o rotulo dado e devolve a posicao em batidas."""
        anchor = self._ancora_atual()
        beats = label_to_beats(label, anchor)
        # O Live recebe float32: o valor sobe para nunca cair antes do
        # comeco do frame.
        self._cliente().set_song_time(beats_float32(beats))
        return {"beats": beats, "label": label}

    @_travado
    def start(self, label: str | None) -> dict[str, Any]:
        """Troca a origem do beat zero; None volta para a do sidecar."""
        base = self._ancora_sidecar
        if base is None:
            raise SemSetCarregado(ERRO_SEM_SET)
        if label is None:
            self._ancora = base
            self._sobreposto = False
        else:
            amostras = int(label_to_samples(label, base.timecode_rate, base.sample_rate))
            self._ancora = replace(base, live_origin_samples=amostras)
            self._sobreposto = True
        return self._estado()

    @_travado
    def video_path(self) -> Path | None:
        """Caminho do video do set carregado."""
        return self._video[0] if self._video else None

    def _ancora_atual(self) -> Anchor:
        """Ancora efetiva do relogio; erro quando nenhum set foi carregado."""
        if self._ancora is None:
            raise SemSetCarregado(ERRO_SEM_SET)
        return self._ancora

    def _rotulo_de_origem(self, anchor: Anchor) -> str:
        """Rotulo do timecode no beat zero."""
        return samples_to_label(
            anchor.live_origin_samples, anchor.timecode_rate, anchor.sample_rate
        )

    def _chave_da_taxa(self, anchor: Anchor) -> str:
        """Chave da taxa de frames no RATES_LTC (25, 29.97, 30, ...).

        Uma taxa que o LTC nao conhece vem como NUM/DEN, com df no fim
        quando ela e de drop frame: a pagina funciona em qualquer taxa.
        """
        taxa = anchor.timecode_rate
        for chave, candidata in RATES_LTC.items():
            if candidata == taxa:
                return chave
        sufixo = "df" if taxa.drop_frame else ""
        return f"{taxa.num}/{taxa.den}{sufixo}"

    def _video_atual(self, anchor: Anchor) -> dict[str, Any] | None:
        """Dados do video para a pagina; None quando o set nao tem video.

        O clip de video nao se move quando a origem do TC e trocada: o
        inicio do video continua relativo a origem do sidecar.
        """
        if self._video is None:
            return None
        _caminho, inicio, offset = self._video
        origem = self._ancora_sidecar or anchor
        return {
            "url": "/api/timecode/video",
            "start_seconds": (inicio - origem.live_origin_samples) / origem.sample_rate,
            "source_offset_seconds": offset / origem.sample_rate,
        }


# A ponte viva do Studio: o web_app usa sempre esta instancia.
BRIDGE = TimecodeBridge()


def get_page(handler: Any) -> None:
    """Responde com a pagina do relogio de timecode."""
    corpo = load_timecode_page().encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.end_headers()
    handler.wfile.write(corpo)


def get_state(handler: Any) -> None:
    """Responde com o estado do relogio."""
    _rota(handler, BRIDGE.state)


def post_load(handler: Any, payload: dict[str, Any]) -> None:
    """Carrega o set informado e devolve o estado."""
    _rota(handler, lambda: BRIDGE.load(str(payload.get("als") or "")))


def post_goto(handler: Any, payload: dict[str, Any]) -> None:
    """Move o Live para o rotulo informado."""
    _rota(handler, lambda: BRIDGE.goto(str(payload.get("label") or "")))


def post_start(handler: Any, payload: dict[str, Any]) -> None:
    """Troca a origem do beat zero; um rotulo nulo repoe a do sidecar."""
    rotulo = payload.get("label")
    _rota(handler, lambda: BRIDGE.start(None if rotulo is None else str(rotulo)))


def get_video(handler: Any) -> None:
    """Serve o video do set, inteiro ou em partes (Range)."""
    caminho = BRIDGE.video_path()
    if caminho is None or not caminho.is_file():
        handler.send_error(404, "Not Found")
        return
    tamanho = caminho.stat().st_size
    pedido = handler.headers.get("Range")
    faixa = _faixa_de_bytes(pedido, tamanho) if pedido else (0, tamanho - 1)
    if faixa is None:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{tamanho}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    inicio, fim = faixa
    if pedido:
        # O navegador pede o resto em novas requisicoes: nunca mais que 4 MiB.
        fim = min(fim, inicio + LIMITE_RANGE - 1)
    total = fim - inicio + 1
    handler.send_response(206 if pedido else 200)
    handler.send_header("Content-Type", "video/mp4")
    handler.send_header("Accept-Ranges", "bytes")
    if pedido:
        handler.send_header("Content-Range", f"bytes {inicio}-{fim}/{tamanho}")
    handler.send_header("Content-Length", str(total))
    handler.end_headers()
    _escreve_blocos(handler, caminho, inicio, total)


def _faixa_de_bytes(pedido: str, tamanho: int) -> tuple[int, int] | None:
    """Le um intervalo de bytes unico; None quando o pedido nao e servivel."""
    texto = pedido.strip()
    if not texto.lower().startswith("bytes=") or "," in texto:
        return None
    inicio_texto, _, fim_texto = texto[len("bytes=") :].partition("-")
    if not inicio_texto.strip():
        return _ultimos_bytes(fim_texto, tamanho)
    if not inicio_texto.isdigit() or (fim_texto and not fim_texto.isdigit()):
        return None
    return _faixa_com_inicio(int(inicio_texto), fim_texto, tamanho)


def _faixa_com_inicio(inicio: int, fim_texto: str, tamanho: int) -> tuple[int, int] | None:
    """Intervalo de bytes com inicio e fim explicitos."""
    fim = min(int(fim_texto), tamanho - 1) if fim_texto else tamanho - 1
    if inicio >= tamanho or fim < inicio:
        return None
    return inicio, fim


def _ultimos_bytes(fim_texto: str, tamanho: int) -> tuple[int, int] | None:
    """Ultimos N bytes do arquivo, quando o Range traz so o sufixo."""
    if not fim_texto.isdigit():
        return None
    quantidade = int(fim_texto)
    if quantidade <= 0:
        return None
    return max(0, tamanho - quantidade), tamanho - 1


def _escreve_blocos(handler: Any, caminho: Path, inicio: int, total: int) -> None:
    """Manda o trecho do arquivo em blocos de 1 MiB."""
    with caminho.open("rb") as arquivo:
        arquivo.seek(inicio)
        for posicao in range(inicio, inicio + total, TAMANHO_CHUNK):
            bloco = arquivo.read(min(TAMANHO_CHUNK, inicio + total - posicao))
            if not bloco:
                break
            handler.wfile.write(bloco)


def _rota(handler: Any, acao: Any) -> None:
    """Executa a acao da rota e mapeia os erros de entrada para 400."""
    try:
        dados = acao()
    except ERROS_DE_ENTRADA as exc:
        handler._send_json({"ok": False, "error": str(exc)}, status=400)
        return
    handler._send_json({"ok": True, **dados})
