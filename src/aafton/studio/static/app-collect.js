// The Collect media tab and the collect option of the two export tabs.
// Every piece of markup comes from a template of the page.

// --- size formatting -----------------------------------------------------

App.formatSize = (sizeBytes) => {
  const n = Number(sizeBytes || 0);
  if (n <= 0) return "0 MB";
  const gb = 1024 * 1024 * 1024;
  const mb = 1024 * 1024;
  const kb = 1024;
  if (n >= gb) return (n / gb).toFixed(2) + " GB";
  if (n >= 100 * kb) return (n / mb).toFixed(1) + " MB";
  return Math.max(1, Math.round(n / kb)) + " KB";
};

// --- collect media sheet -------------------------------------------------

App.collectRowState = (item, includeVideo) => {
  if (item.raw_state === "ready" && !includeVideo && item.item_kind === "video") {
    return { st: "off", label: "Left out" };
  }
  return item.state || { st: "ok", label: "Copy" };
};

App.collectFacts = (data, includeVideo) => {
  const base = (data && data.facts) || [];
  if (!base.length) return [];
  const items = (data && data.items) || [];
  const copyable = items.filter(
    (it) => it.raw_state === "ready" && (includeVideo || it.item_kind !== "video")
  );
  const copyBytes = copyable.reduce((acc, it) => acc + Number(it.size_bytes || 0), 0);
  const videos = items.filter((it) => it.raw_state === "ready" && it.item_kind === "video");
  const videoBytes = videos.reduce((acc, it) => acc + Number(it.size_bytes || 0), 0);
  let subCopy = copyable.length + " to copy";
  if (videos.length) {
    subCopy = includeVideo ? "picture " + App.formatSize(videoBytes) : "picture left out";
  }
  return base.map((fact) => {
    if (fact.k !== "To copy") return fact;
    return { k: "To copy", v: App.formatSize(copyBytes), sub: subCopy, st: fact.st };
  });
};

App.renderCollectFoot = (p, items, renamed) => {
  const total = (items || []).length;
  const list = renamed || [];
  let text = total === 1 ? "1 file." : total + " files.";
  if (list.length === 1) {
    text +=
      " Two files with the same name from different folders both go in; the second is renamed " +
      list[0].renamed +
      ".";
  } else if (list.length > 1) {
    text += " " + list.length + " files with repeated names are renamed.";
  }
  App.fill(p.el, "collect-foot", text);
};

App.renderCollectHint = (p, source) => {
  const src = source || {};
  const name = src.name || "";
  const fmt = src.format || "als";
  if (!name) return;
  const text =
    "The folder gets " +
    name +
    "." +
    fmt +
    " and " +
    name +
    " Media/. It must be empty.";
  App.fill(p.el, "collect-dest-hint", text);
};

App.renderCollectMedia = (p) => {
  const data = p.data || {};
  const items = data.items || [];
  const includeVideo = App.checked(p.el, "include_video");
  App.renderFacts(p, App.collectFacts(data, includeVideo));
  const body = App.slot(p.el, "track-rows");
  if (!body) return;
  body.textContent = "";
  p.open = null;
  p.lanes = [];
  items.forEach((item, position) => {
    const row = App.tpl("tpl-track-collect");
    const index = Number.isInteger(item.index) ? item.index : position;
    row.dataset.index = String(index);
    App.fill(row, "n", String(index + 1));
    App.fill(row, "name", item.name || "");
    App.fill(row, "kind", item.kind || "");
    App.fill(row, "size", item.size || "");
    App.fill(row, "folder", item.folder || "");
    const st = App.collectRowState(item, includeVideo);
    const state = App.slot(row, "state");
    if (state) {
      state.textContent = st.label || "";
      if (st.st) state.dataset.st = st.st;
    }
    body.appendChild(row);
  });
  App.renderCollectFoot(p, items, data.renamed);
  App.renderCollectHint(p, data.source);
};

// --- export checkbox sync ------------------------------------------------

App.syncAafCollect = (p) => {
  if (!p || p.direction !== "to_aaf") return;
  const box = App.slot(p.el, "collect_media");
  if (!box) return;
  const format = App.radio(p.el, "b-format") || "aaf";
  const media = App.radio(p.el, "b-media") || "link";
  box.disabled = !(format === "aaf" && media === "link");
};

// --- collect options and verdict -----------------------------------------

App.collectOptions = (p) => ({
  include_video: App.checked(p.el, "include_video"),
});

App.syncCollectVerdict = (p, failure) => {
  if (p && p.result && p.result.collect_error && failure) {
    failure.hidden = false;
    failure.textContent = p.result.collect_error;
  }
  if (!p || p.direction !== "collect") return;
  const result = p.result || {};
  const fmt = result.format || (p.data && p.data.source && p.data.source.format) || "als";
  const open = App.$(p.el, '[data-act="open-output"]');
  if (open) open.hidden = !result.written || fmt !== "als";
};

// --- output browse spec --------------------------------------------------

App.outputBrowseSpec = (p, act) => {
  const fields = {
    "browse-output": { field: "output", kind: "save_file", filter: "als" },
    "browse-audio-dir": { field: "audio_dir", kind: "folder", filter: "all" },
    "browse-production-audio": { field: "production_audio", kind: "folder", filter: "all" },
    "browse-template": { field: "template", kind: "open_file", filter: "als" },
    "browse-video": { field: "video", kind: "open_file", filter: "video" },
    "browse-session-text": { field: "session_text", kind: "open_file", filter: "text" },
    "browse-new-cut": { field: "new_cut", kind: "open_file", filter: "source" },
    "browse-set": { field: "set", kind: "open_file", filter: "als" },
  };
  const spec = fields[act];
  if (!spec) return null;
  let kind = spec.kind;
  let filter = spec.filter;
  let title = "Choose the file";
  if (act === "browse-output") {
    if (p.direction === "collect") {
      kind = "folder";
      filter = "all";
      title = "Choose the destination folder";
    } else {
      title = p.direction === "to_aaf" ? "Save the AAF" : "Save the Live set";
      if (p.direction === "to_aaf") filter = App.radio(p.el, "b-format") || "aaf";
    }
  } else if (act === "browse-audio-dir") title = "Choose the audio folder";
  else if (act === "browse-production-audio") title = "Choose the production audio folder";
  else if (act === "browse-template") title = "Choose a Live template";
  else if (act === "browse-video") title = "Choose the picture";
  else if (act === "browse-session-text") title = "Choose the session text";
  else if (act === "browse-new-cut") title = "Choose the new cut";
  else if (act === "browse-set") title = "Choose the Live set";
  return { field: spec.field, kind: kind, filter: filter, title: title };
};
