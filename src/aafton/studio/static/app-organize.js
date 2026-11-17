// Session organization proposal, view switch, sheet rendering and manual edits
// for the AAF to Live panel. Classification lives on the server; this script
// only renders the returned proposal and sends edits back.

App.ORGANIZE_HINTS = {
  permissive: "Moves a clip on one signal. Fast, check the list.",
  balanced: "Moves a clip when two signals agree.",
  strict: "Moves a clip only when every signal agrees.",
};

App.emptyEdits = () => ({
  groups: {},
  tracks: {},
  origins: {},
  group_names: {},
  track_names: {},
  track_name_origins: {},
});

App.orgState = (p) => {
  if (!p.org) {
    p.org = {
      edits: App.emptyEdits(),
      proposal: null,
      summary: null,
      view: "proposed",
      busy: false,
      error: "",
      inputPath: "",
      reqSeq: 0,
      open: new Set(),
      lanes: [],
    };
  }
  return p.org;
};

App.serializeEdits = (edits) => {
  const origins = Object.keys(edits.origins)
    .sort()
    .map((key) => {
      const item = edits.origins[key];
      return { origin: item.origin, accepted: Boolean(item.accepted) };
    });
  return {
    groups: Object.assign({}, edits.groups),
    tracks: Object.assign({}, edits.tracks),
    origins: origins,
    group_names: Object.assign({}, edits.group_names),
    track_names: Object.assign({}, edits.track_names),
    track_name_origins: Object.assign({}, edits.track_name_origins),
  };
};

App.organizeGroups = (p) => (App.radio(p.el, "a-organize") === "prefix" ? "prefix" : "none");

App.organizeSignals = (p) => {
  const active = [];
  for (const box of App.$$(p.el, "[data-sig]")) {
    if (box.checked && box.dataset.sig) active.push(box.dataset.sig);
  }
  return active;
};

App.organizeColorOn = (p) => {
  const box = App.$(p.el, '[data-sig="color"]');
  return box ? box.checked : true;
};

App.addOrganizeOptions = (p, opts) => {
  opts.certainty = App.radio(p.el, "a-certainty") || "permissive";
  opts.signals = App.organizeSignals(p);
  opts.edits = App.serializeEdits(App.orgState(p).edits);
};

App.resetOrgViewRadio = (p) => {
  const prop = App.$(p.el, 'input[name="a-view"][value="proposed"]');
  if (prop) prop.checked = true;
};

App.clearOrganize = (p) => {
  if (!p || p.direction !== "to_live") return;
  const st = App.orgState(p);
  st.reqSeq += 1;
  st.busy = false;
  st.error = "";
  st.proposal = null;
  st.summary = null;
  st.lanes = [];
  if (!p.lastInput) {
    st.inputPath = "";
    st.edits = App.emptyEdits();
    st.open.clear();
    st.view = "proposed";
    App.resetOrgViewRadio(p);
  }
  App.renderOrganizeView(p);
};

App.afterReadOrganize = (p, value, previous) => {
  if (!p || p.direction !== "to_live") return;
  const st = App.orgState(p);
  const changed = Boolean((previous && value !== previous) || (st.inputPath && st.inputPath !== value));
  if (changed) {
    st.edits = App.emptyEdits();
    st.open.clear();
    st.view = "proposed";
    App.resetOrgViewRadio(p);
  }
  st.inputPath = value || "";
  st.proposal = null;
  st.summary = null;
  st.error = "";
  st.lanes = [];
  if (App.radio(p.el, "a-organize") === "type") {
    App.fetchOrganize(p);
  } else {
    App.renderOrganizeView(p);
  }
};

