"""Track de video com automacao nao para o export: ela nao vai ao AAF."""

from __future__ import annotations

from aafton.als_ancora import start_padrao
from aafton.pipeline import export_session
from aafton.report import Report
from tests.unit.test_als_sem_sidecar import clip, escrever_wav, montar_set


def test_automacao_da_track_de_video_nao_para_o_export_estrito(tmp_path):
    dx = escrever_wav(tmp_path, "dx.wav")
    video = tmp_path / "corte.mov"
    video.write_bytes(b"")
    tracks = [("VIDEO", [clip(video, 0.0, 8.0)]), ("DX", [clip(dx, 0.0, 4.0)])]
    als = montar_set(tmp_path, tracks, automacao_em="VIDEO")
    report = Report()

    export_session(als, tmp_path / "saida.aaf", report, start=start_padrao())

    codigos = [issue.code for issue in report.issues]
    assert "WRITE_SESSION_FEATURE_UNSUPPORTED" not in codigos
    assert "ALS_VIDEO_LEFT_OUT" in codigos
    assert (tmp_path / "saida.aaf").exists()


def test_automacao_de_track_de_audio_ainda_para_o_export(tmp_path):
    dx = escrever_wav(tmp_path, "dx.wav")
    als = montar_set(tmp_path, [("DX", [clip(dx, 0.0, 4.0)])], automacao_em="DX")
    report = Report(strict=False)

    export_session(als, tmp_path / "saida.aaf", report, start=start_padrao())

    assert "WRITE_SESSION_FEATURE_UNSUPPORTED" in [i.code for i in report.issues]
