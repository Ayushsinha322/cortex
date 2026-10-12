/* cortex — the graph window.
   Canvas force layout + lazy filesystem expansion + a reader panel that can
   hand any file to your terminal. */

(() => {
"use strict";

const CFG = window.CORTEX;
const TOKEN = CFG.token;
const ROOT = CFG.root;
const AUTO = CFG.autoExpand || { depth: 0, budget: 0 };
const READ_ONLY = !!CFG.readOnly;
/* Where a terminal program ends up: "here", in the terminal cortex was
   launched from; a "window" of its own, when it was launched from an app
   menu; or nowhere, when there is no terminal to be had. */
const TERMINAL = CFG.terminal === undefined ? "here" : CFG.terminal;

const GROUPS = ["dir", "note", "code", "config", "doc", "media", "archive",
                "other", "tag", "ghost"];
const TAG_PREFIX = "tag:";
const isTag = (id) => id.startsWith(TAG_PREFIX);
/* What git thinks of a file, as a dot on its node. Amber for work in progress,
   green for work already staged, red for something you have to resolve. */
const GIT_COLOR = {
  modified: "#f5a524", staged: "#34d399", untracked: "#8fa3c4",
  conflict: "#f43f5e", inside: "#f5a5247a",
};
const COLOR = {
  dir: "#f5a524", note: "#34d399", code: "#60a5fa", config: "#a78bfa",
  doc: "#f472b6", media: "#fb7185", archive: "#94a3b8", other: "#64748b",
  tag: "#c084fc", ghost: "#9fb0c8",
};
const LABEL = {
  dir: "folders", note: "notes", code: "code", config: "config",
  doc: "docs", media: "media", archive: "archives", other: "other",
  tag: "tags", ghost: "unwritten",
};

// ------------------------------------------------------------------- state
const nodes = new Map();            // path -> node
let links = [];                     // {a, b, kind, key}
const expanded = new Set();
const hidden = new Set();           // hidden groups
let sel = null, hover = null, matches = new Set();
let showSemantic = true, focusMode = false, maximized = false;
let showLabels = true;
let semanticEdges = null, editors = [], primaryEditor = null;
let connIndex = null, connOpen = true;
let searchMode = "names";       // or "text" -- search inside files
let hitRows = [], hitIndex = -1;
let selLine = null;             // line to open the selection at, from a hit
let gitStates = new Map();      // path -> modified | staged | untracked | ...
let gitBranch = null, showGit = true;
let savedPos = new Map();       // path -> [x, y] from the last time you looked
let savedCam = null, layoutDirty = false;
let cam = { s: 1, x: 0, y: 0 };
let needsFit = false;
let booted = false;             // the page is built and the chrome can be placed

/* ROOT is the folder cortex was launched on and the hard security boundary.
   viewRoot is what the graph is currently showing, which the user can narrow to
   any folder inside it without restarting. */
let viewRoot = ROOT;
let sidebarOpen = false;
let rootDirs = null;              // cached folder list for the sidebar
const SIDEBAR_W = 260;
const sidebarInset = () => (sidebarOpen ? SIDEBAR_W : 0);

/* The layout cools down and then freezes. Without this the graph jitters
   forever, which reads as flicker: labels keep crossing the collision
   threshold and blink on and off. Anything that changes the graph reheats it. */
let alpha = 1, dirty = true, shownLabels = new Set();
let labelPlan = [];             // what the last frame's label pass placed
const COOL = 0.982, ALPHA_MIN = 0.004;
const reheat = (a = 0.75) => {
  alpha = Math.max(alpha, a);
  dirty = true;
  layoutDirty = true;
};
const repaint = () => { dirty = true; };

const $ = (id) => document.getElementById(id);
const stage = $("stage");
const ctx = stage.getContext("2d");
let W = 0, H = 0, DPR = 1, bg = null;

function makeBackdrop() {
  bg = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2,
                                Math.max(W, H) * 0.75);
  bg.addColorStop(0, "#0b1120");
  bg.addColorStop(1, "#070910");
}

function resize() {
  DPR = Math.min(window.devicePixelRatio || 1, 2);
  W = window.innerWidth; H = window.innerHeight;
  stage.width = Math.round(W * DPR); stage.height = Math.round(H * DPR);
  stage.style.width = W + "px"; stage.style.height = H + "px";
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  makeBackdrop();
  dirty = true;
  if (booted) placeChrome();
}
window.addEventListener("resize", resize);
resize();
cam = { s: 1, x: W / 2, y: H / 2 };

// --------------------------------------------------------------------- api
/* Every request goes through here, so that the program behind the window
   going away is noticed once and said plainly, instead of as a scatter of
   "could not read that folder" toasts from whatever happened to ask next. */
let unreachable = 0, gone = false;
async function reach(url, init) {
  let res;
  try {
    res = await fetch(url, init);
  } catch (err) {
    // a request we called off is not the server going away
    if (err && err.name === "AbortError") throw err;
    if (++unreachable >= 2) serverGone();
    throw err;
  }
  unreachable = 0;
  return res;
}

function apiURL(path, params = {}) {
  const url = new URL(path, location.origin);
  url.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return url;
}
async function api(path, params = {}, init = undefined) {
  const res = await reach(apiURL(path, params), init);
  if (!res.ok) {
    const err = new Error(`${path} -> ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

/* A 403 to a request carrying our token means the token is no longer the
   server's: cortex was restarted, and this window belongs to the old one. */
const staleToken = (err) => err && err.status === 403;
/* Like api(), but a refusal is an answer: the server says why in the body,
   and that is what the toast should say. */
async function query(path, params = {}) {
  const res = await reach(apiURL(path, params));
  return res.json();
}
async function post(path, body, params = {}) {
  const res = await reach(apiURL(path, params), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
/* A snapshot carries its few images inside itself, as data: URLs by path. */
const RAW = CFG.raw || {};
const rawURL = (p) => RAW[p] ||
  `/api/raw?t=${encodeURIComponent(TOKEN)}&path=${encodeURIComponent(p)}`;

// ------------------------------------------------------------- graph model
const NOW = Date.now() / 1000;

function radiusOf(n) {
  if (n.dir) return Math.max(5.5, Math.min(17, 5.5 + 1.7 * Math.log2(1 + (n.kids || 0))));
  return Math.max(3, Math.min(9.5, 3 + Math.log2(1 + n.size / 2048) * 0.5));
}

function addNode(raw, near) {
  const found = nodes.get(raw.id);
  if (found) {
    if (raw.kids !== undefined) { found.kids = raw.kids; found.r = radiusOf(found); }
    return found;
  }
  // where it sat last time, if we have been here before; otherwise a ring
  // around whatever it grew out of
  const remembered = savedPos.get(raw.id);
  const angle = Math.random() * Math.PI * 2;
  const dist = 50 + Math.random() * 70;
  const n = Object.assign({}, raw, {
    x: remembered ? remembered[0] : (near ? near.x : 0) + Math.cos(angle) * dist,
    y: remembered ? remembered[1] : (near ? near.y : 0) + Math.sin(angle) * dist,
    vx: 0, vy: 0,
    fresh: raw.mtime ? (NOW - raw.mtime) < 7 * 86400 : false,
    depth: raw.id === viewRoot
      ? 0 : raw.id.slice(viewRoot.length).split("/").filter(Boolean).length,
  });
  n.r = radiusOf(n);
  nodes.set(n.id, n);
  return n;
}

function addLink(a, b, kind) {
  if (a === b || !nodes.has(a) || !nodes.has(b)) return false;
  // \u0000 cannot occur in a path, so it is a safe key separator
  const key = (a < b ? a + "\u0000" + b : b + "\u0000" + a) + "|" + kind;
  if (linkKeys.has(key)) return false;
  linkKeys.add(key);
  links.push({ a, b, kind, key });
  return true;
}
const linkKeys = new Set();

async function expand(id, opts = {}) {
  const parent = nodes.get(id);
  if (!parent || !parent.dir || expanded.has(id)) return;
  expanded.add(id);
  try {
    const kids = await api("/api/children", { path: id });
    const fresh = [];
    for (const kid of kids) {
      addNode(kid, parent);
      addLink(id, kid.id, "tree");
      fresh.push(kid.id);
    }
    linkUp(fresh);
    updateStats();
    reheat();
    if (disk) measure();               // size what just arrived
  } catch (err) {
    expanded.delete(id);
    if (!opts.quiet) toast("could not read that folder", true);
  }
}

/* Everything under these nodes leaves the graph. Used both by collapsing a
   folder and by a file disappearing from the disk under us. */
function dropDescendants(ids) {
  const doomed = new Set();
  const walk = (pid) => {
    for (const n of nodes.values()) {
      if (n.parent === pid && !doomed.has(n.id)) {
        doomed.add(n.id);
        if (n.dir) walk(n.id);
      }
    }
  };
  for (const id of ids) walk(id);
  return doomed;
}

function forget(doomed, vanished = false) {
  if (!doomed.size) return false;
  for (const d of doomed) {
    nodes.delete(d); expanded.delete(d); matches.delete(d);
  }
  links = links.filter((l) => {
    const drop = doomed.has(l.a) || doomed.has(l.b);
    if (drop) linkKeys.delete(l.key);
    return !drop;
  });
  if (sel && doomed.has(sel.id)) {
    // An open editor stays open: its text exists nowhere else. If the file
    // left the disk -- a git checkout, another program -- it says so and
    // offers to put it back; if only its folder was collapsed, nothing is
    // wrong at all.
    if (editing && editing.path === sel.id) {
      if (vanished) fileVanished();
    } else {
      closePanel();
    }
  }
  return true;
}

function collapse(id) {
  expanded.delete(id);
  forget(dropDescendants([id]));
  updateStats();
  reheat(0.45);
}

/* ------------------------------------------------------------ live refresh

   The graph is otherwise a photograph taken at launch. The server watches the
   folders the UI has read and says which ones changed; each open one is then
   re-listed and reconciled in place, so a file you just wrote appears where it
   belongs instead of after a relaunch. */
async function refreshDir(path) {
  if (!expanded.has(path) || !nodes.has(path)) return false;
  let kids;
  try { kids = await api("/api/children", { path }); }
  catch (err) { return false; }
  if (!Array.isArray(kids) || !nodes.has(path)) return false;

  const want = new Map(kids.map((k) => [k.id, k]));
  const vanished = [...nodes.values()]
    .filter((n) => n.parent === path && !want.has(n.id))
    .map((n) => n.id);
  let touched = forget(new Set([...vanished, ...dropDescendants(vanished)]), true);

  const parent = nodes.get(path);
  for (const k of kids) {
    const had = nodes.get(k.id);
    if (had) {
      if (had.size !== k.size || had.mtime !== k.mtime || had.kids !== k.kids) {
        had.size = k.size; had.mtime = k.mtime; had.kids = k.kids;
        had.fresh = k.mtime ? (Date.now() / 1000 - k.mtime) < 7 * 86400 : false;
        had.r = radiusOf(had);
        touched = true;
      }
    } else {
      const n = addNode(k, parent);
      addLink(path, n.id, "tree");
      touched = true;
    }
  }
  return touched;
}

let pulseBusy = false;
async function pulse() {
  if (pulseBusy || gone) return;
  if (typeof document.visibilityState === "string"
      && document.visibilityState === "hidden") return;
  pulseBusy = true;
  try {
    const r = await api("/api/pulse");
    // another window on this cortex opened a different folder; this one is
    // now looking at a map the server no longer serves
    if (r && r.root && r.root !== ROOT) { followRoot(r.root); return; }
    const changed = (r && r.changed) || [];
    if (!changed.length) return;

    let touched = false;
    for (const path of changed) {
      if (await refreshDir(path)) touched = true;
    }
    if (r.reindexed) { semanticEdges = null; applySemantic(); }
    loadGit();
    if (touched) { updateStats(); reheat(0.3); }
    if (sel && changed.includes(parentOf(sel.id))) renderBody(sel);
  } catch (err) {
    /* the server going away is not worth a toast every two seconds */
    if (staleToken(err)) serverGone();
  } finally {
    pulseBusy = false;
  }
}

const parentOf = (p) => p.slice(0, p.lastIndexOf("/"));

/* ---------------------------------------------------------------- layout

   A force-directed graph settles somewhere different every run, so a project
   you open daily looks different daily and the spatial memory that makes a map
   worth having never forms. Positions are saved per mapped folder, with the
   camera, and restored on the next launch. It is a cache: deleting it costs one
   re-settle. */
async function loadLayout() {
  if (!CFG.rememberLayout) return;
  try {
    const saved = await api("/api/layout");
    savedPos = new Map(Object.entries(saved.positions || {}));
    savedCam = saved.cam || null;
  } catch (err) { savedPos = new Map(); savedCam = null; }
}

async function saveLayout() {
  if (!CFG.rememberLayout || !layoutDirty || !nodes.size || gone) return;
  layoutDirty = false;
  const positions = {};
  for (const n of nodes.values()) positions[n.id] = [n.x, n.y];
  try {
    // the folder rides along, so a save that arrives after the window has
    // opened another folder is refused instead of filed under the new one
    await post("/api/layout", { root: ROOT, positions,
                                cam: { s: cam.s, x: cam.x, y: cam.y } });
  } catch (err) { layoutDirty = true; }
}

async function applySemantic() {
  if (!semanticEdges) {
    try {
      const snap = await api("/api/links");
      semanticEdges = snap.edges || [];
      ghostNames = snap.ghosts || {};
      buildSuggestIndex(snap.suggest);
      $("stat-index").textContent = snap.ready
        ? `${snap.notes} notes · ${snap.sources} sources indexed`
        : `indexing… ${snap.edges.length} links`;
      buildConnIndex();
      // a note that has since been written is no longer a ghost
      const stale = [...nodes.keys()].filter((id) => isGhost(id) && !connIndex.has(id));
      if (stale.length) forget(new Set(stale));
      if (!snap.ready) setTimeout(() => { semanticEdges = null; applySemantic(); }, 4000);
    } catch (err) { return; }
  }
  if (!connIndex) buildConnIndex();
  linkUp(nodes.keys());
}

/* Draw the semantic edges that touch these nodes.

   This used to walk the entire edge list every time anything appeared, which
   on a home directory is tens of thousands of edges re-examined per folder you
   opened, per search, and every four seconds while the index was still being
   built. The connection index already maps a path to its edges, so looking up
   only what arrived turns that into work proportional to the new nodes. */
function linkUp(ids) {
  if (!connIndex || !showSemantic) return false;
  let added = false, novel = false;
  for (const id of ids) {
    const touching = connIndex.get(id);
    if (!touching) continue;
    for (const c of touching) {
      if (c.kind === "tag" && !nodes.has(c.path)) {
        // a tag only one note carries is a label, not a meeting point, and
        // pays for its node in clutter without joining anything up. Selecting
        // it from the reader still brings it in, on request.
        if ((tagWeight.get(c.path) || 0) < 2) continue;
        addTagNode(c.path, nodes.get(id));
      }
      if (c.kind === "ghost" && !nodes.has(c.path)) {
        if (hist) continue;            // the past has no plans in it
        addGhostNode(c.path, nodes.get(id));
      }
      if (!addLink(id, c.path, c.kind)) continue;
      added = true;
      // a link between two nodes the saved layout already placed was part of
      // that layout, so it has nothing new to say about where they belong
      if (!savedPos.has(id) || !savedPos.has(c.path)) novel = true;
    }
  }
  if (added) {
    if (novel || !savedPos.size) reheat(savedPos.size ? 0.12 : 0.4);
    if (sel) renderConnections(sel);
    updateStats();
  }
  return added;
}

/* Ask git what it thinks of the folder now in view. Cheap on a project, and
   skipped entirely when there is no repository above it -- a home directory
   full of repositories gets nothing until you narrow to one. */
async function loadGit() {
  try {
    const g = await api("/api/git", { path: viewRoot });
    gitStates = new Map(Object.entries(g.states || {}));
    gitBranch = g.branch || null;
  } catch (err) {
    gitStates = new Map();
    gitBranch = null;
  }
  if (sel) renderGitChip(sel);
  updateStats();
  dirty = true;
}

function renderGitChip(n) {
  const chip = $("p-git");
  if (!chip) return;
  const state = gitStates.get(n.id);
  chip.hidden = !state || state === "inside";
  if (chip.hidden) return;
  chip.textContent = state;
  chip.style.color = GIT_COLOR[state];
  chip.style.borderColor = GIT_COLOR[state] + "55";
}

/* ------------------------------------------------------------- connections

   Backlinks. The index holds every semantic edge under the root, whether or
   not both ends have been grown into the graph, so the reader can list a
   connection the canvas has not drawn yet -- clicking one grows it.

   Edges are stored source first and deduplicated by unordered pair, so a pair
   of notes that link to each other survives once, in the direction seen
   first. */
function buildConnIndex() {
  connIndex = new Map();
  tagWeight = new Map();
  const add = (from, to, kind, out) => {
    let list = connIndex.get(from);
    if (!list) connIndex.set(from, (list = []));
    list.push({ path: to, kind, out });
  };
  for (const [a, b, kind] of semanticEdges || []) {
    if (kind === "tag") tagWeight.set(b, (tagWeight.get(b) || 0) + 1);
    add(a, b, kind, true);
    add(b, a, kind, false);
  }
  for (const list of connIndex.values()) {
    list.sort((x, y) =>
      (x.out === y.out ? 0 : x.out ? -1 : 1) ||
      baseName(x.path).localeCompare(baseName(y.path)));
  }
}

const baseName = (p) =>
  isTag(p) ? "#" + p.slice(TAG_PREFIX.length)
  : p.startsWith("ghost:") ? (ghostNames[p] || p.slice(6))
  : p.slice(p.lastIndexOf("/") + 1);

/* How many files carry each tag, which is what sizes its node. */
let tagWeight = new Map();

function addTagNode(id, near) {
  const n = addNode({
    id, name: "#" + id.slice(TAG_PREFIX.length), dir: false, group: "tag",
    size: 0, mtime: 0, parent: null, tag: true,
  }, near);
  n.tag = true;
  n.r = Math.max(4.5, Math.min(14, 4.5 + 1.6 * Math.log2(
    1 + (tagWeight.get(id) || 1))));
  return n;
}

function hitsNote(term, shown, total) {
  if (shown) {
    return shown < total
      ? `${total - shown} more outside this folder`
      : (shown >= 200 ? "more matches than shown — narrow the search" : "");
  }
  return total ? `nothing here — ${total} outside this folder`
               : `nothing called “${term}”`;
}

/* The folder a path is in, relative to the root, trimmed from the left when
   it is long: the end of a path is the part that identifies it. Trimming here
   rather than with CSS avoids the bidi trick that would otherwise be needed,
   which moves the slashes about. */
function dirLabel(p, cap = 34) {
  const dir = p.slice(0, p.lastIndexOf("/"));
  if (dir === ROOT) return "./";
  const rel = (dir.startsWith(ROOT + "/") ? dir.slice(ROOT.length + 1) : dir) + "/";
  return rel.length > cap ? "…" + rel.slice(rel.length - cap + 1) : rel;
}

function renderConnections(n) {
  const box = $("p-links");
  box.textContent = "";
  const list = (n && connIndex && connIndex.get(n.id)) || [];
  const suggested = (n && suggestIndex.get(n.id)) || [];
  if (!list.length && !suggested.length) { box.hidden = true; return; }
  box.hidden = false;
  box.classList.toggle("closed", !connOpen);

  const outs = list.filter((c) => c.out).length;
  const head = document.createElement("button");
  head.className = "conn-head";
  head.title = "Files linked to this one — click one to grow it into the graph (c)";
  const caret = document.createElement("span");
  caret.className = "caret";
  caret.textContent = "\u25be";
  const label = document.createElement("span");
  label.textContent =
    `${list.length} connection${list.length === 1 ? "" : "s"}`;
  const detail = document.createElement("span");
  detail.className = "muted";
  detail.textContent = `${outs} out \u00b7 ${list.length - outs} in`
    + (suggested.length ? ` \u00b7 ${suggested.length} suggested` : "");
  head.append(caret, label, detail);
  head.addEventListener("click", () => {
    connOpen = !connOpen;
    renderConnections(n);
  });
  box.appendChild(head);
  if (!connOpen) return;

  const rows = document.createElement("div");
  rows.className = "conn-list";
  for (const c of list.slice(0, 300)) {
    const row = document.createElement("button");
    row.className = "conn " + c.kind;
    row.title = c.path;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = c.out ? "\u2192" : "\u2190";
    const name = document.createElement("span");
    name.className = "nm";
    name.textContent = baseName(c.path);
    const where = document.createElement("span");
    where.className = "where";
    // a tag or an unwritten note has no folder to name
    where.textContent = c.kind === "ghost" ? "not written yet"
      : c.kind === "tag" ? "tag" : dirLabel(c.path, 18);
    row.append(arrow, name, where);
    row.addEventListener("click", () => focusOn(c.path));
    rows.appendChild(row);
  }
  box.appendChild(rows);
  renderSuggestions(n, box);
}

/* Open the first few levels on launch, smallest folders first so the node
   budget buys as much breadth as possible. Used when cortex is pointed at a
   project rather than a whole home directory. */
async function autoGrow(maxDepth, budget) {
  for (let d = 1; d <= maxDepth; d++) {
    const level = [...nodes.values()]
      .filter((n) => n.dir && !expanded.has(n.id) && n.depth === d)
      .sort((a, b) => (a.kids || 0) - (b.kids || 0));
    if (!level.length) return;
    for (const n of level) {
      if (nodes.size >= budget) return;
      await expand(n.id, { quiet: true });
    }
  }
}

/* Throw away the graph and rebuild it around one folder. Nothing outside it is
   drawn, searched, or reachable until you come back out. */
async function setScope(rawRoot) {
  if (nodes.size) loadGit();          // the new folder may be a different repo
  viewRoot = rawRoot.id;
  nodes.clear();
  links = [];
  linkKeys.clear();
  expanded.clear();
  matches.clear();
  closePanel();
  qbox.value = "";

  const rn = addNode(Object.assign({}, rawRoot, { parent: null }), null);
  rn.x = 0; rn.y = 0;
  await expand(rn.id);
  // narrowing to a folder is a request to see that folder, so open it up even
  // when the launch root was left lazy
  const depth = viewRoot === ROOT ? AUTO.depth : Math.max(AUTO.depth, 3);
  if (depth > 0) await autoGrow(depth, AUTO.budget || 700);

  applySemantic();
  updateScopeUI();
  if (sidebarOpen && rootDirs) renderSidebar();
  needsFit = true;
  reheat(1);
}

function isolate(id) {
  const n = nodes.get(id);
  if (!n || !n.dir || n.id === viewRoot) return;
  toast(`showing only ${n.name}`);
  return setScope(n);
}

async function showFullMap() {
  if (viewRoot === ROOT) return;
  try {
    await setScope(await api("/api/root"));
    toast("showing the whole map");
  } catch (err) { toast("could not reload the root", true); }
}

function updateScopeUI() {
  const pill = $("scope");
  const narrowed = viewRoot !== ROOT;
  pill.hidden = !narrowed;
  if (narrowed) {
    const name = viewRoot.split("/").pop() || viewRoot;
    $("scope-name").textContent = name;
    pill.title = `Showing only ${viewRoot.replace(ROOT, "~")} — click for the whole map`;
    qbox.placeholder = `search inside ${name}`;
  } else {
    qbox.placeholder = `search everything under ${CFG.title || "~"}`;
  }
}

/* ---------------------------------------------------------------- sidebar
   A list of the folders directly under the launch root, used as a project
   switcher: pick one and the graph is rebuilt around it. Closing the sidebar
   puts the whole map back, so it is never a mode you can get stuck in. */

async function toggleSidebar(on) {
  const next = on === undefined ? !sidebarOpen : !!on;
  if (next === sidebarOpen) return;
  sidebarOpen = next;
  $("sidebar").hidden = !sidebarOpen;
  document.body.classList.toggle("sidebar-open", sidebarOpen);

  placeChrome();
  if (sidebarOpen) {
    await fillSidebar();
    $("sb-filter").focus();
  } else {
    await showFullMap();          // closing means "show me everything again"
  }
  needsFit = true;
  reheat(0.6);
}

async function fillSidebar() {
  const list = $("sb-list");
  if (!rootDirs) {
    list.innerHTML = '<div class="sb-empty">reading…</div>';
    try {
      const kids = await api("/api/children", { path: ROOT });
      rootDirs = kids.filter((k) => k.dir);
    } catch (err) {
      list.innerHTML = '<div class="sb-empty">could not read the root</div>';
      return;
    }
  }
  renderSidebar();
}

function renderSidebar() {
  const list = $("sb-list");
  const term = $("sb-filter").value.trim().toLowerCase();
  const shown = rootDirs.filter((d) => !term || d.name.toLowerCase().includes(term));
  list.textContent = "";

  const row = (label, count, active, onClick, extraClass) => {
    const b = document.createElement("button");
    b.className = "sb-row" + (active ? " on" : "") + (extraClass ? " " + extraClass : "");
    b.innerHTML = '<span class="bullet"></span>'
      + `<span class="nm"></span><span class="ct">${count}</span>`;
    b.querySelector(".nm").textContent = label;   // never trust a filename as html
    b.addEventListener("click", onClick);
    list.appendChild(b);
    return b;
  };

  if (!term) {
    row(`All of ${CFG.title || "~"}`, rootDirs.length, viewRoot === ROOT,
        () => showFullMap(), "all");
  }
  if (!shown.length) {
    const d = document.createElement("div");
    d.className = "sb-empty";
    d.textContent = term ? `nothing matching “${term}”` : "no folders here";
    list.appendChild(d);
    return;
  }
  for (const dir of shown) {
    row(dir.name, dir.kids ?? "", viewRoot === dir.id, () => pickFromSidebar(dir));
  }
}

async function pickFromSidebar(dir) {
  if (viewRoot === dir.id) return;
  await setScope(dir);
  renderSidebar();
}

/* Grow the graph along a real path until that node exists, then select it. */
async function revealPath(abs, line) {
  if (!abs.startsWith(ROOT)) { toast("outside the mapped root", true); return; }
  // a link can point outside the folder we narrowed to; widen back out for it
  if (viewRoot !== ROOT && !abs.startsWith(viewRoot + "/")) await showFullMap();
  const rel = abs.slice(viewRoot.length).split("/").filter(Boolean);
  let cur = viewRoot;
  for (let i = 0; i < rel.length; i++) {
    if (!expanded.has(cur)) await expand(cur, { quiet: true });
    cur = cur + "/" + rel[i];
    if (!nodes.has(cur)) break;
  }
  const node = nodes.get(abs);
  if (!node) { toast("could not locate that file", true); return; }
  select(node);
  if (line) setLine(line);
  centerOn(node);
}

/* A line the selection should open at, from a content-search hit. Shown in the
   reader's header, passed to the editor, and forgotten as soon as the
   selection moves. */
function setLine(line) {
  selLine = line || null;
  // a tour being recorded keeps the line a search hit landed on
  const last = tourRec && tourRec.stops[tourRec.stops.length - 1];
  if (last && sel && last.path === sel.id && selLine) { last.line = selLine; renderTourCard(); }
  const meta = $("p-meta");
  if (selLine && meta && !meta.textContent.includes("line ")) {
    meta.textContent += `  ·  line ${selLine}`;
  }
  const pre = $("p-body").querySelector(".codeview");
  if (!pre || !selLine) return;
  const gutter = pre.querySelectorAll(".ln")[selLine - 1];
  if (gutter) {
    gutter.classList.add("hit-line");
    gutter.scrollIntoView({ block: "center" });
  }
}

// ----------------------------------------------------------------- physics
const CELL = 140, REPULSE = 3000, SPRING = 0.024, DAMP = 0.85, GRAVITY = 0.0022;

function simulate(decay = true) {
  if (alpha < ALPHA_MIN) return false;
  const all = [...nodes.values()];
  if (!all.length) return false;

  const grid = new Map();
  for (const n of all) {
    const key = ((n.x / CELL) | 0) + ":" + ((n.y / CELL) | 0);
    let bucket = grid.get(key);
    if (!bucket) { bucket = []; grid.set(key, bucket); }
    bucket.push(n);
  }

  for (const n of all) {
    const gx = (n.x / CELL) | 0, gy = (n.y / CELL) | 0;
    for (let i = -1; i <= 1; i++) {
      for (let j = -1; j <= 1; j++) {
        const bucket = grid.get((gx + i) + ":" + (gy + j));
        if (!bucket) continue;
        for (const m of bucket) {
          if (m === n) continue;
          let dx = n.x - m.x, dy = n.y - m.y;
          let d2 = dx * dx + dy * dy;
          if (d2 > CELL * CELL * 2.25) continue;
          if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
          const d = Math.sqrt(d2), f = REPULSE / d2;
          n.vx += (dx / d) * f; n.vy += (dy / d) * f;
        }
      }
    }
  }

  for (const l of links) {
    const a = nodes.get(l.a), b = nodes.get(l.b);
    if (!a || !b) continue;
    const semantic = l.kind !== "tree";
    const rest = semantic ? 170 : 52 + a.r + b.r;
    let dx = b.x - a.x, dy = b.y - a.y;
    const d = Math.hypot(dx, dy) || 0.01;
    const f = (d - rest) * (semantic ? SPRING * 0.4 : SPRING);
    dx /= d; dy /= d;
    a.vx += dx * f; a.vy += dy * f;
    b.vx -= dx * f; b.vy -= dy * f;
  }

  for (const n of all) {
    if (n.id === viewRoot) { n.x = 0; n.y = 0; n.vx = 0; n.vy = 0; continue; }
    n.vx -= n.x * GRAVITY; n.vy -= n.y * GRAVITY;
    if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
    n.vx *= DAMP; n.vy *= DAMP;
    const speed = Math.hypot(n.vx, n.vy);
    if (speed > 14) { n.vx = (n.vx / speed) * 14; n.vy = (n.vy / speed) * 14; }
    n.x += n.vx * alpha; n.y += n.vy * alpha;
  }

  if (decay) alpha *= COOL;
  return true;
}

// ------------------------------------------------------------------ render
const toScreenX = (x) => x * cam.s + cam.x;
const toScreenY = (y) => y * cam.s + cam.y;

function visible(n) {
  // the path between two files stays in sight whatever is filtered out
  if (pathSet && pathSet.has(n.id)) return !hist || !hist.show || hist.show.has(n.id);
  if (pathOnly && pathSet) return false;
  // in the history, only what existed at that point
  if (hist && hist.show && !hist.show.has(n.id)) return false;
  if (hidden.has(n.group)) return false;
  if (focusMode && sel) {
    if (n === sel) return true;
    return neighbours.has(n.id);
  }
  return true;
}
let neighbours = new Set();
function recomputeNeighbours() {
  dirty = true;
  neighbours = new Set();
  if (!sel) return;
  for (const l of links) {
    if (l.a === sel.id) neighbours.add(l.b);
    else if (l.b === sel.id) neighbours.add(l.a);
  }
}

function draw() {
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  // links
  ctx.lineWidth = 1;
  for (const l of links) {
    const a = nodes.get(l.a), b = nodes.get(l.b);
    if (!a || !b || !visible(a) || !visible(b)) continue;
    const semantic = l.kind !== "tree";
    if (semantic && !showSemantic) continue;
    const lit = sel && (l.a === sel.id || l.b === sel.id);
    if (semantic) {
      ctx.strokeStyle = lit ? "#34d399dd"
        : l.kind === "code" ? "#60a5fa2e"
        : l.kind === "tag" ? "#c084fc38" : "#34d3992e";
      ctx.lineWidth = lit ? 1.6 : 1;
    } else {
      ctx.strokeStyle = lit ? "#a9c8ffcc" : "#1e293b";
      ctx.lineWidth = lit ? 1.5 : 1;
    }
    ctx.beginPath();
    ctx.moveTo(toScreenX(a.x), toScreenY(a.y));
    ctx.lineTo(toScreenX(b.x), toScreenY(b.y));
    ctx.stroke();
  }
  drawSuggestions();

  // nodes
  for (const n of nodes.values()) {
    if (!visible(n)) continue;
    const x = toScreenX(n.x), y = toScreenY(n.y);
    const r = Math.max(1.8, n.r * cam.s);
    if (x < -60 || x > W + 60 || y < -60 || y > H + 60) continue;
    const c = COLOR[n.group] || COLOR.other;
    const active = n === sel || n === hover || matches.has(n.id);

    if (active || n.fresh) {
      ctx.shadowColor = c;
      ctx.shadowBlur = active ? 22 : 10;
    }
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    if (n.ghost) {
      // a note not written yet: an outline where it will go
      ctx.fillStyle = "#0b1120";
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.setLineDash([2.5, 2.5]);
      ctx.strokeStyle = c;
      ctx.lineWidth = 1.4;
      ctx.stroke();
      ctx.setLineDash([]);
    } else if (n.tag) {
      ctx.fillStyle = "#0b1120";
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = c;
      ctx.lineWidth = Math.max(1.2, r * 0.22);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, Math.max(1, r * 0.3), 0, Math.PI * 2);
      ctx.fillStyle = c;
      ctx.fill();
    } else if (n.dir && expanded.has(n.id)) {
      ctx.fillStyle = "#0b1120";
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = c;
      ctx.lineWidth = Math.max(1.4, r * 0.28);
      ctx.stroke();
    } else {
      ctx.fillStyle = c;
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    if (n === sel) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(x, y, r + 5, 0, Math.PI * 2);
      ctx.stroke();
    }

    if (drafts.has(n.id)) {
      // unsaved changes: a dashed ring, so a draft set aside is not forgotten
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "#ffd166";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, r + (n === sel ? 9 : 4), 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const state = showGit && gitStates.get(n.id);
    if (state && r > 2.4) {
      const inside = state === "inside";
      const gr = Math.max(1.8, r * (inside ? 0.24 : 0.34));
      const off = r * 0.72;
      ctx.beginPath();
      ctx.arc(x + off, y - off, gr, 0, Math.PI * 2);
      ctx.fillStyle = GIT_COLOR[state];
      ctx.fill();
      ctx.strokeStyle = "#070910";
      ctx.lineWidth = 1.1;
      ctx.stroke();
    }
  }
  ctx.shadowBlur = 0;
  drawOverlays();
  drawCaption();

  // labels, in a separate pass, with cheap collision avoidance
  const boxes = [];
  labelPlan = [];
  const fits = (bx, by, bw, bh) => {
    for (let i = boxes.length - 1, seen = 0; i >= 0 && seen < 220; i--, seen++) {
      const o = boxes[i];
      if (bx < o[0] + o[2] && bx + bw > o[0] && by < o[1] + o[3] && by + bh > o[1])
        return false;
    }
    return true;
  };
  if (!showLabels) { shownLabels = new Set(); return; }

  // A label that was visible last frame keeps priority, so a node drifting
  // past another one cannot make its label strobe.
  const ordered = [...nodes.values()].sort((p, q) =>
    (shownLabels.has(p.id) ? 0 : 1) - (shownLabels.has(q.id) ? 0 : 1)
    || q.r - p.r);
  const nextShown = new Set();
  for (const n of ordered) {
    if (!visible(n)) continue;
    const active = n === sel || n === hover || matches.has(n.id);
    if (!active && cam.s < 0.4) continue;
    if (!active && !n.dir && cam.s < 0.85) continue;
    const bypass = n === sel || n === hover;
    const x = toScreenX(n.x), y = toScreenY(n.y);
    if (x < -40 || x > W + 40 || y < -40 || y > H + 40) continue;
    const r = Math.max(1.8, n.r * cam.s);
    const size = active ? 12.5 : Math.max(9.5, Math.min(12.5, 11 * Math.sqrt(cam.s)));
    ctx.font = `${active || n.dir ? 600 : 400} ${size}px ui-sans-serif, system-ui, sans-serif`;
    const full = disk ? diskLabel(n) : n.name;
    const cap = disk ? 46 : 34;
    const text = full.length > cap ? full.slice(0, cap - 2) + "…" : full;
    const w = ctx.measureText(text).width;
    const bx = x - w / 2, by = y + r + 3, bh = size + 3;
    if (!bypass && !fits(bx, by, w, bh)) continue;
    boxes.push([bx, by, w, bh]);
    nextShown.add(n.id);
    labelPlan.push({ text, x, y: by, size, active, dir: n.dir, w, bh, bx });
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    if (active) {
      ctx.fillStyle = "#04060bcc";
      ctx.fillRect(bx - 4, by - 2, w + 8, bh + 2);
    }
    ctx.fillStyle = active ? "#f4f7fc"
      : n.dir ? "#c4d0e4" : "#7f8da6";
    ctx.fillText(text, x, by);
  }
  shownLabels = nextShown;
}

/* Run the simulation without painting, so the first frame the user sees is
   already spread out rather than a ball of overlapping circles. */
function warmup(steps) {
  for (let i = 0; i < steps; i++) simulate(false);
  alpha = 0.3;                    // let it settle the rest of the way on screen
  dirty = true;
}

let frameCount = 0, drawCount = 0;

function frame() {
  frameCount++;
  const moving = simulate();
  if (needsFit) { needsFit = false; warmup(180); fit(); }
  stepFlight();
  tickModes();
  const animating = Date.now() < animateUntil;
  if (moving || dirty || animating) { drawCount++; draw(); dirty = false; }
  requestAnimationFrame(frame);
}

/* Read-only introspection, so "is it still repainting?" is a question that can
   be answered instead of guessed at. Nothing in the UI depends on this. */
CFG.debug = () => ({
  alpha: Number(alpha.toFixed(4)), frozen: alpha < ALPHA_MIN, dirty,
  frames: frameCount, draws: drawCount,
  nodes: nodes.size, links: links.length, labels: shownLabels.size,
  selected: sel ? sel.id : null, cam: { s: cam.s, x: cam.x, y: cam.y },
});

/* A test seam. The stub-DOM suites drive the real app.js, and selecting a node
   through a synthetic canvas click would test the hit-testing maths rather
   than the thing under test. Returns false when that path is not in the graph,
   which is itself worth asserting. */
CFG.debug.pulse = () => pulse();
CFG.debug.expand = (id) => expand(id);
CFG.debug.saveLayout = () => { layoutDirty = true; return saveLayout(); };
CFG.debug.at = (id) => nodes.get(id);
CFG.debug.key = (name) =>
  keyHandler({ key: name, target: null, preventDefault() {},
               metaKey: false, ctrlKey: false, altKey: false, shiftKey: false });
CFG.debug.select = (id) => {
  const n = nodes.get(id);
  if (n) select(n);
  return !!n;
};
CFG.debug.visible = (id) => { const n = nodes.get(id); return !!n && visible(n); };
CFG.debug.modes = () => ({
  live: !!live, disk: !!disk, history: hist ? hist.v : null,
  path: pathHops ? pathHops.map((h) => h.id) : null,
  tour: tourPlay ? tourPlay.i : null, recording: tourRec ? tourRec.stops.length : null,
  views: viewsList.map((v) => v.name),
});

// -------------------------------------------------------------- navigation
function fit(pad = 90) {
  const pts = [...nodes.values()].filter(visible);
  if (!pts.length) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of pts) {
    minX = Math.min(minX, n.x - n.r); maxX = Math.max(maxX, n.x + n.r);
    minY = Math.min(minY, n.y - n.r); maxY = Math.max(maxY, n.y + n.r);
  }
  const panelW = (panel.classList.contains("open") && !maximized) ? 520 : 0;
  const left = sidebarInset();
  const availW = Math.max(200, W - panelW - left - pad * 2);
  const availH = Math.max(200, H - pad * 2 - 70);
  const s = Math.max(0.06, Math.min(1.15,
    Math.min(availW / Math.max(1, maxX - minX), availH / Math.max(1, maxY - minY))));
  cam.s = s;
  cam.x = left + pad + availW / 2 - ((minX + maxX) / 2) * s;
  cam.y = pad + 40 + availH / 2 - ((minY + maxY) / 2) * s;
  showZoom();
}

function centerOn(n) {
  const panelW = (panel.classList.contains("open") && !maximized) ? 520 : 0;
  const left = sidebarInset();
  cam.s = Math.max(cam.s, 1.1);
  cam.x = left + (W - left - panelW) / 2 - n.x * cam.s;
  cam.y = H / 2 - n.y * cam.s;
  showZoom();
}

function zoomAt(cx, cy, factor) {
  const next = Math.max(0.05, Math.min(8, cam.s * factor));
  cam.x = cx - (cx - cam.x) * (next / cam.s);
  cam.y = cy - (cy - cam.y) * (next / cam.s);
  cam.s = next;
  showZoom();
}
const showZoom = () => {
  $("z-level").textContent = Math.round(cam.s * 100) + "%";
  dirty = true;
};

function pick(mx, my) {
  let best = null, bestD = Infinity;
  for (const n of nodes.values()) {
    if (!visible(n)) continue;
    const d = Math.hypot(toScreenX(n.x) - mx, toScreenY(n.y) - my);
    const hit = Math.max(8, n.r * cam.s + 6);
    if (d < hit && d < bestD) { bestD = d; best = n; }
  }
  return best;
}

// ------------------------------------------------------------- interaction
let dragging = null, panning = false, travel = 0, lastPt = { x: 0, y: 0 };
let shiftDown = false;

stage.addEventListener("mousedown", (e) => {
  lastPt = { x: e.clientX, y: e.clientY };
  travel = 0;
  shiftDown = !!e.shiftKey;
  flight = null;                  // a hand on the camera outranks a flight
  touched = Date.now();
  const n = pick(e.clientX, e.clientY);
  if (n) { dragging = n; n.pinned = true; }
  else { panning = true; stage.classList.add("panning"); }
});

window.addEventListener("mousemove", (e) => {
  const dx = e.clientX - lastPt.x, dy = e.clientY - lastPt.y;
  travel += Math.abs(dx) + Math.abs(dy);
  lastPt = { x: e.clientX, y: e.clientY };
  if (dragging) {
    dragging.x += dx / cam.s; dragging.y += dy / cam.s;
    reheat(0.35);
    return;
  }
  if (panning) { cam.x += dx; cam.y += dy; dirty = true; return; }
  const n = pick(e.clientX, e.clientY);
  if (n !== hover) { hover = n; stage.classList.toggle("overnode", !!n); dirty = true; }
});

window.addEventListener("mouseup", () => {
  if (dragging) {
    dragging.pinned = false;
    if (travel < 4) {
      // shift-click: the chain of links from what was selected to here
      if (shiftDown && sel && sel !== dragging && !sel.tag && !dragging.tag) {
        showPath(sel, dragging);
      }
      select(dragging);
    }
    dragging = null;
  } else if (panning && travel < 4 && !maximized) {
    closePanel();
  }
  panning = false;
  stage.classList.remove("panning");
});

stage.addEventListener("dblclick", async (e) => {
  const n = pick(e.clientX, e.clientY);
  if (!n) { fit(); return; }
  if (!n.dir) { select(n); return; }
  if (expanded.has(n.id)) collapse(n.id);
  else await expand(n.id);
  // the preceding click already opened the panel, so its expand/collapse
  // button and contents list are now a step behind
  if (sel === n) { buildActions(n); renderBody(n); }
});

stage.addEventListener("wheel", (e) => {
  e.preventDefault();
  closeMenu();
  flight = null;
  touched = Date.now();
  zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0013));
}, { passive: false });

/* Right-click is where people look for what can be done to a thing. */
stage.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const n = pick(e.clientX, e.clientY);
  if (n) {
    const prior = sel;
    select(n);
    openMenu(e.clientX, e.clientY, n.name, menuFor(n, prior));
  } else {
    openMenu(e.clientX, e.clientY, null, backgroundMenu());
  }
});

// -------------------------------------------------------------------- panel
const panel = $("panel");
const fmtSize = (b) =>
  b < 1024 ? b + " B"
  : b < 1048576 ? (b / 1024).toFixed(1) + " KB"
  : b < 1073741824 ? (b / 1048576).toFixed(1) + " MB"
  : (b / 1073741824).toFixed(2) + " GB";

function closePanel() {
  closeEditor(false);
  setMax(false);
  panel.classList.remove("open");
  $("p-links").hidden = true;
  sel = null; selLine = null; focusMode = false; recomputeNeighbours();
  dirty = true;
  placeChrome();
}

/* Full-screen the reader. The rendered content is reused as-is, so a PDF is
   not reloaded and you keep your scroll position and page. */
function setMax(on) {
  maximized = !!on && panel.classList.contains("open");
  panel.classList.toggle("max", maximized);
  const btn = $("p-max");
  if (btn) {
    btn.innerHTML = maximized ? "exit &#10530;" : "full &#10530;";
    btn.title = maximized ? "Leave full screen (esc)" : "Full screen (m)";
    btn.classList.toggle("on", maximized);
  }
  dirty = true;
  placeChrome();
}

function select(n) {
  // leaving a file mid-edit keeps the draft; it is not lost, only set aside
  if (editing && editing.path !== n.id) closeEditor(false);
  if (heldDraft !== n.id) heldDraft = null;   // coming back resumes the draft
  sel = n;
  selLine = null;
  focusMode = false;
  recomputeNeighbours();
  panel.classList.add("open");
  $("p-kind").textContent = n.ghost ? "unwritten" : n.tag ? "tag"
    : n.missing ? "gone" : n.dir ? "folder" : (n.group === "other" ? "file" : n.group);
  $("p-name").textContent = n.name;
  $("p-path").textContent = n.ghost ? "linked to, but not written yet"
    : n.tag ? "a tag, not a file" : n.id.replace(ROOT, "~");
  describe(n);
  renderGitChip(n);
  buildActions(n);
  renderConnections(n);
  renderBody(n);
  placeChrome();
  if (tourRec) addStop(n);
}

let editBtn = null;               // the reader's "edit", shown once we know it is text
let openDropdown = null;          // the one action menu that may be open

function describe(n) {
  $("p-meta").textContent = n.tag || n.missing ? ""
    : (n.dir ? `${n.kids || 0} item${n.kids === 1 ? "" : "s"}` : fmtSize(n.size)) +
      (n.mtime ? "  ·  " + new Date(n.mtime * 1000).toLocaleString() : "");
}

function buildActions(n) {
  const bar = $("p-acts");
  bar.textContent = "";
  editBtn = null;

  if (n.tag) {
    // nothing on disk to edit, page or open
    const b = document.createElement("button");
    b.className = "ghost";
    b.textContent = focusMode ? "showing everything" : "focus";
    b.title = "hide everything not carrying this tag";
    b.addEventListener("click", () => {
      focusMode = !focusMode;
      recomputeNeighbours();
      buildActions(n);
    });
    bar.appendChild(b);
    return;
  }

  const mk = (label, cls, fn, title) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    if (title) b.title = title;
    b.addEventListener("click", fn);
    bar.appendChild(b);
    return b;
  };

  if (!n.dir && !READ_ONLY) {
    editBtn = mk("edit", "ghost", () => startEdit(n),
                 "edit it here (i) — ctrl-s saves");
    editBtn.hidden = !drafts.has(n.id);   // shown once the preview says it is text
  }
  if (primaryEditor) {
    mk(primaryEditor.label + (primaryEditor.gui ? " ⧉" : ""), "primary",
       () => act("edit", n.id, primaryEditor.id, selLine),
       editorTitle(primaryEditor));
  }
  if (!n.dir && TERMINAL === "here") {
    mk("read", "ghost", () => act("read", n.id, null, selLine),
       "page through it in the terminal");
  }

  const others = editors.filter((e) => !primaryEditor || e.id !== primaryEditor.id);
  if (others.length) {
    dropdown(bar, "editor ▾", others.map((ed) => ({
      label: ed.label + (ed.gui ? "  ⧉" : ""),
      title: editorTitle(ed),
      run: () => act("edit", n.id, ed.id, selLine),
    })));
  }

  if (TERMINAL) {
    mk(TERMINAL === "here" ? "shell here" : "terminal here", "ghost",
       () => act("shell", n.id),
       TERMINAL === "here" ? "drop a shell into this directory, in your terminal"
                           : "open a terminal window in this folder");
  }
  if (!SNAPSHOT) {
    mk("open ⧉", "ghost", () => act("open", n.id), "hand to the desktop default app");
    dropdown(bar, "file ▾", fileItems(n).filter((it) => it !== "-"));
  }
  if (n.dir) {
    mk(expanded.has(n.id) ? "collapse" : "expand", "ghost", () => {
      expanded.has(n.id) ? collapse(n.id) : expand(n.id);
      buildActions(n);
    });
  }
  if (n.dir && n.id !== viewRoot) {
    mk("only this", "ghost", () => isolate(n.id),
       "drop everything else and graph only this folder (o)");
  }
  if (viewRoot !== ROOT) {
    mk("whole map", "ghost", () => showFullMap(), "back to everything (b)");
  }
  mk("focus", "ghost", () => {
    focusMode = !focusMode;
    recomputeNeighbours();
    toast(focusMode ? "focus mode — only linked nodes" : "showing everything");
  }, "hide everything not connected to this node");
}

function editorTitle(ed) {
  if (ed.gui) return `open in ${ed.label}`;
  return TERMINAL === "here" ? `open in ${ed.label} in your terminal`
                             : `open in ${ed.label} in a terminal window`;
}

/* A button that opens a short menu, for the actions that would otherwise
   crowd the bar. Only one is ever open, and one listener closes it. */
function dropdown(bar, label, items) {
  const wrap = document.createElement("div");
  wrap.className = "more";
  const trigger = document.createElement("button");
  trigger.className = "ghost";
  trigger.textContent = label;
  const menu = document.createElement("div");
  menu.className = "menu";
  for (const it of items) {
    const item = document.createElement("button");
    item.textContent = it.label;
    if (it.title) item.title = it.title;
    item.addEventListener("click", () => {
      menu.classList.remove("open");
      openDropdown = null;
      it.run();
    });
    menu.appendChild(item);
  }
  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    const opening = !menu.classList.contains("open");
    if (openDropdown && openDropdown !== menu) openDropdown.classList.remove("open");
    menu.classList.toggle("open", opening);
    openDropdown = opening ? menu : null;
  });
  wrap.append(trigger, menu);
  bar.appendChild(wrap);
  return menu;
}

/* Whether the reader can also be the editor: text it can show is text it can
   edit, except where what it shows is a conversion of the file. */
const editable = (p) => !READ_ONLY && (p.kind === "markdown" || p.kind === "text")
  && p.ext !== ".ipynb" && p.ext !== ".docx";

function setEditable(n, ok) {
  if (editBtn && sel === n) editBtn.hidden = !ok;
  if (sel === n) sel.editable = ok;
}

async function renderBody(n) {
  // the editor is the body while it is open; a refresh must not wipe it
  if (editing && editing.path === n.id) return;
  const body = $("p-body");
  body.classList.remove("fill");     // reset before any early return below
  if (n.ghost) return renderGhostBody(n, body);
  if (n.tag) return renderTagBody(n, body);
  if (n.missing) {
    body.textContent = "";
    body.appendChild(hint("This file is not here now: it existed at this point in the "
      + "history, and was deleted or renamed later."));
    return;
  }
  if (n.dir && disk) {
    body.textContent = "";
    renderDiskBody(n, body);
    return;
  }
  if (n.dir) {
    const kids = expanded.has(n.id) ? null : await api("/api/children", { path: n.id })
      .catch(() => null);
    body.innerHTML = "";
    const note = document.createElement("div");
    note.className = "empty";
    note.innerHTML = expanded.has(n.id)
      ? "This folder is open in the graph. <b>Double-click</b> the node to collapse it."
      : "<b>Double-click</b> the node to grow this folder into the graph.";
    body.appendChild(note);
    if (kids && kids.length) {
      const list = document.createElement("div");
      list.className = "md";
      list.innerHTML = "<h3>Contents</h3>";
      const ul = document.createElement("ul");
      for (const k of kids.slice(0, 200)) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.className = "wiki";
        a.textContent = k.name + (k.dir ? "/" : "");
        a.addEventListener("click", () => revealPath(k.id));
        li.appendChild(a);
        ul.appendChild(li);
      }
      list.appendChild(ul);
      body.appendChild(list);
    }
    return;
  }

  // a file left mid-edit opens straight back into the editor, draft and all --
  // unless the editor was closed on purpose and this is only a refresh
  if (drafts.has(n.id) && !READ_ONLY) {
    if (heldDraft !== n.id) return startEdit(n);
    body.textContent = "";
    body.appendChild(hint("<b>Unsaved changes.</b> Press <b>i</b> to keep editing."));
    return;
  }

  body.innerHTML = '<div class="empty">reading…</div>';
  let p;
  try { p = await api("/api/preview", { path: n.id }); }
  catch (err) { body.innerHTML = '<div class="empty">could not read this file</div>'; return; }
  if (sel !== n || (editing && editing.path === n.id)) return;   // moved on
  setEditable(n, editable(p));

  body.textContent = "";

  if (p.kind === "markdown") {
    body.appendChild(markdownView(p.text || "", n.id));
    if (p.truncated) body.appendChild(hint("truncated — open it in your editor for the rest"));
    return;
  }

  if (p.kind === "text") {
    const pre = document.createElement("pre");
    pre.className = "codeview";
    pre.innerHTML = window.HL.block(p.text || "", p.lang || "text");
    body.appendChild(pre);
    if (p.truncated) body.appendChild(hint(`first ${p.lines} lines`));
    return;
  }

  if (p.kind === "pdf") {
    body.classList.add("fill");        // let it take all the height that is left
    const frame = document.createElement("iframe");
    frame.className = "viewer-frame";
    frame.src = rawURL(n.id);
    body.appendChild(frame);
    return;
  }

  if (p.kind === "image") {
    body.classList.add("fill");
    const img = document.createElement("img");
    img.className = "viewer-img";
    img.src = rawURL(n.id);
    body.appendChild(img);
    return;
  }

  if (p.kind === "video" || p.kind === "audio") {
    if (p.kind === "video") body.classList.add("fill");
    const el = document.createElement(p.kind);
    el.controls = true;
    el.className = "viewer-img";
    el.src = rawURL(n.id);
    if (p.kind === "audio") el.style.width = "100%";
    body.appendChild(el);
    return;
  }

  if (p.kind === "table") {
    const wrap = document.createElement("div");
    wrap.className = "tablewrap";
    const t = document.createElement("table");
    for (const row of p.rows || []) {
      const tr = document.createElement("tr");
      for (const cell of row) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.appendChild(td);
      }
      t.appendChild(tr);
    }
    wrap.appendChild(t);
    body.appendChild(wrap);
    if (p.truncated) body.appendChild(hint("first 300 rows"));
    return;
  }

  const msgs = {
    binary: "Binary file — nothing to render here. Hand it to the desktop with <b>open ⧉</b>.",
    toobig: "Too large to preview safely. Open it in your editor instead.",
    error: "Could not read this file.",
  };
  body.appendChild(hint(msgs[p.kind] || "No preview for this type."));
}

/* Rendered markdown whose links lead somewhere: a relative link or a
   [[wikilink]] grows the graph to its target instead of navigating the
   window. Shared by the reader and the editor's preview. */
function markdownView(text, path) {
  const dirOf = path.slice(0, path.lastIndexOf("/"));
  const div = document.createElement("div");
  div.className = "md";
  div.innerHTML = window.MD.render(text, {
    // a link to a note not written yet is drawn as one
    wiki: (name) => !ghostNames[ghostKey(name)],
    resolve: (u) => /^(https?:|data:)/.test(u) ? u
      : rawURL(u.startsWith("/") ? u : dirOf + "/" + u),
  });
  div.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if (!a) return;
    if (a.dataset.rel) {
      e.preventDefault();
      let rel = a.dataset.rel.split("#")[0];
      try { rel = decodeURIComponent(rel); } catch (err) { /* left as written */ }
      revealPath(rel.startsWith("/") ? rel : normalize(dirOf + "/" + rel));
    } else if (a.dataset.wiki) {
      e.preventDefault();
      jumpToWiki(a.dataset.wiki);
    }
  });
  return div;
}

/* A tag's "contents" are the notes carrying it. */
function renderTagBody(n, body) {
  body.textContent = "";
  const carriers = (connIndex && connIndex.get(n.id)) || [];
  const note = document.createElement("div");
  note.className = "empty";
  note.textContent = carriers.length
    ? `Carried by ${carriers.length} note${carriers.length === 1 ? "" : "s"}.`
    : "Nothing carries this tag any more.";
  body.appendChild(note);
  if (!carriers.length) return;

  const list = document.createElement("div");
  list.className = "md";
  const ul = document.createElement("ul");
  for (const c of carriers) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.className = "wiki";
    a.textContent = baseName(c.path);
    a.title = c.path;
    a.addEventListener("click", () => focusOn(c.path));
    li.appendChild(a);
    ul.appendChild(li);
  }
  list.appendChild(ul);
  body.appendChild(list);
}

/* Follow a connection. A tag has no path to reveal, so it is selected where it
   already stands in the graph. */
function focusOn(id) {
  if (isGhost(id)) {
    const g = nodes.get(id) || addGhostNode(id, sel);
    linkUp([id]);
    updateStats();
    select(g);
    flyTo(camOn(g));
    return;
  }
  if (!isTag(id)) return revealPath(id);
  let n = nodes.get(id);
  if (!n) {
    // a tag too thinly carried to draw itself is still worth seeing when you
    // click it, so bring it in beside whatever you clicked it from
    n = addTagNode(id, sel);
    linkUp([id]);
    updateStats();
    reheat(0.35);
  }
  select(n);
  centerOn(n);
}

function hint(html) {
  const d = document.createElement("div");
  d.className = "empty";
  d.style.marginTop = "14px";
  d.innerHTML = html;
  return d;
}

function normalize(path) {
  const parts = [];
  for (const seg of path.split("/")) {
    if (!seg || seg === ".") continue;
    if (seg === "..") parts.pop();
    else parts.push(seg);
  }
  return "/" + parts.join("/");
}

async function jumpToWiki(name) {
  // a note that does not exist yet: go to its ghost, which offers to make it
  if (ghostNames[ghostKey(name)]) { focusOn(ghostKey(name)); return; }
  try {
    const r = await api("/api/search", { q: name });
    const stem = name.toLowerCase();
    const best = (r.results || []).find((h) => {
      const nm = h.node.name.toLowerCase();
      return nm === stem || nm.replace(/\.[^.]+$/, "") === stem;
    }) || (r.results || [])[0];
    if (!best) { toast(`no note called “${name}”`, true); return; }
    await revealPath(best.node.id);
  } catch (err) { toast("search failed", true); }
}

// ------------------------------------------------------------------ actions
async function act(kind, path, editor, line) {
  try {
    const r = await post("/api/action", { kind, path, editor, line });
    if (!r.ok) { toast(r.error || "action failed", true); return; }
    const how = r.how || (r.terminal ? "here" : "app");
    toast(how === "here" ? "→ switch to your terminal"
        : how === "window" ? "opening a terminal window…" : "opening…");
  } catch (err) { toast("could not reach cortex", true); }
}

let toastTimer = null;
function toast(msg, warn) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("warn", !!warn);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2800);
}

/* ----------------------------------------------------------------- editing

   The reader can also be the editor, so a note can be changed without a
   terminal or another app. It is a plain textarea on purpose: what it saves
   is exactly what was typed, byte for byte, with the file's own line endings
   put back.

   Nothing is written until you save, and a save never lands on top of a
   change it did not see: the server compares the version the editor loaded
   with what is on disk now and refuses if they differ. Unsaved text is never
   thrown away either. Leave a file mid-edit and the draft is kept, marked on
   its node, and the file opens straight back into it. */
const drafts = new Map();          // path -> {text, version}, while unsaved
let editing = null;                // the open editor, see startEdit
let heldDraft = null;              // a draft left on purpose, with esc or close

const isDirty = () => !!editing && editing.area.value !== editing.base;

async function startEdit(n, opts = {}) {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  if (!n || n.dir || n.tag) return;
  if (editing && editing.path === n.id) { editing.area.focus(); return; }
  heldDraft = null;

  let src;
  try { src = await query("/api/source", { path: n.id }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!src || !src.ok) { toast((src && src.error) || "cannot edit this here", true); return; }
  if (sel !== n) return;                        // the selection moved on
  if (!src.writable) { toast("this file is read-only on disk", true); return; }
  if (editing) closeEditor(false);

  const draft = drafts.get(n.id);
  const note = /\.(md|markdown|mdx|txt)$/i.test(n.id);
  const body = $("p-body");
  body.classList.remove("fill");
  body.textContent = "";
  panel.classList.add("editing");

  const bar = document.createElement("div");
  bar.className = "ed-bar";
  const state = document.createElement("span");
  state.className = "ed-state";
  const button = (label, title, fn, cls = "ghost sm") => {
    const b = document.createElement("button");
    b.className = cls;
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", fn);
    return b;
  };
  const revert = button("revert", "throw away what you typed since the last save",
                        () => revertEdit());
  const preview = button("preview", "see it rendered", () => togglePreview());
  const save = button("save", "write it to disk (ctrl-s)", () => saveEdit(), "primary sm");
  const done = button("close", "back to reading (esc) — unsaved changes are kept",
                      () => closeEditor());
  preview.hidden = !/\.(md|markdown|mdx)$/i.test(n.id);
  bar.append(state, revert, preview, save, done);

  const notice = document.createElement("div");
  notice.className = "ed-notice";
  notice.hidden = true;

  const area = document.createElement("textarea");
  area.className = "ed-area";
  area.spellcheck = note;
  area.value = draft ? draft.text : src.text;
  const view = document.createElement("div");
  view.className = "ed-preview";
  view.hidden = true;
  body.append(bar, notice, area, view);

  editing = {
    path: n.id, base: src.text, version: draft ? draft.version : src.version,
    unit: indentUnit(n.id, src.text), area, view, state, notice,
    save, revert, preview, previewing: false,
  };
  area.addEventListener("input", () => refreshEditState());
  area.addEventListener("keydown", (e) => editorKey(e));
  refreshEditState();

  if (src.mixed && !draft) {
    showNotice(`This file mixes line endings. Saving will use ${src.eol.toUpperCase()} `
               + "throughout.", []);
  }
  if (draft && draft.version !== src.version) {
    showNotice("It changed on disk while you had unsaved changes.", [
      ["keep mine", "save over it with what is here", () => saveEdit(true)],
      ["take theirs", "throw away your changes and load what is on disk",
       () => reloadEdit()],
    ]);
  } else if (draft) {
    showNotice("Your unsaved changes are back.", []);
  }

  area.focus();
  const at = opts.atEnd ? area.value.length
    : selLine ? offsetOfLine(area.value, selLine) : 0;
  area.setSelectionRange(at, at);
  if (selLine) area.scrollTop = Math.max(0, (selLine - 6) * 20);
}

/* Bring the bar, the draft and the node's mark up to date with the text. */
function refreshEditState() {
  const ed = editing;
  if (!ed) return;
  const dirty = isDirty();
  ed.state.textContent = dirty ? "● unsaved" : "editing";
  ed.state.classList.toggle("dirty", dirty);
  ed.save.disabled = !dirty;
  ed.revert.hidden = !dirty;
  const had = drafts.has(ed.path);
  if (dirty) drafts.set(ed.path, { text: ed.area.value, version: ed.version });
  else drafts.delete(ed.path);
  if (had !== dirty) { updateDrafts(); repaint(); }
}

async function saveEdit(force = false) {
  const ed = editing;
  if (!ed || ed.saving) return false;
  const text = ed.area.value;
  if (!force && text === ed.base) return true;
  ed.state.textContent = "saving…";
  ed.saving = true;
  let r;
  try { r = await post("/api/save", { path: ed.path, text, version: ed.version, force }); }
  catch (err) {
    ed.saving = false;
    refreshEditState();
    toast("could not reach cortex — nothing was saved", true);
    return false;
  }
  ed.saving = false;
  if (!r.ok) {
    refreshEditState();
    if (r.conflict) {
      showNotice("It changed on disk since you opened it.", [
        ["keep mine", "save over it with what is here", () => saveEdit(true)],
        ["take theirs", "throw away your changes and load what is on disk",
         () => reloadEdit()],
      ]);
    } else if (r.gone) {
      showNotice("It is no longer on the disk — deleted or moved.", [
        ["save it again", "write it back where it was", () => saveEdit(true)],
      ]);
    } else {
      toast(r.error || "could not save", true);
    }
    return false;
  }
  ed.version = r.version;
  ed.base = text;
  if (editing === ed) {
    hideNotice();
    refreshEditState();
  } else {
    // the editor closed while the save was on its way; anything typed after
    // pressing save is still a draft, now based on the version just written
    const d = drafts.get(ed.path);
    if (d && d.text === text) drafts.delete(ed.path);
    else if (d) d.version = r.version;
    updateDrafts();
    repaint();
  }
  if (!nodes.has(ed.path)) {
    // saved back after it had vanished from the disk: bring its node back
    await settle(parentOf(ed.path));
    const back = nodes.get(ed.path);
    if (back && editing === ed) select(back);
  }
  const n = nodes.get(ed.path);
  if (n) {
    n.size = r.size; n.mtime = Date.now() / 1000; n.fresh = true;
    n.r = radiusOf(n);
    if (sel === n) describe(n);
  }
  toast("saved");
  return true;
}

function fileVanished() {
  showNotice("It is no longer on the disk — deleted or moved by something else.", [
    ["save it again", "write it back where it was", () => saveEdit(true)],
    ["discard", "close it and forget what was typed", () => discardEdit()],
  ]);
}

function discardEdit() {
  if (!editing) return;
  drafts.delete(editing.path);
  updateDrafts();
  repaint();
  closeEditor(false);
  closePanel();
}

function revertEdit() {
  if (!editing) return;
  editing.area.value = editing.base;
  refreshEditState();
  editing.area.focus();
}

/* Take what is on disk, dropping what was typed. */
async function reloadEdit() {
  const ed = editing;
  if (!ed) return;
  let src;
  try { src = await query("/api/source", { path: ed.path }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!src || !src.ok) { toast((src && src.error) || "could not reload it", true); return; }
  if (editing !== ed) return;
  ed.base = src.text;
  ed.version = src.version;
  ed.area.value = src.text;
  hideNotice();
  refreshEditState();
}

/* Leave the editor. Unsaved text stays behind as a draft, never lost. */
function closeEditor(rerender = true) {
  if (!editing) return;
  const left = editing;
  editing = null;
  panel.classList.remove("editing");
  if (drafts.has(left.path)) toast("unsaved changes kept — they are marked on the node");
  if (rerender && sel && sel.id === left.path) {
    heldDraft = left.path;
    renderBody(sel);
  }
}

function togglePreview() {
  const ed = editing;
  if (!ed) return;
  ed.previewing = !ed.previewing;
  ed.view.textContent = "";
  if (ed.previewing) ed.view.appendChild(markdownView(ed.area.value, ed.path));
  ed.view.hidden = !ed.previewing;
  ed.area.hidden = ed.previewing;
  ed.preview.textContent = ed.previewing ? "source" : "preview";
  if (!ed.previewing) ed.area.focus();
}

function showNotice(text, choices) {
  const box = editing && editing.notice;
  if (!box) return;
  box.textContent = "";
  const msg = document.createElement("span");
  msg.textContent = text;
  box.appendChild(msg);
  for (const [label, title, fn] of choices) {
    const b = document.createElement("button");
    b.className = "ghost sm";
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", fn);
    box.appendChild(b);
  }
  box.hidden = false;
}
function hideNotice() {
  if (editing) editing.notice.hidden = true;
}

function updateDrafts() {
  const el = $("stat-drafts");
  el.hidden = !drafts.size;
  el.textContent = `${drafts.size} unsaved`;
  el.title = [...drafts.keys()].map((p) => p.replace(ROOT, "~")).join("\n");
}

/* --------------------------------------------------- typing in the editor

   A textarea knows nothing about code or lists. These few keys are the ones
   whose absence is felt at once: tab indents, enter keeps the indentation and
   carries a list on, ctrl-s saves, esc leaves. */
function editorKey(e) {
  const area = e.target;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    if (e.stopPropagation) e.stopPropagation();   // the window would save it again
    saveEdit();
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    closeEditor();
    return;
  }
  if (mod || e.altKey) return;
  if (e.key === "Tab") {
    e.preventDefault();
    indentLines(area, editing ? editing.unit : "  ", e.shiftKey);
    return;
  }
  if (e.key === "Enter" && !e.shiftKey && continueLine(area)) e.preventDefault();
}

/* What a tab inserts: whatever the file already indents with. */
function indentUnit(path, text) {
  if (/(^|\n)\t/.test(text) || /\.go$|(^|\/)makefile$/i.test(path)) return "\t";
  const spaced = text.match(/^ +(?=\S)/gm) || [];
  const widths = spaced.map((s) => s.length).filter((w) => w <= 8);
  const step = widths.length ? Math.min(...widths) : 0;
  if (step === 2 || step === 4) return " ".repeat(step);
  return /\.(py|rs|java|c|h|cc|cpp|hpp|cs|kt|swift|php)$/i.test(path) ? "    " : "  ";
}

/* Insert as typing would, so ctrl-z can take it back. execCommand is the only
   way into the undo history a script has; setRangeText is the fallback. */
function insertText(area, text) {
  const done = typeof document.execCommand === "function"
    && document.execCommand(text ? "insertText" : "delete", false, text);
  if (!done) {
    area.setRangeText(text, area.selectionStart, area.selectionEnd, "end");
    area.dispatchEvent(new Event("input"));
  }
}

function replaceRange(area, start, end, text) {
  area.setSelectionRange(start, end);
  insertText(area, text);
}

function indentLines(area, unit, outdent) {
  const v = area.value;
  const s = area.selectionStart, e = area.selectionEnd;
  if (!outdent && s === e) { insertText(area, unit); return; }
  const start = v.lastIndexOf("\n", s - 1) + 1;
  // a selection ending at the start of a line does not take that line along
  const last = e > s && v[e - 1] === "\n" ? e - 1 : e;
  const nl = v.indexOf("\n", last);
  const stop = nl === -1 ? v.length : nl;
  const width = unit === "\t" ? 4 : unit.length;
  const strip = new RegExp(`^(\\t| {1,${width}})`);
  const block = v.slice(start, stop).split("\n")
    .map((ln) => outdent ? ln.replace(strip, "") : unit + ln).join("\n");
  replaceRange(area, start, stop, block);
  area.setSelectionRange(start, start + block.length);
}

/* Enter keeps the line's indentation, and carries a list on to its next item;
   enter on an item with nothing in it ends the list instead. */
function continueLine(area) {
  const v = area.value, at = area.selectionStart;
  if (at !== area.selectionEnd) return false;
  const lineStart = v.lastIndexOf("\n", at - 1) + 1;
  const line = v.slice(lineStart, at);
  const m = /^([ \t]*)((?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[[ xX]\][ \t]+)?)?/.exec(line);
  const indent = m[1], marker = m[2] || "";
  if (!indent && !marker) return false;
  if (marker && line.trim() === marker.trim()) {
    replaceRange(area, lineStart, at, "");
    return true;
  }
  let next = marker.replace(/\[[xX]\]/, "[ ]");
  const num = /^(\d+)([.)])/.exec(next);
  if (num) next = `${Number(num[1]) + 1}${num[2]}` + next.slice(num[0].length);
  insertText(area, "\n" + indent + next);
  return true;
}

function offsetOfLine(text, line) {
  let at = 0;
  for (let i = 1; i < line; i++) {
    const nl = text.indexOf("\n", at);
    if (nl === -1) return text.length;
    at = nl + 1;
  }
  return at;
}

/* ------------------------------------------------------ files and folders

   New, rename and trash, so that a note can be made, named and thrown away
   without a file manager. Trash is the desktop's own, so anything thrown
   away here can be restored from there. There is no permanent delete. */
const parentOfNode = (n) => n.dir ? n.id : parentOf(n.id);

/* The disk changed under `dir` because we changed it: show it now rather
   than on the next pulse. */
async function settle(dir) {
  if (expanded.has(dir)) await refreshDir(dir);
  updateStats();
  reheat(0.3);
}

async function fileOp(body) {
  let r;
  try { r = await post("/api/fs", body); }
  catch (err) { toast("could not reach cortex", true); return null; }
  if (!r.ok) { toast(r.error || "that did not work", true); return null; }
  return r;
}

async function newEntry(folder, kind = "file") {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  const where = folder === ROOT ? (CFG.title || "~") : folder.replace(ROOT, "~");
  const name = await ask({
    title: kind === "dir" ? "New folder" : "New note",
    text: `in ${where}` + (kind === "dir" ? "" : " — a name without an extension becomes a .md note"),
    value: kind === "dir" ? "" : "untitled.md",
    select: kind === "dir" ? null : [0, "untitled".length],
    ok: "create",
  });
  if (!name) return;
  const r = await fileOp({ op: kind === "dir" ? "mkdir" : "new", path: folder, name });
  if (!r) return;
  await settle(folder);
  await revealPath(r.path);
  if (kind !== "dir" && sel && sel.id === r.path) startEdit(sel, { atEnd: true });
}

async function renameEntry(n) {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  if (!n || n.tag || n.id === ROOT) return;
  const dot = n.dir ? -1 : n.name.lastIndexOf(".");
  const name = await ask({
    title: `Rename ${n.dir ? "folder" : "file"}`,
    value: n.name,
    select: [0, dot > 0 ? dot : n.name.length],
    ok: "rename",
  });
  if (!name || name === n.name) return;
  const r = await fileOp({ op: "rename", path: n.id, name });
  if (!r) return;               // nothing moved, and the editor is untouched

  // carry what belonged to the old name across to the new one; the draft is
  // current, since the editor writes it on every keystroke
  const under = (p) => p === n.id || p.startsWith(n.id + "/");
  if (editing && under(editing.path)) closeEditor(false);
  const was = { x: n.x, y: n.y, open: expanded.has(n.id) };
  for (const [path, d] of [...drafts]) {
    if (under(path)) {
      drafts.delete(path);
      drafts.set(r.path + path.slice(n.id.length), d);
    }
  }
  updateDrafts();
  if (n.id === viewRoot) await showFullMap();
  await settle(parentOf(n.id));
  const moved = nodes.get(r.path);
  if (moved) { moved.x = was.x; moved.y = was.y; }
  if (moved && was.open) await expand(r.path);
  await revealPath(r.path);
  toast(`renamed to ${name}`);
}

async function trashEntry(n) {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  if (!n || n.tag || n.id === ROOT) return;
  const under = (p) => p === n.id || p.startsWith(n.id + "/");
  const lost = [...drafts.keys()].filter(under).length;
  const what = n.dir ? `the folder ${n.name} and everything in it` : n.name;
  const yes = await ask({
    title: "Move to the trash?",
    text: `${what} will go to your desktop's trash, where it can be restored.`
      + (!lost ? "" : n.dir
          ? ` Unsaved changes to ${lost} file${lost === 1 ? "" : "s"} in it will be lost.`
          : " Its unsaved changes will be lost."),
    ok: "move to trash", danger: true,
  });
  if (!yes) return;
  const r = await fileOp({ op: "trash", path: n.id });
  if (!r) return;
  if (editing && under(editing.path)) closeEditor(false);
  for (const path of [...drafts.keys()]) {
    if (under(path)) drafts.delete(path);
  }
  updateDrafts();
  if (n.id === viewRoot || viewRoot.startsWith(n.id + "/")) await showFullMap();
  await settle(parentOf(n.id));
  toast(`moved ${n.name} to the trash`);
}

function copyPath(n) {
  navigator.clipboard.writeText(n.id).then(() => toast("path copied"),
                                           () => toast("copy blocked", true));
}

/* Everything that can be done to a node, for the right-click menu and the
   reader's file menu alike. "-" separates groups. */
function fileItems(n) {
  if (n.tag) return [];
  const items = [];
  const write = !READ_ONLY;
  if (n.dir && write) {
    items.push({ label: "New note here…", key: "n", run: () => newEntry(n.id) });
    items.push({ label: "New folder here…", run: () => newEntry(n.id, "dir") });
    items.push("-");
  }
  items.push({ label: "Show in file manager", run: () => act("reveal", n.id) });
  items.push({ label: "Copy path", run: () => copyPath(n) });
  if (write && n.id !== ROOT) {
    items.push("-");
    items.push({ label: "Rename…", key: "F2", run: () => renameEntry(n) });
    items.push({ label: "Move to trash", key: "del", danger: true,
                 run: () => trashEntry(n) });
  }
  return items;
}

/* ------------------------------------------------------------ ask sheet

   The browser's prompt() and confirm() block the page and look like an
   error. This is the same question, in the window's own clothes. */
let askResolve = null;

function ask({ title, text = "", value = null, select = null, ok = "ok",
               danger = false }) {
  return new Promise((resolve) => {
    if (askResolve) askResolve(null);
    askResolve = resolve;
    closeMenu();
    $("ask-title").textContent = title;
    $("ask-text").textContent = text;
    $("ask-text").hidden = !text;
    const input = $("ask-input");
    input.hidden = value === null;
    input.value = value || "";
    $("ask-error").hidden = true;
    const okBtn = $("ask-ok");
    okBtn.textContent = ok;
    okBtn.classList.toggle("danger", danger);
    $("ask").classList.add("open");
    if (value === null) { okBtn.focus(); return; }
    input.focus();
    if (select) input.setSelectionRange(select[0], select[1]);
    else input.select();
  });
}

function answerAsk(yes) {
  const input = $("ask-input");
  let answer = null;
  if (yes) {
    answer = input.hidden ? true : input.value.trim();
    if (answer === "") {
      $("ask-error").textContent = "give it a name";
      $("ask-error").hidden = false;
      return;
    }
  }
  $("ask").classList.remove("open");
  const resolve = askResolve;
  askResolve = null;
  if (resolve) resolve(answer);
}

/* --------------------------------------------------------- context menu */
function openMenu(x, y, title, items) {
  const m = $("ctx");
  m.textContent = "";
  if (title) {
    const head = document.createElement("div");
    head.className = "ctx-head";
    head.textContent = title;
    m.appendChild(head);
  }
  let rows = 0;
  for (const it of items) {
    if (it === "-") {
      if (!m.lastChild || m.lastChild.className === "ctx-sep") continue;
      const sep = document.createElement("div");
      sep.className = "ctx-sep";
      m.appendChild(sep);
      continue;
    }
    const b = document.createElement("button");
    b.className = "ctx-item" + (it.danger ? " danger" : "");
    const label = document.createElement("span");
    label.textContent = it.label;
    b.appendChild(label);
    if (it.key) {
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = it.key;
      b.appendChild(k);
    }
    b.addEventListener("click", () => { closeMenu(); it.run(); });
    m.appendChild(b);
    rows++;
  }
  if (!rows) return;
  m.hidden = false;
  menuOpen = true;
  m.style.left = Math.max(8, Math.min(x, W - 250)) + "px";
  m.style.top = Math.max(8, Math.min(y, H - 34 * rows - 60)) + "px";
}

let menuOpen = false;
function closeMenu() {
  $("ctx").hidden = true;
  menuOpen = false;
}

function menuFor(n, prior = null) {
  if (n.ghost) {
    return READ_ONLY || SNAPSHOT ? []
      : [{ label: "Create this note", run: () => createGhost(n) }];
  }
  if (n.tag) {
    return [{ label: focusMode ? "Show everything" : "Focus on this tag", key: "f",
              run: () => { focusMode = !focusMode; recomputeNeighbours(); } }];
  }
  const items = [];
  const from = prior && prior !== n && !prior.tag ? prior : null;
  if (from) {
    items.push({ label: `Path from ${from.name} to here`, key: "⇧click",
                 run: () => showPath(from, n) });
    items.push("-");
  }
  if (SNAPSHOT) {
    if (n.dir) items.push({ label: expanded.has(n.id) ? "Collapse" : "Expand", key: "e",
                            run: () => { expanded.has(n.id) ? collapse(n.id) : expand(n.id); } });
    return items;
  }
  if (!n.dir) {
    if (!READ_ONLY) items.push({ label: "Edit here", key: "i", run: () => startEdit(n) });
    if (primaryEditor) {
      items.push({ label: `Open in ${primaryEditor.label}`, key: "⏎",
                   run: () => act("edit", n.id, primaryEditor.id) });
    }
    items.push({ label: "Open with default app", run: () => act("open", n.id) });
  } else {
    items.push({ label: expanded.has(n.id) ? "Collapse" : "Expand", key: "e",
                 run: () => { expanded.has(n.id) ? collapse(n.id) : expand(n.id);
                              if (sel === n) buildActions(n); } });
    if (n.id !== viewRoot) {
      items.push({ label: "Only this folder", key: "o", run: () => isolate(n.id) });
    }
    if (TERMINAL) {
      items.push({ label: "Open a terminal here", run: () => act("shell", n.id) });
    }
  }
  items.push("-");
  return items.concat(fileItems(n));
}

function backgroundMenu() {
  const here = nodes.get(viewRoot);
  const name = here ? here.name : (CFG.title || "~");
  const items = [];
  if (!READ_ONLY) {
    items.push({ label: `New note in ${name}…`, key: "n", run: () => newEntry(viewRoot) });
    items.push({ label: "New folder…", run: () => newEntry(viewRoot, "dir") });
    items.push("-");
  }
  items.push({ label: "Open another folder…", key: "p", run: () => openPlaces() });
  if (viewRoot !== ROOT) items.push({ label: "Whole map", key: "b", run: () => showFullMap() });
  items.push({ label: "Fit to screen", key: "0", run: () => fit() });
  return items;
}

/* ---------------------------------------------------------------- places

   Opening a folder used to mean quitting and typing a path into a terminal.
   Now it is this sheet: type a path (folders complete as you type), or pick
   a saved project or a recent folder. The server swaps what it maps and the
   window reloads around it -- the one way to be sure nothing from the old
   folder lingers in the new one. */
let placesData = null, completeRows = [], completeIndex = -1, completeTimer = null;
let completedFor = null;          // what the suggestions on show were for

async function openPlaces() {
  closeMenu();
  $("places").classList.add("open");
  $("pl-path").value = "";
  hideComplete();
  $("pl-path").focus();
  try { placesData = await api("/api/places"); }
  catch (err) { toast("could not reach cortex", true); return; }
  renderPlaces();
}

function closePlaces() {
  $("places").classList.remove("open");
  hideComplete();
}

const shortPath = (p) =>
  CFG.home && (p === CFG.home || p.startsWith(CFG.home + "/"))
    ? "~" + p.slice(CFG.home.length) : p;

function renderPlaces() {
  const d = placesData;
  if (!d) return;

  const quick = $("pl-quick");
  quick.textContent = "";
  const chip = (label, path, title) => {
    const b = document.createElement("button");
    b.className = "ghost sm";
    b.textContent = label;
    b.title = title || path;
    b.addEventListener("click", () => openFolder(path));
    quick.appendChild(b);
  };
  if (d.parent) chip("↑ up one level", d.parent, `open ${shortPath(d.parent)}`);
  if (d.home && d.home !== d.root) chip("home", d.home);

  const lists = $("pl-lists");
  lists.textContent = "";
  const section = (title, rows, empty) => {
    const h = document.createElement("h3");
    h.textContent = title;
    lists.appendChild(h);
    if (!rows.length) {
      const e = document.createElement("div");
      e.className = "pl-empty";
      e.textContent = empty;
      lists.appendChild(e);
    }
    for (const r of rows) lists.appendChild(r);
  };

  const row = (name, path, opts = {}) => {
    const b = document.createElement("button");
    b.className = "pl-row" + (path === d.root ? " here" : "");
    b.title = path;
    if (name) {
      const nm = document.createElement("span");
      nm.className = "nm";
      nm.textContent = name;
      b.appendChild(nm);
    }
    const pth = document.createElement("span");
    pth.className = "pth";
    pth.textContent = shortPath(path);
    b.appendChild(pth);
    const tag = opts.missing ? "missing" : path === d.root ? "open now" : "";
    if (tag) {
      const t = document.createElement("span");
      t.className = "tag" + (opts.missing ? " bad" : "");
      t.textContent = tag;
      b.appendChild(t);
    }
    if (opts.forget) {
      const x = document.createElement("span");
      x.className = "x";
      x.textContent = "✕";
      x.title = "forget this project — the folder itself is not touched";
      x.addEventListener("click", (e) => { e.stopPropagation(); opts.forget(); });
      b.appendChild(x);
    }
    b.addEventListener("click", () => { if (!opts.missing) openFolder(path); });
    return b;
  };

  section("Projects", d.projects.map((p) => row(p.name, p.path, {
    missing: p.missing, forget: () => projectOp("forget", p.name),
  })), "None saved yet. Name the folder you are in, below, to keep it here.");
  section("Recent", d.recent.map((p) => row(p.split("/").pop() || "/", p)),
          "Folders you open will be listed here.");

  const named = d.projects.find((p) => p.path === d.root);
  const nameBox = $("pl-name");
  nameBox.value = named ? named.name : (d.root.split("/").pop() || "");
  $("pl-save-btn").textContent = named ? "rename project" : "save as project";

  const launcher = $("pl-launcher");
  launcher.textContent = "";
  const l = d.launcher || {};
  if (l.supported) {
    const msg = document.createElement("span");
    msg.textContent = l.installed ? "cortex is in your app menu."
                                  : "Start cortex without a terminal:";
    const b = document.createElement("button");
    b.className = "ghost sm";
    b.textContent = l.installed ? "remove it" : "add it to the app menu";
    b.addEventListener("click", () => launcherOp(l.installed ? "remove" : "install"));
    launcher.append(msg, b);
  }
  $("pl-quit").hidden = !CFG.canQuit;
}

async function projectOp(op, name) {
  let r;
  try { r = await post("/api/project", { op, name }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "that did not work", true); return; }
  placesData = r;
  renderPlaces();
  toast(op === "save" ? `saved as ${name}` : `forgot ${name}`);
}

async function saveProject() {
  const name = $("pl-name").value.trim();
  if (!name) { toast("give it a name", true); return; }
  const was = placesData && placesData.projects.find((p) => p.path === placesData.root);
  if (was && was.name !== name) await projectOp("forget", was.name);
  await projectOp("save", name);
}

async function launcherOp(op) {
  let r;
  try { r = await post("/api/launcher", { op }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "that did not work", true); return; }
  if (placesData) { placesData.launcher = r.launcher; renderPlaces(); }
  toast(op === "install" ? "added — look for cortex in your app menu"
                         : "removed from the app menu");
}

let reloading = false;
async function openFolder(path) {
  if (!path) return;
  if (drafts.size) {
    // asked, not refused: a draft whose file has gone would otherwise keep
    // this window in this folder for good
    const yes = await ask({
      title: "Leave unsaved changes behind?",
      text: `${drafts.size} file${drafts.size === 1 ? " has" : "s have"} unsaved `
        + "changes, which will be lost if another folder is opened now.",
      ok: "discard and open", danger: true,
    });
    if (!yes) return;
    drafts.clear();
    updateDrafts();
  }
  // the old folder's layout is saved while it is still the open one
  await saveLayout();
  let r;
  try { r = await post("/api/open", { path }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "could not open that", true); return; }
  reloading = true;
  if (location.reload) location.reload();
}

/* Another window on this cortex opened a different folder. */
let followed = false;
function followRoot() {
  if (followed) return;
  followed = true;
  if (drafts.size) {
    toast("another window opened a different folder — save is no longer possible here", true);
    return;
  }
  reloading = true;
  if (location.reload) location.reload();
}

function hideComplete() {
  const box = $("pl-complete");
  box.hidden = true;
  box.textContent = "";
  completeRows = [];
  completeIndex = -1;
}

async function completePath() {
  const typed = $("pl-path").value;
  if (!typed.trim()) { hideComplete(); completedFor = typed; return; }
  let r;
  try { r = await api("/api/dirs", { path: typed }); }
  catch (err) { return; }
  if ($("pl-path").value !== typed) return;          // typing moved on
  completedFor = typed;
  const box = $("pl-complete");
  box.textContent = "";
  completeRows = [];
  completeIndex = -1;
  for (const dir of r.dirs || []) {
    const b = document.createElement("button");
    b.className = "pl-row";
    const pth = document.createElement("span");
    pth.className = "pth";
    pth.textContent = shortPath(dir) + "/";
    b.appendChild(pth);
    b.addEventListener("click", () => openFolder(dir));
    b._path = dir;
    box.appendChild(b);
    completeRows.push(b);
  }
  box.hidden = !completeRows.length;
}

function moveComplete(step) {
  if (!completeRows.length) return;
  if (completeIndex >= 0) completeRows[completeIndex].classList.remove("on");
  completeIndex = (completeIndex + step + completeRows.length) % completeRows.length;
  const row = completeRows[completeIndex];
  row.classList.add("on");
  if (row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
}

function placesKey(e) {
  const input = $("pl-path");
  if (e.key === "Escape") {
    e.preventDefault();
    if (!$("pl-complete").hidden) hideComplete(); else closePlaces();
    return;
  }
  if (e.key === "ArrowDown") { e.preventDefault(); moveComplete(1); return; }
  if (e.key === "ArrowUp") { e.preventDefault(); moveComplete(-1); return; }
  if (e.key === "Tab" && input.value.trim()) {
    // complete the way a shell does, and never let tab walk out of the box
    // just because the suggestions are a keystroke behind
    e.preventDefault();
    tabComplete();
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    enterPath();
  }
}

/* Make sure the suggestions are for what is in the box right now. */
async function freshCompletions() {
  clearTimeout(completeTimer);
  if (completedFor !== $("pl-path").value) await completePath();
  return completeRows.map((r) => r._path);
}

async function tabComplete() {
  const input = $("pl-path");
  const picked = completeIndex >= 0 ? completeRows[completeIndex]._path : null;
  const dirs = picked ? [picked] : await freshCompletions();
  if (!dirs.length) return;
  // one folder: take it and go on into it; several: as far as they agree
  let common = dirs[0];
  for (const d of dirs.slice(1)) {
    let i = 0;
    while (i < common.length && i < d.length && common[i] === d[i]) i++;
    common = common.slice(0, i);
  }
  const next = shortPath(common) + (dirs.length === 1 ? "/" : "");
  if (next.length >= input.value.length) input.value = next;
  completePath();
}

/* Enter opens what is highlighted, or what was typed -- or, when what was
   typed is only the start of one folder's name, that folder. */
async function enterPath() {
  const input = $("pl-path");
  if (completeIndex >= 0) { openFolder(completeRows[completeIndex]._path); return; }
  const typed = input.value.trim();
  const dirs = await freshCompletions();
  const want = typed.replace(/\/+$/, "");
  const exact = dirs.find((d) => d === want || shortPath(d) === want);
  openFolder(exact || (dirs.length === 1 && !typed.endsWith("/") ? dirs[0] : typed));
}

/* -------------------------------------------------------------- lifecycle

   Closing the window is how cortex is quit when there is no terminal to
   press ctrl-c in, so the page says hello when it opens and goodbye when it
   closes, and the server leaves once the last one has gone. A reload says
   both, a moment apart, which the server allows for. */
const CLIENT = Math.random().toString(36).slice(2, 12);

function hello() {
  post("/api/hello", {}, { c: CLIENT }).catch(() => {});
}

function goodbye() {
  if (navigator.sendBeacon) {
    navigator.sendBeacon(String(apiURL("/api/bye", { c: CLIENT })), "bye");
  }
}

async function quitCortex() {
  if (drafts.size) {
    const yes = await ask({
      title: "Quit with unsaved changes?",
      text: `${drafts.size} file${drafts.size === 1 ? " has" : "s have"} unsaved changes, which will be lost.`,
      ok: "quit anyway", danger: true,
    });
    if (!yes) return;
    drafts.clear();
  }
  closePlaces();
  try { await post("/api/quit", {}); } catch (err) { /* gone already */ }
  serverGone(true);
}

/* The program behind the window has stopped: say so once, plainly, and stop
   asking it things. */
function serverGone(quit) {
  if (gone) return;
  gone = true;
  $("gone-text").textContent = quit
    ? "You quit it. Start it again from your app menu, or from a terminal with cortex."
    : TERMINAL === "here"
      ? "The program behind this window is no longer running — the terminal it was in may have been closed. Start it again with cortex."
      : "The program behind this window is no longer running. Start it again from your app menu.";
  $("gone").classList.add("open");
  if (quit && window.close) window.close();
}

/* ---------------------------------------------------------------- motion

   Most of the time the graph is frozen and costs nothing to show. A few
   things need it to keep drawing for a while -- the camera flying somewhere,
   a file that just changed glowing and fading, the history playing -- and
   each says for how long by pushing `animateUntil` out. When they are all
   done, the graph is frozen again. */
const SNAPSHOT = CFG.snapshot || null;
let animateUntil = 0;
const animateFor = (ms) => {
  animateUntil = Math.max(animateUntil, Date.now() + ms);
  dirty = true;
};

let flight = null;

/* The camera that puts `n` in the middle of the free part of the window. */
function camOn(n, s = Math.max(cam.s, 1.15)) {
  const panelW = (panel.classList.contains("open") && !maximized) ? 520 : 0;
  const left = sidebarInset();
  return { s, x: left + (W - left - panelW) / 2 - n.x * s, y: H / 2 - n.y * s };
}

/* The camera that frames all of these, the way fit() frames the graph. */
function camAround(list, pad = 140) {
  if (!list.length) return null;
  if (list.length === 1) return camOn(list[0]);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const n of list) {
    minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
    minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
  }
  const panelW = (panel.classList.contains("open") && !maximized) ? 520 : 0;
  const left = sidebarInset();
  const availW = Math.max(200, W - panelW - left - pad * 2);
  const availH = Math.max(200, H - pad * 2 - 70);
  const s = Math.max(0.08, Math.min(1.6,
    Math.min(availW / Math.max(1, maxX - minX), availH / Math.max(1, maxY - minY))));
  return { s, x: left + pad + availW / 2 - ((minX + maxX) / 2) * s,
           y: pad + 40 + availH / 2 - ((minY + maxY) / 2) * s };
}

function flyTo(to, ms = 700) {
  if (!to) return;
  flight = { from: { s: cam.s, x: cam.x, y: cam.y }, to: { s: to.s, x: to.x, y: to.y },
             t0: Date.now(), ms };
  animateFor(ms + 40);
}

/* One step of a flight. It travels through the world point at the middle
   of the screen and zooms in log space, so it neither swings wide nor rushes
   the last stretch. */
function stepFlight() {
  const f = flight;
  if (!f) return;
  const k = Math.min(1, (Date.now() - f.t0) / f.ms);
  const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
  const px = W / 2, py = H / 2;
  const fx = (px - f.from.x) / f.from.s, fy = (py - f.from.y) / f.from.s;
  const tx = (px - f.to.x) / f.to.s, ty = (py - f.to.y) / f.to.s;
  const s = Math.exp(Math.log(f.from.s) + (Math.log(f.to.s) - Math.log(f.from.s)) * e);
  cam = { s, x: px - (fx + (tx - fx) * e) * s, y: py - (fy + (ty - fy) * e) * s };
  if (k >= 1) { cam = { ...f.to }; flight = null; }
  showZoom();
}

/* --------------------------------------------------------------- chrome

   The floating pieces -- the dock, the timeline, the tour and path cards --
   centre themselves on the part of the canvas the sidebar and the reader are
   not covering. And on a narrow window the dock rides up a row rather than
   sit on top of the counts in the corner. */
function placeChrome() {
  const body = document.body;
  const left = sidebarOpen ? SIDEBAR_W : 0;
  const right = panel.classList.contains("open") && !maximized ? Math.min(520, W * 0.94) : 0;
  if (body.style && body.style.setProperty) {
    body.style.setProperty("--free-left", left + "px");
    body.style.setProperty("--free-right", right + "px");
  }
  const dock = $("dock"), stats = $("stats"), zoom = $("zoomer");
  const d = dock.getBoundingClientRect(), st = stats.getBoundingClientRect();
  const z = zoom.getBoundingClientRect();
  if (!(d.width > 0) || !(st.right >= 0)) return;
  const centre = left + (W - left - right) / 2;
  const clash = st.right + 10 > centre - d.width / 2
    || (z.left > 0 && right === 0 && centre + d.width / 2 > z.left - 10);
  body.classList.toggle("dock-high", clash);
}

/* ------------------------------------------------------------------- dock */
let popFor = null;

function openPop(kind, fill) {
  const pop = $("pop");
  if (popFor === kind) { closePop(); return; }
  popFor = kind;
  pop.textContent = "";
  pop.hidden = false;
  fill(pop);
}

function closePop() {
  $("pop").hidden = true;
  popFor = null;
}

function popHead(pop, text, extra) {
  const h = document.createElement("div");
  h.className = "pop-head";
  const t = document.createElement("span");
  t.textContent = text;
  const sp = document.createElement("span");
  sp.className = "spacer";
  h.append(t, sp);
  if (extra) h.appendChild(extra);
  pop.appendChild(h);
}

function popRow(pop, { key, name, sub, title, run, remove }) {
  const b = document.createElement("button");
  b.className = "pop-row";
  if (title) b.title = title;
  const k = document.createElement("span");
  k.className = "k";
  k.textContent = key || "";
  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = name;
  b.append(k, nm);
  if (sub) {
    const s = document.createElement("span");
    s.className = "sub";
    s.textContent = sub;
    b.appendChild(s);
  }
  if (remove) {
    const x = document.createElement("span");
    x.className = "x";
    x.textContent = "✕";
    x.title = remove.title;
    x.addEventListener("click", (e) => { e.stopPropagation(); remove.run(); });
    b.appendChild(x);
  }
  b.addEventListener("click", () => run());
  pop.appendChild(b);
  return b;
}

function popText(pop, text) {
  const d = document.createElement("div");
  d.className = "pop-empty";
  d.textContent = text;
  pop.appendChild(d);
}

function popFoot(pop, buttons) {
  const f = document.createElement("div");
  f.className = "pop-foot";
  for (const [label, run, cls] of buttons) {
    const b = document.createElement("button");
    b.className = cls || "ghost sm";
    b.textContent = label;
    b.addEventListener("click", run);
    f.appendChild(b);
  }
  pop.appendChild(f);
}

function dockState() {
  $("d-live").classList.toggle("on", !!live);
  $("d-history").classList.toggle("on", !!hist);
  $("d-tours").classList.toggle("on", !!(tourPlay || tourRec));
  $("d-disk").classList.toggle("on", !!disk);
}

/* ------------------------------------------------------------- grafting

   Put files on the graph with their folder chains, the way a search result
   is grafted on, without opening every folder on the way. A narrowed view
   widens if one of them lies outside it. */
async function ensureNodes(ids, { missing = false } = {}) {
  const want = [...new Set(ids)].filter((id) => typeof id === "string"
    && id.startsWith("/") && !nodes.has(id));
  if (!want.length) return true;
  if (viewRoot !== ROOT
      && want.some((id) => id !== viewRoot && !id.startsWith(viewRoot + "/"))) {
    await showFullMap();
  }
  let r;
  try { r = await post("/api/nodes", { paths: want }); }
  catch (err) { return false; }
  const grafted = [];
  for (const hit of (r && r.hits) || []) {
    // a file that is gone is only drawn by the history, which asks for them
    if (hit.node.missing && !missing) continue;
    grafted.push(...graft(hit));
  }
  if (grafted.length) {
    linkUp(grafted);
    updateStats();
    reheat(0.25);
  }
  return true;
}

function graft(hit) {
  let prev = nodes.get(viewRoot);
  const added = [];
  if (!prev) return added;
  const under = (id) => id.startsWith(viewRoot + "/");
  for (const anc of hit.ancestors || []) {
    if (anc.id === viewRoot || !under(anc.id)) continue;
    if (!nodes.has(anc.id)) added.push(anc.id);
    const a = addNode(anc, prev);
    addLink(prev.id, a.id, "tree");
    prev = a;
  }
  if (!under(hit.node.id)) return added;
  if (!nodes.has(hit.node.id)) added.push(hit.node.id);
  const n = addNode(hit.node, prev);
  addLink(prev.id, n.id, "tree");
  return added;
}

/* --------------------------------------------------------------- overlays

   Drawn over the nodes and under the labels: the chain between two files,
   the stop a tour is on, and -- added below -- what has just changed. */
function arrowHead(x1, y1, x2, y2, back, size) {
  const ang = Math.atan2(y2 - y1, x2 - x1);
  const tx = x2 - Math.cos(ang) * back, ty = y2 - Math.sin(ang) * back;
  ctx.beginPath();
  ctx.moveTo(tx, ty);
  ctx.lineTo(tx - Math.cos(ang - 0.42) * size, ty - Math.sin(ang - 0.42) * size);
  ctx.lineTo(tx - Math.cos(ang + 0.42) * size, ty - Math.sin(ang + 0.42) * size);
  ctx.closePath();
  ctx.fill();
}

function drawOverlays() {
  const now = Date.now();
  drawHeat(now);
  drawPath();
  if (tourPlay && sel) {
    const x = toScreenX(sel.x), y = toScreenY(sel.y);
    const r = Math.max(1.8, sel.r * cam.s);
    const breathe = 0.5 + 0.5 * Math.sin(now / 380);
    ctx.strokeStyle = `rgba(255, 209, 102, ${0.35 + 0.4 * breathe})`;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(x, y, r + 11 + breathe * 4, 0, Math.PI * 2);
    ctx.stroke();
    animateFor(200);
  }
}

/* ------------------------------------------------------------------ path

   How does main.py reach db.py? Select one and shift-click the other, and
   the chain of imports and links between them lights up: following imports
   in the direction they run when such a chain exists, and any link at all
   when it does not. Tags are left out -- two notes tagged #work are not
   "connected" in any sense worth a path. */
let pathHops = null;            // [{id, kind, out}] from the start, while shown
let pathSet = null;             // ids on it, which stay visible whatever the filters
let pathOnly = false;

function findPath(from, to) {
  if (!connIndex || from === to) return null;
  const usable = (c) => c.kind === "note" || c.kind === "code";
  const search = (directed) => {
    const prev = new Map([[from, null]]);
    const queue = [from];
    for (let i = 0; i < queue.length && !prev.has(to); i++) {
      for (const c of connIndex.get(queue[i]) || []) {
        if (!usable(c) || (directed && !c.out) || prev.has(c.path)) continue;
        prev.set(c.path, { from: queue[i], kind: c.kind, out: c.out });
        queue.push(c.path);
      }
    }
    if (!prev.has(to)) return null;
    const hops = [];
    for (let cur = to; cur !== from; cur = prev.get(cur).from) {
      const p = prev.get(cur);
      hops.unshift({ id: cur, kind: p.kind, out: p.out });
    }
    return hops;
  };
  const directed = search(true);
  if (directed) return { directed: true, hops: directed };
  const any = search(false);
  return any ? { directed: false, hops: any } : null;
}

async function showPath(a, b) {
  if (!a || !b || a === b || a.tag || b.tag) return;
  if (!connIndex) { toast("the links are still being indexed", true); return; }
  const found = findPath(a.id, b.id);
  if (!found) {
    pathHops = null;
    pathSet = null;
    renderPathCard(a, b, null);
    repaint();
    return;
  }
  const hops = [{ id: a.id, kind: null, out: null }, ...found.hops];
  await ensureNodes(hops.map((h) => h.id));
  for (let i = 1; i < hops.length; i++) addLink(hops[i - 1].id, hops[i].id, hops[i].kind);
  pathHops = hops;
  pathSet = new Set(hops.map((h) => h.id));
  renderPathCard(a, b, found);
  const onGraph = hops.map((h) => nodes.get(h.id)).filter(Boolean);
  flyTo(camAround(onGraph));
  repaint();
}

function clearPath() {
  if (!pathHops && $("pathcard").hidden) return false;
  pathHops = null;
  pathSet = null;
  pathOnly = false;
  $("pathcard").hidden = true;
  updateStats();
  repaint();
  return true;
}

function hopVerb(h) {
  if (h.kind === "code") return h.out ? "imports" : "is imported by";
  return h.out ? "links to" : "is linked from";
}

function renderPathCard(a, b, found) {
  const card = $("pathcard");
  card.textContent = "";
  card.hidden = false;
  const label = document.createElement("span");
  label.className = "pc-label";
  label.textContent = "path";
  card.appendChild(label);
  const stepBtn = (id) => {
    const s = document.createElement("button");
    s.className = "pc-step";
    s.textContent = baseName(id);
    s.title = id;
    s.addEventListener("click", () => {
      const n = nodes.get(id);
      if (n) { select(n); flyTo(camOn(n)); }
    });
    return s;
  };
  if (!found) {
    const none = document.createElement("span");
    none.className = "pc-none";
    none.textContent = `No chain of imports or links joins ${a.name} and ${b.name}.`;
    card.appendChild(none);
  } else {
    card.appendChild(stepBtn(pathHops[0].id));
    for (const h of pathHops.slice(1)) {
      const verb = document.createElement("span");
      verb.className = "pc-arrow";
      verb.textContent = hopVerb(h);
      card.append(verb, stepBtn(h.id));
    }
    const only = document.createElement("button");
    only.className = "ghost sm" + (pathOnly ? " on" : "");
    only.textContent = "only the path";
    only.title = "hide everything that is not on the path";
    only.addEventListener("click", () => {
      pathOnly = !pathOnly;
      only.classList.toggle("on", pathOnly);
      updateStats();
      reheat(0.2);
    });
    card.appendChild(only);
  }
  const x = document.createElement("button");
  x.className = "ghost sm";
  x.textContent = "✕";
  x.title = "clear the path (esc)";
  x.addEventListener("click", () => clearPath());
  card.appendChild(x);
}

function drawPath() {
  if (!pathHops) return;
  ctx.lineWidth = 2.6;
  ctx.strokeStyle = "#ffd166";
  ctx.fillStyle = "#ffd166";
  for (let i = 1; i < pathHops.length; i++) {
    const a = nodes.get(pathHops[i - 1].id), b = nodes.get(pathHops[i].id);
    if (!a || !b) continue;
    const ax = toScreenX(a.x), ay = toScreenY(a.y);
    const bx = toScreenX(b.x), by = toScreenY(b.y);
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(bx, by);
    ctx.stroke();
    // the arrow says which way the import or link runs
    const toward = pathHops[i].out;
    const [hx, hy, tx, ty, end] = toward ? [ax, ay, bx, by, b] : [bx, by, ax, ay, a];
    arrowHead(hx, hy, tx, ty, Math.max(1.8, end.r * cam.s) + 5, 9);
  }
  ctx.font = "700 10px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  pathHops.forEach((h, i) => {
    const n = nodes.get(h.id);
    if (!n) return;
    const x = toScreenX(n.x), y = toScreenY(n.y);
    const r = Math.max(1.8, n.r * cam.s);
    ctx.beginPath();
    ctx.arc(x - r - 7, y - r - 7, 8, 0, Math.PI * 2);
    ctx.fillStyle = "#ffd166";
    ctx.fill();
    ctx.fillStyle = "#1a1300";
    ctx.fillText(String(i + 1), x - r - 7, y - r - 6.5);
  });
}

