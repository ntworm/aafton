// The rendering of the sheet: the facts, the tracks, the lanes, the notes
// and the verdict. Every piece of markup comes from a template of the page.

// --- the facts of the source ---------------------------------------------

App.renderFacts = (p, facts) => {
  const list = App.slot(p.el, "facts");
  if (!list) return;
  list.textContent = "";
  for (const fact of facts || []) {
    const node = App.tpl("tpl-fact");
    App.fill(node, "k", fact.k || "");
    App.fill(node, "v", fact.v || "");
    const sub = App.slot(node, "sub");
    if (fact.sub) App.fill(node, "sub", fact.sub);
    else if (sub) sub.remove();
    if (fact.st) node.dataset.st = fact.st;
    list.appendChild(node);
  }
};

// --- the track sheet -----------------------------------------------------

App.renderTracks = (p) => {
  const data = p.data || {};
  const tracks = data.tracks || [];
  const timeline = data.timeline || {};
  const body = App.slot(p.el, "track-rows");
  if (!body) return;
  body.textContent = "";
  p.open = null;
  p.lanes = [];
  const withPick = p.direction === "to_aaf";
  tracks.forEach((track, position) => {
    const row = App.tpl(withPick ? "tpl-track-pick" : App.tplFor(p, "tpl-track"));
    const index = Number.isInteger(track.index) ? track.index : position;
    row.dataset.index = String(index);
    App.fill(row, "n", String(index + 1).padStart(2, "0"));
    App.markSibling(row, track);
    App.fill(row, "name", track.name || "");
    App.fill(row, "kind", track.kind || "");
    App.fill(row, "takes", String(track.takes || 0));
    App.fill(row, "clips", String(track.clips || 0));
    const state = App.slot(row, "state");
    if (state) {
      state.textContent = track.state && track.state.label ? track.state.label : "";
      if (track.state && track.state.st) state.dataset.st = track.state.st;
    }
    if (withPick) {
      const box = App.slot(row, "pick");
      if (box) {
        box.checked = track.pick === true;
        box.disabled = track.pick === null || track.pick === undefined;
        if (box.disabled) row.setAttribute("aria-disabled", "true");
      }
    }
    const canvas = App.slot(row, "lane");
    body.appendChild(row);
    p.lanes.push({ canvas: canvas, track: track, row: row });
  });
  App.fill(p.el, "tc-start", timeline.start_tc || "");
  App.fill(p.el, "tc-end", timeline.end_tc || "");
  if (data.blocks) App.renderDays(p, data.blocks);
  App.syncSiblingFoot(p, tracks);
  App.paintLanes(p);
};

// One row of the sheet opens the row below it; only one is open at a time.
App.toggleRow = (p, index) => {
  const lane = p.lanes.find((item) => Number(item.row.dataset.index) === Number(index));
  if (!lane) return;
  if (p.open === Number(index)) {
    App.closeRow(p, lane);
  } else {
    const open = p.lanes.find((item) => Number(item.row.dataset.index) === p.open);
    if (open) App.closeRow(p, open);
    App.openRow(p, lane);
  }
  App.paintLanes(p);
};

App.closeRow = (p, lane) => {
  const detail = lane.row.nextElementSibling;
  if (detail && detail.classList.contains("detail")) detail.remove();
  lane.row.classList.remove("open");
  const name = App.$(lane.row, ".name");
  if (name) name.setAttribute("aria-expanded", "false");
  if (p.open === Number(lane.row.dataset.index)) p.open = null;
};

App.openRow = (p, lane) => {
  const track = lane.track || {};
  const detail = App.tpl(App.tplFor(p, "tpl-detail"));
  const table = App.slot(p.el, "tracks");
  const head = table ? App.$(table, "thead tr") : null;
  const cell = detail.firstElementChild;
  if (cell) cell.colSpan = head ? head.children.length : 1;
  const reasons = App.slot(detail, "reasons");
  const list = track.reasons || [];
  if (reasons && list.length) {
    reasons.hidden = false;
    for (const text of list) {
      const item = App.tpl("tpl-item");
      App.fill(item, "text", text);
      reasons.appendChild(item);
    }
  }
  const rows = App.slot(detail, "clip-rows");
  const clips = track.clip_rows || [];
  const clipTpl = App.tplFor(p, "tpl-clip");
  if (rows && clips.length) {
    for (const clip of clips) {
      // Each field of the clip fills the slot of the same name.
      const line = App.tpl(clipTpl);
      for (const key of Object.keys(clip)) App.fill(line, key, clip[key] || "");
      rows.appendChild(line);
    }
    if (Number(track.clips_more || 0) > 0) {
      const more = App.tpl(clipTpl);
      App.fill(more, "name", "+ " + track.clips_more + " more clips");
      rows.appendChild(more);
    }
  } else {
    const wrap = App.slot(detail, "clips-wrap");
    if (wrap) wrap.remove();
  }
  lane.row.after(detail);
  lane.row.classList.add("open");
  const name = App.$(lane.row, ".name");
  if (name) name.setAttribute("aria-expanded", "true");
  p.open = Number(lane.row.dataset.index);
};

