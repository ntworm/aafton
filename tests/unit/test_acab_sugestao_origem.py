"""Sugestao de origem do Live quando o primeiro clip comeca no meio de um frame.

O Live so aceita origens de frame inteiro: o limite de cima do intervalo
sugerido e o frame do primeiro clip arredondado para baixo, e o de baixo e o
frame zero. Em drop frame, os dois limites usam ';'.
"""

from __future__ import annotations

from aafton.aaf_read import read_aaf
from aafton.ltc_track import add_ltc_track
from aafton.report import Report
from tests.aaf_factory import build_linked_aaf, wav_bytes
from tests.unit.test_acomp_ltc_textos import SR, _issue, _sessao

# A 25 fps um frame tem 1920 samples: 0,75 frame sao 1440 samples.
QUARTO_DE_FRAME = 1440


def _wav(tmp_path):
    """WAV de origem com 30 s de audio."""
    caminho = tmp_path / "cena.wav"
    caminho.write_bytes(wav_bytes(SR * 30))
    return caminho


def _sessao_df(tmp_path, wav, inicio_samples, duracao_samples):
    """Sessao lida de um AAF com timecode 29.97 em drop frame."""
    aaf = build_linked_aaf(
        tmp_path / "cena_df.aaf",
        [(inicio_samples, duracao_samples, 0)],
        wav,
        start_timecode_frames=0,
        fps=30,
        drop=True,
    )
    return read_aaf(aaf, Report(strict=False))


def test_o_primeiro_clip_no_meio_do_frame_da_o_frame_de_baixo(tmp_path):
    """Um clip que comeca no meio de um frame tem o frame de baixo como limite."""
    wav = _wav(tmp_path)
    sessao = _sessao(tmp_path, wav, QUARTO_DE_FRAME, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(sessao, -10 * SR, tmp_path / "midia", relatorio)

    aviso = _issue(relatorio, "MTC_CHASE_ORIGIN_NEGATIVE")
    assert "and 00:00:00:00 (the first clip)" in aviso.message
    assert "00:00:00:01" not in aviso.message


def test_em_drop_frame_os_dois_limites_usam_o_separador_do_drop_frame(tmp_path):
    """Em drop frame, o intervalo da origem usa ';' nos dois limites."""
    wav = _wav(tmp_path)
    sessao = _sessao_df(tmp_path, wav, 5 * SR, 15 * SR)
    relatorio = Report(strict=False)

    add_ltc_track(sessao, -10 * SR, tmp_path / "midia", relatorio)

    aviso = _issue(relatorio, "MTC_CHASE_ORIGIN_NEGATIVE")
    assert "between 00:00:00;00 and" in aviso.message
    assert "between 00:00:00:00 and" not in aviso.message