/* ------------------------------------------------------------------ views

   A view is the way you are looking right now -- the folder it is narrowed
   to, the kinds hidden, the folders open, the camera -- kept under a name.
   The first nine are on the number keys. */
let viewsList = [];

async function loadViews() {
  if (SNAPSHOT) return;
  try { viewsList = (await api("/api/views")).views || []; }
  catch (err) { viewsList = []; }
}

function currentView(name) {
  return {
    name, scope: viewRoot, hidden: [...hidden], solo: soloGroup,
    semantic: showSemantic, labels: showLabels,
    expanded: [...expanded].filter((id) => id !== viewRoot && nodes.has(id)),
    selected: sel && !sel.tag && sel.id.startsWith("/") ? sel.id : null,
    cam: { s: cam.s, x: cam.x, y: cam.y },
  };
}

async function saveView() {
  const here = nodes.get(viewRoot);
  const name = await ask({
    title: "Save this view",
    text: "The folder in view, what is hidden, what is open and where you are "
      + "looking — kept under a name. The first nine are on the number keys.",
    value: here && viewRoot !== ROOT ? here.name : "",
    ok: "save",
  });
  if (!name) return;
  let r;
  try { r = await post("/api/views", { op: "save", view: currentView(name) }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "could not save the view", true); return; }
  viewsList = r.views || [];
  const slot = viewsList.findIndex((v) => v.name.toLowerCase() === name.toLowerCase());
  toast(slot >= 0 && slot < 9 ? `saved — press ${slot + 1} to come back to it`
                              : "view saved");
  if (popFor === "views") { popFor = null; openViews(); }
}

