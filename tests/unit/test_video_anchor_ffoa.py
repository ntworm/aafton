"""Timecode zero num arquivo de video e ausencia, nao declaracao.

O ffmpeg escreve um stream `tmcd` com 00:00:00:00 em todo MP4 que gera, entao um
proxy exportado sem timecode fica indistinguivel de um que comeca de fato na hora
zero. Obedecer a esse zero joga a imagem para o inicio da timeline; e como a
origem da sessao e escolhida a partir do primeiro clip, ela vai junto, empurrando
todo o audio uma hora para a frente.

Medido na TESTE_AAF antes do conserto: video em beat 0, audio da AMB 1ST em beat
3619, origem 00:00:00:00. Depois: video e REF em beat 10 -> 70, origem
00:59:50:00.

Um timecode nao-zero continua ganhando do FFOA, porque ai e informacao de
verdade, e a discordancia entre os dois continua sendo avisada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aafton.ir import ClipIR, MarkerIR, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.video import process_video

SEM_TC = Path("tests/fixtures/video/sem_timecode.mp4")
COM_TC = Path("tests/fixtures/video/com_timecode.mp4")

UMA_HORA = 3600 * 48000


def _sessao_com_ffoa(ffoa_samples: int | None):
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
    )
    track = TrackIR(id="t0", source_index=0, name="REF", kind="audio", channel_count=1)
    track.clips.append(
        ClipIR(
            id="c0",
            track_id="t0",
            event_index=0,
            name="ref",
            media_id=None,
            timeline_start_samples=UMA_HORA,
            timeline_end_samples=UMA_HORA + 48000,
            source_offset_samples=0,
            channel_index=0,
        )
    )
    session.tracks.append(track)
    if ffoa_samples is not None:
        session.markers.append(
            MarkerIR(
                id="mk0",
                name="FFOA",
                position_samples=ffoa_samples,
            )
        )
    return session


def _video_track(session):
    return next((t for t in session.tracks if getattr(t, "is_video", False)), None)


@pytest.mark.skipif(not SEM_TC.exists(), reason="fixture de video ausente")
class TestAncoragem:
    def test_timecode_zero_com_ffoa_ancora_no_ffoa(self, tmp_path):
        """O caso do defeito: proxy sem timecode e sessao que comeca na hora 1."""
        session = _sessao_com_ffoa(UMA_HORA)
        report = Report(strict=False)
        process_video(session, tmp_path / "x.aaf", str(SEM_TC), None, report)

        codes = [i.code for i in report.issues]
        assert "VIDEO_TIMECODE_PLACEHOLDER" in codes, codes
        assert "VIDEO_ANCHOR_FROM_FFOA" in codes, codes
        assert "VIDEO_ANCHOR_FROM_FILE" not in codes

        video = _video_track(session)
        assert video is not None
        assert video.clips[0].timeline_start_samples == UMA_HORA

    def test_timecode_zero_sem_ffoa_nao_inventa(self, tmp_path):
        """Sem FFOA nao ha autoridade melhor, entao o zero do arquivo vale."""
        session = _sessao_com_ffoa(None)
        report = Report(strict=False)
        process_video(session, tmp_path / "x.aaf", str(SEM_TC), None, report)

        codes = [i.code for i in report.issues]
        assert "VIDEO_ANCHOR_FROM_FILE" in codes, codes
        assert "VIDEO_TIMECODE_PLACEHOLDER" not in codes
        assert _video_track(session).clips[0].timeline_start_samples == 0

    def test_ffoa_em_zero_nao_muda_nada(self, tmp_path):
        """FFOA na hora zero concorda com o arquivo; nao ha o que preferir."""
        session = _sessao_com_ffoa(0)
        report = Report(strict=False)
        process_video(session, tmp_path / "x.aaf", str(SEM_TC), None, report)

        codes = [i.code for i in report.issues]
        assert "VIDEO_ANCHOR_FROM_FILE" in codes, codes
        assert _video_track(session).clips[0].timeline_start_samples == 0

    @pytest.mark.skipif(not COM_TC.exists(), reason="fixture de video ausente")
    def test_timecode_de_verdade_ganha_do_ffoa(self, tmp_path):
        """Um timecode nao-zero e informacao e continua mandando.

        Este e o controle: sem ele, preferir o FFOA sempre passaria nos outros
        tres e a ferramenta ignoraria o timecode real de um master exportado
        com a hora certa.
        """
        session = _sessao_com_ffoa(UMA_HORA + 48000 * 30)  # FFOA 30s depois
        report = Report(strict=False)
        process_video(session, tmp_path / "x.aaf", str(COM_TC), None, report)

        codes = [i.code for i in report.issues]
        assert "VIDEO_ANCHOR_FROM_FILE" in codes, codes
        assert "VIDEO_TIMECODE_PLACEHOLDER" not in codes
        # 01:00:00:00 declarado no arquivo, nao o FFOA que esta 30s depois
        assert _video_track(session).clips[0].timeline_start_samples == UMA_HORA
        # E a discordancia continua sendo avisada.
        assert "VIDEO_ANCHOR_DISAGREES_WITH_FFOA" in codes, codes

    def test_video_start_explicito_ganha_de_tudo(self, tmp_path):
        session = _sessao_com_ffoa(UMA_HORA)
        report = Report(strict=False)
        process_video(session, tmp_path / "x.aaf", str(SEM_TC), "00:00:10:00", report)

        codes = [i.code for i in report.issues]
        assert "VIDEO_ANCHOR_EXPLICIT" in codes, codes
        assert _video_track(session).clips[0].timeline_start_samples == 10 * 48000
