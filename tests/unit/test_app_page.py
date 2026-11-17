"""Testes da pagina do app: os scripts entram na ordem e falam dos slots do desenho."""

from __future__ import annotations

import re
from importlib import resources

from aafton.studio.page import load_page

# The scripts of the page, in the order they are joined into one script.
SCRIPTS = ("app-core.js", "app-sheet.js", "app-flow.js")

# The texts that open and close the script part of the page.
SCRIPT_OPEN = "<script>"
SCRIPT_CLOSE = "</script>"

# The names a script gives to a slot and to an action.
SLOT_SELECTOR = re.compile(r'\[data-f="([a-z0-9_-]+)"\]')
ACT_SELECTOR = re.compile(r'\[data-act="([a-z0-9_-]+)"\]')
SLOT_CALL = re.compile(r'App\.(?:slot|fill)\(\s*[^,]+,\s*"([a-z0-9_-]+)"')

# The first argument of a call of App.$ or App.$$: the root it looks in.
HELPER_CALL = re.compile(r"App\.\$\$?\(\s*([^,)]*)")


def _static(name: str) -> str:
    """The text of a file of the static folder of the studio."""
    return (resources.files("aafton.studio") / "static" / name).read_text(encoding="utf-8")


def _script_part(page: str) -> str:
    """The text of the script that the page carries."""
    start = page.index(SCRIPT_OPEN) + len(SCRIPT_OPEN)
    end = page.index(SCRIPT_CLOSE, start)
    return page[start:end]


def test_load_page_leaves_no_marker():
    page = load_page()
    assert "/*__STUDIO_CSS__*/" not in page
    assert "/*__STUDIO_JS__*/" not in page
    assert ":root{" in page


def test_load_page_carries_the_three_scripts_in_order():
    script = _script_part(load_page())
    core, sheet, flow = (_static(name) for name in SCRIPTS)
    assert core in script
    assert sheet in script
    assert flow in script
    assert script.index(core) < script.index(sheet) < script.index(flow)
    assert "const App = {}" in script
    assert script.rstrip().endswith("App.start();")


def test_the_script_builds_no_markup_and_sets_no_style():
    script = _script_part(load_page())
    assert "innerHTML" not in script
    assert "outerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    assert ".style" not in script
    assert "console.log" not in script


def test_every_slot_of_the_scripts_exists_in_the_markup():
    html = _static("index.html")
    script = _script_part(load_page())
    names = set(SLOT_SELECTOR.findall(script)) | set(SLOT_CALL.findall(script))
    assert names
    for name in names:
        assert f'data-f="{name}"' in html, name


def test_every_action_of_the_scripts_exists_in_the_markup():
    html = _static("index.html")
    script = _script_part(load_page())
    names = set(ACT_SELECTOR.findall(script))
    assert names
    for name in names:
        assert f'data-act="{name}"' in html, name


def test_the_helpers_of_the_scripts_look_in_a_root():
    script = _script_part(load_page())
    roots = HELPER_CALL.findall(script)
    assert roots
    for root in roots:
        root = root.strip()
        assert root, "a call of App.$ or App.$$ has no root"
        assert root[0] not in "\"'`", root


def test_the_script_carries_the_selection_of_the_live_to_aaf_sheet():
    script = _script_part(load_page())
    names = set(SLOT_SELECTOR.findall(script)) | set(SLOT_CALL.findall(script))
    assert "App.syncSelection" in script
    for name in ("tracks-summary", "pick-all", "guide-list"):
        assert name in names, name


def test_the_script_names_the_hint_of_the_edl_block():
    """O EDL so e lido depois da taxa: o script precisa do aviso do bloco."""
    script = _script_part(load_page())
    names = set(SLOT_SELECTOR.findall(script)) | set(SLOT_CALL.findall(script))
    assert "edl-hint" in names
    assert "edl-rate-wrap" in names


def test_every_script_is_short_and_declares_one_name_only():
    for name in SCRIPTS:
        text = _static(name)
        assert len(text.splitlines()) < 400, name
        declared = [line for line in text.splitlines() if line.startswith("const ")]
        if name == "app-core.js":
            assert declared == ["const App = {};"], name
        else:
            assert not declared, name


def test_the_script_has_no_autosize_and_shows_the_tail_of_the_paths():
    script = _script_part(load_page())
    assert "autosize" not in script
    assert "App.showTail" in script