async function forgetView(v) {
  let r;
  try { r = await post("/api/views", { op: "forget", name: v.name }); }
  catch (err) { toast("could not reach cortex", true); return; }
  viewsList = (r && r.views) || viewsList.filter((x) => x !== v);
  if (popFor === "views") { popFor = null; openViews(); }
}

async function applyView(v) {
  if (!v) return;
  closePop();
  if (v.scope && v.scope !== viewRoot) {
    if (v.scope === ROOT) await showFullMap();
    else {
      let r;
      try { r = await post("/api/nodes", { paths: [v.scope] }); } catch (err) { r = null; }
      const hit = r && r.hits && r.hits[0];
      if (!hit || hit.node.missing || !hit.node.dir) {
        toast(`the folder of “${v.name}” is gone`, true);
      } else {
        await setScope(hit.node);
      }
    }
  }
  hidden.clear();
  for (const g of v.hidden || []) hidden.add(g);
  soloGroup = v.solo || null;
  afterFilterChange();
  showSemantic = v.semantic !== false;
  $("btn-links").classList.toggle("on", showSemantic);
  showLabels = v.labels !== false;

  // the folders the view had open, outermost first; and shut the others, or
  // it would not look the way it did
  const want = new Set(v.expanded || []);
  for (const id of [...expanded]) {
    if (id !== viewRoot && !want.has(id) && nodes.has(id)) collapse(id);
  }
  const order = [...want].sort((a, b) => a.split("/").length - b.split("/").length);
  for (const id of order) {
    if (!nodes.has(id)) await ensureNodes([id]);
    if (nodes.has(id) && !expanded.has(id)) await expand(id, { quiet: true });
  }
  const pick = v.selected && nodes.get(v.selected);
  if (pick) select(pick); else closePanel();
  if (v.cam) flyTo(v.cam, 600);
  updateStats();
  if (!SNAPSHOT) toast(`view: ${v.name}`);
}

