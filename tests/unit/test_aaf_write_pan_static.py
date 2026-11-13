"""Defeito D3: declaracao e diagnostico de pan estatico em tracks estereo/multicanal.

No AAF exportado, apenas faixas mono suportam a operacao Mono Audio Pan. Faixas com
mais de um canal (estereo ou multicanal 5.1/7.1) nao carregam o pan estatico, mas o
exportador deve relatar explicitamente a limitacao (WRITE_STEREO_PAN_UNSUPPORTED para
estereo e WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED para multicanal).
"""

from pathlib import Path
import wave

from aafton.aaf_write import write_aaf
from aafton.ir import ClipIR, MediaAsset, SessionIR, TrackIR
from aafton.report import Report
from aafton.timecode import TimecodeRate


def _criar_wav(caminho: Path, duracao_samples: int = 48000, canais: int = 1) -> Path:
    """Cria WAV silencioso com cabecalho valido."""
    with wave.open(str(caminho), "wb") as handle:
        handle.setnchannels(canais)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\0" * (duracao_samples * canais * 2))
    return caminho


def _sessao_com_track(track: TrackIR, wav: Path, canais_midia: int = 1) -> SessionIR:
    """Monta sessao minima com a track fornecida."""
    return SessionIR(
        name="sessao_pan_teste",
        sample_rate=48000,
        timecode_rate=TimecodeRate(25, 1),
        start_timecode_samples=0,
        tracks=[track],
        media=[
            MediaAsset(
                id="m1",
                filename=wav.name,
                channels=canais_midia,
                sample_rate=48000,
                status="resolved",
                resolved_path=str(wav),
            )
        ],
    )


def test_stereo_track_non_zero_pan_emits_warning(tmp_path):
    """Track estereo com pan estatico != 0.0 emite WRITE_STEREO_PAN_UNSUPPORTED."""
    wav = _criar_wav(tmp_path / "stereo.wav", canais=2)
    track = TrackIR(
        id="t1",
        source_index=0,
        name="Stereo Music",
        kind="audio",
        channel_count=2,
        pan=0.3,
        clips=[
            ClipIR(
                id="c1",
                track_id="t1",
                channel_index=0,
                event_index=0,
                name="Musica",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
            ClipIR(
                id="c2",
                track_id="t1",
                channel_index=1,
                event_index=0,
                name="Musica",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
        ],
    )
    session = _sessao_com_track(track, wav, canais_midia=2)
    report = Report(strict=False)

    out_aaf = tmp_path / "stereo_pan.aaf"
    write_aaf(session, out_aaf, report)

    codigos = [i.code for i in report.issues]
    assert "WRITE_STEREO_PAN_UNSUPPORTED" in codigos
    issue = next(i for i in report.issues if i.code == "WRITE_STEREO_PAN_UNSUPPORTED")
    assert issue.severity == "warning"
    assert issue.context["track"] == "Stereo Music"
    assert issue.context["channels"] == 2
    assert abs(issue.context["pan"] - 0.3) < 1e-4


def test_multichannel_track_non_zero_pan_emits_warning(tmp_path):
    """Track 5.1 (6 canais) com pan estatico emite WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED."""
    wav = _criar_wav(tmp_path / "surround.wav", canais=6)
    track = TrackIR(
        id="t1",
        source_index=0,
        name="5.1 FX",
        kind="audio",
        channel_count=6,
        pan=-0.5,
        clips=[
            ClipIR(
                id=f"c{i}",
                track_id="t1",
                channel_index=i,
                event_index=0,
                name="FX",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            )
            for i in range(6)
        ],
    )
    session = _sessao_com_track(track, wav, canais_midia=6)
    report = Report(strict=False)

    out_aaf = tmp_path / "surround_pan.aaf"
    write_aaf(session, out_aaf, report)

    codigos = [i.code for i in report.issues]
    assert "WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED" in codigos
    issue = next(i for i in report.issues if i.code == "WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED")
    assert issue.severity == "warning"
    assert issue.context["track"] == "5.1 FX"
    assert issue.context["channels"] == 6


def test_stereo_track_center_pan_no_warning(tmp_path):
    """Track estereo centralizada (pan = 0.0) nao emite nenhum aviso de pan."""
    wav = _criar_wav(tmp_path / "stereo.wav", canais=2)
    track = TrackIR(
        id="t1",
        source_index=0,
        name="Stereo Centro",
        kind="audio",
        channel_count=2,
        pan=0.0,
        clips=[
            ClipIR(
                id="c1",
                track_id="t1",
                channel_index=0,
                event_index=0,
                name="Centro",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
            ClipIR(
                id="c2",
                track_id="t1",
                channel_index=1,
                event_index=0,
                name="Centro",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
        ],
    )
    session = _sessao_com_track(track, wav, canais_midia=2)
    report = Report(strict=False)

    out_aaf = tmp_path / "stereo_centro.aaf"
    write_aaf(session, out_aaf, report)

    codigos = [i.code for i in report.issues]
    assert "WRITE_STEREO_PAN_UNSUPPORTED" not in codigos
    assert "WRITE_PAN_MULTICHANNEL_STATIC_UNSUPPORTED" not in codigos


def test_stereo_track_strict_mode_does_not_fail_on_pan_warning(tmp_path):
    """Como a limitacao e warning (nao erro), exportacao em strict=True sucede sem excecao."""
    wav = _criar_wav(tmp_path / "stereo.wav", canais=2)
    track = TrackIR(
        id="t1",
        source_index=0,
        name="Stereo Music",
        kind="audio",
        channel_count=2,
        pan=0.4,
        clips=[
            ClipIR(
                id="c1",
                track_id="t1",
                channel_index=0,
                event_index=0,
                name="Musica",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
            ClipIR(
                id="c2",
                track_id="t1",
                channel_index=1,
                event_index=0,
                name="Musica",
                media_id="m1",
                timeline_start_samples=0,
                timeline_end_samples=48000,
                source_offset_samples=0,
            ),
        ],
    )
    session = _sessao_com_track(track, wav, canais_midia=2)
    report = Report(strict=True)

    out_aaf = tmp_path / "stereo_strict.aaf"
    # Nao deve levantar ReportError
    write_aaf(session, out_aaf, report)
    assert any(i.code == "WRITE_STEREO_PAN_UNSUPPORTED" for i in report.issues)