App.syncOrganizeControls = (p) => {
  if (!p || p.direction !== "to_live" || !p.el) return;
  const isType = App.radio(p.el, "a-organize") === "type";
  const certainty = App.radio(p.el, "a-certainty") || "permissive";
  const certWrap = App.slot(p.el, "cert-wrap");
  const certHint = App.slot(p.el, "cert-hint");
  const sigWrap = App.slot(p.el, "org-signals-wrap");
  const orgTable = App.slot(p.el, "org-tracks");
  if (certWrap) certWrap.hidden = !isType;
  if (certHint) {
    certHint.hidden = !isType;
    certHint.textContent = App.ORGANIZE_HINTS[certainty] || App.ORGANIZE_HINTS.permissive;
  }
  if (sigWrap) sigWrap.hidden = !isType;
  if (orgTable) orgTable.dataset.colored = App.organizeColorOn(p) ? "1" : "0";
};

App.updateOrgSummary = (p, isType, hasProposal) => {
  const st = App.orgState(p);
  const box = App.slot(p.el, "org-sum");
  if (!box) return;
  const visible = Boolean(isType && p.data && (hasProposal || st.busy || st.error));
  box.hidden = !visible;
  const elProp = App.slot(p.el, "org-sum-proposed");
  const elSent = App.slot(p.el, "org-sum-sent");
  const elLoad = App.slot(p.el, "org-sum-loading");
  const elErr = App.slot(p.el, "org-sum-error");
  if (elProp) elProp.hidden = true;
  if (elSent) elSent.hidden = true;
  if (elLoad) elLoad.hidden = true;
  if (elErr) {
    elErr.hidden = true;
    elErr.textContent = "";
  }
  if (!visible) return;
  if (st.error) {
    if (elErr) {
      elErr.textContent = st.error;
      elErr.hidden = false;
    }
    return;
  }
  if (!hasProposal) {
    if (elLoad) elLoad.hidden = false;
    return;
  }
  if (st.view === "sent") {
    if (elSent) elSent.hidden = false;
    return;
  }
  const sum = st.summary || {};
  App.fill(p.el, "sum-tracks", String(sum.tracks || 0));
  App.fill(p.el, "sum-groups", String(sum.groups || 0));
  App.fill(p.el, "sum-moved", String(sum.moved || 0));
  App.fill(p.el, "sum-stay", String(sum.stay || 0));
  const edited = Number(sum.edited || 0);
  const wrap = App.slot(p.el, "sum-edited-wrap");
  if (wrap) wrap.hidden = edited <= 0;
  App.fill(p.el, "sum-edited", String(edited));
  if (elProp) elProp.hidden = false;
};

App.orgFromText = (units) => {
  const labels = [];
  for (const u of units || []) {
    const lbl = u.source_label || "";
    if (lbl && labels.indexOf(lbl) < 0) labels.push(lbl);
  }
  if (labels.length <= 3) return { text: labels.join(", "), title: labels.join(", ") };
  return {
    text: labels.slice(0, 3).join(", ") + " +" + (labels.length - 3),
    title: labels.join(", "),
  };
};

App.orgChannelLabel = (count) => {
  const n = Number(count || 1);
  if (n === 2) return "stereo";
  if (n > 2) return n + " ch";
  return "mono";
};

App.orgUnitClipCount = (units) => {
  let total = 0;
  for (const u of units || []) {
    const keys = u.origin_keys || [];
    total += keys.length > 0 ? keys.length : 1;
  }
  return total;
};

