// The two flows: the read of the source, the options, the conversion and
// the actions of the result.

// --- the read ------------------------------------------------------------

// The destination of the conversion, when the user did not choose one.
App.fillOutput = (p, suggest, value, previous) => {
  const data = suggest || {};
  const output = App.slot(p.el, "output");
  if (!output || !data.output) return;
  if (p.outputTouched && value === previous) return;
  let text = data.output;
  if (p.direction === "to_aaf") text = App.withFormat(text, App.radio(p.el, "b-format") || "aaf");
  output.value = text;
  App.showTail(output);
};

// Reads the source and renders what it holds. Only the answer of the last
// request is used.
App.read = async (p, force) => {
  const input = App.slot(p.el, "input");
  const value = input ? input.value.trim() : "";
  App.syncEdl(p);
  if (!value) {
    p.lastInput = "";
    App.resetRead(p);
    return;
  }
  if (p.direction === "to_live" && App.isEdl(value) && !App.radio(p.el, "a-edl-rate")) {
    // An EDL is read only after its frame rate is chosen: no request is
    // sent and the panel stays empty while the hint of the block shows.
    App.resetRead(p);
    return;
  }
  if (p.direction === "reconform" && !App.reconformReady(p)) return App.resetRead(p);
  if (!force && p.data && value === p.lastInput) return;
  const previous = p.lastInput;
  const seq = ++p.seq;
  App.setPhase(p, "reading");
  const body = { direction: p.direction, input: value };
  if (p.direction === "to_live") {
    body.audio_dir = App.text(p.el, "audio_dir") || null;
    body.edl_rate = App.radio(p.el, "a-edl-rate") || null;
    body.production_audio = App.productionFolder(p);
  }
  if (p.direction === "reconform") Object.assign(body, App.reconformFields(p));
  const route = App.readRoute(p);
  const res = await App.api("POST", route, body);
  if (seq !== p.seq) return;
  if (!res || !res.ok) {
    const failure = (res && res.error) || {};
    App.clearRead(p);
    App.setPhase(p, "error");
    App.fill(p.el, "read-error", failure.message || "The source could not be read.");
    return;
  }
  p.lastInput = value;
  p.data = res;
  App.renderRead(p, res, value, previous);
  App.setPhase(p, "ready");
};

App.renderRead = (p, res, value, previous) => {
  if (p.direction === "collect") {
    App.renderCollectMedia(p);
  } else if (p.direction === "reconform") {
    App.renderReconform(p);
  } else {
    App.renderFacts(p, res.facts);
    App.renderTracks(p);
  }
  App.renderNotes(p, res.notes);
  if (p.direction === "to_live") {
    App.renderMedia(p, res.media);
    App.renderSuggestions(p, res.suggest);
    App.afterReadOrganize(p, value, previous);
  } else if (p.direction === "to_aaf") {
    App.renderGuide(p, res.guide, res);
    App.syncStart(p, res);
    App.syncSelection(p);
  }
  App.fillOutput(p, res.suggest, value, previous);
};

// --- the options of the conversion ---------------------------------------

// The body of the convert request, as the spec gives it.
App.gather = (p) => {
  const body = {
    direction: p.direction,
    input: App.text(p.el, "input"),
    output: App.text(p.el, "output"),
    overwrite: false,
  };
  if (p.direction === "to_live") {
    body.audio_dir = App.text(p.el, "audio_dir") || null;
    body.edl_rate = App.radio(p.el, "a-edl-rate") || null;
    body.production_audio = App.productionFolder(p);
    body.options = {
      stereo: App.radio(p.el, "a-stereo"),
      fades: App.radio(p.el, "a-fades"),
      groups: App.organizeGroups(p),
      organize: App.radio(p.el, "a-organize") || "off",
      template: App.text(p.el, "template"),
      bpm: App.text(p.el, "bpm"),
      live_origin: App.text(p.el, "live_origin"),
      video: App.text(p.el, "video"),
      video_start: App.text(p.el, "video_start"),
      session_text: App.text(p.el, "session_text"),
      collect_media: App.checked(p.el, "collect_media"),
      ltc: App.checked(p.el, "ltc"),
      mode: App.radio(p.el, "a-mode"),
    };
    if (body.options.organize === "type") App.addOrganizeOptions(p, body.options);
  } else if (p.direction === "production") {
    body.options = App.paOptions(p);
  } else if (p.direction === "collect") {
    body.options = App.collectOptions(p);
  } else if (p.direction === "reconform") {
    App.reconformGather(p, body);
  } else {
    body.tracks = App.selectedTracks(p);
    body.options = App.aafOptions(p);
  }
  return body;
};

