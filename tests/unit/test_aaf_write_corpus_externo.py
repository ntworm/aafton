"""Regressoes do corpus externo: timecode em drop frame e midia fora de WAV/AIFF.

O corpus externo do LibAAF mostrou dois defeitos no export .als -> AAF:

- o inicio do timecode em drop frame saia um frame antes, e todo clip voltava
  um frame (1601 samples em 29.97) na ida e volta;
- midia que nao e WAV nem AIFF derrubava o export com traceback do pyaaf2.

Os AAFs aqui sao sinteticos, construidos com a fabrica de testes. Nenhum
teste usa rede.
"""

import os
import subprocess
import sys
from pathlib import Path

import aaf2
import pytest

from aafton.aaf_read import read_aaf
from aafton.als_read import read_session
from aafton.cli import main, write_destination
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate, frames_to_label
from tests.aaf_factory import build_linked_aaf, wav_bytes

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "live12.als"
SR = 48000
# 01:00:00;00 na taxa nominal de cada variante com drop frame.
INICIO_DROP_FRAME = ((30, 107892), (60, 215784), (120, 431568))
# 107892 frames de 29.97 valem 172799827,2 samples em 48 kHz.
INICIO_EM_SAMPLES = 172799827

pytestmark = pytest.mark.skipif(not TEMPLATE.exists(), reason="template do Live ausente")


def _codigo_cli(argumentos):
    """Codigo de saida do CLI, aceitando o SystemExit que o argparse levanta."""
    try:
        return main(argumentos)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1


def _importar(aaf, saida):
    """Importa o AAF para .als com o template do projeto."""
    return _codigo_cli(
        ["import", str(aaf), str(saida), "--template", str(TEMPLATE), "--media", "link"]
    )


def _exportar(als, saida):
    """Exporta o .als para AAF em modo estrito."""
    return _codigo_cli(["export", str(als), str(saida)])


def _wav(tmp_path):
    """WAV externo de dois minutos, ligado pelos clips do AAF sintetico."""
    wav = tmp_path / "cena12.wav"
    wav.write_bytes(wav_bytes(SR * 120))
    return wav


def _aaf_de_origem(tmp_path, *, fps=25, drop=False, frames_inicio=90000):
    """AAF sintetico com dois clips linkados e um timecode de inicio."""
    wav = _wav(tmp_path)
    return build_linked_aaf(
        tmp_path / "origem.aaf",
        [(0, 96000, 24000), (192000, 96000, 0)],
        wav,
        fps=fps,
        drop=drop,
        start_timecode_frames=frames_inicio,
    )


def _timecode_do_aaf(aaf):
    """Segmento de timecode da composition do AAF."""
    with aaf2.open(str(aaf), "r") as f:
        comp = next(m for m in f.content.mobs if type(m).__name__ == "CompositionMob")
        return next(s for s in comp.slots if s.name == "TC").segment


def _posicoes(sessao):
    """Posicao de cada clip da sessao, em samples."""
    return [
        (clip.timeline_start_samples, clip.timeline_end_samples)
        for track in sessao.tracks
        for clip in track.clips
    ]


@pytest.mark.parametrize("fps,frames_inicio", INICIO_DROP_FRAME)
def test_drop_frame_volta_ao_inicio_e_aos_clips(tmp_path, fps, frames_inicio):
    """O inicio em drop frame nao pode sair um frame antes do AAF de origem."""
    taxa = TimecodeRate(fps * 1000, 1001, True)
    assert frames_to_label(frames_inicio, taxa) == "01:00:00;00"

    aaf = _aaf_de_origem(tmp_path, fps=fps, drop=True, frames_inicio=frames_inicio)
    als = tmp_path / "importado.als"
    assert _importar(aaf, als) == 0

    saida = tmp_path / "saida.aaf"
    assert _exportar(als, saida) == 0

    origem = read_aaf(aaf, Report(strict=False))
    destino = read_aaf(saida, Report(strict=False))

    timecode = _timecode_do_aaf(saida)
    assert timecode.start == frames_inicio
    assert timecode.drop is True
    assert timecode.fps == fps

    assert origem.start_timecode_samples == INICIO_EM_SAMPLES
    assert destino.start_timecode_samples == INICIO_EM_SAMPLES
    assert _posicoes(origem)[0][0] == INICIO_EM_SAMPLES
    assert _posicoes(destino) == _posicoes(origem)


def test_export_com_mp3_para_sem_traceback(tmp_path, capsys):
    """Midia que nao e WAV nem AIFF precisa falhar no relatorio, sem traceback."""
    aaf = _aaf_de_origem(tmp_path)
    als = tmp_path / "importado.als"
    assert _importar(aaf, als) == 0

    # O clip do set passa a apontar para um arquivo com bytes de MP3.
    (tmp_path / "cena12.wav").write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)

    saida = tmp_path / "saida.aaf"
    codigo = _exportar(als, saida)
    capturado = capsys.readouterr()

    assert codigo != 0
    assert "Traceback" not in capturado.err
    assert "WRITE_MEDIA_FORMAT_UNSUPPORTED" in capturado.err
    assert not saida.exists()


def test_export_com_mp3_registra_o_erro_no_relatorio(tmp_path):
    """O erro de formato entra no relatorio antes de qualquer descritor."""
    aaf = _aaf_de_origem(tmp_path)
    als = tmp_path / "importado.als"
    assert _importar(aaf, als) == 0
    (tmp_path / "cena12.wav").write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64)

    sessao = read_session(als, Report(strict=False))
    report = Report(strict=True)
    with pytest.raises(StrictModeError):
        write_destination(sessao, tmp_path / "saida.aaf", report)

    erros = [issue for issue in report.issues if issue.code == "WRITE_MEDIA_FORMAT_UNSUPPORTED"]
    assert len(erros) == 1
    assert "cena12.wav" in erros[0].message
    assert "WAV" in erros[0].message
    assert "AIFF" in erros[0].message
    assert "converted" in erros[0].message


def test_export_com_mp3_no_terminal_para_sem_traceback(tmp_path):
    """O comando do terminal sai com codigo de erro e sem traceback."""
    aaf = _aaf_de_origem(tmp_path)
    als = tmp_path / "importado.als"
    assert _importar(aaf, als) == 0
    (tmp_path / "cena12.wav").write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)

    resultado = subprocess.run(
        [sys.executable, "-m", "aafton.cli", "export", str(als), str(tmp_path / "saida.aaf")],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )

    assert resultado.returncode != 0
    assert "Traceback" not in resultado.stderr
    assert "WRITE_MEDIA_FORMAT_UNSUPPORTED" in resultado.stderr