App.appendOrgClipRows = (p, body, units, trackExcluded) => {
  const origins = App.orgState(p).edits.origins;
  for (const u of units || []) {
    const row = App.tpl("tpl-org-clip");
    const on = Boolean(u.accepted);
    row.dataset.on = on ? "1" : "0";
    const pick = App.slot(row, "pick");
    const keys = u.origin_keys || [];
    if (pick) {
      pick.checked = on;
      if (trackExcluded || !u.kind || keys.length === 0) {
        pick.disabled = true;
      } else {
        pick.dataset.orgOrigins = JSON.stringify(keys);
      }
    }
    const nameEl = App.slot(row, "name");
    if (nameEl) {
      const isPair = u.channel_count === 2 && (u.source_tracks || []).length === 2;
      nameEl.textContent = (u.name || "") + (isPair ? " (L+R)" : "");
      nameEl.title = u.media_filename || u.name || "";
    }
    const score = Math.max(0, Math.min(3, Number(u.score || 0)));
    App.fill(row, "dots-on", "\u25cf".repeat(score));
    App.fill(row, "dots-off", "\u25cb".repeat(3 - score));
    App.fill(row, "why", (u.reasons || []).join(" \u00b7 "));
    App.fill(row, "from", u.source_label || "");
    const isEdited = keys.some((pair) =>
      Object.prototype.hasOwnProperty.call(origins, pair[0] + ":" + pair[1]),
    );
    const editedEl = App.slot(row, "edited");
    if (editedEl) editedEl.hidden = !isEdited;
    body.appendChild(row);
  }
};

App.appendOrgGroupRow = (body, g) => {
  const row = App.tpl("tpl-org-group");
  const sw = App.slot(row, "sw");
  const pick = App.slot(row, "pick");
  const glabel = App.slot(row, "glabel");
  const gname = App.slot(row, "gname");
  if (pick) {
    pick.checked = true;
    pick.dataset.orgGroup = g.key;
  }
  if (sw) sw.dataset.kind = g.is_family ? "family" : g.key;
  if (g.is_family) {
    if (glabel) glabel.hidden = true;
    if (gname) {
      gname.hidden = false;
      gname.value = g.label || "";
      gname.dataset.orgGroupKey = g.key;
    }
  } else {
    if (gname) gname.hidden = true;
    if (glabel) {
      glabel.hidden = false;
      glabel.textContent = g.label || "";
    }
  }
  let clips = 0;
  for (const t of g.tracks || []) clips += App.orgUnitClipCount(t.units);
  App.fill(row, "clips", String(clips));
  const count = (g.tracks || []).length;
  App.fill(row, "tracks-count", count + (count === 1 ? " track" : " tracks"));
  body.appendChild(row);
};

App.appendOrgUnsortedHeader = (body, leftTracks) => {
  const row = App.tpl("tpl-org-group");
  row.classList.add("row-asis");
  const pick = App.slot(row, "pick");
  const sw = App.slot(row, "sw");
  const glabel = App.slot(row, "glabel");
  const gname = App.slot(row, "gname");
  if (pick) pick.hidden = true;
  if (sw) sw.dataset.kind = "unsorted";
  if (gname) gname.hidden = true;
  if (glabel) {
    glabel.hidden = false;
    glabel.textContent = "NOT SORTED";
  }
  let clips = 0;
  for (const t of leftTracks) clips += App.orgUnitClipCount(t.units);
  App.fill(row, "clips", String(clips));
  App.fill(row, "tracks-count", "no type and no file with a similar name: left on the editor's track");
  body.appendChild(row);
};

