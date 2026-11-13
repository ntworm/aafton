"""Colapso do par estereo que vem de um arquivo estereo so.

O par so vira um clip quando os dois canais sao o mesmo audio no mesmo
lugar, com o mesmo ganho, mudo e fades: o clip do canal direito sai e um
ganho proprio dele se perderia.
"""

from __future__ import annotations

from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.stereo_fonte_unica import colapsar_fonte_unica
from aafton.timecode import TimecodeRate


def _sessao(
    ganho_direito: float = 1.0, mudo_direito: bool = False, canais: tuple = (1, 2)
) -> SessionIR:
    asset = MediaAsset(
        id="m0", filename="estereo.wav", channels=2, sample_rate=48000, resolved_path="C:/a.wav"
    )
    clips = [
        ClipIR(
            id=f"t0c0_ch{canal}",
            track_id="t0",
            channel_index=canal,
            event_index=0,
            name="estereo",
            media_id="m0",
            timeline_start_samples=0,
            timeline_end_samples=48000,
            source_offset_samples=0,
            gain_linear=ganho_direito if canal == 1 else 1.0,
            muted=mudo_direito if canal == 1 else False,
            source_channel=canais[canal],
        )
        for canal in (0, 1)
    ]
    track = TrackIR(id="t0", source_index=0, name="A1", kind="audio", channel_count=2)
    track.clips = clips
    return SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[asset],
    )


def test_par_igual_vira_um_clip():
    sessao = _sessao()
    assert colapsar_fonte_unica(sessao, Report(strict=False)) == 1
    assert len(sessao.tracks[0].clips) == 1


def test_par_com_ganho_diferente_nao_colapsa():
    sessao = _sessao(ganho_direito=0.5)
    assert colapsar_fonte_unica(sessao, Report(strict=False)) == 0
    assert len(sessao.tracks[0].clips) == 2


def test_par_com_canal_mudo_nao_colapsa():
    sessao = _sessao(mudo_direito=True)
    assert colapsar_fonte_unica(sessao, Report(strict=False)) == 0
    assert len(sessao.tracks[0].clips) == 2


def test_par_com_canais_trocados_nao_colapsa():
    sessao = _sessao(canais=(2, 1))
    assert colapsar_fonte_unica(sessao, Report(strict=False)) == 0


def test_par_sem_canal_declarado_nao_colapsa():
    sessao = _sessao(canais=(None, None))
    assert colapsar_fonte_unica(sessao, Report(strict=False)) == 0
