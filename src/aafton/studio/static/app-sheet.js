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
    const row = App.tpl(withPick ? "tpl-track-pick" : "tpl-track");
    const index = Number.isInteger(track.index) ? track.index : position;
    row.dataset.index = String(index);
    App.fill(row, "n", String(index + 1).padStart(2, "0"));
    App.fill(row, "name", track.name || "");
    App.fill(row, "kind", track.kind || "");
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
  const detail = App.tpl("tpl-detail");
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
  if (rows && clips.length) {
    for (const clip of clips) {
      const line = App.tpl("tpl-clip");
      App.fill(line, "name", clip.name || "");
      App.fill(line, "in", clip.in || "");
      App.fill(line, "out", clip.out || "");
      App.fill(line, "gain", clip.gain || "");
      App.fill(line, "fades", clip.fades || "");
      rows.appendChild(line);
    }
    if (Number(track.clips_more || 0) > 0) {
      const more = App.tpl("tpl-clip");
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

// The bars of a lane: one per span of the track, over the timeline.
App.paintLanes = (p) => {
  const data = p.data || {};
  const timeline = data.timeline || {};
  const t0 = Number(timeline.start_samples);
  const t1 = Number(timeline.end_samples);
  const length = t1 - t0;
  for (const lane of p.lanes) {
    const canvas = lane.canvas;
    if (!canvas) continue;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) continue;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    App.paintTone(p, lane, canvas);
    if (!w || !h || !(length > 0)) continue;
    const k = w / length;
    ctx.fillStyle = getComputedStyle(canvas).color;
    for (const pair of lane.track.spans || []) {
      const a = Number(pair[0]);
      const b = Number(pair[1]);
      ctx.fillRect(Math.floor((a - t0) * k), 3, Math.max(1, Math.round((b - a) * k) - 1), h - 6);
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

App.renderGuide = (p, guide) => {
  const names = guide || [];
  const block = App.slot(p.el, "guide");
  const list = App.slot(p.el, "guide-list");
  if (list) list.textContent = "";
  if (block) block.hidden = names.length === 0;
  for (const name of names) {
    const item = App.tpl("tpl-item");
    App.fill(item, "text", name);
    if (list) list.appendChild(item);
  }
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
