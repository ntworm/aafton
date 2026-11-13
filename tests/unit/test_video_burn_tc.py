"""Testes do proxy de video com timecode queimado.

O modulo src/aafton/video_burn.py e a opcao --video-no-burn do import
ainda nao existem: todo teste deste arquivo falha hoje, e a falha tem que vir
da feature que falta, nunca de um defeito do proprio teste.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aafton.cli import main
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes

TEMPLATE = Path("templates/live12.als")


def _cria_video(caminho: Path, *, com_audio: bool) -> Path:
    """Monta um video de teste de 2 s, 320x240 e 25 fps."""
    args = ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2"]
    if com_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if com_audio:
        args += ["-c:a", "aac", "-shortest"]
    args.append(str(caminho))
    res = subprocess.run(args, check=False, capture_output=True, text=True)
    if res.returncode != 0:
        raise AssertionError(f"ffmpeg falhou ao criar o video: {res.stderr[-2000:]}")
    return caminho


def _ffprobe(caminho: Path) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams"]
    res = subprocess.run([*cmd, str(caminho)], check=True, capture_output=True, text=True)
    return json.loads(res.stdout)


def _frame_cinza(caminho: Path, destino: Path) -> bytes:
    """Extrai o frame 0 do video como bytes cinza crus."""
    cmd = ["ffmpeg", "-y", "-i", str(caminho), "-frames:v", "1", "-f", "rawvideo"]
    cmd += ["-pix_fmt", "gray"]
    subprocess.run([*cmd, str(destino)], check=True, capture_output=True)
    return destino.read_bytes()


def _aaf_de_audio(tmp_path: Path) -> Path:
    """AAF de audio apenas, com os mesmos dados do fixture de test_cli.py."""
    wav = tmp_path / "cena12_tk3.wav"
    wav.write_bytes(wav_bytes(48000 * 10))
    return build_linked_aaf(tmp_path / "cena12.aaf", [(0, 96000, 24000), (192000, 96000, 0)], wav)


def _argv_import(aaf: Path, als: Path, video: Path, *extra: str) -> list[str]:
    """Argv do import da CLI com --video e --video-start."""
    base = ["import", str(aaf), str(als), "--template", str(TEMPLATE), "--media", "link"]
    return [*base, "--video", str(video), "--video-start", "01:00:00:00", *extra]


@pytest.fixture
def importacao_queimada(tmp_path):
    """Import da CLI com --video, que deve queimar o timecode no proxy."""
    aaf = _aaf_de_audio(tmp_path)
    video = _cria_video(tmp_path / "video.mp4", com_audio=True)
    als = tmp_path / "saida.als"
    codigo = main(_argv_import(aaf, als, video))
    return aaf, als, codigo, tmp_path / "saida.aafton.json"


def test_drawtext_filter_em_25fps_escapa_os_dois_pontos():
    from aafton.video_burn import drawtext_filter

    filtro = drawtext_filter(start_label="01:00:00:00", rate=TimecodeRate(25, 1), fontfile=None)
    assert "drawtext=" in filtro
    assert "timecode='01\\:00\\:00\\:00'" in filtro
    assert "rate=25/1" in filtro
    assert "box=1" in filtro


def test_drawtext_filter_em_29_97_drop_frame_usa_ponto_e_virgula():
    from aafton.video_burn import drawtext_filter

    rate = TimecodeRate(30000, 1001, drop_frame=True)
    filtro = drawtext_filter(start_label="01:00:00;00", rate=rate, fontfile=None)
    assert "timecode='01\\:00\\:00\\;00'" in filtro
    assert "rate=30000/1001" in filtro


def test_drawtext_filter_com_fontfile_escapa_os_dois_pontos_da_unidade():
    from aafton.video_burn import drawtext_filter

    fonte = "C:/Windows/Fonts/consola.ttf"
    filtro = drawtext_filter(start_label="01:00:00:00", rate=TimecodeRate(25, 1), fontfile=fonte)
    assert "fontfile='C\\:/Windows/Fonts/consola.ttf'" in filtro
    assert "font=monospace" not in filtro


def test_drawtext_filter_sem_fontfile_usa_fonte_monospace():
    from aafton.video_burn import drawtext_filter

    filtro = drawtext_filter(start_label="01:00:00:00", rate=TimecodeRate(25, 1), fontfile=None)
    assert "font=monospace" in filtro
    assert "fontfile=" not in filtro


def test_burn_command_sem_audio_adiciona_audio_silencioso_e_shortest():
    # Assinatura congelada: video_path, output_path, start_label, rate, has_audio, fontfile.
    from aafton.video_burn import burn_command, drawtext_filter

    rate = TimecodeRate(25, 1)
    entrada, saida = Path("entrada.mp4"), Path("saida.mp4")
    argv = burn_command(entrada, saida, "01:00:00:00", rate, has_audio=False, fontfile=None)
    assert argv[0] == "ffmpeg"
    assert argv[1] == "-y"
    assert argv[2:4] == ["-i", "entrada.mp4"]
    pos = argv.index("anullsrc=channel_layout=stereo:sample_rate=48000")
    assert argv[pos - 3 : pos - 1] == ["-f", "lavfi"]
    assert argv[pos - 1] == "-i"
    assert "-shortest" in argv
    i = argv.index("-vf")
    assert argv[i + 1] == drawtext_filter(start_label="01:00:00:00", rate=rate, fontfile=None)
    assert argv[argv.index("-c:v") + 1] == "libx264"
    assert argv[argv.index("-pix_fmt") + 1] == "yuv420p"
    assert argv[argv.index("-c:a") + 1] == "aac"
    assert argv[-1] == "saida.mp4"


def test_burn_command_com_audio_nao_adiciona_audio_silencioso():
    from aafton.video_burn import burn_command

    entrada, saida = Path("entrada.mp4"), Path("saida.mp4")
    argv = burn_command(entrada, saida, "01:00:00:00", TimecodeRate(25, 1), True, None)
    assert not any("anullsrc" in parte for parte in argv)
    assert "-shortest" not in argv
    assert argv[-1] == "saida.mp4"


def test_burn_timecode_proxy_sem_ffmpeg_avisa_e_devolve_none(tmp_path, monkeypatch):
    from aafton import video_burn

    monkeypatch.setattr("aafton.video_burn.shutil.which", lambda cmd: None)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video de mentira")
    report = Report(strict=False)
    resultado = video_burn.burn_timecode_proxy(video, "01:00:00:00", TimecodeRate(25, 1), report)
    assert resultado is None
    assert any(issue.code == "VIDEO_BURN_UNAVAILABLE" for issue in report.issues)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
def test_ffmpeg_queima_timecode_e_gera_proxy_com_audio(tmp_path):
    from aafton.video_burn import burn_timecode_proxy

    video = _cria_video(tmp_path / "video.mp4", com_audio=False)
    report = Report(strict=False)
    saida = burn_timecode_proxy(video, "01:00:00:00", TimecodeRate(25, 1), report)
    assert saida is not None
    assert saida.name == "video_LiveProxy_TC.mp4"
    assert saida.exists()

    dados = _ffprobe(saida)
    streams = dados["streams"]
    stream_video = next(s for s in streams if s["codec_type"] == "video")
    assert stream_video["r_frame_rate"] == "25/1"
    assert abs(float(dados["format"]["duration"]) - 2.0) <= 0.1
    assert any(s["codec_type"] == "audio" for s in streams)

    issue = next(i for i in report.issues if i.code == "VIDEO_TC_BURNED")
    assert issue.context["start"] == "01:00:00:00"
    assert Path(issue.context["path"]).name == "video_LiveProxy_TC.mp4"
    assert "rate" in issue.context

    fonte = _frame_cinza(video, tmp_path / "fonte.gray")
    queimado = _frame_cinza(saida, tmp_path / "queimado.gray")
    assert len(fonte) == len(queimado)
    corte = len(fonte) * 4 // 5
    assert fonte[corte:] != queimado[corte:]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
def test_process_video_com_burn_aponta_para_o_proxy_queimado(tmp_path):
    from aafton.aaf_read import read_aaf
    from aafton.video import process_video

    aaf = _aaf_de_audio(tmp_path)
    video = _cria_video(tmp_path / "video.mp4", com_audio=True)
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    process_video(session, aaf, str(video), "01:00:00:00", report, burn_timecode=True)

    track_video = next(t for t in session.tracks if t.is_video)
    asset = session.media_by_id(track_video.clips[0].media_id)
    assert asset is not None
    assert asset.resolved_path is not None
    assert asset.resolved_path.endswith("_LiveProxy_TC.mp4")
    assert any(issue.code == "VIDEO_TC_BURNED" for issue in report.issues)


def test_process_video_sem_burn_nao_cria_arquivo_tc(tmp_path):
    from aafton.aaf_read import read_aaf
    from aafton.video import process_video

    aaf = _aaf_de_audio(tmp_path)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video de mentira")
    report = Report(strict=False)
    session = read_aaf(aaf, report)
    process_video(session, aaf, str(video), "01:00:00:00", report, burn_timecode=False)

    assert list(tmp_path.glob("*_LiveProxy_TC.mp4")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
@pytest.mark.skipif(not TEMPLATE.exists(), reason="template live12.als nao existe")
def test_cli_import_com_video_queima_timecode_e_registra_no_sidecar(importacao_queimada):
    aaf, als, codigo, sidecar = importacao_queimada
    assert codigo == 0
    dados = json.loads(sidecar.read_text(encoding="utf-8"))
    video_path = dados["video_path"]
    assert video_path is not None
    assert video_path.endswith("_LiveProxy_TC.mp4")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
@pytest.mark.skipif(not TEMPLATE.exists(), reason="template live12.als nao existe")
def test_cli_import_com_video_no_burn_nao_cria_arquivo_tc(tmp_path):
    aaf = _aaf_de_audio(tmp_path)
    video = _cria_video(tmp_path / "video.mp4", com_audio=True)
    als = tmp_path / "saida.als"
    codigo = main(_argv_import(aaf, als, video, "--video-no-burn"))
    assert codigo == 0
    assert list(tmp_path.glob("*_LiveProxy_TC.mp4")) == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg nao esta instalado")
@pytest.mark.skipif(not TEMPLATE.exists(), reason="template live12.als nao existe")
def test_cli_verify_aceita_o_par_gerado_com_video_queimado(importacao_queimada):
    aaf, als, codigo, sidecar = importacao_queimada
    assert codigo == 0
    dados = json.loads(sidecar.read_text(encoding="utf-8"))
    video_path = dados["video_path"]
    assert video_path is not None
    assert video_path.endswith("_LiveProxy_TC.mp4")
    assert main(["verify", str(aaf), str(als)]) == 0