App.appendOrgTrackRow = (p, body, item, kindAttr, isLeft) => {
  const st = App.orgState(p);
  const row = App.tpl("tpl-org-track");
  const isOpen = st.open.has(item.key);
  row.dataset.orgKey = item.key;
  if (isOpen) row.classList.add("open");
  const pick = App.slot(row, "pick");
  const sw = App.slot(row, "sw");
  const expand = App.slot(row, "expand");
  const tname = App.slot(row, "tname");
  if (expand) expand.setAttribute("aria-expanded", isOpen ? "true" : "false");
  if (isLeft) {
    if (pick) pick.hidden = true;
    if (tname) tname.hidden = true;
    App.fill(row, "label", item.name || "");
  } else {
    if (pick) {
      pick.checked = true;
      pick.dataset.orgTrack = item.key;
    }
    if (sw) sw.dataset.kind = kindAttr;
    App.fill(row, "label", "");
    if (tname) {
      tname.hidden = false;
      tname.value = item.name || "";
      tname.dataset.orgTrackKey = item.key;
    }
  }
  App.fill(row, "kind", App.orgChannelLabel(item.channel_count));
  App.fill(row, "clips", String((item.units || []).length));
  const fromCell = App.slot(row, "from");
  if (fromCell) {
    if (isLeft) {
      fromCell.textContent = item.excluded ? "excluded" : "as sent";
    } else {
      const fromInfo = App.orgFromText(item.units);
      fromCell.textContent = fromInfo.text;
      fromCell.title = fromInfo.title;
    }
  }
  const canvas = App.slot(row, "lane");
  if (canvas && kindAttr && !isLeft) canvas.dataset.kind = kindAttr;
  body.appendChild(row);
  st.lanes.push({ canvas: canvas, units: item.units || [], isLeft: isLeft });
  if (isOpen) App.appendOrgClipRows(p, body, item.units || [], Boolean(item.excluded));
};

App.renderOrganizedSheet = (p) => {
  const st = App.orgState(p);
  const body = App.slot(p.el, "org-rows");
  if (!body || !st.proposal) return;
  body.textContent = "";
  st.lanes = [];
  const timeline = (p.data && p.data.timeline) || {};
  App.fill(p.el, "org-tc-start", timeline.start_tc || "");
  App.fill(p.el, "org-tc-end", timeline.end_tc || "");
  let dividerShown = false;
  for (const g of st.proposal.groups || []) {
    if (g.is_family && !dividerShown) {
      dividerShown = true;
      body.appendChild(App.tpl("tpl-org-divider"));
    }
    App.appendOrgGroupRow(body, g);
    const kindAttr = g.is_family ? "family" : g.key;
    for (const t of g.tracks || []) {
      App.appendOrgTrackRow(p, body, t, kindAttr, false);
    }
  }
  const leftTracks = st.proposal.left_tracks || [];
  if (leftTracks.length > 0) {
    App.appendOrgUnsortedHeader(body, leftTracks);
    for (const lt of leftTracks) {
      App.appendOrgTrackRow(p, body, lt, "", true);
    }
  }
  App.paintOrganizedLanes(p);
};

App.paintOrganizedLanes = (p) => {
  if (!p || p.direction !== "to_live" || !p.data) return;
  const st = App.orgState(p);
  const orgTable = App.slot(p.el, "org-tracks");
  if (!st.lanes.length || (orgTable && orgTable.hidden)) return;
  const timeline = p.data.timeline || {};
  const t0 = Number(timeline.start_samples);
  const t1 = Number(timeline.end_samples);
  const length = t1 - t0;
  const dpr = window.devicePixelRatio || 1;
  for (const lane of st.lanes) {
    const canvas = lane.canvas;
    if (!canvas) continue;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) continue;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    if (!w || !h || !(length > 0)) continue;
    const cs = getComputedStyle(canvas);
    const activeColor = cs.color;
    const stayColor = cs.getPropertyValue("--lane-stay").trim() || activeColor;
    const k = w / length;
    for (const u of lane.units || []) {
      const a = Number(u.start_samples);
      const b = Number(u.end_samples);
      ctx.fillStyle = lane.isLeft || u.accepted ? activeColor : stayColor;
      ctx.fillRect(
        Math.floor((a - t0) * k),
        3,
        Math.max(1, Math.round((b - a) * k) - 1),
        h - 6,
      );
    }
  }
};