function openViews() {
  openPop("views", (pop) => {
    popHead(pop, "Views");
    if (!viewsList.length) {
      popText(pop, "Nothing saved yet. Arrange the graph the way you want it — "
        + "narrowed to a folder, some kinds hidden, some folders open — and save "
        + "it here. The first nine are on keys 1 to 9.");
    }
    viewsList.forEach((v, i) => popRow(pop, {
      key: i < 9 ? String(i + 1) : "", name: v.name,
      sub: v.scope && v.scope !== ROOT ? baseName(v.scope) : "",
      title: `open this view${i < 9 ? ` (${i + 1})` : ""}`,
      run: () => applyView(v),
      remove: { title: "forget this view", run: () => forgetView(v) },
    }));
    popFoot(pop, [["save this view…", () => saveView(), "ghost sm"]]);
  });
}

/* ------------------------------------------------------------------ tours

   A walk through the project, with something said at each stop, kept as a
   markdown file in its .tours folder -- so it is committed with the code and
   reads properly on GitHub. Record one by selecting files in the order you
   would show them; play one and the graph flies from stop to stop. */
let tourRec = null;              // {title, stops: [{path, line, note}]}
let tourPlay = null;             // {tour, i}

function openTours() {
  openPop("tours", async (pop) => {
    popHead(pop, "Tours");
    let list = [];
    try { list = (await api("/api/tours")).tours || []; } catch (err) { list = []; }
    if (popFor !== "tours") return;
    if (!list.length) {
      popText(pop, "A tour walks someone through this folder, stop by stop, with "
        + "something said at each. Record one: select files in the order you would "
        + "show them, and write a line about each.");
    }
    for (const t of list) {
      popRow(pop, {
        key: "▶", name: t.title, sub: `${t.stops} stop${t.stops === 1 ? "" : "s"}`,
        title: t.intro || "play this tour", run: () => playTour(t.path),
        remove: READ_ONLY || SNAPSHOT ? null
          : { title: "move this tour to the trash", run: () => trashTour(t) },
      });
    }
    if (!READ_ONLY && !SNAPSHOT) popFoot(pop, [["record a tour…", () => startRecording()]]);
  });
}

