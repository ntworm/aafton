"""Perda silenciosa da curva de ganho: o AAF grava ganho constante.

Quando o pyaaf2 instalado nao tem as definicoes de VaryingValue, o envelope
de ganho vira um ganho constante e o pan automatico nao cabe no AAF. O
relatorio nao pode anunciar uma automacao que nao foi gravada: a curva perdida
tem de virar erro, com o mesmo formato do erro de pan.
"""

from __future__ import annotations

from pathlib import Path

import aaf2
import pytest

from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, FadeIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from tests.aaf_factory import wav_bytes

ORIGEM = 172800000
SR = 48000


def _sessao(
    tmp_path: Path,
    *,
    ganho: list[tuple[int, float]] | None = None,
    pan: list[tuple[int, float]] | None = None,
    fade: bool = False,
) -> SessionIR:
    """Sessao minima com uma track mono e um clip."""
    wav = tmp_path / "cena.wav"
    wav.write_bytes(wav_bytes(SR * 5))
    asset = MediaAsset(id="m1", filename="cena.wav", status="resolved", resolved_path=str(wav))
    track = TrackIR(id="t0", source_index=0, name="AUDIO 1", kind="audio", channel_count=1)
    clip = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="cena.wav",
        media_id="m1",
        timeline_start_samples=ORIGEM,
        timeline_end_samples=ORIGEM + 2 * SR,
        source_offset_samples=0,
    )
    if fade:
        # Fades que nao cabem no clip: assim eles ficam como envelope de ganho
        # e a curva perdida tem de virar erro, como no caso da automacao.
        clip.fade_in = FadeIR(kind="in", duration_samples=3 * SR // 2)
        clip.fade_out = FadeIR(kind="out", duration_samples=SR)
    track.clips.append(clip)
    if ganho is not None:
        track.gain_points = ganho
    if pan is not None:
        track.pan_points = pan
    return SessionIR(
        name="ganho",
        sample_rate=SR,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=ORIGEM,
        tracks=[track],
        media=[asset],
    )


def _parametros_do_aaf(caminho: Path) -> list[str]:
    """Nomes dos parametros de operacao que o AAF gravou."""
    nomes: list[str] = []

    def visita(no) -> None:
        if no is None:
            return
        nomes.extend(type(p).__name__ for p in (getattr(no, "parameters", None) or []))
        filhos = list(getattr(no, "components", None) or [])
        filhos += list(getattr(no, "segments", None) or [])
        for filho in filhos:
            visita(filho)

    with aaf2.open(str(caminho), "r") as arquivo:
        for mob in arquivo.content.mobs:
            for slot in mob.slots:
                visita(getattr(slot, "segment", None))
    return nomes


@pytest.mark.parametrize("com_fade", [False, True])
def test_curva_perdida_da_erro_de_dependencia(tmp_path, monkeypatch, com_fade):
    """A curva que virou ganho constante nao pode ser anunciada como gravada."""
    monkeypatch.setattr("aafton.aaf_write._curva_suportada", lambda: False)
    sessao = _sessao(
        tmp_path,
        ganho=None if com_fade else [(ORIGEM, 1.0), (ORIGEM + SR, 0.5)],
        fade=com_fade,
    )

    report = Report(strict=False)
    write_aaf(sessao, tmp_path / "saida.aaf", report)

    codigos = [i.code for i in report.issues]
    assert "WRITE_GAIN_AUTOMATION_DEPENDENCY_MISSING" in codigos
    assert "GAIN_AUTOMATION_WRITTEN" not in codigos
    assert "FADE_CONVERTED_TO_ENVELOPE" not in codigos
    achado = next(i for i in report.issues if i.code == "WRITE_GAIN_AUTOMATION_DEPENDENCY_MISSING")
    assert achado.context["track"] == "AUDIO 1"
    assert achado.context["clip"] == "cena.wav"
    assert achado.context["dependency"] == "pyaaf2"
    assert achado.context["cause"]


def test_pan_sem_curva_nao_escreve_varying_value(tmp_path, monkeypatch):
    """Sem VaryingValue o pan automatico nao cabe: o AAF leva o pan estatico."""
    monkeypatch.setattr("aafton.aaf_write._curva_suportada", lambda: False)
    sessao = _sessao(tmp_path, pan=[(ORIGEM, 0.0), (ORIGEM + SR, 0.5)])
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(sessao, destino, report)

    codigos = [i.code for i in report.issues]
    assert "WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING" in codigos
    parametros = _parametros_do_aaf(destino)
    assert "VaryingValue" not in parametros
    assert "ConstantValue" in parametros


def test_ganho_com_a_curva_real_mantem_os_relatorios(tmp_path):
    """Com o pyaaf2 completo a curva e gravada e nada muda no relatorio."""
    sessao = _sessao(
        tmp_path,
        ganho=[(ORIGEM, 1.0), (ORIGEM + SR, 0.5)],
        pan=[(ORIGEM, 0.0), (ORIGEM + SR, 0.5)],
    )
    destino = tmp_path / "saida.aaf"

    report = Report(strict=False)
    write_aaf(sessao, destino, report)

    codigos = [i.code for i in report.issues]
    assert "GAIN_AUTOMATION_WRITTEN" in codigos
    assert "PAN_AUTOMATION_EXPORTED" in codigos
    assert "WRITE_GAIN_AUTOMATION_DEPENDENCY_MISSING" not in codigos
    assert "WRITE_PAN_AUTOMATION_DEPENDENCY_MISSING" not in codigos
    assert "VaryingValue" in _parametros_do_aaf(destino)