// --- the conversion ------------------------------------------------------

// Sends the conversion and shows what it was worth.
App.convert = async (p, extra) => {
  const failure = App.slot(p.el, "convert-error");
  const exists = App.slot(p.el, "exists");
  if (failure) {
    failure.textContent = "";
    failure.hidden = true;
  }
  if (exists) exists.hidden = true;
  App.setPhase(p, "working");
  const body = Object.assign(App.gather(p), extra || {});
  const route = App.runRoute(p);
  const res = await App.api("POST", route, body);
  if (!res || !res.ok) {
    const error = (res && res.error) || {};
    App.setPhase(p, "ready");
    if (error.code === "exists" && exists) {
      exists.hidden = false;
      return;
    }
    if (failure) {
      failure.hidden = false;
      failure.textContent = error.message || "The conversion failed.";
    }
    return;
  }
  p.result = res;
  App.setPhase(p, "done");
  App.renderNotes(p, res.notes);
  App.renderVerdict(p);
  App.syncCollectVerdict(p, failure);
};

// The permissive mode of the panel.
App.setPermissive = (p) => {
  const name = p.direction === "to_live" ? "a-mode" : "b-mode";
  const radio = p.el.querySelector(`input[name="${name}"][value="permissive"]`);
  if (radio) radio.checked = true;
};

// --- the actions ---------------------------------------------------------

// The actions of the result: open, show and save the report.
App.resultAction = async (p, act) => {
  if (act === "save-report") {
    const path = await App.browse(p, "save_file", "report", "Save report");
    if (!path) return;
    const res = await App.api("POST", "/api/app/report", { direction: p.direction, path: path });
    App.reportFailure(p, res);
    return;
  }
  const route = act === "open-output" ? "/api/app/open" : "/api/app/reveal";
  const res = await App.api("POST", route, { direction: p.direction });
  App.reportFailure(p, res);
};

// The native dialogs: the path they give back fills the field beside the
// button that was pressed.
App.browseAction = async (p, act) => {
  if (act === "browse-input") {
    const source = App.inputBrowse(p);
    const path = await App.browse(p, source.kind, source.filter, source.title);
    if (!path) return;
    const input = App.slot(p.el, "input");
    if (input) input.value = path;
    App.showTail(input);
    App.syncEdl(p);
    await App.reconformReread(p);
    return;
  }
  const spec = App.outputBrowseSpec(p, act);
  if (!spec) return;
  const path = await App.browse(p, spec.kind, spec.filter, spec.title);
  if (!path) return;
  const field = App.slot(p.el, spec.field);
  if (!field) return;
  field.value = path;
  App.showTail(field);
  if (spec.field === "output") p.outputTouched = true;
  if (spec.field === "production_audio") return App.read(p, true);
  if (spec.field === "new_cut" || spec.field === "set") return App.reconformReread(p);
  if (spec.field === "audio_dir") App.refreshOrganize(p);
  App.stale(p);
};

// One action of the panel.
App.action = (p, act) => {
  if (!act) return;
  if (act === "convert") {
    App.convert(p, {});
    return;
  }
  if (act === "convert-replace") {
    App.convert(p, { overwrite: true });
    return;
  }
  if (act === "cancel-replace") {
    const exists = App.slot(p.el, "exists");
    if (exists) exists.hidden = true;
    return;
  }
  if (act === "convert-permissive") {
    App.setPermissive(p);
    App.convert(p, { overwrite: true });
    return;
  }
  if (act === "reread") return App.read(p, true);
  if (act === "leave-out-render") return App.applyLeaveOut(p);
  if (act === "org-reset") return App.resetOrganize(p);
  if (act === "open-output" || act === "reveal-output" || act === "save-report") {
    App.resultAction(p, act);
    return;
  }
  if (act.indexOf("browse-") === 0) App.browseAction(p, act);
};

// --- the events of a panel -----------------------------------------------