async function trashTour(t) {
  const yes = await ask({
    title: "Move this tour to the trash?", text: `“${t.title}” will go to your `
      + "desktop's trash, where it can be restored.", ok: "move to trash", danger: true,
  });
  if (!yes) return;
  let r;
  try { r = await post("/api/tours", { op: "delete", path: t.path }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "could not remove it", true); return; }
  popFor = null;
  openTours();
}

async function startRecording() {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  closePop();
  const title = await ask({
    title: "Record a tour",
    text: "Select files in the order you would show them, and write a line about "
      + "each as you go. Name it for the person who will play it.",
    value: "", ok: "start recording",
  });
  if (!title) return;
  tourPlay = null;
  tourRec = { title, stops: [] };
  if (sel && !sel.tag && sel.id.startsWith("/")) addStop(sel);
  renderTourCard();
  dockState();
}

function addStop(n) {
  if (!tourRec || !n || n.tag || !n.id.startsWith("/") || n.missing) return;
  const last = tourRec.stops[tourRec.stops.length - 1];
  if (last && last.path === n.id) return;
  tourRec.stops.push({ path: n.id, line: selLine || null, note: "" });
  renderTourCard();
}

async function finishRecording() {
  const rec = tourRec;
  if (!rec) return;
  if (!rec.stops.length) { toast("select at least one file first", true); return; }
  let r;
  try { r = await post("/api/tours", { op: "save", title: rec.title, intro: "", stops: rec.stops }); }
  catch (err) { toast("could not reach cortex — the tour is still here", true); return; }
  if (!r.ok) { toast(r.error || "could not save the tour", true); return; }
  tourRec = null;
  $("tourcard").hidden = true;
  dockState();
  toast(`tour saved in ${r.path.replace(ROOT, "~")}`);
}

async function cancelRecording() {
  if (tourRec && tourRec.stops.length > 1) {
    const yes = await ask({ title: "Throw this tour away?",
      text: `${tourRec.stops.length} stops have been recorded.`, ok: "throw it away",
      danger: true });
    if (!yes) return;
  }
  tourRec = null;
  $("tourcard").hidden = true;
  dockState();
}

async function playTour(path) {
  if (tourRec) {
    toast("save or cancel the tour you are recording first", true);
    return;
  }
  let t;
  try { t = await query("/api/tour", { path }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!t || !t.ok) { toast((t && t.error) || "could not read the tour", true); return; }
  if (!t.stops.length) { toast("this tour has no stops", true); return; }
  closePop();
  tourRec = null;
  tourPlay = { tour: t, i: 0 };
  dockState();
  await showStop();
}

async function showStop() {
  const play = tourPlay;
  if (!play) return;
  const stop = play.tour.stops[play.i];
  renderTourCard();
  if (stop.missing) return;
  await ensureNodes([stop.path]);
  const n = nodes.get(stop.path);
  if (!n || tourPlay !== play) return;
  select(n);
  if (stop.line) setLine(stop.line);
  flyTo(camOn(n, Math.max(cam.s, 1.3)));
}

function tourStep(d) {
  if (!tourPlay) return;
  const j = tourPlay.i + d;
  if (j < 0 || j >= tourPlay.tour.stops.length) {
    if (j >= tourPlay.tour.stops.length) toast("that was the last stop");
    return;
  }
  tourPlay.i = j;
  showStop();
}

function endTour() {
  if (!tourPlay) return false;
  tourPlay = null;
  $("tourcard").hidden = true;
  dockState();
  repaint();
  return true;
}

function renderTourCard() {
  const card = $("tourcard");
  card.textContent = "";
  card.hidden = false;
  const head = document.createElement("div");
  head.className = "tc-head";
  const btn = (label, title, run, cls = "ghost sm") => {
    const b = document.createElement("button");
    b.className = cls;
    b.textContent = label;
    b.title = title;
    b.addEventListener("click", run);
    return b;
  };
  const spacer = () => { const s = document.createElement("span"); s.className = "spacer"; return s; };

  if (tourRec) {
    const rec = document.createElement("span");
    rec.className = "rec";
    rec.textContent = "● REC";
    const name = document.createElement("b");
    name.textContent = tourRec.title;
    const count = document.createElement("span");
    count.className = "tc-step";
    count.textContent = `${tourRec.stops.length} stop${tourRec.stops.length === 1 ? "" : "s"}`;
    head.append(rec, name, spacer(), count);
    card.appendChild(head);
    const last = tourRec.stops[tourRec.stops.length - 1];
    if (!last) {
      const p = document.createElement("div");
      p.className = "pop-empty";
      p.textContent = "Select the first file you would show someone.";
      card.appendChild(p);
    } else {
      const file = document.createElement("div");
      file.className = "tc-file";
      file.textContent = last.path.replace(ROOT + "/", "") + (last.line ? `, line ${last.line}` : "");
      const note = document.createElement("textarea");
      note.className = "tc-note";
      note.placeholder = "What should someone notice here? (markdown)";
      note.value = last.note;
      note.addEventListener("input", () => { last.note = note.value; });
      card.append(file, note);
    }
    const btns = document.createElement("div");
    btns.className = "tc-btns";
    if (last) {
      btns.appendChild(btn("remove this stop", "take the last stop out", () => {
        tourRec.stops.pop();
        renderTourCard();
      }));
    }
    btns.append(spacer(), btn("cancel", "stop recording, without saving", () => cancelRecording()),
                btn("save tour", "write the tour into the project's .tours folder",
                    () => finishRecording(), "primary sm"));
    card.appendChild(btns);
    return;
  }

  const play = tourPlay;
  if (!play) { card.hidden = true; return; }
  const stop = play.tour.stops[play.i];
  const name = document.createElement("b");
  name.textContent = play.tour.title;
  const count = document.createElement("span");
  count.className = "tc-step";
  count.textContent = `${play.i + 1} / ${play.tour.stops.length}`;
  head.append(name, spacer(), count, btn("✕", "end the tour (esc)", () => endTour()));
  card.appendChild(head);

  const file = document.createElement("div");
  file.className = "tc-file" + (stop.missing ? " missing" : "");
  file.textContent = (stop.label || stop.path.replace(ROOT + "/", ""))
    + (stop.missing ? " — no longer here" : "");
  card.appendChild(file);
  const text = [play.i === 0 ? play.tour.intro : "", stop.note].filter(Boolean).join("\n\n");
  if (text) {
    const body = document.createElement("div");
    body.className = "tc-body";
    body.appendChild(markdownView(text, play.tour.path));
    card.appendChild(body);
  }
  const btns = document.createElement("div");
  btns.className = "tc-btns";
  const back = btn("‹ back", "the previous stop (←)", () => tourStep(-1));
  back.disabled = play.i === 0;
  const dots = document.createElement("div");
  dots.className = "tc-dots";
  play.tour.stops.forEach((_, i) => {
    const d = document.createElement("i");
    if (i === play.i) d.className = "on";
    dots.appendChild(d);
  });
  const last = play.i === play.tour.stops.length - 1;
  const next = last ? btn("done", "end the tour", () => endTour(), "primary sm")
                    : btn("next ›", "the next stop (→)", () => tourStep(1), "primary sm");
  btns.append(back, spacer(), dots, spacer(), next);
  card.appendChild(btns);
}

/* ------------------------------------------------------------------ today

   One note per day, made on first use, in the folder's daily/ (or the
   Daily/ or journal/ already there). */
async function openToday() {
  if (READ_ONLY) { toast("cortex was started read-only", true); return; }
  let r;
  try { r = await post("/api/daily", {}); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "could not open today's note", true); return; }
  await settle(parentOf(r.path));
  await ensureNodes([r.path]);
  const n = nodes.get(r.path);
  if (!n) { toast("today's note is outside this view", true); return; }
  select(n);
  flyTo(camOn(n));
  startEdit(n, { atEnd: true });
}

/* --------------------------------------------------------------- capture

   Started with --capture -- "Quick note" on the app-menu icon -- the window
   is only this: one box, and enter puts what is in it into today's note. */
async function quitQuietly() {
  try { await post("/api/quit", {}); } catch (err) { /* gone already */ }
  gone = true;
  if (window.close) window.close();
}

