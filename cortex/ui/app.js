/* cortex — the graph window.
   Canvas force layout + lazy filesystem expansion + a reader panel that can
   hand any file to your terminal. */

(() => {
"use strict";

const CFG = window.CORTEX;
const TOKEN = CFG.token;
const ROOT = CFG.root;
const AUTO = CFG.autoExpand || { depth: 0, budget: 0 };

const GROUPS = ["dir", "note", "code", "config", "doc", "media", "archive", "other"];
/* What git thinks of a file, as a dot on its node. Amber for work in progress,
   green for work already staged, red for something you have to resolve. */
const GIT_COLOR = {
  modified: "#f5a524", staged: "#34d399", untracked: "#8fa3c4",
  conflict: "#f43f5e", inside: "#f5a5247a",
};
const COLOR = {
  dir: "#f5a524", note: "#34d399", code: "#60a5fa", config: "#a78bfa",
  doc: "#f472b6", media: "#fb7185", archive: "#94a3b8", other: "#64748b",
};
const LABEL = {
  dir: "folders", note: "notes", code: "code", config: "config",
  doc: "docs", media: "media", archive: "archives", other: "other",
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
let selLine = null;             // line to open the selection at, from a hit
let gitStates = new Map();      // path -> modified | staged | untracked | ...
let gitBranch = null, showGit = true;
let cam = { s: 1, x: 0, y: 0 };
let needsFit = false;

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
const COOL = 0.982, ALPHA_MIN = 0.004;
const reheat = (a = 0.75) => { alpha = Math.max(alpha, a); dirty = true; };
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
}
window.addEventListener("resize", resize);
resize();
cam = { s: 1, x: W / 2, y: H / 2 };

// --------------------------------------------------------------------- api
async function api(path, params = {}) {
  const url = new URL(path, location.origin);
  url.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}
async function post(path, body) {
  const res = await fetch(`${path}?t=${encodeURIComponent(TOKEN)}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}
const rawURL = (p) =>
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
  const angle = Math.random() * Math.PI * 2;
  const dist = 50 + Math.random() * 70;
  const n = Object.assign({}, raw, {
    x: (near ? near.x : 0) + Math.cos(angle) * dist,
    y: (near ? near.y : 0) + Math.sin(angle) * dist,
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
  if (a === b || !nodes.has(a) || !nodes.has(b)) return;
  // \u0000 cannot occur in a path, so it is a safe key separator
  const key = (a < b ? a + "\u0000" + b : b + "\u0000" + a) + "|" + kind;
  if (linkKeys.has(key)) return;
  linkKeys.add(key);
  links.push({ a, b, kind, key });
}
const linkKeys = new Set();

async function expand(id, opts = {}) {
  const parent = nodes.get(id);
  if (!parent || !parent.dir || expanded.has(id)) return;
  expanded.add(id);
  try {
    const kids = await api("/api/children", { path: id });
    for (const kid of kids) {
      addNode(kid, parent);
      addLink(id, kid.id, "tree");
    }
    if (showSemantic) applySemantic();
    updateStats();
    reheat();
  } catch (err) {
    expanded.delete(id);
    if (!opts.quiet) toast("could not read that folder", true);
  }
}

function collapse(id) {
  expanded.delete(id);
  const doomed = new Set();
  const walk = (pid) => {
    for (const n of nodes.values()) {
      if (n.parent === pid && !doomed.has(n.id)) {
        doomed.add(n.id);
        if (n.dir) walk(n.id);
      }
    }
  };
  walk(id);
  for (const d of doomed) { nodes.delete(d); expanded.delete(d); matches.delete(d); }
  links = links.filter((l) => {
    const drop = doomed.has(l.a) || doomed.has(l.b);
    if (drop) linkKeys.delete(l.key);
    return !drop;
  });
  if (sel && doomed.has(sel.id)) closePanel();
  updateStats();
  reheat(0.45);
}

async function applySemantic() {
  if (!semanticEdges) {
    try {
      const snap = await api("/api/links");
      semanticEdges = snap.edges || [];
      $("stat-index").textContent = snap.ready
        ? `${snap.notes} notes · ${snap.sources} sources indexed`
        : `indexing… ${snap.edges.length} links`;
      if (!snap.ready) setTimeout(() => { semanticEdges = null; applySemantic(); }, 4000);
    } catch (err) { return; }
  }
  const before = links.length;
  for (const [a, b, kind] of semanticEdges) addLink(a, b, kind);
  if (links.length !== before) reheat(0.4);
  buildConnIndex();
  if (sel) renderConnections(sel);
  updateStats();
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
  const add = (from, to, kind, out) => {
    let list = connIndex.get(from);
    if (!list) connIndex.set(from, (list = []));
    list.push({ path: to, kind, out });
  };
  for (const [a, b, kind] of semanticEdges || []) {
    add(a, b, kind, true);
    add(b, a, kind, false);
  }
  for (const list of connIndex.values()) {
    list.sort((x, y) =>
      (x.out === y.out ? 0 : x.out ? -1 : 1) ||
      baseName(x.path).localeCompare(baseName(y.path)));
  }
}

const baseName = (p) => p.slice(p.lastIndexOf("/") + 1);

function dirLabel(p) {
  const dir = p.slice(0, p.lastIndexOf("/"));
  if (dir === ROOT) return "./";
  return (dir.startsWith(ROOT + "/") ? dir.slice(ROOT.length + 1) : dir) + "/";
}

function renderConnections(n) {
  const box = $("p-links");
  box.textContent = "";
  const list = (n && connIndex && connIndex.get(n.id)) || [];
  if (!list.length) { box.hidden = true; return; }
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
  detail.textContent = `${outs} out \u00b7 ${list.length - outs} in`;
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
    row.className = "conn " + (c.kind === "note" ? "note" : "code");
    row.title = c.path;
    const arrow = document.createElement("span");
    arrow.className = "arrow";
    arrow.textContent = c.out ? "\u2192" : "\u2190";
    const name = document.createElement("span");
    name.className = "nm";
    name.textContent = baseName(c.path);
    const where = document.createElement("span");
    where.className = "where";
    where.textContent = dirLabel(c.path);
    row.append(arrow, name, where);
    row.addEventListener("click", () => revealPath(c.path));
    rows.appendChild(row);
  }
  box.appendChild(rows);
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
        : l.kind === "code" ? "#60a5fa2e" : "#34d3992e";
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
    if (n.dir && expanded.has(n.id)) {
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

  // labels, in a separate pass, with cheap collision avoidance
  const boxes = [];
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
    const text = n.name.length > 34 ? n.name.slice(0, 32) + "…" : n.name;
    const w = ctx.measureText(text).width;
    const bx = x - w / 2, by = y + r + 3, bh = size + 3;
    if (!bypass && !fits(bx, by, w, bh)) continue;
    boxes.push([bx, by, w, bh]);
    nextShown.add(n.id);
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
  if (moving || dirty) { drawCount++; draw(); dirty = false; }
  requestAnimationFrame(frame);
}

/* Read-only introspection, so "is it still repainting?" is a question that can
   be answered instead of guessed at. Nothing in the UI depends on this. */
CFG.debug = () => ({
  alpha: Number(alpha.toFixed(4)), frozen: alpha < ALPHA_MIN, dirty,
  frames: frameCount, draws: drawCount,
  nodes: nodes.size, links: links.length, labels: shownLabels.size,
  selected: sel ? sel.id : null,
});

/* A test seam. The stub-DOM suites drive the real app.js, and selecting a node
   through a synthetic canvas click would test the hit-testing maths rather
   than the thing under test. Returns false when that path is not in the graph,
   which is itself worth asserting. */
CFG.debug.select = (id) => {
  const n = nodes.get(id);
  if (n) select(n);
  return !!n;
};

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

stage.addEventListener("mousedown", (e) => {
  lastPt = { x: e.clientX, y: e.clientY };
  travel = 0;
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
    if (travel < 4) select(dragging);
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
  zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0013));
}, { passive: false });

stage.addEventListener("contextmenu", (e) => e.preventDefault());

// -------------------------------------------------------------------- panel
const panel = $("panel");
const fmtSize = (b) =>
  b < 1024 ? b + " B"
  : b < 1048576 ? (b / 1024).toFixed(1) + " KB"
  : b < 1073741824 ? (b / 1048576).toFixed(1) + " MB"
  : (b / 1073741824).toFixed(2) + " GB";

function closePanel() {
  setMax(false);
  panel.classList.remove("open");
  $("p-links").hidden = true;
  sel = null; selLine = null; focusMode = false; recomputeNeighbours();
  dirty = true;
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
}

function select(n) {
  sel = n;
  selLine = null;
  focusMode = false;
  recomputeNeighbours();
  panel.classList.add("open");
  $("p-kind").textContent = n.dir ? "folder" : (n.group === "other" ? "file" : n.group);
  $("p-name").textContent = n.name;
  $("p-path").textContent = n.id.replace(ROOT, "~");
  $("p-meta").textContent =
    (n.dir ? `${n.kids || 0} item${n.kids === 1 ? "" : "s"}` : fmtSize(n.size)) +
    (n.mtime ? "  ·  " + new Date(n.mtime * 1000).toLocaleString() : "");
  renderGitChip(n);
  buildActions(n);
  renderConnections(n);
  renderBody(n);
}

function buildActions(n) {
  const bar = $("p-acts");
  bar.textContent = "";

  const mk = (label, cls, fn, title) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = cls;
    if (title) b.title = title;
    b.addEventListener("click", fn);
    bar.appendChild(b);
    return b;
  };

  if (primaryEditor) {
    mk(primaryEditor.label, "primary",
       () => act("edit", n.id, primaryEditor.id, selLine),
       `open in ${primaryEditor.label} in your terminal`);
  }
  if (!n.dir) {
    mk("read", "ghost", () => act("read", n.id, null, selLine),
       "page through it in the terminal");
  }

  const others = editors.filter((e) => !primaryEditor || e.id !== primaryEditor.id);
  if (others.length) {
    const wrap = document.createElement("div");
    wrap.className = "more";
    const trigger = document.createElement("button");
    trigger.className = "ghost";
    trigger.textContent = "editor ▾";
    const menu = document.createElement("div");
    menu.className = "menu";
    for (const ed of others) {
      const item = document.createElement("button");
      item.textContent = ed.label + (ed.gui ? "  ⧉" : "");
      item.addEventListener("click", () => {
        menu.classList.remove("open");
        act("edit", n.id, ed.id, selLine);
      });
      menu.appendChild(item);
    }
    trigger.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.classList.toggle("open");
    });
    document.addEventListener("click", () => menu.classList.remove("open"));
    wrap.append(trigger, menu);
    bar.appendChild(wrap);
  }

  mk("shell here", "ghost", () => act("shell", n.id),
     "drop a shell into this directory, in your terminal");
  mk("open ⧉", "ghost", () => act("open", n.id), "hand to the desktop default app");
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

async function renderBody(n) {
  const body = $("p-body");
  body.classList.remove("fill");     // reset before any early return below
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

  body.innerHTML = '<div class="empty">reading…</div>';
  let p;
  try { p = await api("/api/preview", { path: n.id }); }
  catch (err) { body.innerHTML = '<div class="empty">could not read this file</div>'; return; }
  if (sel !== n) return;                          // selection moved on

  const dirOf = n.id.slice(0, n.id.lastIndexOf("/"));
  body.textContent = "";

  if (p.kind === "markdown") {
    const div = document.createElement("div");
    div.className = "md";
    div.innerHTML = window.MD.render(p.text || "", {
      wiki: () => true,
      resolve: (u) => /^(https?:|data:)/.test(u) ? u
        : rawURL(u.startsWith("/") ? u : dirOf + "/" + u),
    });
    div.addEventListener("click", (e) => {
      const a = e.target.closest("a");
      if (!a) return;
      if (a.dataset.rel) {
        e.preventDefault();
        const rel = a.dataset.rel.split("#")[0];
        revealPath(rel.startsWith("/") ? rel : normalize(dirOf + "/" + rel));
      } else if (a.dataset.wiki) {
        e.preventDefault();
        jumpToWiki(a.dataset.wiki);
      }
    });
    body.appendChild(div);
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
    toast(r.terminal ? "→ switch to your terminal" : "opening…");
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
  }
  if (e.key === "Enter") { clearTimeout(searchTimer); runSearch(); }
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

  const box = $("hits");
  box.textContent = "";
  box.hidden = false;

  for (const h of hits.slice(0, 200)) {
    if (nodes.has(h.path)) matches.add(h.path);
    const row = document.createElement("button");
    row.className = "hit";
    row.title = `${h.path}:${h.line}`;
    const where = document.createElement("span");
    where.className = "hit-where";
    where.textContent = `${baseName(h.path)}:${h.line}`;
    const text = document.createElement("span");
    text.className = "hit-text";
    markTerm(text, h.text || "", term);
    row.append(where, text);
    row.addEventListener("click", () => {
      hideHits();
      revealPath(h.path, h.line);
    });
    box.appendChild(row);
  }

  if (!hits.length) {
    const note = document.createElement("div");
    note.className = "hits-note";
    note.textContent = all.length
      ? `no matches here — ${all.length} outside this folder`
      : `nothing contains “${term}”`;
    box.appendChild(note);
  } else if (r.truncated || hits.length > 200) {
    const note = document.createElement("div");
    note.className = "hits-note";
    note.textContent = "more matches than shown — narrow the search";
    box.appendChild(note);
  }

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
  hideHits();
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
  }
  if (showSemantic) applySemantic();
  updateStats();
  const shown = results.length;
  $("stat-index").textContent =
    `${shown} match${shown === 1 ? "" : "es"} for “${term}”`
    + (shown < r.count ? ` (${r.count - shown} outside this folder)` : "");
  if (shown) {
    warmup(220);
    fit();
  }
}

// ------------------------------------------------------------------ filters
function buildFilters() {
  const nav = $("filters");
  for (const g of GROUPS) {
    const chip = document.createElement("button");
    chip.className = "chip-f on";
    chip.style.color = COLOR[g];
    chip.innerHTML = `<i></i><span>${LABEL[g]}</span>`;
    chip.title = `show / hide ${LABEL[g]}`;
    chip.addEventListener("click", () => {
      if (hidden.has(g)) hidden.delete(g); else hidden.add(g);
      chip.classList.toggle("on", !hidden.has(g));
      chip.classList.toggle("off", hidden.has(g));
      updateStats();
      reheat(0.3);
    });
    nav.appendChild(chip);
  }
}

function updateStats() {
  dirty = true;
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
window.addEventListener("keydown", (e) => {
  if (e.target === qbox || e.target === $("sb-filter")) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  switch (e.key) {
    case "/":
      e.preventDefault(); qbox.focus(); qbox.select(); break;
    case "Escape":
      if ($("help").classList.contains("open")) $("help").classList.remove("open");
      else if (maximized) setMax(false);
      else closePanel();
      break;
    case "Enter":
      if (sel && primaryEditor) act("edit", sel.id, primaryEditor.id, selLine); break;
    case "r":
      if (sel && !sel.dir) act("read", sel.id, null, selLine); break;
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
      if (sel && sel.dir) isolate(sel.id); break;
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
  }
});

// --------------------------------------------------------------------- wire
$("p-close").addEventListener("click", closePanel);
$("p-max").addEventListener("click", () => setMax(!maximized));
$("p-path").addEventListener("click", () => {
  if (!sel) return;
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

// --------------------------------------------------------------------- boot
(async () => {
  buildFilters();
  showZoom();
  toggleSearchMode("names");        // label, placeholder and list, in one place

  try {
    const list = await api("/api/editors");
    editors = list.editors || [];
    primaryEditor = editors.find((e) => e.env)
                 || editors.find((e) => !e.gui) || editors[0] || null;
  } catch (err) { /* actions degrade gracefully */ }

  try {
    await setScope(await api("/api/root"));
  } catch (err) {
    toast("cortex backend not reachable", true);
  }

  applySemantic();
  loadGit();
  needsFit = true;
  frame();
})();

})();
