// The Reconform tab: the change list between two picture cuts and the copy
// of the Live set conformed to the new cut. Markup comes from the page.

// --- routes of the panels ------------------------------------------------

App.readRoute = (p) => {
  if (p.direction === "collect") return "/api/collect/read";
  if (p.direction === "reconform") return "/api/reconform/read";
  return "/api/app/read";
};

App.runRoute = (p) => {
  if (p.direction === "collect") return "/api/collect/run";
  if (p.direction === "reconform") return "/api/reconform/run";
  return "/api/app/convert";
};

// --- the fields of the tab -----------------------------------------------

App.reconformFields = (p) => ({
  new_cut: App.text(p.el, "new_cut"),
  set: App.text(p.el, "set") || null,
  edl_rate: App.radio(p.el, "r-edl-rate") || null,
});

App.reconformHasEdl = (p) =>
  App.isEdl(App.text(p.el, "input")) || App.isEdl(App.text(p.el, "new_cut"));

// The two cuts are read only when both are chosen and, for an EDL, once
// the frame rate is chosen or the set brings it.
App.reconformReady = (p) => {
  if (!App.text(p.el, "input") || !App.text(p.el, "new_cut")) return false;
  if (!App.reconformHasEdl(p)) return true;
  return Boolean(App.radio(p.el, "r-edl-rate") || App.text(p.el, "set"));
};

App.syncReconformEdl = (p) => {
  const wrap = App.slot(p.el, "r-edl-wrap");
  if (wrap) wrap.hidden = !App.reconformHasEdl(p);
};

// --- the change list -----------------------------------------------------

App.renderReconform = (p) => {
  const data = p.data || {};
  App.renderFacts(p, data.facts);
  const body = App.slot(p.el, "track-rows");
  if (!body) return;
  body.textContent = "";
  p.open = null;
  p.lanes = [];
  for (const item of data.items || []) {
    const row = App.tpl("tpl-track-reconform");
    App.fill(row, "n", String(item.index + 1));
    App.fill(row, "kind", item.kind || "");
    App.fill(row, "old", item.old || "");
    App.fill(row, "new", item.new || "");
    App.fill(row, "delta", item.delta || "");
    App.fill(row, "source", item.source || "");
    const state = App.slot(row, "state");
    const st = item.state || {};
    if (state) {
      state.textContent = st.label || "";
      if (st.st) state.dataset.st = st.st;
    }
    body.appendChild(row);
  }
  const count = (data.items || []).length;
  let foot = count === 1 ? "1 change." : count + " changes.";
  if (data.ambiguous) foot += " A shot is used twice: the set cannot be conformed.";
  App.fill(p.el, "reconform-foot", foot);
};

// The primary button needs a set and a change list without ambiguity.
App.reconformCanRun = (p) =>
  Boolean(p.data && !p.data.ambiguous && App.text(p.el, "set"));

App.reconformGather = (p, body) => {
  Object.assign(body, App.reconformFields(p));
  body.options = { apply: App.checked(p.el, "apply") };
};

// A new source or set: the EDL block follows and the cuts are read again.
App.reconformReread = (p) => {
  App.syncReconformEdl(p);
  return App.read(p, true);
};

App.reconformChange = (p, target) => {
  if (p.direction !== "reconform") return false;
  const names = ["new_cut", "set"];
  const isField = names.some((name) => target === App.slot(p.el, name));
  if (!isField && target.name !== "r-edl-rate") return false;
  App.reconformReread(p);
  return true;
};