function startCapture() {
  document.body.classList.add("capture");
  $("capture").hidden = false;
  $("cap-where").textContent = `→ today's note in ${CFG.title || ROOT}`;
  const box = $("cap-text");
  const send = async () => {
    const text = box.value.trim();
    if (!text) { quitQuietly(); return; }
    $("cap-ok").disabled = true;
    let r;
    try { r = await post("/api/capture", { text }); }
    catch (err) { $("cap-ok").disabled = false; toast("could not reach cortex", true); return; }
    if (!r.ok) { $("cap-ok").disabled = false; toast(r.error || "could not add it", true); return; }
    toast("added to today's note");
    setTimeout(quitQuietly, 450);
  };
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    else if (e.key === "Escape") { e.preventDefault(); quitQuietly(); }
  });
  $("cap-ok").addEventListener("click", () => send());
  box.focus();
}

/* ----------------------------------------------------------------- share

   The view as one HTML file that opens in any browser, for someone without
   cortex. What goes into it is said before it is made, and the text of the
   files goes in only when asked: a file sent is a file you cannot take back. */
function shareFiles() {
  return [...nodes.values()].filter((n) => visible(n) && !n.dir && !n.tag
    && !n.missing && n.id.startsWith("/"));
}

function openShare() {
  closeMenu();
  const shown = [...nodes.values()].filter(visible).length;
  const files = shareFiles().length;
  $("share-text").textContent = `One HTML file of this view: ${shown} node`
    + `${shown === 1 ? "" : "s"}, where they sit now, with their links.`;
  $("share-contents-label").textContent = `Include the text of the ${files} file`
    + `${files === 1 ? "" : "s"} on screen — anyone you send it to can read them.`;
  $("share-contents").checked = false;
  $("share").classList.add("open");
  $("share-ok").focus();
}

async function makeShare() {
  $("share").classList.remove("open");
  const positions = {};
  for (const n of nodes.values()) if (!n.missing) positions[n.id] = [n.x, n.y];
  const view = currentView("shared");
  const body = {
    root: viewRoot, title: (nodes.get(viewRoot) || {}).name || CFG.title,
    folders: [...expanded].filter((id) => nodes.has(id)),
    files: shareFiles().map((n) => n.id),
    positions, cam: { s: cam.s, x: cam.x, y: cam.y },
    contents: $("share-contents").checked, view,
  };
  toast("making the file…");
  let res;
  try {
    res = await reach(apiURL("/api/snapshot"), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) { toast("could not reach cortex", true); return; }
  if (!res.ok) { toast("could not make the file", true); return; }
  const blob = await res.blob();
  offerDownload(blob, `${(body.title || "cortex").replace(/[^\w.-]+/g, "_")}-map.html`);
  toast(`saved a ${fmtSize(blob.size)} map — it opens in any browser`);
}

/* ---------------------------------------------------------------- palette

   ctrl-k: everything cortex can do, and every file, by typing a few
   letters of it. The keys are shown beside each, so the palette is also how
   the keys are learnt. */
let palRows = [], palIndex = 0, palTimer = null, palFiles = [];

function commands() {
  const write = !READ_ONLY && !SNAPSHOT;
  const list = [
    { label: "Open another folder…", key: "p", run: () => openPlaces(), on: !SNAPSHOT },
    { label: "Today's note", key: "d", run: () => openToday(), on: write },
    { label: "New note…", key: "n", on: write,
      run: () => newEntry(sel && !sel.tag ? parentOfNode(sel) : viewRoot) },
    { label: live ? "Live: stop watching" : "Live: watch files change", key: "w",
      run: () => toggleLive(), on: !SNAPSHOT },
    { label: hist ? "History: back to now" : "History: replay through git", key: "h",
      run: () => toggleHistory(), on: !SNAPSHOT },
    { label: "Play a tour…", key: "T", run: () => openTours() },
    { label: "Record a tour…", run: () => startRecording(), on: write },
    { label: "Save this view…", run: () => saveView(), on: !SNAPSHOT },
    { label: "Views…", key: "v", run: () => openViews(), on: !SNAPSHOT },
    ...viewsList.map((v, i) => ({ label: `View: ${v.name}`, key: i < 9 ? String(i + 1) : "",
                                  run: () => applyView(v) })),
    { label: disk ? "Disk: back to item counts" : "Disk: size by space on disk", key: "u",
      run: () => toggleDisk(), on: !SNAPSHOT },
    { label: "Share this map…", run: () => openShare(), on: !SNAPSHOT },
    { label: "Save a picture (PNG)", key: "x", run: () => exportScene("png") },
    { label: "Save a picture (SVG)", key: "X", run: () => exportScene("svg") },
    { label: "Fit the graph to the screen", key: "0", run: () => fit() },
    { label: showSemantic ? "Hide links" : "Show links", key: "l",
      run: () => $("btn-links").click() },
    { label: showLabels ? "Hide labels" : "Show labels", key: "t", run: () => keyHandlerKey("t") },
    { label: showGit ? "Hide git marks" : "Show git marks", key: "g", run: () => keyHandlerKey("g") },
    { label: "Directories sidebar", key: "s", run: () => toggleSidebar() },
    { label: "Back to the whole map", key: "b", run: () => showFullMap(), on: viewRoot !== ROOT },
    { label: "Clear the path", key: "esc", run: () => clearPath(), on: !!pathHops },
    { label: "Keyboard shortcuts", key: "?", run: () => $("help").classList.add("open") },
    { label: "Quit cortex", run: () => quitCortex(), on: !!CFG.canQuit && !SNAPSHOT },
  ];
  return list.filter((c) => c.on !== false);
}

const keyHandlerKey = (key) => keyHandler({
  key, target: null, preventDefault() {}, metaKey: false, ctrlKey: false,
  altKey: false, shiftKey: false,
});

/* How well `term` matches `text`: its letters in order, scored for runs and
   word starts. -1 when they are not all there. */
function fuzzy(term, text) {
  const t = text.toLowerCase();
  if (!term) return 0;
  const at = t.indexOf(term);
  if (at !== -1) return 1000 - at * 2 - (t.length - term.length) / 10
    + (at === 0 || /[\s:/_.-]/.test(t[at - 1]) ? 200 : 0);
  let score = 0, from = 0, run = 0;
  for (const ch of term) {
    const i = t.indexOf(ch, from);
    if (i === -1) return -1;
    run = i === from ? run + 1 : 0;
    score += 10 + run * 6 + (i === 0 || /[\s:/_.-]/.test(t[i - 1]) ? 12 : 0);
    from = i + 1;
  }
  return score - t.length / 10;
}

function openPalette() {
  closeMenu();
  closePop();
  $("palette").classList.add("open");
  const input = $("pal-input");
  input.value = "";
  palFiles = [];
  renderPalette();
  input.focus();
}

function closePalette() {
  $("palette").classList.remove("open");
  clearTimeout(palTimer);
}

function renderPalette() {
  const term = $("pal-input").value.trim().toLowerCase();
  const scored = commands().map((c) => ({ c, s: fuzzy(term, c.label) }))
    .filter((x) => x.s >= 0).sort((a, b) => b.s - a.s);
  const list = $("pal-list");
  list.textContent = "";
  palRows = [];
  palIndex = 0;
  const add = (kind, label, sub, run) => {
    const b = document.createElement("button");
    b.className = "pal-row";
    const k = document.createElement("span");
    k.className = "kind";
    k.textContent = kind;
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = label;
    const s = document.createElement("span");
    s.className = "sub";
    s.textContent = sub || "";
    b.append(k, nm, s);
    b.addEventListener("click", () => { closePalette(); run(); });
    list.appendChild(b);
    b._run = run;
    palRows.push(b);
  };
  for (const { c } of scored.slice(0, term ? 12 : 40)) add("", c.label, c.key, c.run);
  for (const hit of palFiles.slice(0, 30)) {
    add("file", hit.node.name + (hit.node.dir ? "/" : ""), dirLabel(hit.node.id, 40),
        () => revealPath(hit.node.id));
  }
  if (!palRows.length) {
    const e = document.createElement("div");
    e.className = "pal-empty";
    e.textContent = term.length < 2 ? "Type to search." : "Nothing by that name.";
    list.appendChild(e);
  }
  if (palRows[0]) palRows[0].classList.add("on");
}

async function paletteFiles() {
  const term = $("pal-input").value.trim();
  if (term.length < 2) { palFiles = []; renderPalette(); return; }
  let r;
  try { r = await api("/api/search", { q: term }); } catch (err) { return; }
  if ($("pal-input").value.trim() !== term) return;
  const inScope = (p) => viewRoot === ROOT || p === viewRoot || p.startsWith(viewRoot + "/");
  palFiles = (r.results || []).filter((h) => inScope(h.node.id));
  renderPalette();
}

function paletteKey(e) {
  if (e.key === "Escape") { e.preventDefault(); closePalette(); return; }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (!palRows.length) return;
    palRows[palIndex].classList.remove("on");
    palIndex = (palIndex + (e.key === "ArrowDown" ? 1 : -1) + palRows.length) % palRows.length;
    palRows[palIndex].classList.add("on");
    if (palRows[palIndex].scrollIntoView) palRows[palIndex].scrollIntoView({ block: "nearest" });
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    const row = palRows[palIndex];
    if (row) { closePalette(); row._run(); }
  }
}

/* ------------------------------------------------------- the save button */
function openSaveMenu(e) {
  if (e) e.stopPropagation();
  const menu = $("save-menu");
  if (menu.classList.contains("open")) { menu.classList.remove("open"); return; }
  menu.textContent = "";
  const item = (label, key, run) => {
    const b = document.createElement("button");
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = key || "";
    b.append(k, document.createTextNode(label));
    b.addEventListener("click", () => { menu.classList.remove("open"); run(); });
    menu.appendChild(b);
  };
  item("Picture of this view (PNG)", "x", () => exportScene("png"));
  item("Picture, sharp at any size (SVG)", "X", () => exportScene("svg"));
  if (!SNAPSHOT) item("Interactive map (HTML)…", "", () => openShare());
  if (hist && !SNAPSHOT) item("Video of the history (WebM)", "", () => recordHistory());
  menu.classList.add("open");
}

/* ------------------------------------------------------------------ live

   Files light up as they change and leave a fading trail, so you can watch
   an AI agent, a build or a git pull move through the project as it happens.
   The server keeps a feed of file-level changes -- the kernel's own events on
   Linux, a careful poll elsewhere -- and while live is on the window asks it
   for whatever is new, waiting on the answer rather than asking over and
   over. */
