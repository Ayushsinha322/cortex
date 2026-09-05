/* A stub DOM for driving the real cortex/ui/app.js under node.
 *
 * Why a stub and not a browser: Chrome pauses requestAnimationFrame in
 * background tabs, so "has the layout stopped repainting?" cannot be answered
 * from an automated tab -- it reports a frozen canvas whether the code is
 * right or not. Here frames are pumped by hand, so the answer is real. The
 * same stub then serves every other headless UI test for free.
 *
 * Not a test itself. Required by render-loop.test.js and connections.test.js.
 */

const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "cortex", "ui", "app.js");

// ----------------------------------------------------------- stub elements
function makeCtx2d(stageRef) {
  return new Proxy({}, {
    get(_t, prop) {
      if (prop === "measureText") return (s) => ({ width: String(s).length * 6 });
      if (prop === "createRadialGradient" || prop === "createLinearGradient") {
        return () => ({ addColorStop() {} });
      }
      if (prop === "canvas") return stageRef();
      return () => {};
    },
    set() { return true; },
  });
}

function selectorMatches(node, sel) {
  if (sel.startsWith(".")) return node.classList.contains(sel.slice(1));
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  return node.tagName === sel.toUpperCase();
}

function descend(node, sel, out, first) {
  for (const child of node.children) {
    if (selectorMatches(child, sel)) {
      out.push(child);
      if (first) return out;
    }
    descend(child, sel, out, first);
    if (first && out.length) return out;
  }
  return out;
}

/* Anchors with a download attribute, so a test can see what was saved. */
const downloads = [];

function element(id, ctx2d) {
  const el = {
    id, tagName: "DIV", value: "",
    hidden: false, title: "", src: "", controls: false,
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
    click(init) {
      if (el.download) downloads.push({ name: el.download, href: el.href });
      this.dispatchEvent(Object.assign(
        { type: "click", target: this, currentTarget: this,
          stopPropagation() {}, shiftKey: false }, init || {}));
    },
    remove() {},
    toBlob(cb, type) { cb(new global.Blob(["stub-canvas-bytes"], { type })); },
    getContext: () => ctx2d,
    appendChild(c) { this.children.push(c); return c; },
    append(...c) { this.children.push(...c); },
    replaceChildren() { this.children = []; },
    closest: () => null,
    /* Enough of a selector engine for the class and tag lookups app.js does. */
    querySelectorAll(sel) { return descend(el, sel, []); },
    querySelector(sel) { return descend(el, sel, [], true)[0] || null; },
    focus() {}, blur() {}, select() {}, scrollTo() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1600, height: 900 }),
  };

  /* textContent and innerHTML replace everything under the element. Emulating
     that matters: app.js clears a container with `el.textContent = ""` before
     refilling it, and a stub that kept the old children would let a stale
     render pass for a fresh one. */
  let text = "", html = "";
  Object.defineProperty(el, "textContent", {
    get: () => text,
    set(v) { text = String(v); html = ""; el.children = []; },
    enumerable: true,
  });
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set(v) { html = String(v); text = ""; el.children = []; },
    enumerable: true,
  });

  /* className and classList are two views of one set of classes. app.js sets
     the class in bulk on elements it creates and then asks classList about
     them, so a stub where the two drift apart would answer wrongly. */
  Object.defineProperty(el, "className", {
    get: () => [...el.classList._s].join(" "),
    set(v) {
      el.classList._s = new Set(String(v).split(/\s+/).filter(Boolean));
    },
    enumerable: true,
  });
  return el;
}

// -------------------------------------------------------------- the harness
/* opts:
     root      absolute path the graph is rooted at
     children  (path) -> node[]        what /api/children answers
     rootNode  ()     -> node          what /api/root answers
     links     ()     -> snapshot      what /api/links answers
     editors   ()     -> entry[]       what /api/editors answers            */
