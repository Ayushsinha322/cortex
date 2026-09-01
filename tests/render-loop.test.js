/* Tests the real cortex/ui/app.js render loop against a stub DOM.
 *
 * Why a stub instead of a browser: Chrome pauses requestAnimationFrame in
 * background tabs, so measuring "is it still repainting?" from an automated
 * tab reports a frozen canvas whether or not the fix works. Here frames are
 * pumped by hand, so the answer is real.
 *
 * What it guards: the layout must cool to a stop and stop repainting. A
 * simulation that never settles jitters, and jitter reads as flicker -- labels
 * sit on the collision threshold and blink on and off.
 *
 * Run: node tests/render-loop.test.js   (or tests/run)
 */

const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "cortex", "ui", "app.js");

// ------------------------------------------------------------- fake tree
const ROOT = "/home/u/proj";
const DIRS = 14, FILES_PER_DIR = 14, ROOT_FILES = 18;

function fsnode(id, isDir, kids) {
  return {
    id, name: id.split("/").pop(), dir: isDir,
    group: isDir ? "dir" : (id.endsWith(".md") ? "note" : "code"),
    size: 4096, mtime: Date.now() / 1000 - 99999,
    parent: id === ROOT ? null : id.slice(0, id.lastIndexOf("/")),
    ...(isDir ? { kids } : {}),
  };
}

function childrenOf(p) {
  if (p === ROOT) {
    const out = [];
    for (let i = 0; i < DIRS; i++) out.push(fsnode(`${ROOT}/dir${i}`, true, FILES_PER_DIR));
    for (let i = 0; i < ROOT_FILES; i++) out.push(fsnode(`${ROOT}/file${i}.md`, false));
    return out;
  }
  if (/\/dir\d+$/.test(p)) {
    const out = [];
    for (let i = 0; i < FILES_PER_DIR; i++) out.push(fsnode(`${p}/mod${i}.py`, false));
    return out;
  }
  return [];
}

// ----------------------------------------------------------- stub canvas
const ctx2d = new Proxy({}, {
  get(_t, prop) {
    if (prop === "measureText") return (s) => ({ width: String(s).length * 6 });
    if (prop === "createRadialGradient" || prop === "createLinearGradient") {
      return () => ({ addColorStop() {} });
    }
    if (prop === "canvas") return stage;
    return () => {};
  },
  set() { return true; },
});

function element(id) {
  return {
    id, tagName: "DIV", value: "", textContent: "", innerHTML: "",
    style: {}, dataset: {}, children: [], width: 1600, height: 900,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, on) {
        if (on === undefined) this._s.has(c) ? this._s.delete(c) : this._s.add(c);
        else on ? this._s.add(c) : this._s.delete(c);
      },
    },
    _ev: {},
    addEventListener(k, fn) { (this._ev[k] = this._ev[k] || []).push(fn); },
    removeEventListener() {},
    dispatchEvent(e) {
      (this._ev[e.type] || []).forEach((fn) => fn(e));
      return true;
    },
    click() {
      this.dispatchEvent({ type: "click", target: this, currentTarget: this,
                           stopPropagation() {} });
    },
    getContext: () => ctx2d,
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    replaceChildren() { this.children = []; },
    closest: () => null,
    focus() {}, blur() {}, select() {}, scrollTo() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1600, height: 900 }),
  };
}

const byId = new Map();
const stage = element("stage");
byId.set("stage", stage);

global.document = {
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, element(id));
    return byId.get(id);
  },
  createElement(tag) {
    const e = element("new-" + tag);
    e.tagName = String(tag).toUpperCase();
    return e;
  },
  addEventListener() {},
  body: element("body"),
};

// ------------------------------------------------------- manual frame pump
let pending = [];
global.requestAnimationFrame = (fn) => pending.push(fn);
function pump(frames) {
  for (let i = 0; i < frames; i++) {
    const due = pending;
    pending = [];
    due.forEach((fn) => fn(Date.now()));
  }
}

global.window = {
  CORTEX: { token: "T", root: ROOT, autoExpand: { depth: 0, budget: 0 } },
  innerWidth: 1600, innerHeight: 900, devicePixelRatio: 2,
  addEventListener() {},
  requestAnimationFrame: global.requestAnimationFrame,
};
global.location = { origin: "http://127.0.0.1:9999" };
global.navigator = { clipboard: { writeText: async () => {} } };
global.Event = class { constructor(t) { this.type = t; } };
global.MouseEvent = class {
  constructor(t, o = {}) { Object.assign(this, o); this.type = t; }
};
global.WheelEvent = global.MouseEvent;
global.KeyboardEvent = global.MouseEvent;

global.fetch = async (url) => {
  const u = String(url);
  const body =
      u.includes("/api/root") ? fsnode(ROOT, true, DIRS + ROOT_FILES)
    : u.includes("/api/children") ? childrenOf(decodeURIComponent((u.split("path=")[1] || "")))
    : u.includes("/api/editors") ? { editors: [{ id: "nvim", label: "Neovim", gui: false }] }
    : u.includes("/api/links") ? { ready: true, edges: [], notes: 0, sources: 0, elapsed: 0 }
    : u.includes("/api/search") ? { results: [], count: 0 }
    : {};
  return { ok: true, json: async () => body };
};

// ------------------------------------------------------------------- run
let failures = 0;
function check(name, ok, detail) {
  if (ok) { console.log(`ok    ${name}`); return; }
  failures++;
  console.log(`FAIL  ${name}${detail === undefined ? "" : `\n      ${detail}`}`);
}

(async () => {
  new Function(fs.readFileSync(SRC, "utf8"))();   // app.js is an IIFE

  await new Promise((r) => setTimeout(r, 60));    // let boot's awaits resolve
  pump(1);
  await new Promise((r) => setTimeout(r, 60));

  const D = () => global.window.CORTEX.debug();
  check("app.js exposes its debug hook", typeof D === "function");

  const built = D();
  check("the root expanded into nodes", built.nodes > 20, `nodes=${built.nodes}`);
  check("tree links were created", built.links > 20, `links=${built.links}`);

  const samples = [];
  for (let i = 0; i < 12; i++) { pump(50); samples.push(D()); }
  const last = samples[samples.length - 1];
  const idleDraws = last.draws - samples[samples.length - 4].draws;

  console.log(`      alpha: ${samples.map((s) => s.alpha.toFixed(3)).join(" -> ")}`);
  console.log(`      frames=${last.frames} draws=${last.draws}`);

  check("the layout cools to a freeze", last.frozen, `alpha=${last.alpha}`);
  check("a frozen layout stops repainting", idleDraws === 0,
        `${idleDraws} draws in 150 idle frames`);
  check("it drew far fewer times than it ticked", last.draws < last.frames,
        `draws=${last.draws} frames=${last.frames}`);
  check("labels were laid out", last.labels > 0, `labels=${last.labels}`);

  const before = D().draws;
  document.getElementById("z-in").click();
  pump(3);
  check("interaction repaints a frozen graph", D().draws > before,
        `draws went ${before} -> ${D().draws}`);

  const zoomed = D();
  document.getElementById("btn-fit").click();
  pump(3);
  check("fit repaints too", D().draws > zoomed.draws);

  console.log(failures ? `\n${failures} failing` : `\nall render-loop tests pass`);
  process.exit(failures ? 1 : 0);
})();
