"use strict";

// The one object the three scripts share: app-core.js declares it, the
// other two only add properties to it.

const App = {};

// --- helpers -------------------------------------------------------------

App.$ = (root, sel) => root.querySelector(sel);
App.$$ = (root, sel) => Array.from(root.querySelectorAll(sel));
App.slot = (root, name) => root.querySelector(`[data-f="${name}"]`);
App.tpl = (id) => document.getElementById(id).content.firstElementChild.cloneNode(true);

// The template of a panel: its own, "<id>-<direction>", when the page has
// one, the shared one otherwise.
App.tplFor = (p, id) => {
  const own = id + "-" + p.direction;
  return document.getElementById(own) ? own : id;
};

App.fill = (root, name, text) => {
  const sel = `[data-f="${name}"]`;
  const node = root.nodeType === 1 && root.matches(sel) ? root : root.querySelector(sel);
  if (node) node.textContent = text === null || text === undefined ? "" : String(text);
  return node;
};

// The name of a path, without its folders.
App.fileName = (path) => {
  const parts = String(path || "").split(/[\\/]/);
  return parts[parts.length - 1] || "";
};

// A path with the suffix of a format of the Live to AAF sheet.
App.withFormat = (path, format) => {
  const base = String(path || "").replace(/\.(aaf|edl|xml|fcpxml)$/i, "");
  return base ? base + "." + format : "";
};

// True when the path names an EDL file.
App.isEdl = (value) => /\.edl$/i.test(String(value || "").trim());

// The radio of a group that is checked, as text.
App.radio = (root, name) => {
  const el = root.querySelector(`input[name="${name}"]:checked`);
  return el ? el.value : "";
};

// Every radio of a group, as a list.
App.radios = (root, name) => Array.from(root.querySelectorAll(`input[name="${name}"]`));

// The value of a text field, trimmed.
App.text = (root, field) => {
  const el = App.slot(root, field);
  return el ? el.value.trim() : "";
};

// The state of a checkbox.
App.checked = (root, field) => {
  const el = App.slot(root, field);
  return Boolean(el && el.checked);
};

// --- the API client ------------------------------------------------------

App.api = async (method, path, body) => {
  const init = { method: method };
  if (method === "POST") {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body || {});
  }
  try {
    const response = await fetch(path, init);
    return await response.json();
  } catch (err) {
    return { ok: false, error: { code: "network", message: "The aafton server did not answer." } };
  }
};

// The path a save dialog starts from: the destination field, or the
// destination the engine suggested when the field is empty. The report is
// offered beside the destination, with its name and the .md suffix.
App.saveInitial = (p, filter) => {
  const typed = App.text(p.el, "output");
  const suggest = (p.data && p.data.suggest) || {};
  let dest = typed || suggest.output || "";
  if (dest && !typed && p.direction === "to_aaf") {
    dest = App.withFormat(dest, App.radio(p.el, "b-format") || "aaf");
  }
  if (!dest) return "";
  if (filter === "report" && p.direction === "collect") return dest.replace(/[\\/]+$/, "") + ".md";
  if (filter === "report") return dest.replace(/\.[^.\\/]*$/, "") + ".md";
  return dest;
};

// The native dialog of the server; the path, or "" when it was cancelled.
// A dialog that failed shows its message in the panel. A save dialog opens
// on the path the panel computes for this call.
App.browse = async (p, kind, filter, title) => {
  const box = App.slot(p.el, "convert-error");
  if (box) box.hidden = true;
  const res = await App.api("POST", "/api/app/browse", {
    kind: kind,
    filter: filter,
    title: title,
    initial: kind === "save_file" ? App.saveInitial(p, filter) : "",
  });
  if (!res || !res.ok) {
    App.reportFailure(p, res);
    return "";
  }
  return typeof res.path === "string" ? res.path : "";
};

// A failed action shows its message in the panel.
App.reportFailure = (p, res) => {
  if (res && res.ok) return;
  const error = (res && res.error) || {};
  const box = App.slot(p.el, "convert-error");
  if (!box) return;
  box.hidden = false;
  box.textContent = error.message || "The action failed.";
};

// A path field shows the end of its value, the name of the file, when it
// is not being edited.
App.showTail = (input) => {
  if (!input) return;
  input.scrollLeft = input.scrollWidth;
  input.title = input.value;
};