// A verdict on screen describes the options on screen: a change of any of
// them sends the panel back to ready.
App.stale = (p) => {
  if (p.phase === "done") {
    App.setPhase(p, "ready");
    App.renderNotes(p, p.data && p.data.notes);
  }
  App.hideExists(p);
};

// The selection of the Live to AAF sheet: the summary line, the pick-all
// box, the primary button and the lanes.
App.syncSelection = (p) => {
  const lanes = App.pickableLanes(p);
  let selected = 0;
  let clips = 0;
  for (const lane of lanes) {
    const box = App.slot(lane.row, "pick");
    if (!box || !box.checked) continue;
    selected += 1;
    clips += Math.max(0, Number((lane.track || {}).clips || 0) - (p.leaveOutRender ? Number((lane.track || {}).render_clips || 0) : 0));
  }
  const summary = App.slot(p.el, "tracks-summary");
  if (summary) {
    const text = selected + " of " + lanes.length + " tracks selected";
    summary.textContent = text + " \u00b7 " + clips + " clips";
  }
  const all = App.slot(p.el, "pick-all");
  if (all) {
    all.checked = lanes.length > 0 && selected === lanes.length;
    all.indeterminate = selected > 0 && selected < lanes.length;
  }
  App.syncPrimary(p);
  App.paintLanes(p);
};

App.onChange = (p, target) => {
  if (!target) return;
  if (target === App.slot(p.el, "input")) {
    App.syncEdl(p);
    App.read(p, false);
    return;
  }
  if (App.reconformChange(p, target)) return;
  if (target.name === "a-edl-rate") {
    App.syncEdl(p);
    App.read(p, true);
    return;
  }
  if (target === App.slot(p.el, "production_audio")) return App.read(p, true);
  if (target.name === "b-format") {
    App.syncFormat(p);
    App.stale(p);
    return;
  }
  if (App.handleOrganizeChange(p, target)) return;
  if (target.name === "b-media") {
    App.syncAafCollect(p);
    App.stale(p);
    return;
  }
  if (target.matches && target.matches('[data-f="include_video"]')) {
    if (p.data) App.renderCollectMedia(p);
    App.stale(p);
    return;
  }
  if (target.matches && target.matches('[data-f="pick-all"]')) {
    App.pickAll(p, target.checked);
    App.syncSelection(p);
    App.stale(p);
    return;
  }
  if (target.matches && target.matches('[data-f="pick"]')) {
    App.syncSelection(p);
    App.stale(p);
    return;
  }
  if (target.name === "b-fps" || target === App.slot(p.el, "start_tc")) App.updateRuler(p);
  App.stale(p);
};

App.onInput = (p, target) => {
  if (!target) return;
  if (target === App.slot(p.el, "input")) {
    App.syncEdl(p);
    App.stale(p);
    return;
  }
  if (target === App.slot(p.el, "output")) {
    p.outputTouched = true;
    App.hideExists(p);
  }
  if (target === App.slot(p.el, "start_tc")) App.updateRuler(p);
  App.stale(p);
};

// One listener per panel: the events of the sheet and of the options are
// delegated to it.
App.wirePanel = (p) => {
  const el = p.el;
  el.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target.closest("*") : null;
    if (!target) return;
    const name = target.closest("button.name");
    if (name) {
      const row = name.closest("tr.row");
      if (row && row.dataset.orgKey) App.toggleOrgRow(p, row.dataset.orgKey);
      else if (row) App.toggleRow(p, Number(row.dataset.index));
      return;
    }
    const button = target.closest("[data-act]");
    if (button && el.contains(button)) App.action(p, button.getAttribute("data-act"));
  });
  el.addEventListener("change", (event) => App.onChange(p, event.target));
  el.addEventListener("input", (event) => App.onInput(p, event.target));
  el.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const target = event.target;
    if (!target || !target.matches || !target.matches('[data-f="input"]')) return;
    event.preventDefault();
    App.read(p, false);
  });
};

// --- the start -----------------------------------------------------------

App.start = () => {
  for (const key of Object.keys(App.panels)) {
    const p = App.panels[key];
    if (!p.el) continue;
    App.wirePanel(p);
    App.observeLanes(p);
    App.wirePaths(p);
  }
  App.wireTabs();
  App.loadInfo();
  const hashed = Object.keys(App.hashes).find((key) => App.hashes[key] === window.location.hash);
  App.selectTab(hashed || "to_live", false);
};

App.start();
