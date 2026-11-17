"""Filtragem por track_id na selecao de tracks e no replay de issues."""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from aafton.als_ancora import start_padrao
from aafton.ir import ClipIR, SessionIR, TrackIR
from aafton.pipeline import export_session, replay_issues, select_tracks
from aafton.report import Report
from aafton.timecode import TimecodeRate
from aafton.verify import verify_conversion
from tests.unit.test_als_sem_sidecar import (
    ESTICADO_104,
    _automacao_sem_destino,
    clip,
    escrever_wav,
    montar_set,
)


def _adicionar_automacao_na_track(als_path: Path, audio_index: int) -> None:
    """Adiciona automacao nao reconciliada apenas na AudioTrack de indice dado."""
    with gzip.open(als_path, "rb") as handle:
        root = ET.parse(handle).getroot()
    audio_tracks = root.findall("./LiveSet/Tracks/AudioTrack")
    _automacao_sem_destino(audio_tracks[audio_index])
    with gzip.open(als_path, "wb") as handle:
        handle.write(ET.tostring(root, encoding="utf-8"))


def test_selecao_com_tracks_de_mesmo_nome_filtra_issues_e_features_por_track_id(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    video = tmp_path / "cena.mov"
    tracks = [
        ("Audio", [clip(wav, 0.0, 4.0)]),
        ("Audio", [clip(video, 0.0, 2.0), clip(wav, 4.0, 8.0)]),
    ]
    als = montar_set(tmp_path, tracks)
    _adicionar_automacao_na_track(als, 1)

    saida_0 = tmp_path / "so_t0.aaf"
    rep_0 = Report(strict=False)
    sess_0 = export_session(als, saida_0, rep_0, tracks=[0], start=start_padrao())

    assert [t.id for t in sess_0.tracks] == ["t0"]
    assert sess_0.unsupported_export_features == []
    assert [i for i in rep_0.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"] == []
    assert [i for i in rep_0.issues if i.code == "ALS_VIDEO_LEFT_OUT"] == []

    saida_1 = tmp_path / "so_t1.aaf"
    rep_1 = Report(strict=False)
    sess_1 = export_session(als, saida_1, rep_1, tracks=[1], start=start_padrao())

    assert [t.id for t in sess_1.tracks] == ["t1"]
    assert len(sess_1.unsupported_export_features) == 1
    autos = [i for i in rep_1.issues if i.code == "ALS_AUTOMATION_UNSUPPORTED"]
    videos = [i for i in rep_1.issues if i.code == "ALS_VIDEO_LEFT_OUT"]
    assert len(autos) == 1 and autos[0].context["track_id"] == "t1"
    assert len(videos) == 1 and videos[0].context["track_id"] == "t1"


def test_replay_issues_usa_track_id_e_faz_fallback_por_nome_sem_id():
    msg_auto = "automation of 'Audio' is not yet reconciled for export"
    msg_env = "automation of the clip 'c' in track 'SemId' has no representation"
    clip_0 = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="ok",
        media_id=None,
        timeline_start_samples=0,
        timeline_end_samples=48000,
    )
    t0 = TrackIR(
        id="t0", source_index=0, name="Audio", kind="audio", channel_count=1, clips=[clip_0]
    )
    t1 = TrackIR(
        id="t1", source_index=1, name="Audio", kind="audio", channel_count=1, clips=[clip_0]
    )
    t2 = TrackIR(
        id="t2", source_index=2, name="SemId", kind="audio", channel_count=1, clips=[clip_0]
    )
    session = SessionIR(
        name="s",
        sample_rate=48000,
        timecode_rate=TimecodeRate(24, 1),
        start_timecode_samples=0,
        tracks=[t0, t1, t2],
        unsupported_export_features=[msg_auto, msg_auto, msg_env],
    )
    reading = Report(strict=False)
    reading.warning("ALS_AUTOMATION_UNSUPPORTED", msg_auto, {"track": "Audio", "track_id": "t0"})
    reading.warning("ALS_AUTOMATION_UNSUPPORTED", msg_auto, {"track": "Audio", "track_id": "t1"})
    reading.warning("ALS_CLIPS_LEFT_OUT", "left out t1", {"track": "Audio", "track_id": "t1"})
    reading.info("ALS_VIDEO_LEFT_OUT", "video t1", {"track": "Audio", "track_id": "t1"})
    reading.error("ALS_CLIP_UNREADABLE", "bad t1", {"track": "Audio", "track_id": "t1"})
    reading.warning("ALS_AUTOMATION_UNSUPPORTED", msg_env, {"track": "SemId"})
    reading.warning(
        "ALS_AUTOMATION_UNSUPPORTED",
        "group",
        {"track": "SemId", "kind": "GroupTrack"},
    )

    removidas = select_tracks(session, [0], reading)
    out = Report(strict=False)
    replay_issues(reading, out, removidas, [0])

    assert session.unsupported_export_features == [msg_auto]
    codes = [(i.code, i.context.get("track_id"), i.context.get("kind")) for i in out.issues]
    assert codes == [
        ("ALS_AUTOMATION_UNSUPPORTED", "t0", None),
        ("ALS_AUTOMATION_UNSUPPORTED", None, "GroupTrack"),
    ]


def test_leave_out_render_e_subconjunto_com_mesmo_nome_nao_vaza_contagem_no_verify(tmp_path):
    wav = escrever_wav(tmp_path, "dx.wav")
    video = tmp_path / "cena.mp4"
    tracks = [
        ("Audio", [clip(wav, 0.0, 4.0)]),
        ("Audio", [clip(wav, 0.0, 8.0, warp=ESTICADO_104), clip(video, 8.0, 12.0)]),
    ]
    als = montar_set(tmp_path, tracks, bpm=104.0)
    aaf = tmp_path / "sub_t0.aaf"

    report = Report(strict=False)
    export_session(als, aaf, report, tracks=[0], start=start_padrao(), leave_out_render=True)
    res = verify_conversion(aaf, als, leave_out_render=True, tracks=[0])

    assert res.ok is True
    assert "left out: 0 clips (render in Live), 0 MIDI tracks, 0 video" in res.coverage_msg