// The path fields of a panel: the tail of the value is shown when they
// lose the focus and when their width changes (the page gains a scroll
// bar, the window is resized), unless the field is being edited; the
// placeholder they carry is read once.
App.wirePaths = (p) => {
  if (!p.el) return;
  p.placeholders = App.placeholdersOf(p.el);
  p.el.addEventListener("focusout", (event) => {
    const target = event.target;
    if (target && target.matches && target.matches("input.path")) App.showTail(target);
  });

  if (typeof ResizeObserver === "undefined") return;
  const watcher = new ResizeObserver((entries) => {
    entries.forEach((entry) => {
      if (document.activeElement !== entry.target) App.showTail(entry.target);
    });
  });
  App.$$(p.el, "input.path").forEach((input) => watcher.observe(input));
};

// The placeholder a field carries in the markup.
App.placeholdersOf = (el) => {
  const defaults = {};
  for (const field of ["video", "session_text"]) {
    const node = el ? el.querySelector(`[data-f="${field}"]`) : null;
    defaults[field] = node ? node.getAttribute("placeholder") || "" : "";
  }
  return defaults;
};

// The question of the overwrite belongs to one destination.
App.hideExists = (p) => {
  const exists = App.slot(p.el, "exists");
  if (exists) exists.hidden = true;
};

// The panel forgets the source it held.
App.clearRead = (p) => {
  p.data = null;
  p.result = null;
  p.leaveOutRender = false;
  App.renderGuide(p, []);
  App.syncStart(p, null);
  App.renderMedia(p, null);
};

// The panel forgets the source and stays empty; the read that may be
// running is no longer of interest.
App.resetRead = (p) => {
  p.seq += 1;
  App.clearRead(p);
  App.setPhase(p, "empty");
};

// The placeholder of a field: the file the server detected, or the text
// the markup carries.
App.placeholder = (p, field, path) => {
  if (!path) return (p.placeholders || {})[field] || "";
  return "Detected: " + App.fileName(path);
};

// --- the panels ----------------------------------------------------------

App.panelState = (direction) => ({
  el: document.querySelector(`[data-direction="${direction}"]`),
  direction: direction,
  phase: "empty",
  data: null,
  result: null,
  seq: 0,
  lastInput: "",
  outputTouched: false,
  open: null,
  lanes: [],
  leaveOutRender: false,
  placeholders: {},
});

App.panels = {
  to_live: App.panelState("to_live"),
  to_aaf: App.panelState("to_aaf"),
  production: App.panelState("production"),
  collect: App.panelState("collect"),
  reconform: App.panelState("reconform"),
};

// The hash of the URL that selects each direction.
App.hashes = { to_live: "#to-live", to_aaf: "#to-aaf", production: "#production", collect: "#collect", reconform: "#reconform" };

// The audio tracks of the Live to AAF sheet that are checked.
App.selectedTracks = (p) => {
  const chosen = [];
  for (const lane of p.lanes) {
    const box = App.slot(lane.row, "pick");
    if (!box || !box.checked) continue;
    const index = lane.track ? lane.track.audio_index : null;
    if (index !== null && index !== undefined) chosen.push(index);
  }
  return chosen;
};

// The rows of the Live to AAF sheet that the user can pick.
App.pickableLanes = (p) => (p.lanes || []).filter((lane) => {
  const track = lane.track || {};
  return track.pick !== null && track.pick !== undefined;
});

// The pick-all box checks or clears every row that can be picked.
App.pickAll = (p, checked) => {
  for (const lane of App.pickableLanes(p)) {
    const box = App.slot(lane.row, "pick");
    if (box) box.checked = checked;
  }
};

// The phase of a panel; the stylesheet hides what it does not show.
App.setPhase = (p, phase) => {
  p.phase = phase;
  p.el.dataset.phase = phase;
  const busy = phase === "working";
  App.$$(p.el, "fieldset.set").forEach((box) => { box.disabled = busy; });
  App.syncPrimary(p);
};

// The primary button, enabled only when the sheet can be converted.
App.syncPrimary = (p) => {
  const button = App.$(p.el, '[data-act="convert"]');
  if (!button) return;
  let enabled = p.phase === "ready" || p.phase === "done";
  if (enabled && p.direction === "to_aaf" && App.selectedTracks(p).length === 0) enabled = false;
  if (enabled && p.direction === "reconform" && !App.reconformCanRun(p)) enabled = false;
  button.disabled = !enabled;
};