let live = null;
const HEAT_MS = 90_000;          // how long a change glows
const PING_MS = 1400;            // the ring that goes out from it first
const TRAIL = 24;                // recent changes joined by the trail
const heat = new Map();          // path -> {kind, t, x, y}
let trail = [];                  // [{path, t}], oldest first
let fadeTimer = null;
let touched = 0;                 // last time the camera was handled by a person
const KIND_COLOR = { created: "#34d399", modified: "#f5a524",
                     deleted: "#f43f5e", moved: "#60a5fa" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const inView = (p) => p === viewRoot || p.startsWith(viewRoot + "/");

function toggleLive(on) {
  const next = on === undefined ? !live : !!on;
  if (next === !!live) return;
  if (!next) {
    clearInterval(live.clock);
    // the long poll would hold a connection for up to 25 seconds more; a few
    // toggles of that and the browser has none left for anything else
    if (live.abort) live.abort.abort();
    live = null;
    $("feed").hidden = true;
    dockState();
    repaint();
    return;
  }
  live = { seq: null, follow: false, reveal: true, events: [], engine: "",
           count: 0, lastFly: 0, clock: null,
           abort: typeof AbortController === "function" ? new AbortController() : null };
  $("feed").hidden = false;
  live.clock = setInterval(() => { if (live) renderFeed(); }, 5000);
  renderFeed();
  dockState();
  liveLoop(live);
}

async function liveLoop(me) {
  while (live === me && !gone) {
    let r;
    try {
      r = await api("/api/activity", { since: me.seq === null ? 0 : me.seq,
                                       wait: me.seq === null ? 0 : 25 },
                    me.abort ? { signal: me.abort.signal } : undefined);
    } catch (err) {
      if (live !== me || (err && err.name === "AbortError")) return;
      if (staleToken(err)) { serverGone(); return; }
      await sleep(2000);
      continue;
    }
    if (live !== me) return;
    if (r.off) {
      me.engine = "off";
      renderFeed();
      return;
    }
    me.engine = r.engine || "";
    if (me.seq === null) {
      // what changed before live was turned on is not news
      me.seq = r.seq || 0;
      renderFeed();
      continue;
    }
    if (r.dropped) pulse();
    me.seq = r.seq;
    if (r.events && r.events.length) await absorb(r.events);
  }
}

const HEAT_MAX = 400;            // glowing at once; a big checkout keeps its newest

function record(ev) {
  const n = nodes.get(ev.path) || (ev.from && nodes.get(ev.from));
  heat.delete(ev.path);            // re-inserted, so it counts as the newest
  heat.set(ev.path, { kind: ev.kind, t: Date.now(), x: n ? n.x : null,
                      y: n ? n.y : null });
  while (heat.size > HEAT_MAX) heat.delete(heat.keys().next().value);
  if (ev.from) heat.delete(ev.from);
  trail.push({ path: ev.path, t: Date.now() });
  if (trail.length > TRAIL) trail.shift();
}

async function absorb(events) {
  const parents = new Set(), reveal = [];
  for (let ev of events) {
    if (ev.kind === "overflow") {
      for (const dir of expanded) parents.add(dir);
      continue;
    }
    if (!inView(ev.path) && !(ev.from && inView(ev.from))) continue;
    // a save that writes a new copy and renames it over the old one comes
    // through as a creation; for a file already here it is an edit
    if (ev.kind === "created" && nodes.has(ev.path)) ev = { ...ev, kind: "modified" };
    record(ev);
    if (live) {
      live.events.push(ev);
      live.count++;
      if (live.events.length > 400) live.events.shift();
    }
    parents.add(parentOf(ev.path));
    if (ev.from) parents.add(parentOf(ev.from));
    if (ev.kind !== "deleted" && !ev.dir && live && live.reveal && !nodes.has(ev.path)) {
      reveal.push(ev.path);
    }
    if (disk) disk.sizes.delete(parentOf(ev.path));
  }
  for (const dir of parents) if (expanded.has(dir)) await refreshDir(dir);
  // a handful of new files is news; ten thousand from a checkout is a flood,
  // and grafting them all would bury the graph
  if (reveal.length && reveal.length <= 150) await ensureNodes(reveal);
  else if (reveal.length) toast(`${reveal.length} files changed — not all brought into the graph`);
  updateStats();
  renderFeed();
  animateFor(PING_MS);
  // however it was saved -- in place, or as a new copy renamed over it
  if (sel && !sel.dir && events.some((ev) => ev.path === sel.id && ev.kind !== "deleted")) {
    renderBody(sel);
  }
  // follow: fly to the newest change, but never while someone is steering
  const newest = [...events].reverse().find((ev) => ev.kind !== "deleted" && nodes.has(ev.path));
  if (live && live.follow && newest && Date.now() - touched > 4000
      && Date.now() - live.lastFly > 1200) {
    live.lastFly = Date.now();
    flyTo(camOn(nodes.get(newest.path), Math.max(cam.s, 0.9)), 600);
  }
}

function drawHeat(now) {
  if (!heat.size) return;
  const at = (path) => {
    const n = nodes.get(path), h = heat.get(path);
    if (n) return visible(n) ? [n.x, n.y, n] : null;
    return h && h.x !== null ? [h.x, h.y, null] : null;
  };
  // the trail: the order things changed in, as a line from each to the next
  ctx.setLineDash([4, 5]);
  let prev = null;
  for (const p of trail) {
    const age = now - p.t;
    const pos = age < HEAT_MS ? at(p.path) : null;
    if (!pos) { prev = null; continue; }
    const x = toScreenX(pos[0]), y = toScreenY(pos[1]);
    if (prev) {
      const fade = 1 - Math.max(age, prev.age) / HEAT_MS;
      ctx.strokeStyle = `rgba(255, 209, 102, ${(0.07 + 0.4 * fade).toFixed(3)})`;
      ctx.lineWidth = 1 + 1.4 * fade;
      ctx.beginPath();
      ctx.moveTo(prev.x, prev.y);
      ctx.lineTo(x, y);
      ctx.stroke();
    }
    prev = { x, y, age };
  }
  ctx.setLineDash([]);

  let pinging = false, glowing = false;
  for (const [path, h] of heat) {
    const age = now - h.t;
    if (age > HEAT_MS) { heat.delete(path); continue; }
    glowing = true;
    const pos = at(path);
    if (!pos) continue;
    const x = toScreenX(pos[0]), y = toScreenY(pos[1]);
    const r = pos[2] ? Math.max(1.8, pos[2].r * cam.s) : 4;
    const color = KIND_COLOR[h.kind] || KIND_COLOR.modified;
    const fade = 1 - age / HEAT_MS;
    if (age < PING_MS) {
      pinging = true;
      const k = age / PING_MS;
      ctx.globalAlpha = (1 - k) * 0.95;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.arc(x, y, r + 4 + k * 28, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 0.2 + 0.7 * fade;
    ctx.shadowColor = color;
    // a blur per ring is the expensive part of drawing; with hundreds lit
    // at once it is dropped
    ctx.shadowBlur = heat.size > 60 ? 0 : 16 * fade;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if (!pos[2]) ctx.setLineDash([2, 3]);       // gone: an outline of where it was
    ctx.beginPath();
    ctx.arc(x, y, r + 3, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;
    ctx.globalAlpha = 1;
  }
  // the ping needs every frame; the slow fade after it only now and then
  if (pinging) animateFor(40);
  else if (glowing && !fadeTimer) {
    fadeTimer = setTimeout(() => { fadeTimer = null; repaint(); }, 400);
  }
}

const ago = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 5 ? "now" : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m`
    : `${Math.floor(s / 3600)}h`;
};

function renderFeed() {
  if (!live) return;
  $("feed-engine").textContent = live.engine === "inotify" ? "instant"
    : live.engine === "poll" ? "checked every 1.5s"
    : live.engine === "off" ? "off — started with --no-watch" : "";
  $("feed-follow").classList.toggle("on", live.follow);
  $("feed-reveal").classList.toggle("on", live.reveal);
  const count = live.count;
  const btn = $("d-live");
  btn.textContent = "";
  const led = document.createElement("i");
  led.className = "led";
  btn.append(led, document.createTextNode("live"));
  if (count) {
    const c = document.createElement("span");
    c.className = "count";
    c.textContent = String(count);
    btn.appendChild(c);
  }
  const list = $("feed-list");
  list.textContent = "";
  const recent = live.events.slice(-80).reverse();
  if (!recent.length) {
    const e = document.createElement("div");
    e.className = "feed-empty";
    e.textContent = live.engine === "off"
      ? "cortex was started with --no-watch, so it is not watching the disk."
      : "Waiting for something to change. Save a file, run a build, or let an "
        + "agent loose on the project: each change lights up here and on the graph.";
    list.appendChild(e);
    return;
  }
  for (const ev of recent) {
    const row = document.createElement("button");
    row.className = "feed-row";
    row.title = ev.from ? `${ev.from} → ${ev.path}` : ev.path;
    const verb = document.createElement("span");
    verb.className = "verb " + ev.kind;
    verb.textContent = ev.kind;
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = baseName(ev.path) + (ev.dir ? "/" : "");
    const when = document.createElement("span");
    when.className = "when";
    when.textContent = ago(ev.t);
    row.append(verb, nm, when);
    row.addEventListener("click", async () => {
      if (ev.kind === "deleted") { toast(`${baseName(ev.path)} was deleted`); return; }
      await ensureNodes([ev.path]);
      const n = nodes.get(ev.path);
      if (n) { select(n); flyTo(camOn(n)); }
    });
    list.appendChild(row);
  }
}

/* --------------------------------------------------------------- history

   The project's life, replayed: drag the slider, or press play, and the
   graph goes back through git -- files appearing, being renamed, being
   deleted -- with each commit's changes lighting up as it lands. Everything
   that ever existed is laid out together, so nothing moves while you scrub;
   what did not exist yet is simply not drawn. */
let hist = null;
const SPEEDS = [1, 4, 16, 64];

let histLoading = false;

async function toggleHistory(on) {
  const next = on === undefined ? !hist : !!on;
  if (next === !!hist || (next && histLoading)) return;
  if (!next) { exitHistory(); return; }
  histLoading = true;
  try { await enterHistory(); } finally { histLoading = false; }
}

async function enterHistory() {
  toast("reading the history…");
  let r;
  try { r = await api("/api/history", { path: viewRoot }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.repo) {
    toast(r.error ? `no history here: ${r.error}` : "this folder is not in a git repository", true);
    return;
  }
  if (!r.commits.length) { toast("no commit has touched this folder", true); return; }

  // when each file existed, as ranges of slider positions: position v shows
  // the folder after the first v commits, so 0 is before them all
  const life = new Map();
  const open = (p, v) => {
    const spans = life.get(p) || [];
    const last = spans[spans.length - 1];
    if (!last || last[1] !== Infinity) spans.push([v, Infinity]);
    life.set(p, spans);
  };
  const close = (p, v) => {
    const spans = life.get(p);
    const last = spans && spans[spans.length - 1];
    if (last && last[1] === Infinity) last[1] = v;
  };
  for (const p of r.base || []) open(p, 0);
  r.commits.forEach((c, i) => {
    for (const [st, p, from] of c.changes) {
      if (st === "A") open(p, i + 1);
      else if (st === "M") { if (!life.has(p)) open(p, 0); }
      else if (st === "D") close(p, i + 1);
      else if (st === "R") { if (from) close(from, i + 1); open(p, i + 1); }
    }
  });

  // most recently touched first, should the graph have to be capped
  const touchedAt = new Map();
  r.commits.forEach((c, i) => { for (const [, p] of c.changes) touchedAt.set(p, i); });
  const paths = [...life.keys()].sort((a, b) => (touchedAt.get(b) ?? -1) - (touchedAt.get(a) ?? -1))
    .slice(0, 5000);
  const before = new Set(nodes.keys());
  await ensureNodes(paths, { missing: true });
  const grafted = new Set([...nodes.keys()].filter((id) => !before.has(id)));

  hist = {
    commits: r.commits, life, grafted, truncated: r.truncated, v: r.commits.length,
    playing: false, speed: 1, lastStep: 0, show: null, recording: null,
  };
  closePanel();
  $("timeline").hidden = false;
  const range = $("tl-range");
  range.max = String(r.commits.length);
  range.value = String(r.commits.length);
  drawMarks();
  setHistIndex(r.commits.length, false);
  dockState();
  warmup(120);
  flyTo(camAround([...nodes.values()].filter((n) => !n.tag)), 600);
  toast(`${r.commits.length} commit${r.commits.length === 1 ? "" : "s"}`
    + (r.truncated ? ", the most recent" : "") + " — drag, or press space to play");
}

function exitHistory() {
  if (!hist) return;
  const h = hist;
  if (h.recording) h.recording.stop();
  hist = null;
  // what was brought in for the history goes, unless it belongs in the graph
  // anyway: its folder is open, or it was opened itself meanwhile
  const gone = new Set();
  for (const id of h.grafted) {
    const n = nodes.get(id);
    if (n && !n.missing && (expanded.has(n.parent) || expanded.has(id))) continue;
    gone.add(id);
  }
  for (const n of nodes.values()) if (n.missing) gone.add(n.id);
  forget(new Set([...gone, ...dropDescendants([...gone])]));
  $("timeline").hidden = true;
  heat.clear();
  trail = [];
  dockState();
  updateStats();
  reheat(0.2);
}

/* Which nodes exist at position v. Folders show when anything inside does;
   files git never knew about appear only at the end, which is now. */
function histShown(v) {
  const show = new Set([viewRoot]);
  const end = v >= hist.commits.length;
  for (const n of nodes.values()) {
    if (n.dir || n.tag) continue;
    const spans = hist.life.get(n.id);
    const there = spans ? spans.some(([a, b]) => v >= a && v < b) : end && !n.missing;
    if (!there) continue;
    show.add(n.id);
    for (let p = n.parent; p && !show.has(p); p = parentOf(p)) {
      show.add(p);
      if (p === viewRoot || p.length <= viewRoot.length) break;
    }
  }
  return show;
}

function setHistIndex(v, flash = true) {
  if (!hist) return;
  v = Math.max(0, Math.min(hist.commits.length, v));
  const moved = v !== hist.v;
  hist.v = v;
  hist.show = histShown(v);
  $("tl-range").value = String(v);
  if (flash && moved && v > 0) {
    const c = hist.commits[v - 1];
    const kinds = { A: "created", M: "modified", D: "deleted", R: "moved" };
    for (const [st, p] of c.changes) {
      const n = nodes.get(p);
      heat.set(p, { kind: kinds[st] || "modified", t: Date.now(),
                    x: n ? n.x : null, y: n ? n.y : null });
      trail.push({ path: p, t: Date.now() });
    }
    trail = trail.slice(-TRAIL);
    animateFor(PING_MS);
  }
  renderHistInfo();
  updateStats();
  repaint();
}

function renderHistInfo() {
  const info = $("tl-info");
  info.textContent = "";
  const when = document.createElement("span");
  when.className = "when";
  const subj = document.createElement("span");
  subj.className = "subj";
  const counts = document.createElement("span");
  counts.className = "counts";
  const fmt = (t) => new Date(t * 1000).toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" });
  if (hist.v === 0) {
    when.textContent = `before ${fmt(hist.commits[0].t)}`;
    subj.textContent = hist.truncated ? "older history is left out" : "the beginning";
  } else {
    const c = hist.commits[hist.v - 1];
    when.textContent = `${fmt(c.t)} · ${hist.v}/${hist.commits.length}`;
    subj.textContent = c.subject;
    const n = { A: 0, M: 0, D: 0, R: 0 };
    for (const [st] of c.changes) n[st] = (n[st] || 0) + 1;
    counts.textContent = `${c.author} · +${n.A} ~${n.M + n.R} −${n.D}`;
  }
  info.append(when, subj, counts);
  $("tl-play").innerHTML = hist.playing ? "&#10074;&#10074;" : "&#9654;";
  $("tl-speed").textContent = `${hist.speed}×`;
}

/* The commits as ticks along the slider, taller for bigger commits. */
function drawMarks() {
  const c = $("tl-marks");
  if (!c.getContext || !hist) return;
  const w = c.clientWidth || 600, h = c.clientHeight || 20;
  c.width = Math.round(w * DPR);
  c.height = Math.round(h * DPR);
  const g = c.getContext("2d");
  g.setTransform(DPR, 0, 0, DPR, 0, 0);
  g.clearRect(0, 0, w, h);
  const n = hist.commits.length;
  const most = Math.max(1, ...hist.commits.map((x) => x.changes.length));
  hist.commits.forEach((cm, i) => {
    const x = n <= 1 ? w : ((i + 1) / n) * (w - 4) + 2;
    const k = Math.log(1 + cm.changes.length) / Math.log(1 + most);
    g.fillStyle = "#4c8dff";
    g.globalAlpha = 0.25 + 0.55 * k;
    g.fillRect(x - 0.6, h - 2 - k * (h - 4), 1.2, k * (h - 4) + 2);
  });
}

function playHistory(on) {
  if (!hist) return;
  hist.playing = on === undefined ? !hist.playing : !!on;
  if (hist.playing && hist.v >= hist.commits.length) setHistIndex(0, false);
  hist.lastStep = Date.now();
  renderHistInfo();
  if (hist.playing) animateFor(100);
}

/* Called every frame: steps the playback on, at about one commit every
   700ms at 1x, faster at higher speeds. */
function tickModes() {
  if (!hist || !hist.playing) return;
  const every = 700 / hist.speed;
  const now = Date.now();
  if (now - hist.lastStep >= every) {
    hist.lastStep = now;
    if (hist.v >= hist.commits.length) {
      hist.playing = false;
      renderHistInfo();
      if (hist.recording) setTimeout(() => hist && hist.recording && hist.recording.stop(), 900);
      return;
    }
    setHistIndex(hist.v + 1);
  }
  animateFor(100);
}

/* Record the replay, start to finish, as a video: the canvas itself, with
   the date and the commit drawn on it, so the video says what it shows. */
function recordHistory() {
  if (!hist) return;
  if (hist.recording) { hist.recording.stop(); return; }
  if (!stage.captureStream || typeof MediaRecorder === "undefined") {
    toast("this browser cannot record video", true);
    return;
  }
  const type = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"]
    .find((t) => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(stage.captureStream(30),
    type ? { mimeType: type, videoBitsPerSecond: 8e6 } : {});
  const chunks = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  rec.onstop = () => {
    offerDownload(new Blob(chunks, { type: "video/webm" }), exportName("webm"));
    toast("saved the replay as a video");
    if (hist) hist.recording = null;
    $("tl-record").classList.remove("rec");
    $("tl-record").textContent = "record";
  };
  hist.recording = rec;
  $("tl-record").classList.add("rec");
  $("tl-record").textContent = "stop";
  setHistIndex(0, false);
  rec.start(250);
  playHistory(true);
}

function drawCaption() {
  if (!hist || !hist.recording) return;
  const c = hist.v > 0 ? hist.commits[hist.v - 1] : null;
  const date = c ? new Date(c.t * 1000).toLocaleDateString(undefined,
    { day: "numeric", month: "long", year: "numeric" }) : "the beginning";
  ctx.font = "600 15px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#ffd166";
  ctx.fillText(date, 28, H - 52);
  ctx.font = "400 13px ui-sans-serif, system-ui, sans-serif";
  ctx.fillStyle = "#c4d0e4";
  ctx.fillText(c ? c.subject.slice(0, 90) : "", 28, H - 32);
}

/* ------------------------------------------------------------------ disk

   What is filling the drive: every node sized by the space it takes on
   disk, folders by everything inside them -- including what the graph never
   shows, node_modules and .git and build output, since those are usually
   the answer. Sizes arrive as they are counted; a big folder says "at least"
   until it is done. */
let disk = null;

function toggleDisk(on) {
  const next = on === undefined ? !disk : !!on;
  if (next === !!disk) return;
  if (!next) {
    const old = disk;
    disk = null;
    clearTimeout(old.timer);
    for (const n of nodes.values()) n.r = n.tag ? n.r : radiusOf(n);
    $("d-disk").textContent = "disk";
    dockState();
    if (sel && sel.dir) renderBody(sel);
    reheat(0.3);
    return;
  }
  disk = { sizes: new Map(), detail: new Map(), timer: null };
  dockState();
  measure();
}

async function measure() {
  const me = disk;
  if (!me) return;
  // one count at a time: every folder opened asks for one, and each would
  // otherwise start its own chain of asking again
  if (me.busy) { me.again = true; return; }
  me.busy = true;
  clearTimeout(me.timer);
  try { await measureOnce(me); } finally { me.busy = false; }
  if (disk === me && me.again) { me.again = false; measure(); }
}

async function measureOnce(me) {
  const dirs = [...expanded].filter((id) => nodes.has(id) && visible(nodes.get(id)));
  let pending = false;
  for (const dir of dirs) {
    const known = me.detail.get(dir);
    if (known && known.complete) continue;
    let r;
    try { r = await api("/api/du", { path: dir }); } catch (err) { continue; }
    if (disk !== me) return;
    me.detail.set(dir, r);
    me.sizes.set(dir, { bytes: r.bytes, complete: r.complete });
    for (const k of r.children || []) me.sizes.set(k.path, { bytes: k.bytes, complete: k.complete });
    if (!r.complete) pending = true;
  }
  sizeNodes();
  if (sel && sel.dir && me.detail.has(sel.id)) renderBody(sel);
  if (pending && disk === me) me.timer = setTimeout(measure, 900);
}


function sizeNodes() {
  if (!disk) return;
  let most = 1;
  for (const n of nodes.values()) {
    const s = disk.sizes.get(n.id);
    if (s && visible(n) && n.id !== viewRoot) most = Math.max(most, s.bytes);
  }
  for (const n of nodes.values()) {
    if (n.tag) continue;
    const s = disk.sizes.get(n.id);
    // the folder in view holds everything, so it is held to the largest size
    n.r = s ? 3 + 34 * Math.sqrt(Math.min(1, s.bytes / most)) : 3;
  }
  const root = disk.sizes.get(viewRoot);
  const btn = $("d-disk");
  btn.textContent = "disk";
  if (root) {
    const c = document.createElement("span");
    c.className = "count";
    c.textContent = (root.complete ? "" : "≥") + fmtSize(root.bytes);
    btn.appendChild(c);
  }
  reheat(0.15);
}

const diskLabel = (n) => {
  const s = disk && disk.sizes.get(n.id);
  return s ? `${n.name} · ${s.complete ? "" : "≥"}${fmtSize(s.bytes)}` : n.name;
};

/* A folder's reader, in disk mode: where its space goes, biggest first. */
function renderDiskBody(n, body) {
  const d = disk.detail.get(n.id);
  const box = document.createElement("div");
  box.className = "md";
  if (!d) {
    box.appendChild(hint("Counting…"));
    body.appendChild(box);
    return;
  }
  const h = document.createElement("h3");
  h.textContent = `${d.complete ? "" : "at least "}${fmtSize(d.bytes)} in ${d.files} file${d.files === 1 ? "" : "s"}`;
  box.appendChild(h);
  const table = document.createElement("div");
  table.className = "du-list";
  const top = (d.children || []).reduce((m, k) => Math.max(m, k.bytes), 1);
  for (const k of (d.children || []).slice(0, 80)) {
    const row = document.createElement("button");
    row.className = "du-row";
    const bar = document.createElement("i");
    bar.style.width = `${Math.max(1, (k.bytes / top) * 100)}%`;
    const nm = document.createElement("span");
    nm.className = "nm";
    nm.textContent = k.name + (k.dir ? "/" : "");
    const sz = document.createElement("span");
    sz.className = "sz";
    sz.textContent = (k.complete ? "" : "≥") + fmtSize(k.bytes);
    row.append(bar, nm, sz);
    const onGraph = nodes.has(k.path);
    if (!onGraph) {
      const t = document.createElement("span");
      t.className = "tag";
      t.textContent = "not in the graph";
      row.appendChild(t);
      row.title = "hidden from the graph — click to show it in your file manager";
      row.addEventListener("click", () => act("reveal", k.path));
    } else {
      row.addEventListener("click", () => { const m = nodes.get(k.path); select(m); flyTo(camOn(m)); });
    }
    table.appendChild(row);
  }
  box.appendChild(table);
  const more = (d.children || []).length - 80 + (d.more || 0);
  if (more > 0) box.appendChild(hint(`and ${more} smaller`));
  body.appendChild(box);
}

/* ---------------------------------------------------------------- ghosts

   A [[wikilink]] to a note that does not exist yet is a faint, dashed node:
   the note you said you would write. Select it to see who links to it, and
   to make it -- in the folder of the first note that mentions it. */
const GHOST_PREFIX = "ghost:";
const isGhost = (id) => id.startsWith(GHOST_PREFIX);
let ghostNames = {};

const ghostKey = (name) => GHOST_PREFIX + name.trim().toLowerCase()
  .replace(/\s+/g, " ").replace(/\.md$/, "");

function addGhostNode(id, near) {
  const n = addNode({ id, name: ghostNames[id] || id.slice(GHOST_PREFIX.length),
                      dir: false, group: "ghost", size: 0, mtime: 0, parent: null }, near);
  n.tag = true;                    // not a file: no editor, no path, no size
  n.ghost = true;
  n.r = 4.5;
  return n;
}

function renderGhostBody(n, body) {
  body.textContent = "";
  const from = ((connIndex && connIndex.get(n.id)) || []).filter((c) => !c.out);
  const note = document.createElement("div");
  note.className = "empty";
  note.textContent = from.length
    ? `This note does not exist yet. ${from.length} note${from.length === 1 ? " links" : "s link"} to it.`
    : "This note does not exist yet.";
  body.appendChild(note);
  if (!READ_ONLY && !SNAPSHOT) {
    const make = document.createElement("button");
    make.className = "primary";
    const where = ghostFolder(n);
    make.textContent = `create it in ${where === ROOT ? CFG.title || "~" : where.replace(ROOT, "~")}`;
    make.addEventListener("click", () => createGhost(n));
    body.appendChild(make);
  }
  if (from.length) {
    const list = document.createElement("div");
    list.className = "md";
    const ul = document.createElement("ul");
    for (const c of from) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.className = "wiki";
      a.textContent = baseName(c.path);
      a.title = c.path;
      a.addEventListener("click", () => focusOn(c.path));
      li.appendChild(a);
      ul.appendChild(li);
    }
    list.appendChild(ul);
    body.appendChild(list);
  }
}

function ghostFolder(n) {
  const from = ((connIndex && connIndex.get(n.id)) || []).filter((c) => !c.out);
  return from.length ? parentOf(from[0].path) : viewRoot;
}

async function createGhost(n) {
  const folder = ghostFolder(n);
  const name = n.name.replace(/^\/+|\/+$/g, "");
  let r;
  try { r = await post("/api/fs", { op: "new", path: folder, name, parents: true }); }
  catch (err) { toast("could not reach cortex", true); return; }
  if (!r.ok) { toast(r.error || "could not create it", true); return; }
  forget(new Set([n.id]));
  await settle(folder);
  await ensureNodes([r.path]);
  const made = nodes.get(r.path);
  if (made) {
    for (const c of (connIndex && connIndex.get(n.id)) || []) addLink(c.path, made.id, "note");
    select(made);
    flyTo(camOn(made));
    startEdit(made, { atEnd: true });
  }
  toast(`made ${baseName(r.path)}`);
}

/* ------------------------------------------------------------ suggestions

   Notes that talk about the same things but never link to each other. The
   index compares what every note says; the strongest pairs come back with
   the words they share. Select a note to see its suggestions, dashed; link
   one and a real link is written into the note, or dismiss it for good. */
let suggestIndex = new Map();     // path -> [{path, score, terms}]
let showAllSuggest = false;

function buildSuggestIndex(list) {
  suggestIndex = new Map();
  for (const [a, b, score, terms] of list || []) {
    for (const [x, y] of [[a, b], [b, a]]) {
      let l = suggestIndex.get(x);
      if (!l) suggestIndex.set(x, (l = []));
      l.push({ path: y, score, terms });
    }
  }
  for (const l of suggestIndex.values()) l.sort((p, q) => q.score - p.score);
}

function drawSuggestions() {
  if (!showSemantic || !suggestIndex.size) return;
  const pairs = [];
  if (showAllSuggest) {
    for (const [a, l] of suggestIndex) for (const s of l) if (a < s.path) pairs.push([a, s]);
  } else if (sel && suggestIndex.has(sel.id)) {
    for (const s of suggestIndex.get(sel.id)) pairs.push([sel.id, s]);
  }
  if (!pairs.length) return;
  ctx.setLineDash([3, 4]);
  ctx.lineWidth = 1.3;
  for (const [a, s] of pairs) {
    const p = nodes.get(a), q = nodes.get(s.path);
    if (!p || !q || !visible(p) || !visible(q)) continue;
    ctx.strokeStyle = `rgba(103, 232, 249, ${(0.25 + 0.6 * Math.min(1, s.score * 1.6)).toFixed(3)})`;
    ctx.beginPath();
    ctx.moveTo(toScreenX(p.x), toScreenY(p.y));
    ctx.lineTo(toScreenX(q.x), toScreenY(q.y));
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function renderSuggestions(n, box) {
  const list = suggestIndex.get(n.id);
  if (!list || !list.length || n.tag) return;
  const head = document.createElement("div");
  head.className = "conn-sub";
  head.textContent = "suggested — they talk about the same things";
  box.appendChild(head);
  const rows = document.createElement("div");
  rows.className = "conn-list";
  for (const s of list) {
    const row = document.createElement("div");
    row.className = "conn suggest";
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = "≈";
    const name = document.createElement("button");
    name.className = "nm link";
    name.textContent = baseName(s.path);
    name.title = `${s.path}\nshares: ${s.terms.join(", ")} · ${Math.round(s.score * 100)}%`;
    name.addEventListener("click", () => focusOn(s.path));
    const why = document.createElement("span");
    why.className = "where";
    why.textContent = s.terms.slice(0, 3).join(", ");
    row.append(arrow, name, why);
    if (!READ_ONLY && !SNAPSHOT) {
      const add = document.createElement("button");
      add.className = "ghost sm";
      add.textContent = "link";
      add.title = `write a link to ${baseName(s.path)} into ${n.name}`;
      add.addEventListener("click", () => acceptSuggestion(n, s));
      const no = document.createElement("button");
      no.className = "ghost sm";
      no.textContent = "✕";
      no.title = "not related — stop suggesting it";
      no.addEventListener("click", () => dismissSuggestion(n, s));
      row.append(add, no);
    }
    rows.appendChild(row);
  }
  box.appendChild(rows);
}

function dropSuggestion(a, b) {
  for (const [x, y] of [[a, b], [b, a]]) {
    const l = suggestIndex.get(x);
    if (!l) continue;
    const kept = l.filter((s) => s.path !== y);
    if (kept.length) suggestIndex.set(x, kept); else suggestIndex.delete(x);
  }
}

/* "See also" with an ordinary relative link: unambiguous, which a [[name]]
   is not when two notes share a name, and it works on GitHub too. */
function seeAlso(from, to) {
  const a = parentOf(from).split("/").filter(Boolean);
  const b = to.split("/").filter(Boolean);
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  const rel = [...a.slice(i).map(() => ".."), ...b.slice(i)].join("/");
  const label = baseName(to).replace(/\.[^.]+$/, "").replace(/[[\]]/g, "");
  // encodeURIComponent leaves ( and ), which would end the link early
  const enc = (part) => encodeURIComponent(part).replace(/[()]/g,
    (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());
  return `[${label}](${rel.split("/").map(enc).join("/")})`;
}

async function acceptSuggestion(n, s) {
  const link = seeAlso(n.id, s.path);
  if (editing && editing.path === n.id) {
    const area = editing.area;
    area.focus();
    area.setSelectionRange(area.value.length, area.value.length);
    insertText(area, (area.value.endsWith("\n") ? "\n" : "\n\n") + `See also ${link}\n`);
    toast("added — save to keep it");
  } else {
    let src;
    try { src = await query("/api/source", { path: n.id }); }
    catch (err) { toast("could not reach cortex", true); return; }
    if (!src || !src.ok) { toast((src && src.error) || "cannot edit this note", true); return; }
    const text = src.text.replace(/\s*$/, "") + `\n\nSee also ${link}\n`;
    let r;
    try { r = await post("/api/save", { path: n.id, text, version: src.version }); }
    catch (err) { toast("could not reach cortex", true); return; }
    if (!r.ok) { toast(r.error || "could not save", true); return; }
    toast(`linked ${n.name} to ${baseName(s.path)}`);
  }
  dropSuggestion(n.id, s.path);
  if (nodes.has(s.path)) addLink(n.id, s.path, "note");
  buildConnIndexExtra(n.id, s.path);
  renderConnections(n);
  repaint();
}

/* The new link, known at once rather than at the next re-index. */
function buildConnIndexExtra(a, b) {
  if (!connIndex) return;
  const add = (from, to, out) => {
    let l = connIndex.get(from);
    if (!l) connIndex.set(from, (l = []));
    if (!l.some((c) => c.path === to && c.kind === "note")) l.push({ path: to, kind: "note", out });
  };
  add(a, b, true);
  add(b, a, false);
}

async function dismissSuggestion(n, s) {
  dropSuggestion(n.id, s.path);
  renderConnections(n);
  repaint();
  try { await post("/api/dismiss", { a: n.id, b: s.path }); } catch (err) { /* local is enough */ }
}

// ------------------------------------------------------------------- search
const qbox = $("q");
let searchTimer = null;
qbox.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 300);
});
qbox.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if (!$("hits").hidden) { hideHits(); return; }
    qbox.value = ""; matches.clear(); hideHits(); qbox.blur();
    return;
  }
  if (e.key === "ArrowDown") { e.preventDefault(); moveHit(1); return; }
  if (e.key === "ArrowUp") { e.preventDefault(); moveHit(-1); return; }
  if (e.key === "Enter") {
    e.preventDefault();
    clearTimeout(searchTimer);
    if (takeHit()) return;        // a result is waiting; go to it
    runSearch();                  // nothing yet, so ask again
    return;
  }
  if (e.key === "Tab") { e.preventDefault(); toggleSearchMode(); }
});

/* Two searches share one box. Names answers "where did I put it"; text answers
   "where did I say that", which is the question you actually have about your
   own notes. */
function toggleSearchMode(next) {
  searchMode = next || (searchMode === "names" ? "text" : "names");
  const btn = $("mode");
  btn.textContent = searchMode;
  btn.classList.toggle("on", searchMode === "text");
  qbox.placeholder = searchMode === "text"
    ? "search inside files"
    : `search everything under ${CFG.title || ROOT}`;
  hideHits();
  matches.clear();
  if (qbox.value.trim().length >= 2) runSearch(); else updateStats();
}

function hideHits() {
  const box = $("hits");
  box.hidden = true;
  box.textContent = "";
  hitRows = [];
  hitIndex = -1;
}

/* Both searches answer with the same list, because both answer the same
   question: which one of these did you mean? Each item is a label, some
   context, and what to do when it is chosen. */
function showHits(items, note) {
  const box = $("hits");
  box.textContent = "";
  hitRows = [];
  hitIndex = -1;
  if (!items.length && !note) { box.hidden = true; return; }
  box.hidden = false;

  for (const item of items.slice(0, 200)) {
    const row = document.createElement("button");
    row.className = "hit";
    row.title = item.title || "";

    const label = document.createElement("span");
    label.className = "hit-name";
    if (item.colour) label.style.color = item.colour;
    label.textContent = item.label;

    const detail = document.createElement("span");
    detail.className = "hit-sub" + (item.path ? " path" : "");
    if (item.mark) markTerm(detail, item.detail || "", item.mark);
    else detail.textContent = item.detail || "";

    row.append(label, detail);
    row.addEventListener("click", () => choose(item));
    box.appendChild(row);
    row._item = item;
    hitRows.push(row);
  }

  if (note) {
    const line = document.createElement("div");
    line.className = "hits-note";
    line.textContent = note;
    box.appendChild(line);
  }
}

function choose(item) {
  hideHits();
  qbox.blur();                    // so the graph's own keys work straight away
  item.go();
}

/* Arrow through the list without leaving the box. */
function moveHit(step) {
  if (!hitRows.length) return;
  if (hitIndex >= 0) hitRows[hitIndex].classList.remove("on");
  hitIndex = (hitIndex + step + hitRows.length) % hitRows.length;
  const row = hitRows[hitIndex];
  row.classList.add("on");
  if (row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
}

/* Enter takes the highlighted result, or the first one when you have not
   moved -- typing a name and pressing enter should just get you there. */
function takeHit() {
  const row = hitRows[hitIndex >= 0 ? hitIndex : 0];
  if (!row) return false;
  choose(row._item);
  return true;
}

/* Wrap each occurrence of the term in a <mark>, without handing a line out of
   someone's file to innerHTML. */
function markTerm(into, text, term) {
  const low = text.toLowerCase(), needle = term.toLowerCase();
  let i = 0;
  while (needle) {
    const at = low.indexOf(needle, i);
    if (at === -1) break;
    if (at > i) into.appendChild(document.createTextNode(text.slice(i, at)));
    const m = document.createElement("mark");
    m.textContent = text.slice(at, at + needle.length);
    into.appendChild(m);
    i = at + needle.length;
  }
  if (i < text.length) into.appendChild(document.createTextNode(text.slice(i)));
}

async function runTextSearch(term) {
  $("stat-index").textContent = "searching…";
  let r;
  try { r = await api("/api/grep", { q: term }); }
  catch (err) { toast("search failed", true); hideHits(); return; }
  if (qbox.value.trim() !== term) return;          // the box moved on

  const inScope = (path) =>
    viewRoot === ROOT || path === viewRoot || path.startsWith(viewRoot + "/");
  const all = r.hits || [];
  const hits = all.filter((h) => inScope(h.path));

  for (const h of hits) if (nodes.has(h.path)) matches.add(h.path);

  const note = hits.length
    ? (r.truncated || hits.length > 200
        ? "more matches than shown — narrow the search" : "")
    : (all.length ? `nothing here — ${all.length} outside this folder`
                  : `nothing contains “${term}”`);

  showHits(hits.map((h) => ({
    label: `${baseName(h.path)}:${h.line}`,
    detail: h.text || "",
    mark: term,
    colour: COLOR.note,
    title: `${h.path}:${h.line}`,
    go: () => revealPath(h.path, h.line),
  })), note);

  const files = new Set(hits.map((h) => h.path)).size;
  $("stat-index").textContent = hits.length
    ? `${hits.length} line${hits.length === 1 ? "" : "s"} in ${files} file${files === 1 ? "" : "s"}`
    : `nothing contains “${term}”`;
  dirty = true;
}

async function runSearch() {
  const term = qbox.value.trim();
  matches.clear();
  if (term.length < 2) { hideHits(); updateStats(); return; }
  if (searchMode === "text") return runTextSearch(term);
  $("stat-index").textContent = "searching…";
  let r;
  try { r = await api("/api/search", { q: term }); }
  catch (err) { toast("search failed", true); return; }

  // when the graph is narrowed, matches from elsewhere would graft on with no
  // visible parent, so drop them and trim the lineage back to the view root
  const inScope = (path) =>
    viewRoot === ROOT || path === viewRoot || path.startsWith(viewRoot + "/");
  const results = r.results.filter((h) => inScope(h.node.id));

  const rootNode = nodes.get(viewRoot);
  const grafted = [];
  for (const hit of results) {
    let prev = rootNode;
    for (const anc of hit.ancestors) {
      if (anc.id === viewRoot || !inScope(anc.id)) continue;
      const a = addNode(anc, prev);
      addLink(prev.id, a.id, "tree");
      prev = a;
    }
    const n = addNode(hit.node, prev);
    addLink(prev.id, n.id, "tree");
    matches.add(n.id);
    grafted.push(n.id);
  }
  linkUp(grafted);
  updateStats();
  if (qbox.value.trim() !== term) return;          // the box moved on

  const shown = results.length;
  showHits(results.map((hit) => ({
    label: hit.node.name + (hit.node.dir ? "/" : ""),
    detail: dirLabel(hit.node.id),
    path: true,
    colour: COLOR[hit.node.group] || COLOR.other,
    title: hit.node.id,
    go: () => revealPath(hit.node.id),
  })), hitsNote(term, shown, r.count));

  $("stat-index").textContent =
    `${shown} match${shown === 1 ? "" : "es"} for “${term}”`
    + (shown < r.count ? ` (${r.count - shown} outside this folder)` : "");
  if (shown) {
    warmup(220);
    fit();
  }
}

/* Step to the next node in a direction. Linked neighbours win, because that is
   the graph you are reading; anything else visible is the fallback, so the
   arrows never simply stop working. */
function step(dirX, dirY) {
  if (!sel) return;
  let best = null, bestScore = Infinity;

  const consider = (m) => {
    if (m === sel || !visible(m)) return;
    const dx = m.x - sel.x, dy = m.y - sel.y;
    const along = dx * dirX + dy * dirY;
    if (along <= 0) return;                       // behind us
    const across = Math.abs(dx * dirY - dy * dirX);
    if (across > along) return;                   // outside a 45-degree cone
    const score = along + across * 1.6;
    if (score < bestScore) { bestScore = score; best = m; }
  };

  for (const id of neighbours) {
    const m = nodes.get(id);
    if (m) consider(m);
  }
  if (!best) for (const m of nodes.values()) consider(m);
  if (best) { select(best); centerOn(best); }
}

/* ----------------------------------------------------------------- export

   The graph is worth showing to people, and a screenshot of a browser window
   is not the way. PNG is the canvas exactly as drawn; SVG is redrawn from the
   same numbers so it stays sharp at any size, which is what a slide or a print
   wants. Both take the view as it stands -- pan, zoom and filters included. */
const XML_ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const xesc = (s) => String(s).replace(/[&<>"]/g, (c) => XML_ESC[c]);
const round = (v) => Math.round(v * 10) / 10;

function sceneSVG() {
  const out = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" ` +
    `viewBox="0 0 ${W} ${H}" font-family="ui-sans-serif, system-ui, sans-serif">`,
    `<rect width="${W}" height="${H}" fill="#070910"/>`,
  ];

  for (const l of links) {
    const a = nodes.get(l.a), b = nodes.get(l.b);
    if (!a || !b || !visible(a) || !visible(b)) continue;
    const semantic = l.kind !== "tree";
    if (semantic && !showSemantic) continue;
    const stroke = !semantic ? "#1e293b"
      : l.kind === "code" ? "#60a5fa" : l.kind === "tag" ? "#c084fc" : "#34d399";
    const opacity = semantic ? 0.18 : 1;
    out.push(`<line x1="${round(toScreenX(a.x))}" y1="${round(toScreenY(a.y))}" ` +
             `x2="${round(toScreenX(b.x))}" y2="${round(toScreenY(b.y))}" ` +
             `stroke="${stroke}" stroke-opacity="${opacity}" stroke-width="1"/>`);
  }

  for (const n of nodes.values()) {
    if (!visible(n)) continue;
    const x = toScreenX(n.x), y = toScreenY(n.y);
    if (x < -60 || x > W + 60 || y < -60 || y > H + 60) continue;
    const r = Math.max(1.8, n.r * cam.s);
    const c = COLOR[n.group] || COLOR.other;
    const hollow = n.tag || (n.dir && expanded.has(n.id));
    out.push(`<circle cx="${round(x)}" cy="${round(y)}" r="${round(r)}" ` +
             (hollow
               ? `fill="#0b1120" stroke="${c}" stroke-width="${round(Math.max(1.2, r * 0.26))}"/>`
               : `fill="${c}"/>`));
    if (n.tag) {
      out.push(`<circle cx="${round(x)}" cy="${round(y)}" ` +
               `r="${round(Math.max(1, r * 0.3))}" fill="${c}"/>`);
    }
    const state = showGit && gitStates.get(n.id);
    if (state && r > 2.4) {
      const gr = Math.max(1.8, r * (state === "inside" ? 0.24 : 0.34));
      out.push(`<circle cx="${round(x + r * 0.72)}" cy="${round(y - r * 0.72)}" ` +
               `r="${round(gr)}" fill="${GIT_COLOR[state]}" ` +
               `stroke="#070910" stroke-width="1.1"/>`);
    }
  }

  if (showLabels) {
    for (const lab of labelPlan) {
      const fill = lab.active ? "#f4f7fc" : lab.dir ? "#c4d0e4" : "#7f8da6";
      out.push(`<text x="${round(lab.x)}" y="${round(lab.y)}" ` +
               `text-anchor="middle" dominant-baseline="hanging" ` +
               `font-size="${round(lab.size)}" ` +
               `font-weight="${lab.active || lab.dir ? 600 : 400}" ` +
               `fill="${fill}">${xesc(lab.text)}</text>`);
    }
  }

  out.push("</svg>");
  return out.join("\n");
}

function exportName(ext) {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const where = (viewRoot.split("/").pop() || "cortex").replace(/[^\w.-]/g, "_");
  return `${where}-${stamp}.${ext}`;
}

function offerDownload(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function exportScene(kind) {
  draw();                       // the canvas may be mid-freeze; make it current
  if (kind === "svg") {
    offerDownload(new Blob([sceneSVG()], { type: "image/svg+xml" }),
                  exportName("svg"));
    toast("saved an svg of this view");
    return;
  }
  if (!stage.toBlob) { toast("this browser cannot export a png", true); return; }
  stage.toBlob((blob) => {
    if (!blob) { toast("could not make a png", true); return; }
    offerDownload(blob, exportName("png"));
    toast("saved a png of this view");
  }, "image/png");
}

// ------------------------------------------------------------------ filters
/* The chips along the top bar.

   Clicking one shows that kind and takes everything else off the graph, which
   is the way round people reach for: you click "notes" because you want to see
   the notes, not because you want them gone. Folders stay either way, because
   they are the skeleton every other node hangs from and a graph without them
   is a cloud of unconnected dots. Clicking the same chip again brings it all
   back, and shift-clicking still hides one kind on its own. */
let soloGroup = null;
const chipEls = new Map();

function buildFilters() {
  const nav = $("filters");
  for (const g of GROUPS) {
    const chip = document.createElement("button");
    chip.className = "chip-f on";
    chip.style.color = COLOR[g];
    chip.innerHTML = `<i></i><span>${LABEL[g]}</span>`;
    chip.title = `show only ${LABEL[g]} — shift-click to hide just this kind`;
    chip.addEventListener("click", (e) => {
      if (e.shiftKey) muteGroup(g); else soloOnly(g);
    });
    chipEls.set(g, chip);
    nav.appendChild(chip);
  }
}

function soloOnly(g) {
  soloGroup = soloGroup === g ? null : g;
  hidden.clear();
  if (soloGroup) {
    for (const other of GROUPS) {
      if (other === soloGroup) continue;
      if (other === "dir") continue;        // the skeleton always stays
      hidden.add(other);
    }
  }
  afterFilterChange();
}

function muteGroup(g) {
  soloGroup = null;
  if (hidden.has(g)) hidden.delete(g); else hidden.add(g);
  afterFilterChange();
}

function afterFilterChange() {
  for (const [g, chip] of chipEls) {
    const off = hidden.has(g);
    chip.classList.toggle("on", !off);
    chip.classList.toggle("off", off);
    chip.classList.toggle("solo", soloGroup === g);
  }
  updateStats();
  reheat(0.3);
}

function updateStats() {
  dirty = true;
  if (booted) placeChrome();
  let shown = 0;
  for (const n of nodes.values()) if (visible(n)) shown++;
  let semantic = 0;
  for (const l of links) if (l.kind !== "tree") semantic++;
  $("stat-nodes").textContent = `${shown} node${shown === 1 ? "" : "s"}`;
  const gitEl = $("stat-git");
  if (gitEl) {
    let changed = 0;
    for (const st of gitStates.values()) if (st !== "inside") changed++;
    gitEl.hidden = !gitBranch;
    gitEl.textContent = gitBranch
      ? `${gitBranch}${changed ? ` · ${changed} changed` : ""}` : "";
  }
  const pool = semanticEdges ? semanticEdges.length : 0;
  $("stat-links").textContent = `${links.length - semantic} tree · ${semantic}`
    + (pool ? ` of ${pool} semantic` : " semantic");
}

// ----------------------------------------------------------------- keyboard
window.addEventListener("keydown", (e) => keyHandler(e));

function keyHandler(e) {
  // ctrl-k, from anywhere but the palette itself: everything, by name
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K") && !CFG.capture) {
    e.preventDefault();
    if ($("palette").classList.contains("open")) closePalette(); else openPalette();
    return;
  }
  if ($("palette").classList.contains("open")) return;
  if ($("share").classList.contains("open")) {
    if (e.key === "Escape") $("share").classList.remove("open");
    return;
  }
  // ctrl-s saves the open editor wherever focus is, and never the page
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    if (editing) saveEdit();
    return;
  }
  if ($("ask").classList.contains("open")) {
    if (e.key === "Escape") answerAsk(false);
    else if (e.key === "Enter" && e.target !== $("ask-ok")
             && e.target !== $("ask-cancel")) answerAsk(true);
    return;
  }
  if ($("gone").classList.contains("open")) return;
  const tag = e.target && e.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA") return;
  if (e.target === qbox || e.target === $("sb-filter")) return;
  if ($("places").classList.contains("open")) {
    if (e.key === "Escape") closePlaces();
    return;
  }
  if (menuOpen && e.key === "Escape") { closeMenu(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  // while a tour plays, the arrows step through it
  if (tourPlay && (e.key === "ArrowRight" || e.key === "ArrowLeft" || e.key === " ")) {
    e.preventDefault();
    tourStep(e.key === "ArrowLeft" ? -1 : 1);
    return;
  }
  if (hist && (e.key === " " || e.key === "[" || e.key === "]")) {
    e.preventDefault();
    if (e.key === " ") playHistory();
    else { playHistory(false); setHistIndex(hist.v + (e.key === "]" ? 1 : -1)); }
    return;
  }
  // a snapshot can be looked at, not changed
  if (SNAPSHOT && ((e.key.length === 1 && "iwhudpnv".includes(e.key))
                   || e.key === "F2" || e.key === "Delete")) return;
  if (/^[1-9]$/.test(e.key)) {
    const v = viewsList[Number(e.key) - 1];
    if (v) applyView(v); else toast(`no view on ${e.key} yet — press v to save one`);
    return;
  }
  switch (e.key) {
    case "/":
      e.preventDefault(); qbox.focus(); qbox.select(); break;
    case "Escape":
      // one thing at a time, the most recent first
      if ($("help").classList.contains("open")) $("help").classList.remove("open");
      else if ($("save-menu").classList.contains("open")) $("save-menu").classList.remove("open");
      else if (popFor) closePop();
      else if (tourPlay) endTour();
      else if (pathHops || !$("pathcard").hidden) clearPath();
      else if (maximized) setMax(false);
      else closePanel();
      break;
    case "w":
      toggleLive(); break;
    case "h":
      toggleHistory(); break;
    case "T":
      openTours(); break;
    case "v":
      openViews(); break;
    case "u":
      toggleDisk(); break;
    case "d":
      e.preventDefault(); openToday(); break;
    case "X":
      exportScene("svg"); break;
    case "Enter":
      if (sel && !sel.tag && primaryEditor) {
        act("edit", sel.id, primaryEditor.id, selLine);
      }
      break;
    case "r":
      if (sel && !sel.dir && !sel.tag && TERMINAL === "here") {
        act("read", sel.id, null, selLine);
      }
      break;
    case "i":
      // preventDefault, or the i is typed into the editor it just opened
      if (sel && !sel.dir && !sel.tag) { e.preventDefault(); startEdit(sel); }
      break;
    case "n":
      e.preventDefault();
      newEntry(sel && !sel.tag ? parentOfNode(sel) : viewRoot);
      break;
    case "F2":
      if (sel && !sel.tag) { e.preventDefault(); renameEntry(sel); }
      break;
    case "Delete":
      if (sel && !sel.tag) { e.preventDefault(); trashEntry(sel); }
      break;
    case "p":
      e.preventDefault(); openPlaces(); break;
    case "e":
      if (sel && sel.dir) expanded.has(sel.id) ? collapse(sel.id) : expand(sel.id); break;
    case "f":
      if (sel) { focusMode = !focusMode; recomputeNeighbours(); } break;
    case "c":
      if (sel) { connOpen = !connOpen; renderConnections(sel); } break;
    case "g":
      showGit = !showGit;
      dirty = true;
      toast(showGit ? "git marks on" : "git marks off");
      break;
    case "m":
      if (sel) setMax(!maximized); break;
    case "o":
      if (sel && sel.dir && !sel.tag) isolate(sel.id); break;
    case "b":
      showFullMap(); break;
    case "s":
      toggleSidebar(); break;
    case "t":
      showLabels = !showLabels;
      dirty = true;
      toast(showLabels ? "labels on" : "labels off — safe for screenshots");
      break;
    case "l":
      $("btn-links").click(); break;
    case "?":
      $("help").classList.toggle("open"); break;
    case "0":
      fit(); break;
    case "ArrowLeft":
      e.preventDefault(); step(-1, 0); break;
    case "ArrowRight":
      e.preventDefault(); step(1, 0); break;
    case "ArrowUp":
      e.preventDefault(); step(0, -1); break;
    case "ArrowDown":
      e.preventDefault(); step(0, 1); break;
    case "x":
      exportScene(e.shiftKey ? "svg" : "png"); break;
  }
}

// --------------------------------------------------------------------- wire
$("p-close").addEventListener("click", closePanel);
$("p-max").addEventListener("click", () => setMax(!maximized));
$("p-path").addEventListener("click", () => {
  if (!sel || sel.tag) return;
  navigator.clipboard.writeText(sel.id).then(() => toast("path copied"),
                                             () => toast("copy blocked", true));
});
$("scope").addEventListener("click", () => showFullMap());
$("sb-toggle").addEventListener("click", () => toggleSidebar());
$("sb-close").addEventListener("click", () => toggleSidebar(false));
$("sb-filter").addEventListener("input", () => renderSidebar());
$("sb-filter").addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("sb-filter").value) { $("sb-filter").value = ""; renderSidebar(); }
    else toggleSidebar(false);
  }
});
$("mode").addEventListener("click", () => toggleSearchMode());
document.addEventListener("click", (e) => {
  if (!$("hits").hidden && !(e.target.closest && e.target.closest(".searchwrap"))) {
    hideHits();
  }
});
$("btn-fit").addEventListener("click", () => fit());
$("btn-save").addEventListener("click", (e) => openSaveMenu(e));
$("btn-help").addEventListener("click", () => $("help").classList.toggle("open"));
$("help-close").addEventListener("click", () => $("help").classList.remove("open"));
$("help").addEventListener("click", (e) => {
  if (e.target.id === "help") $("help").classList.remove("open");
});
$("btn-links").addEventListener("click", (e) => {
  showSemantic = !showSemantic;
  e.currentTarget.classList.toggle("on", showSemantic);
  if (showSemantic) applySemantic();
});
$("z-in").addEventListener("click", () => zoomAt(W / 2, H / 2, 1.25));
$("z-out").addEventListener("click", () => zoomAt(W / 2, H / 2, 0.8));
document.addEventListener("click", (e) => {
  if (openDropdown) { openDropdown.classList.remove("open"); openDropdown = null; }
  const inside = (sel_) => e.target && e.target.closest && e.target.closest(sel_);
  if (!inside("#save-wrap")) $("save-menu").classList.remove("open");
  if (popFor && !inside("#pop") && !inside("#dock")) closePop();
});

