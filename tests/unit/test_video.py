import subprocess

import pytest

from aafton.report import Report
from aafton.video import process_video
from aafton.timecode import TimecodeRate
from aafton.ir import SessionIR, TrackIR, ClipIR


def test_process_video_no_candidates(tmp_path):
    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(25, 1), start_timecode_samples=0
    )
    rep = Report()
    process_video(session, tmp_path / "fake.aaf", None, None, rep)
    assert any(i.code == "VIDEO_NOT_FOUND" for i in rep.issues)
    nota = next(i for i in rep.issues if i.code == "VIDEO_NOT_FOUND")
    assert nota.severity == "info"
    assert nota.message == "no video next to the AAF or referenced in the file"
    assert len(session.tracks) == 0


def test_process_video_explicit_video_not_found(tmp_path):
    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(25, 1), start_timecode_samples=0
    )
    rep = Report()
    path = tmp_path / "nao_existe.mp4"
    process_video(session, tmp_path / "fake.aaf", str(path), None, rep)
    nota = next(i for i in rep.issues if i.code == "VIDEO_NOT_FOUND")
    assert nota.severity == "warning"
    assert nota.message == f"video not found: {path}"
    assert nota.context == {"path": str(path)}
    assert len(session.tracks) == 0


def test_process_video_explicit_start(tmp_path):
    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(24, 1), start_timecode_samples=0
    )

    # fake audio clip to give bounds
    t = TrackIR(id="t0", source_index=0, name="Audio", kind="audio", channel_count=1)
    c = ClipIR(
        id="c0",
        track_id="t0",
        channel_index=0,
        event_index=0,
        name="clip",
        media_id=None,
        timeline_start_samples=48000 * 3600,
        timeline_end_samples=48000 * 3610,
    )
    t.clips.append(c)
    session.tracks.append(t)

    rep = Report()

    # Need a fake video file
    vf = tmp_path / "vid.mp4"
    vf.write_bytes(b"dummy")

    process_video(session, tmp_path / "fake.aaf", str(vf), "01:00:00:00", rep)
    assert len(session.tracks) == 2
    vt = session.tracks[0]
    assert vt.name == "vid"
    assert vt.is_video is True
    assert vt.muted is True
    vc = vt.clips[0]
    assert vc.anchor_source == "VIDEO_ANCHOR_EXPLICIT"
    assert vc.timeline_start_samples == 48000 * 3600


def test_process_video_file_fallback(tmp_path):
    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(24, 1), start_timecode_samples=0
    )
    rep = Report()
    vf = tmp_path / "vid.mp4"
    vf.write_bytes(b"dummy")

    process_video(session, tmp_path / "fake.aaf", str(vf), None, rep)
    assert len(session.tracks) == 1
    vc = session.tracks[0].clips[0]
    assert vc.anchor_source == "VIDEO_ANCHOR_ASSUMED"
    assert any(i.code == "VIDEO_ANCHOR_ASSUMED" for i in rep.issues)


def test_find_video_file_in_parent_video_files(tmp_path):
    from aafton.video import find_video_file

    session_root = tmp_path / "PROJECT_X"
    aaf_dir = session_root / "AAF"
    video_dir = session_root / "Video Files"
    aaf_dir.mkdir(parents=True)
    video_dir.mkdir(parents=True)

    aaf_file = aaf_dir / "edit_v1.aaf"
    aaf_file.write_bytes(b"dummy_aaf")
    video_file = video_dir / "edit_v1.mp4"
    video_file.write_bytes(b"dummy_mp4")

    found = find_video_file(aaf_file, None)
    assert found == video_file.resolve()


def test_find_video_disambiguate_by_stem(tmp_path):
    from aafton.video import find_video_file

    session_root = tmp_path / "THE_FILM"
    aaf_dir = session_root / "AAF"
    video_dir = session_root / "Video Files"
    aaf_dir.mkdir(parents=True)
    video_dir.mkdir(parents=True)

    aaf_file = aaf_dir / "The Film 1126 2 for sound.aaf"
    aaf_file.write_bytes(b"dummy_aaf")

    vid_target = video_dir / "The Film 1126 2 (mov prores 422 proxy).mov"
    vid_target.write_bytes(b"target")
    vid_other = video_dir / "The Film 1209.mov"
    vid_other.write_bytes(b"other")

    found = find_video_file(aaf_file, None)
    assert found == vid_target.resolve()


def test_transcode_to_live_proxy_reuse(tmp_path, monkeypatch):
    from aafton.video import transcode_to_live_proxy

    rep = Report(strict=False)
    mov = tmp_path / "movie.mov"
    mov.write_bytes(b"dummy")
    proxy = tmp_path / "movie_LiveProxy.mp4"
    proxy.write_bytes(b"existing_proxy")

    monkeypatch.setattr("aafton.video.video_has_audio", lambda p: True)
    res = transcode_to_live_proxy(mov, rep)
    assert res == proxy
    assert any(i.code == "VIDEO_PROXY_REUSED" for i in rep.issues)


def test_video_has_audio(tmp_path, monkeypatch):
    import subprocess
    from aafton.video import video_has_audio

    fake_file = tmp_path / "clip.mp4"
    fake_file.write_bytes(b"dummy")

    # Caso 1: ffprobe retorna apenas stream de video
    class FakeProcessNoAudio:
        returncode = 0
        stdout = '{"streams": [{"codec_type": "video", "codec_name": "h264"}]}'

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProcessNoAudio())
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/ffprobe" if cmd == "ffprobe" else None
    )
    assert video_has_audio(fake_file) is False

    # Caso 2: ffprobe retorna video e audio
    class FakeProcessWithAudio:
        returncode = 0
        stdout = '{"streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}'

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProcessWithAudio())
    assert video_has_audio(fake_file) is True


def test_process_video_without_audio_triggers_proxy(tmp_path, monkeypatch):
    from aafton.video import process_video

    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(24, 1), start_timecode_samples=0
    )
    rep = Report()

    video_file = tmp_path / "silent_video.mp4"
    video_file.write_bytes(b"dummy_mp4")

    # Simula que o arquivo de video nao possui audio
    monkeypatch.setattr("aafton.video.video_has_audio", lambda p: False)
    monkeypatch.setattr("aafton.video.analyze_video", lambda p: (10.0, "01:00:00:00", "h264"))

    proxy_target = tmp_path / "silent_video_LiveProxy.mp4"

    def fake_transcode(vpath, report):
        proxy_target.write_bytes(b"proxy_data")
        return proxy_target

    monkeypatch.setattr("aafton.video.transcode_to_live_proxy", fake_transcode)

    process_video(session, tmp_path / "session.aaf", str(video_file), "01:00:00:00", rep)

    assert len(session.tracks) == 1
    v_track = session.tracks[0]
    assert v_track.is_video is True
    assert v_track.clips[0].media_id == "m_video"
    m_asset = next(m for m in session.media if m.id == "m_video")
    assert m_asset.resolved_path == str(proxy_target)


def test_analyze_video_le_o_ffprobe_em_utf8(tmp_path):
    """Nome com acento nao pode derrubar a leitura: o ffprobe responde em UTF-8.

    No Windows, o padrao do subprocess e cp1252. A saida com a letra acentuada
    quebrava o decode em silencio e a duracao caia no padrao de uma hora.
    """
    from aafton.video import analyze_video

    video = tmp_path / "Águas_Versão.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=24:d=2",
            "-metadata",
            "title=Águas Versão",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    duracao, _, _ = analyze_video(video)
    assert duracao == pytest.approx(2.0, abs=0.1)
