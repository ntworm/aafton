"""Testes unitarios para o servidor HTTP e Web UI do aafton."""

from __future__ import annotations

import json
from pathlib import Path
import threading
import urllib.request
import pytest

from aafton.web_app import run_studio_server
from tests.support.paths import local_media


@pytest.fixture(scope="module")
def studio_server():
    # Inicia o servidor em porta alta de teste sem abrir navegador
    test_port = 8799
    server = run_studio_server(port=test_port, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{test_port}"
    server.shutdown()
    server.server_close()


def test_web_app_index_and_status(studio_server: str):
    # GET /
    with urllib.request.urlopen(f"{studio_server}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
        assert "<title>aafton</title>" in html
        assert 'id="panel-to-live"' in html

    # GET /api/status
    with urllib.request.urlopen(f"{studio_server}/api/status") as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "ok"
        assert "Ableton Live 12" in data["engine"]


def test_web_app_inspect_aaf_and_ptx(studio_server: str):
    aaf_path = local_media("calibracao/calibracao.aaf")
    ptx_path = local_media("Desktop/CAL_AAFTON/CAL_AAFTON.ptx")

    # POST /api/inspect com AAF
    req_aaf = urllib.request.Request(
        f"{studio_server}/api/inspect",
        data=json.dumps({"path": str(aaf_path)}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_aaf) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["type"] == "aaf"
        assert res["session"]["name"] == "CALIBRACAO_AUDIO"
        assert len(res["session"]["tracks"]) >= 7

    # POST /api/inspect com PTX
    req_ptx = urllib.request.Request(
        f"{studio_server}/api/inspect",
        data=json.dumps({"path": str(ptx_path)}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req_ptx) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["type"] == "ptx"
        assert "mono.wav" in res["referenced_wavs"]
        assert "CAL_MONO" in res["detected_tracks"]


def test_web_app_oracle_verify(studio_server: str):
    media_dir = local_media("Desktop/CAL_AAFTON/Audio Files")
    req = urllib.request.Request(
        f"{studio_server}/api/oracle/verify",
        data=json.dumps({"media_dir": str(media_dir)}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["audit"]["total_files"] == 5
        assert res["audit"]["ok_count"] == 5


def test_web_app_fs_list_endpoint(studio_server: str):
    # GET /api/fs/list
    with urllib.request.urlopen(f"{studio_server}/api/fs/list?ext=.aaf,.als") as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert "current" in res
        assert "bookmarks" in res
        assert "drives" in res
        assert isinstance(res["items"], list)


def test_web_app_powershell_browse_endpoint(studio_server: str, monkeypatch):
    import aafton.studio.dialogs as dialogs

    def mock_dialog(kind="open_file", filter_type="all", title="Selecionar"):
        return {"path": "C:/Users/editor/Desktop/CAL_AAFTON/calibracao.aaf"}

    monkeypatch.setattr(dialogs, "pick", mock_dialog)

    req = urllib.request.Request(
        f"{studio_server}/api/fs/powershell_browse",
        data=json.dumps({"kind": "open_file", "filter_type": "aaf"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["path"].endswith("calibracao.aaf")


def test_web_app_detect_video_endpoint(studio_server: str, tmp_path: Path):
    aaf_dir = tmp_path / "MyProject" / "AAF"
    video_dir = tmp_path / "MyProject" / "Video Files"
    aaf_dir.mkdir(parents=True)
    video_dir.mkdir(parents=True)

    fake_aaf = aaf_dir / "scene1.aaf"
    fake_aaf.write_bytes(b"dummy")
    fake_vid = video_dir / "scene1.mp4"
    fake_vid.write_bytes(b"dummy_video")

    url = f"{studio_server}/api/fs/detect_video?path={urllib.request.quote(str(fake_aaf))}"
    with urllib.request.urlopen(url) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True
        assert res["video_path"] == str(fake_vid.resolve())


def test_web_app_import_with_video(studio_server: str, tmp_path: Path):
    calibracao_aaf = local_media("calibracao/calibracao.aaf")

    out_als = tmp_path / "output_test_vid.als"
    fake_vid = tmp_path / "movie.mp4"
    fake_vid.write_bytes(b"dummy_mp4")

    payload = {
        "input": str(calibracao_aaf),
        "output": str(out_als),
        "video": str(fake_vid),
        "permissive": True,
    }
    req = urllib.request.Request(
        f"{studio_server}/api/convert/import",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        res = json.loads(resp.read().decode("utf-8"))
        assert res["ok"] is True

    from aafton.als_xml import load_als

    tree = load_als(out_als)
    tracks = tree.findall(".//Tracks/AudioTrack")
    assert len(tracks) > 0
    first_track_name = tracks[0].find(".//Name/UserName").attrib.get("Value")
    assert first_track_name == "movie"
