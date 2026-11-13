import json
import sys
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

import aafton.web_app as web
from aafton.studio import dialogs_windows


@pytest.fixture
def local_server():
    server = web.run_studio_server(port=0, open_browser=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://external.example"},
        {"Host": "evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_local_api_rejects_other_browser_origins(local_server, headers):
    request = urllib.request.Request(local_server + "/api/status", headers=headers)
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 403


@pytest.mark.parametrize("payload", [b"[]", b"null", b"\xff"])
def test_local_api_rejects_non_object_payload(local_server, payload):
    request = urllib.request.Request(
        local_server + "/api/inspect", data=payload, headers={"Content-Type": "application/json"}
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400


def test_dialog_title_is_a_powershell_string_not_code(monkeypatch):
    scripts = []

    def capture(script):
        scripts.append(script)
        return ""

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(dialogs_windows, "run_powershell", capture)
    web.pick_windows_dialog(title="Ana's title")
    assert "$f.Title = 'Ana''s title'" in scripts[0]


def test_web_import_uses_the_same_pipeline_order_as_cli(local_server, tmp_path, monkeypatch):
    from aafton.ir import SessionIR
    from aafton.timecode import TimecodeRate
    import aafton.session_text as text
    import aafton.stereo as stereo
    import aafton.video as video

    calls = []
    session = SessionIR(
        name="test", sample_rate=48000, timecode_rate=TimecodeRate(25, 1), start_timecode_samples=0
    )
    monkeypatch.setattr(web, "read_aaf", lambda *a, **kw: session)
    monkeypatch.setattr(text, "process_session_text", lambda *a, **kw: calls.append("text"))
    monkeypatch.setattr(web, "check_frame_alignment", lambda *a, **kw: calls.append("frame"))
    monkeypatch.setattr(stereo, "process_stereo_pairing", lambda *a, **kw: calls.append("stereo"))
    monkeypatch.setattr(video, "process_video", lambda *a, **kw: calls.append("video"))
    monkeypatch.setattr(web, "write_session", lambda *a, **kw: calls.append("write"))
    request = urllib.request.Request(
        local_server + "/api/convert/import",
        data=json.dumps({"input": "input.aaf", "output": str(tmp_path / "out.als")}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        assert json.load(response)["ok"]
    assert calls == ["text", "frame", "stereo", "video", "write"]


def test_web_turnover_returns_the_created_directory(local_server, tmp_path, monkeypatch):
    session = tmp_path / "source.als"
    session.write_bytes(b"fixture")
    report = tmp_path / "report.txt"
    report.write_text("OK")
    package = tmp_path / "package"
    monkeypatch.setattr(
        web,
        "create_turnover_package",
        lambda **kw: SimpleNamespace(
            package_dir=package, report_path=report, is_valid=True, manifest_data={}
        ),
    )
    request = urllib.request.Request(
        local_server + "/api/turnover/generate",
        data=json.dumps({"session": str(session)}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        result = json.load(response)
    assert result["ok"] and result["output_dir"] == str(package)


def test_web_export_defaults_to_strict_and_preserves_previous_delivery(local_server, tmp_path):
    from tests.unit.test_review_robustness import make_set
    from aafton.als_xml import load_als, save_als, set_value

    source = make_set(tmp_path)
    root = load_als(source)
    clip = next(root.iter("AudioClip"))
    set_value(clip, "Fades/FadeInLength", 0.6)
    set_value(clip, "Fades/FadeOutLength", 0.6)
    save_als(root, source)
    destination = tmp_path / "delivery.aaf"
    destination.write_bytes(b"previous delivery")
    request = urllib.request.Request(
        local_server + "/api/convert/export",
        data=json.dumps({"input": str(source), "output": str(destination)}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request)
    assert exc.value.code == 400
    assert destination.read_bytes() == b"previous delivery"
