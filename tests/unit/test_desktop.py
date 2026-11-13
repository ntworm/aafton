"""Tests of the desktop app: the window, the dialog backend and the smoke.

The window is a fake: no screen is opened and no real webview is needed.
"""

from __future__ import annotations

import sys
import threading

import pytest

from aafton import __version__, desktop
from aafton.cli import main
from aafton.studio import dialogs

# The kinds of dialog of the fake webview.
OPEN = "open"
SAVE = "save"
FOLDER = "folder"


class FakeWindow:
    """The window of the fake: it records the dialogs it was asked for."""

    def __init__(self, answer=None, shown: bool = True) -> None:
        self.shown = shown
        self.timeout: int | None = None
        self.answer = answer
        self.asked: list[tuple] = []
        self.events = type("Events", (), {"shown": self})()

    def wait(self, timeout: int | None = None) -> bool:
        """The wait of the shown event: it answers the state of the window."""
        self.timeout = timeout
        return self.shown

    def create_file_dialog(
        self,
        dialog_type,
        directory: str = "",
        allow_multiple: bool = False,
        save_filename: str = "",
        file_types: tuple = (),
    ):
        self.asked.append((dialog_type, directory, allow_multiple, save_filename, file_types))
        return self.answer


class FakeServer:
    """The server of the fake: it says when it serves and when it stops."""

    def __init__(self, events: list, port: int = 8765) -> None:
        self.events = events
        self.server_address = ("127.0.0.1", port)
        self.serving = threading.Event()

    def serve_forever(self) -> None:
        self.events.append(("serve", None))
        self.serving.set()

    def shutdown(self) -> None:
        self.events.append(("shutdown", None))

    def server_close(self) -> None:
        self.events.append(("server_close", None))


class FakeWebview:
    """The webview module of the fake: no window is ever opened."""

    FileDialog = type("FileDialog", (), {"OPEN": OPEN, "SAVE": SAVE, "FOLDER": FOLDER})

    def __init__(self, events: list, server: FakeServer | None = None) -> None:
        self.events = events
        self.server = server
        self.window = FakeWindow()
        self.created: tuple | None = None
        self.failure: Exception | None = None

    def create_window(self, title, url, width=None, height=None, min_size=None):
        self.events.append(("create_window", (title, url, width, height, min_size)))
        self.created = (title, url, width, height, min_size)
        return self.window

    def start(self) -> None:
        if self.server is not None:
            self.server.serving.wait(5)
        self.events.append(("start", None))
        if self.failure is not None:
            raise self.failure


@pytest.fixture(autouse=True)
def _sem_janela():
    """Each test starts and ends without the backend of the window."""
    dialogs.set_window_backend(None)
    yield
    dialogs.set_window_backend(None)


@pytest.fixture
def fake(monkeypatch):
    """The fake server and the fake webview, with the calls they received."""
    events: list = []
    registered: list = []
    server = FakeServer(events)
    webview = FakeWebview(events, server)
    original = dialogs.set_window_backend

    def spy(backend):
        events.append(("register", backend))
        registered.append(backend)
        original(backend)

    monkeypatch.setattr(desktop.web_app, "run_studio_server", lambda **kwargs: server)
    monkeypatch.setattr(dialogs, "set_window_backend", spy)
    return events, registered, server, webview


def _names(events: list) -> list[str]:
    """The name of each call, in the order they happened."""
    return [name for name, _ in events]


def test_o_backend_e_registrado_antes_do_servidor_e_do_start(fake):
    events, registered, server, webview = fake

    assert desktop.run_app(webview_module=webview) == 0

    names = _names(events)
    assert names.index("register") < names.index("serve")
    assert names.index("register") < names.index("start")
    assert names.index("start") < names.index("shutdown")
    assert names.index("shutdown") < names.index("server_close")
    assert registered[0] is not None

    title, url, width, height, min_size = webview.created
    assert title == "aafton"
    assert url.startswith("http://127.0.0.1:")
    assert (width, height, min_size) == (1280, 860, (960, 640))


