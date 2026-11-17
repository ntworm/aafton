"""Tests for the P5b session organization controls and sheet markup in Studio."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

from aafton.studio import app_convert, page


class _InputCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str | bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        mapping = dict(attrs)
        entry: dict[str, str | bool] = {
            "type": mapping.get("type") or "",
            "name": mapping.get("name") or "",
            "value": mapping.get("value") or "",
            "data-sig": mapping.get("data-sig") or "",
            "checked": "checked" in mapping,
        }
        self.inputs.append(entry)


def _collect_inputs(html: str) -> list[dict[str, str | bool]]:
    collector = _InputCollector()
    collector.feed(html)
    return collector.inputs


def _static_dir() -> Path:
    return Path(page.__file__).with_name("static")


def test_page_scripts_include_organize_before_flow() -> None:
    assert "app-organize.js" in page.SCRIPTS
    org_idx = page.SCRIPTS.index("app-organize.js")
    flow_idx = page.SCRIPTS.index("app-flow.js")
    assert org_idx < flow_idx

    html = page.load_page()
    assert "App.fetchOrganize" in html
    assert "/api/app/organize" in html
    assert "App.renderOrganizedSheet" in html
    assert html.index("App.fetchOrganize") < html.index("App.gather")


def test_organize_segment_and_certainty_defaults() -> None:
    inputs = _collect_inputs(page.load_page())

    org = [item for item in inputs if item["name"] == "a-organize"]
    assert [item["value"] for item in org] == ["off", "prefix", "type"]
    assert [item["value"] for item in org if item["checked"]] == ["off"]
    assert not [item for item in inputs if item["name"] == "a-groups"]

    cert = [item for item in inputs if item["name"] == "a-certainty"]
    cert_vals = [item["value"] for item in cert]
    assert cert_vals == ["permissive", "balanced", "strict"]
    assert [item["value"] for item in cert if item["checked"]] == ["permissive"]


def test_signals_view_switch_and_mock_texts() -> None:
    html = page.load_page()
    inputs = _collect_inputs(html)

    sigs = [item for item in inputs if item["data-sig"]]
    sig_names = [item["data-sig"] for item in sigs]
    assert sig_names == ["file", "meta", "name", "family", "color"]
    assert all(item["checked"] for item in sigs)

    views = [item for item in inputs if item["name"] == "a-view"]
    assert [item["value"] for item in views] == ["proposed", "sent"]
    assert [item["value"] for item in views if item["checked"]] == ["proposed"]

    assert 'data-f="cert-wrap" hidden' in html
    assert 'data-f="org-signals-wrap" hidden' in html
    assert 'data-f="org-view-switch" hidden' in html
    assert 'data-f="org-sum" hidden' in html
    assert "Grouped by file name" in html
    assert "Undo my edits" in html
    assert "Organize signals" in html
    assert "Organized" in html
    assert "As sent" in html


def test_without_by_type_convert_payload_matches_legacy() -> None:
    flow_js = (_static_dir() / "app-flow.js").read_text(encoding="utf-8")
    org_js = (_static_dir() / "app-organize.js").read_text(encoding="utf-8")

    start = flow_js.index("App.gather = (p) => {")
    end = flow_js.index("\n};", start)
    gather_fn = flow_js[start:end]

    expected_type_check = 'if (body.options.organize === "type") App.addOrganizeOptions(p'
    expected_groups = 'App.radio(p.el, "a-organize") === "prefix" ? "prefix" : "none"'
    expected_guard = 'if (App.radio(p.el, "a-organize") !== "type")'

    assert "groups: App.organizeGroups(p)" in gather_fn
    assert 'organize: App.radio(p.el, "a-organize") || "off"' in gather_fn
    assert expected_type_check in gather_fn
    assert expected_groups in org_js
    assert expected_guard in org_js

    off_opts = app_convert._opcoes_de_organizacao({"organize": "off", "groups": "none"})
    legacy_off = app_convert._opcoes_de_organizacao({"groups": "none"})
    assert off_opts == legacy_off
    assert off_opts["groups"] == "none"
    assert off_opts["organize"] == "off"

    prefix_payload = {"organize": "prefix", "groups": "prefix"}
    prefix_opts = app_convert._opcoes_de_organizacao(prefix_payload)
    legacy_prefix = app_convert._opcoes_de_organizacao({"groups": "prefix"})
    assert prefix_opts == legacy_prefix
    assert prefix_opts["groups"] == "prefix"
    assert prefix_opts["organize"] == "prefix"


def test_organize_edits_send_track_name_origins() -> None:
    org_js = (_static_dir() / "app-organize.js").read_text(encoding="utf-8")

    empty_start = org_js.index("App.emptyEdits = () => ({")
    empty_fn = org_js[empty_start : org_js.index("});", empty_start)]
    assert "track_name_origins: {}" in empty_fn

    ser_start = org_js.index("App.serializeEdits = (edits) => {")
    ser_fn = org_js[ser_start : org_js.index("\n};", ser_start)]
    assert "track_name_origins: Object.assign({}, edits.track_name_origins)" in ser_fn

    rename_start = org_js.index("if (ds.orgTrackKey) {")
    rename_fn = org_js[rename_start : org_js.index("\n  }", rename_start)]
    assert "App.orgAcceptedOrigins(st.proposal, key)" in rename_fn
    assert "st.edits.track_names[key] = clean" in rename_fn
    assert "st.edits.track_name_origins[key] = anchor" in rename_fn
    assert "delete st.edits.track_names[key]" in rename_fn
    assert "delete st.edits.track_name_origins[key]" in rename_fn
    assert "delete st.edits.track_name_origins[other]" in rename_fn

    # Apagar o nome tambem apaga o nome estacionado que cairia na track.
    else_start = org_js.index("} else {", rename_start)
    else_fn = org_js[else_start : rename_start + len(rename_fn)]
    assert "App.PARKED_PREFIX" in else_fn
    assert "other.indexOf(App.PARKED_PREFIX) !== 0" in else_fn
    assert "App.orgPairsOverlap(App.orgNameAnchor(st, other), base)" in else_fn
    assert "delete st.edits.track_names[other]" in else_fn
    assert "delete st.edits.track_name_origins[other]" in else_fn
    assert "delete st.edits.track_names[key]" in else_fn
    assert "delete st.edits.track_name_origins[key]" in else_fn