// the dock, and what its buttons open
$("d-live").addEventListener("click", () => toggleLive());
$("d-history").addEventListener("click", () => toggleHistory());
$("d-tours").addEventListener("click", () => {
  if (tourPlay) endTour(); else if (tourRec) renderTourCard(); else openTours();
});
$("d-views").addEventListener("click", () => openViews());
$("d-disk").addEventListener("click", () => toggleDisk());
$("d-today").addEventListener("click", () => openToday());
$("feed-follow").addEventListener("click", () => {
  if (!live) return;
  live.follow = !live.follow;
  renderFeed();
  toast(live.follow ? "following — the camera goes to each change" : "not following");
});
$("feed-reveal").addEventListener("click", () => {
  if (!live) return;
  live.reveal = !live.reveal;
  renderFeed();
});
$("feed-clear").addEventListener("click", () => {
  heat.clear();
  trail = [];
  if (live) { live.events = []; live.count = 0; renderFeed(); }
  repaint();
});
$("tl-play").addEventListener("click", () => playHistory());
$("tl-range").addEventListener("input", (e) => {
  playHistory(false);
  setHistIndex(Number(e.target.value));
});
$("tl-speed").addEventListener("click", () => {
  if (!hist) return;
  hist.speed = SPEEDS[(SPEEDS.indexOf(hist.speed) + 1) % SPEEDS.length];
  renderHistInfo();
});
$("tl-record").addEventListener("click", () => recordHistory());
$("tl-close").addEventListener("click", () => toggleHistory(false));

$("pal-input").addEventListener("input", () => {
  renderPalette();
  clearTimeout(palTimer);
  palTimer = setTimeout(paletteFiles, 140);
});
$("pal-input").addEventListener("keydown", (e) => paletteKey(e));
$("palette").addEventListener("click", (e) => { if (e.target.id === "palette") closePalette(); });
$("share-ok").addEventListener("click", () => makeShare());
$("share-cancel").addEventListener("click", () => $("share").classList.remove("open"));
$("share").addEventListener("click", (e) => {
  if (e.target.id === "share") $("share").classList.remove("open");
});
window.addEventListener("mousedown", (e) => {
  if (menuOpen && !(e.target && e.target.closest && e.target.closest("#ctx"))) {
    closeMenu();
  }
});

$("ask-ok").addEventListener("click", () => answerAsk(true));
$("ask-cancel").addEventListener("click", () => answerAsk(false));
$("ask").addEventListener("click", (e) => {
  if (e.target.id === "ask") answerAsk(false);
});

$("btn-places").addEventListener("click", () => openPlaces());
$("pl-close").addEventListener("click", () => closePlaces());
$("places").addEventListener("click", (e) => {
  if (e.target.id === "places") closePlaces();
  else if (!(e.target.closest && e.target.closest(".pl-pathwrap"))) hideComplete();
});
$("pl-path").addEventListener("input", () => {
  clearTimeout(completeTimer);
  completeTimer = setTimeout(completePath, 120);
});
$("pl-path").addEventListener("keydown", (e) => placesKey(e));
$("pl-name").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); saveProject(); }
  else if (e.key === "Escape") closePlaces();
});
$("pl-save-btn").addEventListener("click", () => saveProject());
$("pl-quit").addEventListener("click", () => quitCortex());
$("stat-drafts").addEventListener("click", () => {
  const first = drafts.keys().next().value;
  if (first) revealPath(first);
});
$("gone-close").addEventListener("click", () => window.close && window.close());
$("gone-retry").addEventListener("click", () => location.reload && location.reload());

// --------------------------------------------------------------------- boot
(async () => {
  if (CFG.capture) {                 // the quick-note window is only that
    hello();
    window.addEventListener("pagehide", () => goodbye());
    startCapture();
    return;
  }
  buildFilters();
  showZoom();
  toggleSearchMode("names");        // label, placeholder and list, in one place
  // the name identifies a folder; the path is in the tooltip
  $("place-name").textContent = ROOT === CFG.home ? "~" : (ROOT.split("/").pop() || "/");
  $("btn-places").title = `${CFG.title || ROOT} — open another folder (p)`;
  $("stat-ro").hidden = !READ_ONLY || !!SNAPSHOT;
  if (SNAPSHOT) {
    // a snapshot says what it is, where the folder name would be
    $("place-name").textContent = `${CFG.title} · snapshot`;
    $("btn-places").title = `A read-only snapshot, taken ${SNAPSHOT.taken} with cortex `
      + `${SNAPSHOT.version}. It does not change, and cannot change anything.`;
    $("btn-places").addEventListener("click", (e) => {
      e.stopImmediatePropagation();
      toast(`a snapshot taken ${SNAPSHOT.taken} — explore, search, read`);
    }, true);
    for (const id of ["d-live", "d-history", "d-views", "d-disk", "d-today"]) $(id).hidden = true;
  }
  loadViews();

  hello();
  window.addEventListener("pagehide", () => goodbye());
  // the browser's own "leave this page?" is the one prompt worth its
  // interruption: closing now would lose typing that was never saved
  window.addEventListener("beforeunload", (e) => {
    if (!drafts.size || gone) return;
    e.preventDefault();
    e.returnValue = "";
  });

  try {
    const list = await api("/api/editors");
    editors = list.editors || [];
    // the server lists them best first for how cortex was started: terminal
    // editors first from a terminal, windowed ones first from an app menu
    primaryEditor = editors.find((e) => e.env) || editors[0] || null;
  } catch (err) { /* actions degrade gracefully */ }

  await loadLayout();
  try {
    await setScope(await api("/api/root"));
  } catch (err) {
    toast("cortex backend not reachable", true);
  }

  applySemantic();          // not awaited: the graph should paint first
  loadGit();
  if (SNAPSHOT && CFG.view) await applyView(CFG.view);
  if (SNAPSHOT) {
    // the dock has only tours to offer, and only when there are some
    api("/api/tours").then((r) => {
      if (!(r.tours || []).length) $("dock").hidden = true;
    }).catch(() => { $("dock").hidden = true; });
  }
  const every = CFG.pulseMs || 0;
  if (every > 0) setInterval(pulse, every);

  /* A remembered layout opens exactly where you left it: the camera is
     restored and the simulation starts frozen, rather than fitting to screen
     and settling into somewhere new. Anything that arrives later -- a file
     created since, a folder you expand -- reheats it in the ordinary way. */
  if (savedCam && savedPos.size) {
    cam = { s: savedCam.s, x: savedCam.x, y: savedCam.y };
    needsFit = false;
    alpha = 0;
    showZoom();
  } else {
    needsFit = true;
  }
  if (CFG.rememberLayout) {
    layoutDirty = true;
    // only once it has stopped moving: saving mid-settle posts every position
    // in the graph to write down somewhere it is about to leave
    setInterval(() => { if (alpha < ALPHA_MIN) saveLayout(); }, 10000);
    window.addEventListener("pagehide", () => { if (!reloading) saveLayout(); });
  }
  booted = true;
  placeChrome();
  frame();
})();

})();