def test_o_start_que_volta_encerra_o_servidor_e_tira_o_backend(fake):
    events, registered, server, webview = fake

    assert desktop.run_app(webview_module=webview) == 0

    assert "shutdown" in _names(events)
    assert "server_close" in _names(events)
    assert registered[-1] is None


def test_o_backend_mapeia_o_tipo_e_o_filtro_de_cada_dialogo(fake):
    events, registered, server, webview = fake
    desktop.run_app(webview_module=webview)
    backend = registered[0]
    window = webview.window

    backend("open_file", "AAF files", ["aaf", "als"], "Pick")
    kind, _, _, _, filters = window.asked[-1]
    assert (kind, filters) == (OPEN, ("AAF files (*.aaf;*.als)", "All files (*.*)"))

    backend("save_file", "AAF files", ["aaf"], "Pick")
    kind, _, _, name, filters = window.asked[-1]
    assert (kind, name) == (SAVE, "untitled.aaf")
    assert filters == ("AAF files (*.aaf)", "All files (*.*)")

    backend("folder", "All files", [], "Pick")
    kind, _, _, _, filters = window.asked[-1]
    assert (kind, filters) == (FOLDER, ())

    backend("open_file", "All files", [], "Pick")
    assert window.asked[-1][4] == ("All files (*.*)",)


def test_o_backend_normaliza_a_resposta_do_dialogo(fake):
    events, registered, server, webview = fake
    desktop.run_app(webview_module=webview)
    backend = registered[0]
    window = webview.window

    window.answer = ("C:\\cenas\\EP02.aaf", "C:\\cenas\\EP03.aaf")
    assert backend("open_file", "AAF files", ["aaf"], "Pick") == "C:\\cenas\\EP02.aaf"

    window.answer = "C:\\cenas\\EP04.aaf"
    assert backend("open_file", "AAF files", ["aaf"], "Pick") == "C:\\cenas\\EP04.aaf"

    window.answer = None
    assert backend("open_file", "AAF files", ["aaf"], "Pick") is None


def test_o_backend_espera_a_janela_e_nao_abre_o_dialogo_quando_ela_nao_aparece(fake):
    events, registered, server, webview = fake
    webview.window = FakeWindow(shown=False)
    desktop.run_app(webview_module=webview)
    backend = registered[0]

    with pytest.raises(dialogs.DialogError, match="the window did not open"):
        backend("open_file", "AAF files", ["aaf"], "Pick")
    assert webview.window.timeout == 10
    assert webview.window.asked == []


def test_sem_pywebview_o_app_diz_o_que_instalar(fake, capsys, monkeypatch):
    events, registered, server, webview = fake
    monkeypatch.setitem(sys.modules, "webview", None)

    assert desktop.run_app() == 2

    err = capsys.readouterr().err
    assert 'aafton app needs the desktop extra: pip install "aafton[desktop]"' in err


def test_uma_janela_que_falha_diz_o_que_fazer(fake, capsys):
    events, registered, server, webview = fake
    webview.failure = RuntimeError("no display")

    assert desktop.run_app(webview_module=webview) == 2

    err = capsys.readouterr().err
    assert "the window could not start: no display" in err
    assert "Microsoft Edge WebView2 Runtime" in err


def test_o_smoke_sobe_o_servidor_real_e_imprime_a_versao(capsys):
    assert desktop.run_app(smoke=True) == 0

    out = capsys.readouterr().out
    assert out == f"aafton {__version__} ok\n"


def test_o_comando_app_chama_o_run_app_com_o_smoke(monkeypatch):
    chamadas: list = []

    def falso(smoke: bool = False, webview_module=None) -> int:
        chamadas.append(smoke)
        return 0

    monkeypatch.setattr(desktop, "run_app", falso)

    assert main(["app", "--smoke"]) == 0
    assert chamadas == [True]
