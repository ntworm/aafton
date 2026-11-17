"""Crossfade absorvido em track estereo com dois canais do AAF."""

from __future__ import annotations

import copy
from pathlib import Path

import aafton.verify
from aafton.aaf_write import write_aaf
from aafton.als_ancora import start_padrao
from aafton.als_crossfade_ler import crossfades_em_sobreposicao
from aafton.als_write import WriteOptions, write_session
from aafton.bundled import default_template
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.pipeline import export_session
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.unit.test_als_sem_sidecar import clip as clip_als, escrever_wav, montar_set

SR, XF, OFF, T0 = 48000, 12000, 24000, 3600 * 48000


def _c(eid: int, ch: int, ini: int, fim: int, off: int, **kw) -> ClipIR:
    """Clip de canal unico para montar track estereo de teste."""
    return ClipIR(
        id=f"c{eid}_ch{ch}",
        track_id="t0",
        channel_index=ch,
        event_index=eid,
        name="st",
        media_id="m1",
        timeline_start_samples=ini,
        timeline_end_samples=fim,
        source_offset_samples=off,
        **kw,
    )


def _par(b0: int, b1: int, comp: int, off: int, ordem=(0, 1)) -> list[ClipIR]:
    """Par estereo encostado antes de passar por crossfades_em_sobreposicao."""
    fo = FadeIR(kind="out", duration_samples=comp)
    a = [_c(0, ch, T0, b0, 0, fade_out=copy.copy(fo)) for ch in ordem]
    b = [_c(1, ch, b0, b1, off, crossfade_in_state=True) for ch in ordem]
    return a + b


def _sessao(wav: Path, clips: list[ClipIR], fps: int = 25) -> SessionIR:
    """Sessao com track estereo vinculada a dois slots de origem."""
    m = MediaAsset(id="m1", filename=wav.name, channels=2, sample_rate=SR)
    m.duration_samples, m.status, m.resolved_path = SR * 20, "resolved", str(wav)
    t = TrackIR(id="t0", source_index=0, name="DX", kind="audio", channel_count=2, clips=clips)
    t.original_source_indices = [0, 1]
    r = TimecodeRate(fps, 1)
    s = SessionIR(name="st", sample_rate=SR, timecode_rate=r, start_timecode_samples=T0)
    s.tracks, s.media = [t], [m]
    return s


def test_crossfades_em_sobreposicao_agrupa_por_canal_no_estereo(tmp_path):
    wav = escrever_wav(tmp_path, "st.wav", canais=2)
    b0, b1 = T0 + SR, T0 + 2 * SR - XF
    sessao = _sessao(wav, _par(b0, b1, XF, OFF + XF))
    crossfades_em_sobreposicao(sessao)
    for ch in (0, 1):
        a, b = (c for c in sessao.tracks[0].clips if c.channel_index == ch)
        assert a.fade_out == FadeIR(kind="out", duration_samples=XF)
        assert (b.timeline_start_samples, b.source_offset_samples) == (b0 - XF, OFF)
        assert b.fade_in == FadeIR(kind="in", duration_samples=XF)


def test_verify_conversion_estereo_com_sidecar_e_crossfade_absorvido(tmp_path, monkeypatch):
    wav = escrever_wav(tmp_path, "st.wav", canais=2)
    b0, b1 = T0 + SR, T0 + 2 * SR - XF
    fo, fi = FadeIR(kind="out", duration_samples=XF), FadeIR(kind="in", duration_samples=XF)
    c0, c1 = _c(0, 0, T0, b0, 0, fade_out=fo), _c(1, 0, b0 - XF, b1, OFF, fade_in=fi)
    s_als = _sessao(wav, [c0, c1])
    als, aaf = tmp_path / "com_sidecar.als", tmp_path / "origem.aaf"
    opts, rep = WriteOptions(sample_rate=SR), Report(strict=False)
    write_session(s_als, default_template(), als, opts, rep)
    write_aaf(s_als, aaf, Report(strict=False))
    s_aaf = _sessao(wav, _par(b0, b1, XF, OFF + XF))
    monkeypatch.setattr(aafton.verify, "read_aaf", lambda *a, **k: copy.deepcopy(s_aaf))
    res = aafton.verify.verify_conversion(aaf, als)
    assert (res.ok, res.audio_expected, res.audio_compared) == (True, 4, 4)
    assert all(r.delta_samples == 0 for r in res.rows)


def test_verify_conversion_estereo_sem_sidecar_e_crossfade_absorvido(tmp_path, monkeypatch):
    wav = escrever_wav(tmp_path, "st.wav", canais=2)
    c_a = clip_als(wav, 0.0, 2.0, nome="st", fade_out=0.5)
    c_b = clip_als(wav, 2.0, 4.0, nome="st", crossfade=True, loop_start=0.5)
    als = montar_set(tmp_path, [("DX", [c_a, c_b])], bpm=60.0)
    aaf = tmp_path / "saida.aaf"
    export_session(als, aaf, Report(strict=False), start=start_padrao())
    assert aafton.verify.verify_conversion(aaf, als).ok is True

    b0, b1, fade = T0 + 2 * SR, T0 + 4 * SR, 24000
    s_aaf = _sessao(wav, _par(b0, b1, fade, fade, ordem=(1, 0)), fps=24)
    monkeypatch.setattr(aafton.verify, "read_aaf", lambda *a, **k: copy.deepcopy(s_aaf))
    res = aafton.verify.verify_conversion(aaf, als)
    assert (res.ok, res.audio_compared) == (True, 2)
    assert all(r.delta_samples == 0 for r in res.rows)
    assert "media offset verified" in res.coverage_msg