// --- the lanes -----------------------------------------------------------

// The tone of a lane: off for a row that is disabled or not selected, open
// for the row that is open, and nothing for a normal row.
App.paintTone = (p, lane, canvas) => {
  const track = lane.track || {};
  const state = track.state || {};
  const box = App.slot(lane.row, "pick");
  let tone = "";
  if (state.st === "off") tone = "off";
  else if (box && (track.pick === null || track.pick === undefined || !box.checked)) tone = "off";
  else if (p.open === Number(lane.row.dataset.index)) tone = "open";
  if (tone) canvas.dataset.tone = tone;
  else canvas.removeAttribute("data-tone");
};

// A lane cleared at the size of its canvas, with its tone; null when there
// is nothing to draw on.
App.prepareLane = (p, lane) => {
  const canvas = lane.canvas;
  if (!canvas) return null;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);
  App.paintTone(p, lane, canvas);
  if (!w || !h) return null;
  return { canvas: canvas, ctx: ctx, w: w, h: h };
};

// The bars of a lane: one per span of the track, over the timeline. A
// sheet of shooting days draws its lanes day by day.
App.paintLanes = (p) => {
  const data = p.data || {};
  if (data.blocks) {
    App.paintDays(p, data.blocks);
    return;
  }
  const timeline = data.timeline || {};
  const t0 = Number(timeline.start_samples);
  const t1 = Number(timeline.end_samples);
  const length = t1 - t0;
  for (const lane of p.lanes) {
    const view = App.prepareLane(p, lane);
    if (!view || !(length > 0)) continue;
    const k = view.w / length;
    view.ctx.fillStyle = getComputedStyle(view.canvas).color;
    for (const pair of lane.track.spans || []) {
      const a = Number(pair[0]);
      const b = Number(pair[1]);
      view.ctx.fillRect(Math.floor((a - t0) * k), 3, Math.max(1, Math.round((b - a) * k) - 1), view.h - 6);
    }
  }
};

// One observer per sheet: the lanes follow the width of the table.
App.observers = {};

App.observeLanes = (p) => {
  const body = App.slot(p.el, "track-rows");
  if (!body || typeof ResizeObserver === "undefined") return;
  const observer = new ResizeObserver(() => App.paintLanes(p));
  observer.observe(body);
  App.observers[p.direction] = observer;
};

// --- the notes of the engine ---------------------------------------------

App.appendNote = (list, note) => {
  if (!list) return;
  const node = App.tpl("tpl-note");
  node.dataset.sev = note.sev || "";
  App.fill(node, "sev", note.sev || "");
  App.fill(node, "code", note.code || "");
  const count = Number(note.count || 0);
  App.fill(node, "count", count > 1 ? "\u00d7" + count : "");
  App.fill(node, "text", note.text || "");
  list.appendChild(node);
};

App.renderNotes = (p, notes) => {
  const data = notes || {};
  const main = data.main || [];
  const info = data.info || [];
  const total = Number(data.info_total || 0);
  const list = App.slot(p.el, "notes");
  const infoList = App.slot(p.el, "notes-info");
  if (list) list.textContent = "";
  if (infoList) infoList.textContent = "";
  for (const note of main) App.appendNote(list, note);
  for (const note of info) App.appendNote(infoList, note);
  const block = App.slot(p.el, "notes-block");
  if (block) block.hidden = main.length === 0 && total === 0;
  const wrap = App.slot(p.el, "notes-info-wrap");
  if (wrap) wrap.hidden = total === 0;
  const count = App.slot(p.el, "notes-info-count");
  if (count) {
    count.textContent = total === 1 ? "1 informational note" : total + " informational notes";
  }
};

// --- the guide of the Live to AAF sheet ----------------------------------

App.renderGuide = (p, guide, data) => {
  const names = guide || [];
  const block = App.slot(p.el, "guide");
  const list = App.slot(p.el, "guide-list");
  const text = App.slot(p.el, "guide-text");
  const leave = App.$(p.el, '[data-act="leave-out-render"]');
  const canLeave = Boolean(data && data.can_leave_out);
  if (list) list.textContent = "";
  if (block) block.hidden = names.length === 0;
  if (text) {
    text.textContent = canLeave
      ? "These clips are warped, and an AAF plays audio at its own speed. In Live, select them and Consolidate (Ctrl+J), or freeze and flatten the track, save the set, then read it again."
      : "These tracks hold nothing an AAF can carry as they are. In Live, freeze and flatten them, save the set, then read it again.";
  }
  if (leave) {
    leave.hidden = !canLeave;
    leave.disabled = false;
  }
  for (const name of names) {
    const item = App.tpl("tpl-item");
    App.fill(item, "text", name);
    if (list) list.appendChild(item);
  }
};

App.FPS_RATES = {
  "23.976": { num: 24000, den: 1001, nom: 24 },
  "24": { num: 24, den: 1, nom: 24 },
  "25": { num: 25, den: 1, nom: 25 },
  "29.97": { num: 30000, den: 1001, nom: 30 },
  "30": { num: 30, den: 1, nom: 30 },
};