function harness(opts = {}) {
  const ROOT = opts.root || "/home/u/proj";

  let stage;
  const ctx2d = makeCtx2d(() => stage);
  const byId = new Map();
  stage = element("stage", ctx2d);
  byId.set("stage", stage);

  global.document = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, element(id, ctx2d));
      return byId.get(id);
    },
    createElement(tag) {
      const e = element("new-" + tag, ctx2d);
      e.tagName = String(tag).toUpperCase();
      return e;
    },
    createTextNode(text) {
      const e = element("text", ctx2d);
      e.tagName = "#text";
      e.textContent = String(text);
      return e;
    },
    addEventListener() {},
    body: element("body", ctx2d),
  };

  let pending = [];
  global.requestAnimationFrame = (fn) => pending.push(fn);
  const pump = (frames) => {
    for (let i = 0; i < frames; i++) {
      const due = pending;
      pending = [];
      due.forEach((fn) => fn(Date.now()));
    }
  };

  global.window = {
    CORTEX: Object.assign({
      token: "T", root: ROOT,
      autoExpand: opts.autoExpand || { depth: 0, budget: 0 },
    }, opts.config || {}),
    innerWidth: 1600, innerHeight: 900, devicePixelRatio: 2,
    addEventListener() {},
    requestAnimationFrame: global.requestAnimationFrame,
  };
  global.location = { origin: "http://127.0.0.1:9999" };
  global.Blob = class {
    constructor(parts, opts) { this.parts = parts; this.type = (opts || {}).type; }
    text() { return this.parts.join(""); }
  };
  // node has URL; it does not have the object-url half of it
  global.URL.createObjectURL = (blob) => {
    blobs.push(blob);
    return "blob:stub/" + blobs.length;
  };
  global.URL.revokeObjectURL = () => {};
  global.navigator = { clipboard: { writeText: async () => {} } };
  global.Event = class { constructor(t) { this.type = t; } };
  global.MouseEvent = class {
    constructor(t, o = {}) { Object.assign(this, o); this.type = t; }
  };
  global.WheelEvent = global.MouseEvent;
  global.KeyboardEvent = global.MouseEvent;

  /* Every request the app makes, so a test can assert what it asked for as
     well as what it did with the answer. */
  const sent = [];
  const blobs = [];       // everything handed to URL.createObjectURL
  const saved = [];       // every <a download> that was clicked

  const children = opts.children || (() => []);
  const rootNode = opts.rootNode || (() => null);
  const linksOf = opts.links || (() => ({ ready: true, edges: [], notes: 0,
                                          sources: 0, elapsed: 0 }));
  const editorsOf = opts.editors ||
    (() => [{ id: "nvim", label: "Neovim", gui: false, env: false }]);
  const grepOf = opts.grep ||
    (() => ({ hits: [], engine: "python", truncated: false }));
  const pulseOf = opts.pulse || (() => ({ changed: [], reindexed: false }));
  const gitOf = opts.git || (() => ({ repo: null, branch: null, states: {} }));
  const layoutOf = opts.layout || (() => ({ positions: {}, cam: null }));
  const searchOf = opts.search || (() => ({ results: [], count: 0 }));

  global.fetch = async (url, init) => {
    const u = String(url);
    sent.push({ url: u, init, body: init && init.body ? JSON.parse(init.body) : null });
    const q = decodeURIComponent((u.split("q=")[1] || "").split("&")[0]);
    const body =
        u.includes("/api/root") ? rootNode()
      : u.includes("/api/children")
          ? children(decodeURIComponent((u.split("path=")[1] || "").split("&")[0]))
      : u.includes("/api/editors") ? { editors: editorsOf() }
      : u.includes("/api/links") ? linksOf()
      : u.includes("/api/grep") ? grepOf(q)
      : u.includes("/api/pulse") ? pulseOf()
      : u.includes("/api/git") ? gitOf()
      : u.includes("/api/layout") ? layoutOf()
      : u.includes("/api/search") ? searchOf(q)
      : u.includes("/api/action") ? { ok: true, terminal: true }
      : {};
    return { ok: true, json: async () => body };
  };

  /* Run app.js, then let its boot-time awaits resolve and paint once. */
  async function boot() {
    new Function(fs.readFileSync(SRC, "utf8"))();     // app.js is an IIFE
    await new Promise((r) => setTimeout(r, 60));
    pump(1);
    await new Promise((r) => setTimeout(r, 60));
    return global.window.CORTEX.debug;
  }

  /* Type into the search box and let its debounce fire. */
  async function typeSearch(text) {
    const q = global.document.getElementById("q");
    q.value = text;
    q.dispatchEvent({ type: "input", target: q });
    await new Promise((r) => setTimeout(r, 380));
  }

  function key(el, name) {
    el.dispatchEvent({ type: "keydown", key: name, target: el,
                       preventDefault() {}, stopPropagation() {} });
  }

  return {
    ROOT, byId, pump, boot, sent, blobs, downloads, typeSearch, key,
    el: (id) => global.document.getElementById(id),
  };
}

/* A plain filesystem node, shaped the way /api/children answers. */
function fsnode(id, isDir, kids, root) {
  return {
    id, name: id.split("/").pop(), dir: isDir,
    group: isDir ? "dir" : (id.endsWith(".md") ? "note" : "code"),
    size: 4096, mtime: Date.now() / 1000 - 99999,
    parent: id === root ? null : id.slice(0, id.lastIndexOf("/")),
    ...(isDir ? { kids } : {}),
  };
}

/* Tiny assert helpers, shared so both suites report the same way. */
function reporter() {
  let failures = 0;
  const check = (name, ok, detail) => {
    if (ok) { console.log(`ok    ${name}`); return; }
    failures++;
    console.log(`FAIL  ${name}${detail === undefined ? "" : `\n      ${detail}`}`);
  };
  const done = (label) => {
    console.log(failures ? `\n${failures} failing` : `\nall ${label} tests pass`);
    process.exit(failures ? 1 : 0);
  };
  return { check, done };
}

module.exports = { harness, fsnode, reporter };