App.renderOrganizeView = (p) => {
  if (!p || p.direction !== "to_live" || !p.el) return;
  App.syncOrganizeControls(p);
  const st = App.orgState(p);
  const isType = App.radio(p.el, "a-organize") === "type";
  const hasProposal = Boolean(st.proposal && p.data);
  const switcher = App.slot(p.el, "org-view-switch");
  if (switcher) switcher.hidden = !(isType && hasProposal);
  const showProposed = isType && hasProposal && st.view !== "sent";
  const rawTable = App.slot(p.el, "tracks");
  const orgTable = App.slot(p.el, "org-tracks");
  if (rawTable) rawTable.hidden = showProposed;
  if (orgTable) orgTable.hidden = !showProposed;
  if (showProposed) {
    const foot = App.slot(p.el, "siblings-foot");
    if (foot) foot.hidden = true;
    App.renderOrganizedSheet(p);
  } else {
    App.syncSiblingFoot(p, (p.data && p.data.tracks) || []);
    App.paintLanes(p);
  }
  App.updateOrgSummary(p, isType, hasProposal);
};

App.fetchOrganize = async (p) => {
  if (!p || p.direction !== "to_live" || !p.data) return;
  if (App.radio(p.el, "a-organize") !== "type") {
    App.renderOrganizeView(p);
    return;
  }
  const st = App.orgState(p);
  const input = (p.lastInput || App.text(p.el, "input") || "").trim();
  if (!input) return;
  const seq = ++st.reqSeq;
  st.busy = true;
  st.error = "";
  App.renderOrganizeView(p);
  const body = {
    input: input,
    audio_dir: App.text(p.el, "audio_dir") || null,
    production_audio: App.productionFolder(p),
    edl_rate: App.radio(p.el, "a-edl-rate") || null,
    stereo: App.radio(p.el, "a-stereo") || "pair",
    certainty: App.radio(p.el, "a-certainty") || "permissive",
    signals: App.organizeSignals(p),
    edits: App.serializeEdits(st.edits),
  };
  const res = await App.api("POST", "/api/app/organize", body);
  if (seq !== st.reqSeq) return;
  st.busy = false;
  if (!res || !res.ok) {
    const failure = (res && res.error) || {};
    st.proposal = null;
    st.summary = null;
    st.error = failure.message || "Tracks could not be organized.";
    App.renderOrganizeView(p);
    return;
  }
  st.error = "";
  st.proposal = res.proposal || null;
  st.summary = res.summary || null;
  App.adoptResolvedNames(st);
  App.renderOrganizeView(p);
};

// The server resolves each track name onto the key that now holds its clips;
// the page keeps that map so the next rename or clear uses the keys it shows.
// A name whose clips are all unchecked has no track this time: the page parks
// it under a "~" key and keeps sending it, so it comes back with its clips.
App.PARKED_PREFIX = "~";

App.orgAnchorId = (pairs) =>
  (pairs || [])
    .map((pair) => pair[0] + ":" + pair[1])
    .sort()
    .join(",");

// The parking key is the anchor itself: live names never share an origin, so
// two parked names never share a key.
App.parkedKey = (anchor) => App.PARKED_PREFIX + App.orgAnchorId(anchor);

App.adoptResolvedNames = (st) => {
  const resolved = st.proposal && st.proposal.edits;
  if (!resolved) return;
  const names = Object.assign({}, resolved.track_names || {});
  const anchors = Object.assign({}, resolved.track_name_origins || {});
  const placed = new Set(Object.keys(anchors).map((k) => App.orgAnchorId(anchors[k])));
  for (const key of Object.keys(st.edits.track_names)) {
    const anchor = st.edits.track_name_origins[key];
    if (!anchor || !anchor.length || placed.has(App.orgAnchorId(anchor))) continue;
    const parked = App.parkedKey(anchor);
    names[parked] = st.edits.track_names[key];
    anchors[parked] = anchor;
  }
  st.edits.track_names = names;
  st.edits.track_name_origins = anchors;
};

// Before a new name takes a key, a name already stored there for other clips
// (a parked one the server could not place) moves to its parking key.
App.parkNameAt = (st, key, anchor) => {
  if (!Object.prototype.hasOwnProperty.call(st.edits.track_names, key)) return;
  const old = st.edits.track_name_origins[key];
  if (!old || !old.length || App.orgPairsOverlap(old, anchor)) return;
  st.edits.track_names[App.parkedKey(old)] = st.edits.track_names[key];
  st.edits.track_name_origins[App.parkedKey(old)] = old;
};

