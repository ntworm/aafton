"""Testes da pagina de timecode do Studio (fase 2 do relogio TC).

O modulo src/aafton/studio/timecode.py, a pagina /timecode e as rotas
/api/timecode/* ainda nao existem: todo teste deste arquivo falha hoje, e a
falha tem que vir da feature que falta, nunca de um defeito do proprio teste.

As regras congeladas: a pagina traz tc-clock, tc-origin, tc-goto, tc-status e
tc-video; o estado traz connected, beats, label, origin_label, rate, bpm e
tempo_warning; sem Live a resposta vem em menos de 1 s; o video responde 206.
"""

import json
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import aafton.web_app as web
from aafton.als_read import read_session
from aafton.cli import main
from aafton.live_clock import beats_to_label, label_to_beats
from aafton.report import Report
from aafton.sidecar import read_anchor, sidecar_path_for
from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_live_osc import SET_SONG_TIME, ServidorFalso

TEMPLATE = Path("templates/live12.als")
# O bpm do set e o mesmo do Live falso (120), senao o aviso de 119 BPM nao diria.
BPM = 120.0
IDS_DA_PAGINA = ("tc-clock", "tc-origin", "tc-goto", "tc-status", "tc-video")
PADRAO_ID = re.compile(r"id=[\"']?([\w-]+)")
FALTA_MODULO = "src/aafton/studio/timecode.py ainda nao existe: falta a fase 2"


def _sidecar(als: Path) -> dict:
    return json.loads(sidecar_path_for(als).read_text(encoding="utf-8"))


def _json(corpo: bytes) -> dict:
    try:
        return json.loads(corpo.decode("utf-8"))
    except ValueError:
        return {}


def _chama(url: str, caminho: str, payload=None, headers=None) -> tuple[int, dict, bytes]:
    """Chama a rota (GET ou POST JSON) e devolve status, cabecalhos e corpo."""
    dados = json.dumps(payload).encode("utf-8") if payload is not None else None
    envio = dict(headers or {})
    if dados is not None:
        envio["Content-Type"] = "application/json"
    request = urllib.request.Request(url + caminho, data=dados, headers=envio)
    try:
        with urllib.request.urlopen(request) as resposta:
            cabecalhos = {k.lower(): v for k, v in resposta.headers.items()}
            return resposta.status, cabecalhos, resposta.read()
    except urllib.error.HTTPError as erro:
        cabecalhos = {k.lower(): v for k, v in erro.headers.items()}
        return erro.code, cabecalhos, erro.read()


def _espera_song_time(servidor, esperado: float) -> bool:
    """Espera o servidor falso receber a posicao que o Studio mandou."""
    for _ in range(150):
        if abs(servidor.song_time - esperado) <= 1e-3:
            return True
        time.sleep(0.02)
    return False


def _argv_import(aaf: Path, als: Path, *extra: str) -> list[str]:
    """Argv do import da CLI, com o bpm do set igual ao do Live falso."""
    base = ["import", str(aaf), str(als), "--template", str(TEMPLATE), "--media", "link"]
    return [*base, "--bpm", str(BPM), *extra]


@pytest.fixture
def url():
    """O Studio rodando numa porta livre, como nos testes da Web UI."""
    server = web.run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def ponte():
    """A ponte para o Live; None enquanto o modulo da fase 2 nao existir."""
    try:
        from aafton.studio.timecode import BRIDGE
    except ModuleNotFoundError:
        yield None
        return
    BRIDGE.reset()
    yield BRIDGE
    BRIDGE.reset()


@pytest.fixture
def live(ponte):
    """O servidor OSC falso, com a ponte apontada para ele."""
    servidor_falso = ServidorFalso()
    if ponte is not None:
        ponte.configure(send_port=servidor_falso.porta, reply_port=0, timeout=0.2)
        ponte.reset()
    yield servidor_falso
    servidor_falso.close()


@pytest.fixture
def aaf(tmp_path):
    """AAF de audio linkado, como no fixture do test_cli.py."""
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(48000 * 10))
    return build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000), (192000, 96000, 0)], wav)


