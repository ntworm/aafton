// The production audio sheet: the ruler of the shooting days, the lanes
// drawn day by day with the circled takes, and what the flow asks of this
// direction. Every piece of markup comes from a template of the page.

// --- the days ------------------------------------------------------------

// The ruler: one segment per day block, labelled with its date and the
// timecode of its first take.
App.renderDays = (p, blocks) => {
  const ruler = App.slot(p.el, "ruler");
  if (!ruler) return;
  ruler.textContent = "";
  for (const block of blocks || []) {
    const node = App.tpl("tpl-line");
    App.fill(node, "text", block.label || "");
    ruler.appendChild(node);
  }
};

// The x of a position of the set. Each day takes an equal share of the
// width, as the segments of the ruler do, and a clip sits in its place
// inside its day.
App.dayX = (blocks, end, w, at) => {
  const n = blocks.length;
  let i = n - 1;
  while (i > 0 && at < Number(blocks[i].start_samples)) i -= 1;
  const a = Number(blocks[i].start_samples);
  const b = i + 1 < n ? Number(blocks[i + 1].start_samples) : end;
  const share = w / n;
  const inside = b > a ? Math.min(Math.max((at - a) / (b - a), 0), 1) : 0;
  return share * i + inside * share;
};

// The bars of a list of spans, in the colour given.
App.paintSpans = (view, blocks, end, spans, colour) => {
  view.ctx.fillStyle = colour;
  for (const pair of spans || []) {
    const a = App.dayX(blocks, end, view.w, Number(pair[0]));
    const b = App.dayX(blocks, end, view.w, Number(pair[1]));
    view.ctx.fillRect(Math.floor(a), 3, Math.max(1, Math.round(b - a) - 1), view.h - 6);
  }
};

// The lanes of the sheet of days: a line at the start of each day after the
// first, the takes in the colour of the canvas and the circled takes in the
// colour the stylesheet gives them.
App.paintDays = (p, blocks) => {
  const data = p.data || {};
  const end = Number((data.timeline || {}).end_samples);
  for (const lane of p.lanes) {
    const view = App.prepareLane(p, lane);
    if (!view || !blocks.length || !(end > 0)) continue;
    const look = getComputedStyle(view.canvas);
    view.ctx.fillStyle = look.getPropertyValue("--lane-block").trim();
    for (let i = 1; i < blocks.length; i += 1) {
      view.ctx.fillRect(Math.floor((view.w / blocks.length) * i), 0, 1, view.h);
    }
    const track = lane.track || {};
    App.paintSpans(view, blocks, end, track.spans, look.color);
    App.paintSpans(view, blocks, end, track.circled_spans, look.getPropertyValue("--lane-circled").trim());
  }
};

// --- what the flow asks of this direction --------------------------------

// The dialog of the source: a folder for production audio, a file for the
// other directions.
App.inputBrowse = (p) => {
  if (p.direction === "production") {
    return { kind: "folder", filter: "all", title: "Choose the recorder folder" };
  }
  if (p.direction === "collect") {
    return { kind: "open_file", filter: "session", title: "Choose the session file" };
  }
  if (p.direction === "reconform") {
    return { kind: "open_file", filter: "source", title: "Choose the old cut" };
  }
  const filter = p.direction === "to_live" ? "source" : "als";
  return { kind: "open_file", filter: filter, title: "Choose the source file" };
};

// The options of the production audio conversion.
App.paOptions = (p) => ({
  circled_only: App.checked(p.el, "circled_only"),
  template: App.text(p.el, "template"),
  bpm: App.text(p.el, "bpm"),
});

// --- the production audio folder of the AAF to Live sheet ---------------

// The recorder folder the read and the convert carry; null when the field
// is empty or the panel has none.
App.productionFolder = (p) => App.text(p.el, "production_audio") || null;

// A sibling row comes from the recorder files: it is indented under its
// owner track and has no number of its own.
App.markSibling = (row, track) => {
  if (!track.sibling) return;
  row.classList.add("row-exp");
  App.fill(row, "n", "");
};

// The sentence under the sheet shows only when the sheet has sibling rows.
App.syncSiblingFoot = (p, tracks) => {
  const foot = App.slot(p.el, "siblings-foot");
  if (foot) foot.hidden = !(tracks || []).some((track) => track.sibling === true);
};