App.refreshOrganize = (p) => {
  if (p && p.direction === "to_live" && p.data && App.radio(p.el, "a-organize") === "type") {
    App.fetchOrganize(p);
  }
};

App.findOrgGroup = (proposal, key) =>
  ((proposal && proposal.groups) || []).find((g) => g.key === key) || null;

App.findOrgTrack = (proposal, key) => {
  for (const g of (proposal && proposal.groups) || []) {
    for (const t of g.tracks || []) {
      if (t.key === key) return t;
    }
  }
  return null;
};

App.orgAcceptedOrigins = (proposal, key) => {
  const trk = App.findOrgTrack(proposal, key);
  const pairs = [];
  for (const u of (trk && trk.units) || []) {
    if (!u.accepted) continue;
    for (const pair of u.origin_keys || []) pairs.push([Number(pair[0]), Number(pair[1])]);
  }
  return pairs;
};

App.orgNameKeys = (edits) => {
  const keys = new Set(Object.keys(edits.track_names));
  for (const key of Object.keys(edits.track_name_origins)) keys.add(key);
  return Array.from(keys);
};

App.orgNameAnchor = (st, key) => {
  if (Object.prototype.hasOwnProperty.call(st.edits.track_name_origins, key)) {
    return st.edits.track_name_origins[key];
  }
  return App.orgAcceptedOrigins(st.proposal, key);
};

App.orgPairsOverlap = (a, b) => {
  const have = new Set();
  for (const pair of a || []) have.add(pair[0] + ":" + pair[1]);
  for (const pair of b || []) {
    if (have.has(pair[0] + ":" + pair[1])) return true;
  }
  return false;
};

App.toggleOrgRow = (p, key) => {
  if (!p || p.direction !== "to_live" || !key) return;
  const st = App.orgState(p);
  if (st.open.has(key)) st.open.delete(key);
  else st.open.add(key);
  App.renderOrganizedSheet(p);
};

App.resetOrganize = (p) => {
  if (!p || p.direction !== "to_live") return;
  const st = App.orgState(p);
  st.edits = App.emptyEdits();
  App.stale(p);
  if (App.radio(p.el, "a-organize") === "type" && p.data) {
    App.fetchOrganize(p);
  } else {
    App.renderOrganizeView(p);
  }
};

App.applyOrgClipEdit = (st, rawPairs, checked) => {
  let pairs = [];
  try {
    pairs = JSON.parse(rawPairs || "[]");
  } catch (err) {
    pairs = [];
  }
  for (const pair of pairs) {
    const k = pair[0] + ":" + pair[1];
    st.edits.origins[k] = {
      origin: [Number(pair[0]), Number(pair[1])],
      accepted: Boolean(checked),
    };
  }
};