@pytest.fixture
def set_importado(aaf, tmp_path):
    """O set importado, com o sidecar ao lado."""
    if not TEMPLATE.exists():
        pytest.skip("Portao B: template ainda nao autorado")
    als = tmp_path / "saida.als"
    codigo = main(_argv_import(aaf, als))
    assert codigo == 0
    return als


@pytest.fixture
def set_com_video(aaf, tmp_path):
    """O mesmo set, com a track de video e o sidecar apontando para o mp4."""
    if not TEMPLATE.exists():
        pytest.skip("Portao B: template ainda nao autorado")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg nao esta instalado")
    video = tmp_path / "video.mp4"
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
    args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)]
    res = subprocess.run(args, check=False, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr[-2000:]
    als = tmp_path / "saida_video.als"
    extras = ["--video", str(video), "--video-start", "01:00:00:00", "--video-no-burn"]
    codigo = main(_argv_import(aaf, als, *extras))
    assert codigo == 0
    return als


def test_pagina_timecode_tem_os_cinco_elementos(url):
    """A pagina /timecode e HTML e traz os cinco elementos do relogio."""
    status, headers, corpo = _chama(url, "/timecode")
    assert status == 200
    assert (headers.get("content-type") or "").startswith("text/html")
    ids = set(PADRAO_ID.findall(corpo.decode("utf-8")))
    for alvo in IDS_DA_PAGINA:
        assert alvo in ids, f"faltou o elemento {alvo}"


def test_load_e_state_descrevem_o_live_conectado(url, live, set_importado, ponte):
    """Depois do load, o estado traz a posicao e o tempo do Live falso."""
    assert ponte is not None, FALTA_MODULO
    status, _headers, _corpo = _chama(url, "/api/timecode/load", {"als": str(set_importado)})
    assert status == 200
    status, _headers, corpo = _chama(url, "/api/timecode/state")
    estado = _json(corpo)
    ancora = read_anchor(set_importado)
    assert estado["connected"] is True
    assert estado["playing"] is True
    assert estado["beats"] == pytest.approx(4.0)
    assert estado["label"] == beats_to_label(4.0, ancora)
    assert estado["origin_label"] == _sidecar(set_importado)["live_origin_timecode"]
    assert estado["origin_from_sidecar"] is True
    assert estado["rate"] == "25"
    assert estado["bpm"] == pytest.approx(ancora.bpm)
    assert estado["tempo_warning"] is None
    assert estado["video"] is None


def test_tempo_divergente_do_live_gera_aviso(url, live, set_importado, ponte):
    """Com 119 BPM no Live e 120 no sidecar, o estado avisa a divergencia."""
    assert ponte is not None, FALTA_MODULO
    live.tempo = 119.0
    _chama(url, "/api/timecode/load", {"als": str(set_importado)})
    status, _headers, corpo = _chama(url, "/api/timecode/state")
    assert status == 200
    assert isinstance(_json(corpo)["tempo_warning"], str)


def test_sem_live_responde_rapido_com_erro(url, set_importado, ponte):
    """Com um socket mudo, o estado avisa que o Live nao respondeu."""
    assert ponte is not None, FALTA_MODULO
    _chama(url, "/api/timecode/load", {"als": str(set_importado)})
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as mudo:
        mudo.bind(("127.0.0.1", 0))
        porta = mudo.getsockname()[1]
        ponte.configure(send_port=porta, reply_port=0, timeout=0.2)
        inicio = time.monotonic()
        status, _headers, corpo = _chama(url, "/api/timecode/state")
        duracao = time.monotonic() - inicio
    assert status == 200
    assert duracao < 1.0
    estado = _json(corpo)
    assert estado["connected"] is False
    assert estado["error"] is not None
    assert estado["beats"] is None
    assert estado["label"] is None


def test_goto_manda_a_posicao_para_o_live_e_recusa_rotulo_ruim(url, live, set_importado, ponte):
    """O goto vira o rotulo em batidas, manda para o Live e recusa o que nao e TC."""
    assert ponte is not None, FALTA_MODULO
    _chama(url, "/api/timecode/load", {"als": str(set_importado)})
    ancora = read_anchor(set_importado)
    esperado = label_to_beats("01:00:10:00", ancora)
    status, _headers, corpo = _chama(url, "/api/timecode/goto", {"label": "01:00:10:00"})
    dados = _json(corpo)
    assert status == 200
    assert dados["ok"] is True
    assert dados["beats"] == pytest.approx(esperado, abs=1e-3)
    assert _espera_song_time(live, esperado)
    assert SET_SONG_TIME in live.recebidos
    status, _headers, corpo = _chama(url, "/api/timecode/goto", {"label": "nao-e-timecode"})
    assert status == 400
    assert _json(corpo)["ok"] is False


def test_start_sobrepoe_a_origem_sem_mexer_no_sidecar(url, live, set_importado, ponte):
    """O start troca a origem do beat zero em memoria e volta ao sidecar."""
    assert ponte is not None, FALTA_MODULO
    _chama(url, "/api/timecode/load", {"als": str(set_importado)})
    ancora = read_anchor(set_importado)
    lado = _sidecar(set_importado)
    status, _headers, corpo = _chama(url, "/api/timecode/start", {"label": "02:00:00:00"})
    estado = _json(corpo)
    assert status == 200
    assert estado["origin_label"] == "02:00:00:00"
    assert estado["origin_from_sidecar"] is False
    assert _sidecar(set_importado) == lado
    esperado = 10 * ancora.bpm / 60
    status, _headers, _corpo = _chama(url, "/api/timecode/goto", {"label": "02:00:10:00"})
    assert status == 200
    assert _espera_song_time(live, esperado)
    status, _headers, corpo = _chama(url, "/api/timecode/start", {"label": None})
    assert status == 200
    estado = _json(corpo)
    assert estado["origin_from_sidecar"] is True
    assert estado["origin_label"] == lado["live_origin_timecode"]


def test_antes_do_load_o_estado_e_o_goto_respondem_400(url, ponte):
    """Sem set carregado, o Studio recusa o estado e o goto."""
    assert ponte is not None, FALTA_MODULO
    status, _headers, corpo = _chama(url, "/api/timecode/state")
    assert status == 400
    assert _json(corpo)["ok"] is False
    status, _headers, corpo = _chama(url, "/api/timecode/goto", {"label": "01:00:00:00"})
    assert status == 400
    assert _json(corpo)["ok"] is False


def test_video_e_servido_com_e_sem_range(url, live, set_com_video, ponte):
    """O video do set e servido em partes (206) ou inteiro (200)."""
    assert ponte is not None, FALTA_MODULO
    _chama(url, "/api/timecode/load", {"als": str(set_com_video)})
    ancora = read_anchor(set_com_video)
    sessao = read_session(set_com_video, Report(strict=False))
    clips_video = [c for t in sessao.tracks if t.is_video for c in t.clips]
    assert clips_video, "o set importado nao tem clip de video"
    clip = clips_video[0]
    esperado = (clip.timeline_start_samples - ancora.live_origin_samples) / ancora.sample_rate
    status, _headers, corpo = _chama(url, "/api/timecode/state")
    estado = _json(corpo)
    assert status == 200
    assert estado["video"]["url"] == "/api/timecode/video"
    assert estado["video"]["start_seconds"] == pytest.approx(esperado, abs=1e-6)
    arquivo = Path(_sidecar(set_com_video)["video_path"])
    dados = arquivo.read_bytes()
    status, headers, corpo = _chama(url, "/api/timecode/video", headers={"Range": "bytes=0-99"})
    assert status == 206
    assert headers.get("content-range") == f"bytes 0-99/{len(dados)}"
    assert len(corpo) == 100
    assert corpo == dados[:100]
    status, _headers, corpo = _chama(url, "/api/timecode/video")
    assert status == 200
    assert corpo == dados
    ponte.reset()
    status, _headers, _corpo = _chama(url, "/api/timecode/video")
    assert status == 404
