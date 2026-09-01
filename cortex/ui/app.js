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
let showSemantic = true, focusMode = false;
let semanticEdges = null, editors = [], primaryEditor = null;
let cam = { s: 1, x: 0, y: 0 };
let needsFit = false;

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
    depth: raw.id === ROOT
      ? 0 : raw.id.slice(ROOT.length).split("/").filter(Boolean).length,
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
  updateStats();
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

/* Grow the graph along a real path until that node exists, then select it. */
async function revealPath(abs) {
  if (!abs.startsWith(ROOT)) { toast("outside the mapped root", true); return; }
  const rel = abs.slice(ROOT.length).split("/").filter(Boolean);
  let cur = ROOT;
  for (let i = 0; i < rel.length; i++) {
    if (!expanded.has(cur)) await expand(cur, { quiet: true });
    cur = cur + "/" + rel[i];
    if (!nodes.has(cur)) break;
  }
  const node = nodes.get(abs);
  if (!node) { toast("could not locate that file", true); return; }
  select(node);
  centerOn(node);
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
    if (n.id === ROOT) { n.x = 0; n.y = 0; n.vx = 0; n.vy = 0; continue; }
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
  const panelW = $("panel").classList.contains("open") ? 520 : 0;
  const availW = Math.max(200, W - panelW - pad * 2);
  const availH = Math.max(200, H - pad * 2 - 70);
  const s = Math.max(0.06, Math.min(1.15,
    Math.min(availW / Math.max(1, maxX - minX), availH / Math.max(1, maxY - minY))));
  cam.s = s;
  cam.x = pad + availW / 2 - ((minX + maxX) / 2) * s;
  cam.y = pad + 40 + availH / 2 - ((minY + maxY) / 2) * s;
  showZoom();
}

function centerOn(n) {
  const panelW = $("panel").classList.contains("open") ? 520 : 0;
  cam.s = Math.max(cam.s, 1.1);
  cam.x = (W - panelW) / 2 - n.x * cam.s;
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
  } else if (panning && travel < 4) {
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
  panel.classList.remove("open");
  sel = null; focusMode = false; recomputeNeighbours();
  dirty = true;
}

function select(n) {
  sel = n;
  focusMode = false;
  recomputeNeighbours();
  panel.classList.add("open");
  $("p-kind").textContent = n.dir ? "folder" : (n.group === "other" ? "file" : n.group);
  $("p-name").textContent = n.name;
  $("p-path").textContent = n.id.replace(ROOT, "~");
  $("p-meta").textContent =
    (n.dir ? `${n.kids || 0} item${n.kids === 1 ? "" : "s"}` : fmtSize(n.size)) +
    (n.mtime ? "  ·  " + new Date(n.mtime * 1000).toLocaleString() : "");
  buildActions(n);
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
       () => act("edit", n.id, primaryEditor.id),
       `open in ${primaryEditor.label} in your terminal`);
  }
  if (!n.dir) {
    mk("read", "ghost", () => act("read", n.id), "page through it in the terminal");
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
        act("edit", n.id, ed.id);
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
  mk("focus", "ghost", () => {
    focusMode = !focusMode;
    recomputeNeighbours();
    toast(focusMode ? "focus mode — only linked nodes" : "showing everything");
  }, "hide everything not connected to this node");
}

async function renderBody(n) {
  const body = $("p-body");
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
    const frame = document.createElement("iframe");
    frame.className = "viewer-frame";
    frame.src = rawURL(n.id);
    body.appendChild(frame);
    return;
  }

  if (p.kind === "image") {
    const img = document.createElement("img");
    img.className = "viewer-img";
    img.src = rawURL(n.id);
    body.appendChild(img);
    return;
  }

  if (p.kind === "video" || p.kind === "audio") {
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
async function act(kind, path, editor) {
  try {
    const r = await post("/api/action", { kind, path, editor });
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
  if (e.key === "Escape") { qbox.value = ""; matches.clear(); qbox.blur(); }
  if (e.key === "Enter") { clearTimeout(searchTimer); runSearch(); }
});

async function runSearch() {
  const term = qbox.value.trim();
  matches.clear();
  if (term.length < 2) { updateStats(); return; }
  $("stat-index").textContent = "searching…";
  let r;
  try { r = await api("/api/search", { q: term }); }
  catch (err) { toast("search failed", true); return; }

  const rootNode = nodes.get(ROOT);
  for (const hit of r.results) {
    let prev = rootNode;
    for (const anc of hit.ancestors) {
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
  $("stat-index").textContent =
    `${r.count} match${r.count === 1 ? "" : "es"} for “${term}”`;
  if (r.results.length) {
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
  const pool = semanticEdges ? semanticEdges.length : 0;
  $("stat-links").textContent = `${links.length - semantic} tree · ${semantic}`
    + (pool ? ` of ${pool} semantic` : " semantic");
}

// ----------------------------------------------------------------- keyboard
window.addEventListener("keydown", (e) => {
  if (e.target === qbox) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  switch (e.key) {
    case "/":
      e.preventDefault(); qbox.focus(); qbox.select(); break;
    case "Escape":
      if ($("help").classList.contains("open")) $("help").classList.remove("open");
      else closePanel();
      break;
    case "Enter":
      if (sel && primaryEditor) act("edit", sel.id, primaryEditor.id); break;
    case "r":
      if (sel && !sel.dir) act("read", sel.id); break;
    case "e":
      if (sel && sel.dir) expanded.has(sel.id) ? collapse(sel.id) : expand(sel.id); break;
    case "f":
      if (sel) { focusMode = !focusMode; recomputeNeighbours(); } break;
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
$("p-path").addEventListener("click", () => {
  if (!sel) return;
  navigator.clipboard.writeText(sel.id).then(() => toast("path copied"),
                                             () => toast("copy blocked", true));
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

  try {
    const list = await api("/api/editors");
    editors = list.editors || [];
    primaryEditor = editors.find((e) => !e.gui) || editors[0] || null;
  } catch (err) { /* actions degrade gracefully */ }

  try {
    const root = await api("/api/root");
    const rn = addNode(root, null);
    rn.x = 0; rn.y = 0;
    await expand(root.id);
    if (AUTO.depth > 0) await autoGrow(AUTO.depth, AUTO.budget || 700);
  } catch (err) {
    toast("cortex backend not reachable", true);
  }

  applySemantic();
  needsFit = true;
  frame();
})();

})();