App.handleOrganizeChange = (p, target) => {
  if (!p || p.direction !== "to_live" || !target) return false;
  const st = App.orgState(p);
  const ds = target.dataset || {};
  if (target.name === "a-view") {
    st.view = target.value || "proposed";
    App.renderOrganizeView(p);
    return true;
  }
  if (target.name === "a-organize") {
    if (target.value === "type" && p.data) App.fetchOrganize(p);
    else App.renderOrganizeView(p);
    return false;
  }
  if (target.name === "a-certainty" || ds.sig) {
    if (App.radio(p.el, "a-organize") === "type") {
      if (p.data) App.fetchOrganize(p);
      else App.renderOrganizeView(p);
    }
    return false;
  }
  if (target.name === "a-stereo" || target === App.slot(p.el, "audio_dir")) {
    App.refreshOrganize(p);
    return false;
  }
  if (ds.orgGroup) {
    st.edits.groups[ds.orgGroup] = Boolean(target.checked);
    const grp = App.findOrgGroup(st.proposal, ds.orgGroup);
    if (grp && !target.checked) {
      for (const t of grp.tracks || []) {
        for (const u of t.units || []) {
          for (const pair of u.origin_keys || []) {
            delete st.edits.origins[pair[0] + ":" + pair[1]];
          }
        }
      }
    }
    App.fetchOrganize(p);
    App.stale(p);
    return true;
  }
  if (ds.orgTrack) {
    const trk = App.findOrgTrack(st.proposal, ds.orgTrack);
    for (const u of (trk && trk.units) || []) {
      if (!u.accepted) continue;
      for (const pair of u.origin_keys || []) {
        st.edits.origins[pair[0] + ":" + pair[1]] = {
          origin: [Number(pair[0]), Number(pair[1])],
          accepted: false,
        };
      }
    }
    App.fetchOrganize(p);
    App.stale(p);
    return true;
  }
  if (ds.orgOrigins) {
    App.applyOrgClipEdit(st, ds.orgOrigins, target.checked);
    App.fetchOrganize(p);
    App.stale(p);
    return true;
  }
  if (ds.orgGroupKey) {
    const clean = target.value.trim();
    if (clean) st.edits.group_names[ds.orgGroupKey] = clean;
    else delete st.edits.group_names[ds.orgGroupKey];
    App.fetchOrganize(p);
    App.stale(p);
    return true;
  }
  if (ds.orgTrackKey) {
    const key = ds.orgTrackKey;
    const clean = target.value.trim();
    if (clean) {
      const anchor = App.orgAcceptedOrigins(st.proposal, key);
      for (const other of App.orgNameKeys(st.edits)) {
        if (other === key) continue;
        if (App.orgPairsOverlap(App.orgNameAnchor(st, other), anchor)) {
          delete st.edits.track_names[other];
          delete st.edits.track_name_origins[other];
        }
      }
      App.parkNameAt(st, key, anchor);
      st.edits.track_names[key] = clean;
      st.edits.track_name_origins[key] = anchor;
    } else {
      // Clearing a name also clears the parked name whose anchor falls on this
      // track: the server would put it back on the track that just went free.
      const base = App.orgAcceptedOrigins(st.proposal, key);
      for (const other of App.orgNameKeys(st.edits)) {
        if (other === key) continue;
        if (other.indexOf(App.PARKED_PREFIX) !== 0) continue;
        if (App.orgPairsOverlap(App.orgNameAnchor(st, other), base)) {
          delete st.edits.track_names[other];
          delete st.edits.track_name_origins[other];
        }
      }
      delete st.edits.track_names[key];
      delete st.edits.track_name_origins[key];
    }
    App.fetchOrganize(p);
    App.stale(p);
    return true;
  }
  return false;
};

App.initOrganizePanel = (p) => {
  if (!p || p.direction !== "to_live" || !p.el) return;
  App.syncOrganizeControls(p);
  const body = App.slot(p.el, "org-rows");
  if (!body) return;
  body.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const t = event.target;
    if (t && t.classList && t.classList.contains("tname")) {
      event.preventDefault();
      t.blur();
    }
  });
  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(() => App.paintOrganizedLanes(p));
    observer.observe(body);
    App.observers.to_live_org = observer;
  }
};

(function () {
  const baseClearRead = App.clearRead;
  App.clearRead = (p) => {
    baseClearRead(p);
    App.clearOrganize(p);
  };

  const basePaintLanes = App.paintLanes;
  App.paintLanes = (p) => {
    basePaintLanes(p);
    App.paintOrganizedLanes(p);
  };

  const baseObserveLanes = App.observeLanes;
  App.observeLanes = (p) => {
    baseObserveLanes(p);
    App.initOrganizePanel(p);
  };
})();
