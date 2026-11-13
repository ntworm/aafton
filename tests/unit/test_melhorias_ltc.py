"""Testes das melhorias do LTC: origem negativa do Live e limite de 24 horas.

Regras verificadas aqui:

- origem do Live antes de 00:00:00:00: o LTC comeca no frame 0, o nome do
  arquivo, o BWF e os rotulos do relatorio usam 00:00:00:00, e o relatorio
  avisa LTC_START_CLAMPED e MTC_CHASE_ORIGIN_NEGATIVE sem nenhum rotulo
  negativo em nenhuma mensagem;
- write_ltc_wav recusa um inicio negativo antes de criar qualquer arquivo;
- LTC que passaria de 24:00:00:00: erro LTC_TRACK_SKIPPED_24H, sem track e
  sem WAV no modo permissivo, StrictModeError no modo estrito.
"""

from __future__ import annotations

import re

import pytest

from aafton.aaf_read import read_aaf
from aafton.ltc import write_ltc_wav
from aafton.ltc_track import add_ltc_track
from aafton.media_extract import parse_bext
from aafton.report import Report, StrictModeError
from aafton.timecode import TimecodeRate
from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_ltc import _decodificar, _ler_wav

SR = 48000
PRE_ROLL_SEGUNDOS = 10
TAXA = TimecodeRate(25, 1)

# Um rotulo SMPTE com sinal negativo: o '-' na frente de um rotulo.
ROTULO_NEGATIVO = re.compile(r"-\d{2}:\d{2}:\d{2}")


@pytest.fixture
def wav(tmp_path):
    """WAV de origem com 30 s de audio."""
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(SR * 30))
    return caminho


def _sessao(tmp_path, wav, inicio_samples, duracao_samples):
    """Sessao lida de um AAF com um unico clip na posicao pedida.

    O timecode de inicio do AAF e zero: a timeline lida fica nos samples
    absolutos pedidos, sem o deslocamento de uma hora do AAF da fabrica.
    """
    aaf = build_linked_aaf(
        tmp_path / "cena.aaf",
        [(inicio_samples, duracao_samples, 0)],
        wav,
        start_timecode_frames=0,
    )
    return read_aaf(aaf, Report(strict=False))


def _origem(session):
    """Origem do Live: dez segundos antes do primeiro clip, sem piso em zero."""
    inicio = min(clip.timeline_start_samples for clip in session.all_clips())
    return inicio - PRE_ROLL_SEGUNDOS * session.sample_rate


def _issue(report, codigo):
    """A issue do relatorio com o codigo pedido."""
    achadas = [issue for issue in report.issues if issue.code == codigo]
    assert achadas, f"o relatorio nao tem a issue {codigo}"
    return achadas[0]


def _sem_rotulo_negativo(report):
    """Nenhum rotulo negativo pode vazar para o relatorio."""
    for issue in report.issues:
        assert not ROTULO_NEGATIVO.search(issue.message), issue.message
        for valor in issue.context.values():
            if isinstance(valor, str):
                assert not ROTULO_NEGATIVO.search(valor), valor


def test_origem_negativa_comeca_o_ltc_no_frame_zero(tmp_path, wav):
    session = _sessao(tmp_path, wav, 5 * SR, 15 * SR)
    report = Report(strict=False)
    media = tmp_path / "midia"

    track = add_ltc_track(session, _origem(session), media, report)

    assert track is not None
    arquivo = media / "LTC_00-00-00-00_25.wav"
    assert arquivo.exists()

    bext = parse_bext(arquivo)
    assert bext is not None
    assert bext["time_reference"] == 0
    assert "00:00:00:00" in bext["description"]
    assert not ROTULO_NEGATIVO.search(bext["description"])

    assert _issue(report, "LTC_TRACK_WRITTEN").context["start"] == "00:00:00:00"
    assert _issue(report, "LTC_START_CLAMPED").severity == "warning"
    assert _issue(report, "MTC_CHASE_ORIGIN_NEGATIVE").severity == "warning"
    assert not [issue for issue in report.issues if issue.code == "MTC_CHASE_SETTINGS"]

    _sem_rotulo_negativo(report)

    taxa, amostras = _ler_wav(arquivo)
    assert taxa == SR
    # 750 frames: do frame zero ate 10 s depois do ultimo clip.
    assert len(amostras) == 750 * (SR // 25)
    rotulos = [rotulo for _, rotulo in _decodificar(amostras, session.timecode_rate, taxa)]
    assert rotulos[-1] == "00:00:29:24"


def test_write_ltc_wav_recusa_inicio_negativo(tmp_path):
    caminho = tmp_path / "ltc.wav"

    with pytest.raises(ValueError, match="cannot start before 00:00:00:00"):
        write_ltc_wav(caminho, "-00:00:01:00", TAXA, 50, SR, -18.0)

    assert not caminho.exists()


def test_ltc_que_passa_de_24h_e_recusado_no_modo_permissivo(tmp_path, wav):
    inicio = (23 * 3600 + 59 * 60 + 40) * SR
    session = _sessao(tmp_path, wav, inicio, 15 * SR)
    report = Report(strict=False)
    media = tmp_path / "midia"

    track = add_ltc_track(session, _origem(session), media, report)

    assert track is None
    assert not list(media.glob("*.wav"))
    assert not [t for t in session.tracks if t.name == "LTC"]
    assert not [m for m in session.media if m.filename.startswith("LTC_")]

    erro = _issue(report, "LTC_TRACK_SKIPPED_24H")
    assert erro.severity == "error"
    assert erro.context["start"] == "23:59:30:01"
    assert erro.context["end"] == "24:00:05:00"
    _sem_rotulo_negativo(report)


def test_ltc_que_passa_de_24h_interrompe_o_modo_estrito(tmp_path, wav):
    inicio = (23 * 3600 + 59 * 60 + 40) * SR
    session = _sessao(tmp_path, wav, inicio, 15 * SR)
    report = Report(strict=True)
    media = tmp_path / "midia"

    with pytest.raises(StrictModeError):
        add_ltc_track(session, _origem(session), media, report)

    assert not list(media.glob("*.wav"))
    assert not [t for t in session.tracks if t.name == "LTC"]