// The EDL block is shown only for a source that is an EDL, and the hint of
// the block only while no frame rate is chosen.
App.syncEdl = (p) => {
  if (p.direction === "reconform") return App.syncReconformEdl(p);
  const input = App.slot(p.el, "input");
  const wrap = App.slot(p.el, "edl-rate-wrap");
  if (!input || !wrap) return;
  const isEdl = App.isEdl(input.value.trim());
  wrap.hidden = !isEdl;
  const hint = App.slot(p.el, "edl-hint");
  if (hint) hint.hidden = !isEdl || Boolean(App.radio(p.el, "a-edl-rate"));
};

// The format radio drives the suffix of the destination; only the AAF
// format carries the media of the tracks.
App.syncFormat = (p) => {
  if (!App.radios(p.el, "b-format").length) return;
  const format = App.radio(p.el, "b-format") || "aaf";
  const output = App.slot(p.el, "output");
  if (output && output.value.trim()) {
    output.value = App.withFormat(output.value.trim(), format);
    App.showTail(output);
  }
  const media = App.radios(p.el, "b-media");
  if (format !== "aaf") {
    const link = media.find((radio) => radio.value === "link");
    if (link) link.checked = true;
  }
  for (const radio of media) {
    radio.disabled = format !== "aaf";
  }
  App.syncAafCollect(p);
};

// Start timecode and frame rate exist only for a Live set without an aafton record.
App.syncStart = (p, data) => {
  p.leaveOutRender = false;
  const wrap = App.slot(p.el, "start-wrap");
  if (!wrap) return;
  const start = data && data.start;
  wrap.hidden = !start;
  if (!start) return;
  const tc = App.slot(p.el, "start_tc");
  if (tc) tc.value = start.start_tc || "01:00:00:00";
  const fps = String(start.frame_rate || "24");
  for (const radio of App.radios(p.el, "b-fps")) {
    radio.checked = radio.value === fps;
  }
};

// Options of the Live to AAF conversion, including start fields when shown.
App.aafOptions = (p) => {
  const format = App.radio(p.el, "b-format");
  const media = App.radio(p.el, "b-media");
  const opts = {
    media: media,
    format: format,
    mode: App.radio(p.el, "b-mode"),
    collect_media: format === "aaf" && media === "link" && App.checked(p.el, "collect_media"),
  };
  if (p.data && p.data.start) {
    opts.start_tc = App.text(p.el, "start_tc");
    opts.frame_rate = App.radio(p.el, "b-fps");
  }
  if (p.leaveOutRender) opts.leave_out_render = true;
  return opts;
};

// --- the tabs ------------------------------------------------------------

// The direction a tab opens.
App.directionOf = (tab) => {
  const panel = document.getElementById(tab.getAttribute("aria-controls"));
  return panel && panel.dataset.direction ? panel.dataset.direction : "to_live";
};

// Selecting a tab shows its panel and writes the hash of the direction.
App.selectTab = (direction, focus) => {
  const p = App.panels[direction];
  if (!p || !p.el) return;
  for (const tab of App.$$(document, '[role="tab"]')) {
    const on = tab.getAttribute("aria-controls") === p.el.id;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.setAttribute("tabindex", on ? "0" : "-1");
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) panel.hidden = !on;
    if (on && focus) tab.focus();
  }
  history.replaceState(null, "", App.hashes[direction] || "#to-live");
  App.paintLanes(p);
};

// Click selects; the arrow keys, Home and End move between the tabs.
App.wireTabs = () => {
  const list = document.querySelector('[role="tablist"]');
  if (!list) return;
  list.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target.closest('[role="tab"]') : null;
    if (!target) return;
    App.selectTab(App.directionOf(target), false);
  });
  list.addEventListener("keydown", (event) => {
    const target = event.target && event.target.closest ? event.target.closest('[role="tab"]') : null;
    if (!target) return;
    const tabs = App.$$(list, '[role="tab"]');
    const index = tabs.indexOf(target);
    if (index < 0) return;
    let next = -1;
    if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next < 0) return;
    event.preventDefault();
    App.selectTab(App.directionOf(tabs[next]), true);
  });
};

// --- what the server says about itself -----------------------------------

App.loadInfo = async () => {
  const res = await App.api("GET", "/api/app/info");
  if (!res || !res.ok) return;
  App.fill(document, "version", res.version || "");
  if (res.dialogs === false) {
    App.$$(document, '[data-act^="browse-"]').forEach((button) => {
      button.disabled = true;
    });
  }
};