def _body(script_name: str, opening: str) -> list[str]:
    """As linhas do corpo de uma funcao do script, sem brancos nas pontas."""
    lines = _static(script_name).splitlines()
    start = lines.index(opening)
    body = []
    for line in lines[start + 1 :]:
        if line == "};":
            break
        body.append(line.strip())
    return body


def test_the_path_fields_show_their_tail_again_when_their_width_changes():
    """Um observador so reaplica o fim de todo campo de caminho que muda de largura."""
    body = _body("app-core.js", "App.wirePaths = (p) => {")
    guard = 'if (typeof ResizeObserver === "undefined") return;'
    assert guard in body
    assert body.index(guard) > body.index('p.el.addEventListener("focusout", (event) => {')
    assert "const watcher = new ResizeObserver((entries) => {" in body
    assert "if (document.activeElement !== entry.target) App.showTail(entry.target);" in body
    assert 'App.$$(p.el, "input.path").forEach((input) => watcher.observe(input));' in body
    assert "App.showTail" not in "\n".join(_body("app-flow.js", "App.read = async (p, force) => {"))


def test_every_browse_passes_the_panel_and_a_failure_is_shown():
    """O dialogo que falha chega ao painel; o cancelamento nao mostra nada."""
    core = _static("app-core.js")
    flow = _static("app-flow.js")
    assert "App.browse = async (p, kind, filter, title) => {" in core
    assert "App.reportFailure = (p, res) => {" in core
    assert "App.reportFailure = " not in flow
    calls = re.findall(r"App\.browse\(([^)]*)\)", core + flow)
    assert len(calls) == 3
    assert all(call.startswith("p, ") for call in calls)
    assert 'App.browse(p, "save_file", "report", "Save report")' in flow
    body = _body("app-core.js", "App.browse = async (p, kind, filter, title) => {")
    assert body.index('const box = App.slot(p.el, "convert-error");') < body.index(
        'const res = await App.api("POST", "/api/app/browse", {'
    )
    assert "if (box) box.hidden = true;" in body
    assert "App.reportFailure(p, res);" in body


def test_the_script_writes_no_backslash_x_escape():
    script = _script_part(load_page())
    assert "\\x" not in script
    assert script.isascii()


def test_live_to_aaf_has_start_fields_and_leave_out_hidden_when_empty():
    html = _static("index.html")
    start = html.index('id="panel-to-aaf"')
    end = html.index('id="panel-pa"')
    panel = html[start:end]
    script = _script_part(load_page())

    assert 'data-f="facts"' in panel
    assert '<div class="start-wrap" data-f="start-wrap" hidden>' in panel
    assert 'data-f="start_tc"' in panel
    assert 'value="01:00:00:00"' in panel
    for fps in ("23.976", "24", "25", "29.97", "30"):
        assert f'name="b-fps" value="{fps}"' in panel
    assert '<input type="radio" name="b-fps" value="24" checked>' in panel
    assert 'data-f="guide-text"' in panel
    assert (
        '<button class="btn btn-quiet" type="button" data-act="leave-out-render" hidden>'
        "Leave them out</button>"
    ) in panel
    assert "App.syncStart = (p, data) => {" in script
    assert "wrap.hidden = !start;" in script
    assert "leave.hidden = !canLeave;" in script
    assert "App.updateRuler = (p) => {" in script
    assert "App.applyLeaveOut = (p) => {" in script


def test_the_collect_media_tab_panel_and_export_checkboxes_are_in_the_page():
    """A pagina servida traz a aba Collect media, o painel e as duas caixas no export."""
    page = load_page()
    assert 'id="tab-collect"' in page
    assert "Collect media</button>" in page
    assert 'id="panel-collect"' in page
    assert "The Live set or the linked AAF to collect. It is only read, never changed." in page
    assert "Every file the session uses, and what happens to it." in page
    assert "The folder gets the session copy and its Media/ folder. It must be empty." in page
    assert "<span>Include picture</span>" in page
    assert "<span>Collect media next to the set</span>" in page
    assert "Copies every file the set uses into &lt;set name&gt; Media/ beside it." in page
    assert "<span>Collect media next to the AAF</span>" in page
    assert (
        "Copies every linked file into &lt;AAF name&gt; Media/ beside it. "
        "Only with Format AAF and Link files."
    ) in page