App.parseTc = (text, nom) => {
  const m = /^(\d{2,3}):([0-5]\d):([0-5]\d)[:;](\d{2})$/.exec((text || "").trim());
  if (!m) return null;
  const ff = Number(m[4]);
  if (ff >= nom) return null;
  return ((Number(m[1]) * 60 + Number(m[2])) * 60 + Number(m[3])) * nom + ff;
};

App.formatTc = (frames, nom) => {
  const total = Math.max(0, frames);
  const ff = total % nom;
  const secs = Math.floor(total / nom);
  const ss = secs % 60;
  const mins = Math.floor(secs / 60);
  const mm = mins % 60;
  const hh = Math.floor(mins / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return pad(hh) + ":" + pad(mm) + ":" + pad(ss) + ":" + pad(ff);
};

// Recalculates the ruler endpoints client-side when Start timecode or Frame rate changes.
App.updateRuler = (p) => {
  const data = p.data || {};
  if (!data.start || !data.timeline) return;
  const spec = App.FPS_RATES[App.radio(p.el, "b-fps") || "24"] || App.FPS_RATES["24"];
  const base = App.parseTc(App.text(p.el, "start_tc"), spec.nom);
  if (base === null) return;
  const sr = Number(data.sample_rate || 48000);
  const origin = Number(data.origin_samples || 0);
  const labelAt = (samples) => {
    const rel = Number(samples || 0) - origin;
    return App.formatTc(base + Math.round((rel * spec.num) / (sr * spec.den)), spec.nom);
  };
  App.fill(p.el, "tc-start", labelAt(data.timeline.start_samples));
  App.fill(p.el, "tc-end", labelAt(data.timeline.end_samples));
};

// Marks tracks holding render-only clips so exact clips can export without another read.
App.applyLeaveOut = (p) => {
  p.leaveOutRender = true;
  const leave = App.$(p.el, '[data-act="leave-out-render"]');
  if (leave) leave.disabled = true;
  const text = App.slot(p.el, "guide-text");
  if (text) text.textContent = "Warped or looped clips will be left out of the export.";
  for (const lane of p.lanes || []) {
    const track = lane.track || {};
    const renderCount = Number(track.render_clips || 0);
    if (!renderCount || track.mixed_channels) continue;
    const exact = Math.max(0, Number(track.clips || 0) - renderCount);
    const box = App.slot(lane.row, "pick");
    const stateEl = App.slot(lane.row, "state");
    if (exact > 0) {
      track.pick = true;
      if (box) {
        box.disabled = false;
        box.checked = true;
      }
      lane.row.removeAttribute("aria-disabled");
      track.state = { st: "warn", label: renderCount + " left out" };
    } else {
      track.pick = null;
      if (box) {
        box.disabled = true;
        box.checked = false;
      }
      lane.row.setAttribute("aria-disabled", "true");
      track.state = { st: "off", label: "left out" };
    }
    if (stateEl) {
      stateEl.textContent = track.state.label;
      stateEl.dataset.st = track.state.st;
    }
  }
  App.syncSelection(p);
  App.stale(p);
};

// --- the verdict of the conversion --------------------------------------

App.renderVerdict = (p) => {
  const result = p.result;
  const box = App.slot(p.el, "verdict");
  const detail = App.slot(p.el, "verdict-detail");
  if (detail) detail.textContent = "";
  if (!result) {
    if (box) box.removeAttribute("data-state");
    App.fill(p.el, "verdict-word", "");
    return;
  }
  const verdict = result.verdict || {};
  if (box) box.dataset.state = verdict.state || "";
  App.fill(p.el, "verdict-word", verdict.word || "");
  if (detail) {
    for (const line of verdict.detail || []) {
      const node = App.tpl("tpl-line");
      App.fill(node, "text", line);
      detail.appendChild(node);
    }
  }
  const open = App.$(p.el, '[data-act="open-output"]');
  if (open) open.hidden = !result.written;
  const reveal = App.$(p.el, '[data-act="reveal-output"]');
  if (reveal) reveal.hidden = !result.written;
  const force = App.$(p.el, '[data-act="convert-permissive"]');
  if (force) force.hidden = !result.can_force;
};

// --- the media of the AAF to Live read -------------------------------------

// The media that was not found, and the files the engine found beside the
// source.
App.renderMedia = (p, media) => {
  const data = media || {};
  const box = App.slot(p.el, "media-missing");
  if (box) box.hidden = !(Number(data.missing || 0) > 0);
  App.fill(p.el, "media-missing-text", data.missing_text || "");
};

App.renderSuggestions = (p, suggest) => {
  const data = suggest || {};
  const video = App.slot(p.el, "video");
  if (video) video.placeholder = App.placeholder(p, "video", data.video);
  const text = App.slot(p.el, "session_text");
  if (text) text.placeholder = App.placeholder(p, "session_text", data.session_text);
};
