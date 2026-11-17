/* cortex — panes in a terminal tab, the way zellij has them.

   A terminal tab can be split into panes, side by side and above one
   another, with others floating over them; one can fill the tab, they can be
   resized, moved and renamed, what is typed can go to all of them at once,
   and what one has printed can be scrolled and searched. It is driven from
   the keyboard, in modes: ctrl-p and the keys that follow are about panes,
   ctrl-t about tabs, ctrl-n resizes, ctrl-h moves, ctrl-s scrolls, ctrl-o is
   the session, ctrl-g locks all of that away so every key goes to the
   program -- and a bar along the bottom says, in whichever mode it is, what
   each key will do.

   The keys are zellij's, taken from its own default configuration (0.43),
   mode for mode and key for key, so that hands which know zellij need not
   learn anything. What zellij does with plugins has no counterpart here and
   says so when asked for.

   This file is the part with no page in it: which keys do what in which
   mode, and how a tab's panes are arranged -- a tree of splits -- with the
   operations on that tree. Everything here can be tested by itself. */
(() => {
"use strict";

// ---------------------------------------------------------------- the keys
/* How zellij names a key: "Ctrl p", "Alt n", "Alt Shift p", "Left", "h". */
function keyName(e) {
  const named = { ArrowLeft: "Left", ArrowRight: "Right", ArrowUp: "Up", ArrowDown: "Down",
                  Escape: "Esc", " ": "Space" };
  const k = named[e.key] || e.key;
  if (k === "Control" || k === "Alt" || k === "Shift" || k === "Meta") return "";
  const one = k.length === 1;
  if (e.ctrlKey && !e.altKey) return "Ctrl " + (one ? k.toLowerCase() : k);
  if (e.altKey && !e.ctrlKey) {
    return e.shiftKey && one && /[a-z]/i.test(k) ? "Alt Shift " + k.toLowerCase() : "Alt " + k;
  }
  return k;
}

const m = (name) => ["mode", name];
const to = (...actions) => [...actions, m("normal")];
const each = (keys, ...actions) => Object.fromEntries(keys.map((k) => [k, actions]));
const scrolling = {
  ...each(["j", "Down"], ["scroll", "down"]), ...each(["k", "Up"], ["scroll", "up"]),
  ...each(["Ctrl f", "PageDown", "Right", "l"], ["scroll", "page-down"]),
  ...each(["Ctrl b", "PageUp", "Left", "h"], ["scroll", "page-up"]),
  d: [["scroll", "half-down"]], u: [["scroll", "half-up"]],
  "Ctrl c": to(["scroll", "bottom"]),
};

/* Each mode's own keys, as zellij's default configuration has them. */
const MODES = {
  normal: {},
  locked: { "Ctrl g": [m("normal")] },
  resize: {
    "Ctrl n": [m("normal")],
    ...each(["h", "Left"], ["resize", "increase", "left"]),
    ...each(["j", "Down"], ["resize", "increase", "down"]),
    ...each(["k", "Up"], ["resize", "increase", "up"]),
    ...each(["l", "Right"], ["resize", "increase", "right"]),
    H: [["resize", "decrease", "left"]], J: [["resize", "decrease", "down"]],
    K: [["resize", "decrease", "up"]], L: [["resize", "decrease", "right"]],
    ...each(["=", "+"], ["resize", "increase"]), "-": [["resize", "decrease"]],
  },
  pane: {
    "Ctrl p": [m("normal")],
    ...each(["h", "Left"], ["focus", "left"]), ...each(["l", "Right"], ["focus", "right"]),
    ...each(["j", "Down"], ["focus", "down"]), ...each(["k", "Up"], ["focus", "up"]),
    p: [["switch-focus"]],
    n: to(["new"]), d: to(["new", "down"]), r: to(["new", "right"]), s: to(["new", "stacked"]),
    x: to(["close"]), f: to(["fullscreen"]), z: to(["frames"]), w: to(["floating"]),
    e: to(["embed"]), c: [m("renamepane")], i: to(["pin"]),
  },
  move: {
    "Ctrl h": [m("normal")],
    ...each(["n", "Tab"], ["move"]), p: [["move-back"]],
    ...each(["h", "Left"], ["move", "left"]), ...each(["j", "Down"], ["move", "down"]),
    ...each(["k", "Up"], ["move", "up"]), ...each(["l", "Right"], ["move", "right"]),
  },
  tab: {
    "Ctrl t": [m("normal")],
    r: [m("renametab")],
    ...each(["h", "Left", "Up", "k"], ["tab", "prev"]),
    ...each(["l", "Right", "Down", "j"], ["tab", "next"]),
    n: to(["tab", "new"]), x: to(["tab", "close"]), s: to(["sync"]),
    b: to(["break"]), "]": to(["break", "right"]), "[": to(["break", "left"]),
    ...Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [String(n), to(["goto", n])])),
    Tab: [["toggle-tab"]],
  },
  scroll: {
    "Ctrl s": [m("normal")], e: to(["edit-scrollback"]), s: [m("entersearch")], ...scrolling,
  },
  search: {
    "Ctrl s": [m("normal")], ...scrolling,
    n: [["search", "down"]], p: [["search", "up"]],
    c: [["search-option", "case"]], w: [["search-option", "wrap"]],
    o: [["search-option", "word"]],
  },
  entersearch: { ...each(["Ctrl c", "Esc"], m("scroll")), Enter: [["search-go"], m("search")] },
  renametab: { "Ctrl c": [m("normal")], Esc: [["undo-rename"], m("tab")] },
  renamepane: { "Ctrl c": [m("normal")], Esc: [["undo-rename"], m("pane")] },
  session: {
    "Ctrl o": [m("normal")], "Ctrl s": [m("scroll")], d: [["detach"]],
    w: to(["sessions"]), c: to(["plugin", "configuration"]),
    p: to(["plugin", "the plugin manager"]), a: to(["plugin", "about"]),
    s: to(["plugin", "sharing"]),
  },
  tmux: {
    "[": [m("scroll")], "Ctrl b": to(["write", "\x02"]),
    "\"": to(["new", "down"]), "%": to(["new", "right"]), z: to(["fullscreen"]),
    c: to(["tab", "new"]), ",": [m("renametab")],
    p: to(["tab", "prev"]), n: to(["tab", "next"]),
    ...each(["Left", "h"], ["focus", "left"], m("normal")),
    ...each(["Right", "l"], ["focus", "right"], m("normal")),
    ...each(["Down", "j"], ["focus", "down"], m("normal")),
    ...each(["Up", "k"], ["focus", "up"], m("normal")),
    o: [["switch-focus"]], d: [["detach"]], Space: [["layout", "next"]], x: to(["close"]),
  },
};

/* Keys that hold in every mode but the ones named: zellij's shared_except. */
const SHARED = [
  [["locked"], {
    "Ctrl g": [m("locked")], "Ctrl q": [["quit"]],
    "Alt f": [["floating"]], "Alt n": [["new"]],
    "Alt i": [["move-tab", "left"]], "Alt o": [["move-tab", "right"]],
    ...each(["Alt h", "Alt Left"], ["focus-or-tab", "left"]),
    ...each(["Alt l", "Alt Right"], ["focus-or-tab", "right"]),
    ...each(["Alt j", "Alt Down"], ["focus", "down"]),
    ...each(["Alt k", "Alt Up"], ["focus", "up"]),
    ...each(["Alt =", "Alt +"], ["resize", "increase"]), "Alt -": [["resize", "decrease"]],
    "Alt [": [["layout", "prev"]], "Alt ]": [["layout", "next"]],
    "Alt p": [["group"]], "Alt Shift p": [["group"]],
  }],
  [["normal", "locked"], each(["Enter", "Esc"], m("normal"))],
  [["pane", "locked"], { "Ctrl p": [m("pane")] }],
  [["resize", "locked"], { "Ctrl n": [m("resize")] }],
  [["scroll", "locked"], { "Ctrl s": [m("scroll")] }],
  [["session", "locked"], { "Ctrl o": [m("session")] }],
  [["tab", "locked"], { "Ctrl t": [m("tab")] }],
  [["move", "locked"], { "Ctrl h": [m("move")] }],
  [["tmux", "locked"], { "Ctrl b": [m("tmux")] }],
];

const TYPING = new Set(["entersearch", "renametab", "renamepane"]);

/* What a key does in a mode: a list of actions; an empty list when the key
   is the multiplexer's to swallow; null when it is the program's to have. */
function actions(mode, key, ch) {
  if (!key) return null;
  const own = MODES[mode] && MODES[mode][key];
  if (own) return own;
  if (TYPING.has(mode)) {
    // a name, or something to search for, is being typed
    if (key === "Enter") return mode === "entersearch" ? own : [["commit"], m("normal")];
    if (key === "Backspace") return [["erase"]];
    if (ch && ch.length === 1 && !key.startsWith("Ctrl ") && !key.startsWith("Alt ")) {
      return [["type", ch]];
    }
  }
  for (const [except, binds] of SHARED) {
    if (!except.includes(mode) && binds[key]) return binds[key];
  }
  if (mode === "normal") return null;
  return [];                       // in a mode, or locked out of them: not the program's...
}
/* ...except that locked is exactly the mode where every key but one is. */
const forProgram = (mode, key) => mode === "locked" && key !== "Ctrl g";

/* The bar along the bottom: the modes, then what the keys do in this one. */
const RIBBON = [["g", "LOCK", "locked"], ["p", "PANE", "pane"], ["t", "TAB", "tab"],
                ["n", "RESIZE", "resize"], ["h", "MOVE", "move"], ["s", "SEARCH", "scroll"],
                ["o", "SESSION", "session"], ["q", "QUIT", null]];
const HINTS = {
  normal: [["Alt n", "new pane"], ["Alt ←↓↑→", "change focus"], ["Alt f", "floating"],
           ["Alt + −", "resize"], ["Alt [ ]", "layout"]],
  locked: [["Ctrl g", "unlock — until then every key goes to the program"]],
  pane: [["←↓↑→", "move focus"], ["n", "new"], ["d", "down"], ["r", "right"], ["x", "close"],
         ["f", "fullscreen"], ["z", "frames"], ["w", "floating"], ["e", "embed"],
         ["c", "rename"], ["p", "next"], ["enter", "select"]],
  tab: [["←→", "move"], ["n", "new"], ["x", "close"], ["r", "rename"], ["s", "sync"],
        ["b", "break pane out"], ["1-9", "go to"], ["tab", "last"], ["enter", "select"]],
  resize: [["←↓↑→", "grow that way"], ["HJKL", "shrink"], ["+ −", "all round"],
           ["enter", "done"]],
  move: [["←↓↑→", "move the pane"], ["n / tab", "to the next place"], ["p", "back"],
         ["enter", "done"]],
  scroll: [["↓↑", "a line"], ["d u", "half a page"], ["←→ / pgup pgdn", "a page"],
           ["s", "search"], ["e", "open in your editor"], ["ctrl c", "to the end"],
           ["enter", "done"]],
  entersearch: [["type", "what to look for"], ["enter", "search"], ["esc", "back"]],
  search: [["n", "next below"], ["p", "next above"], ["c", "case"], ["w", "wrap"],
           ["o", "whole word"], ["enter", "done"]],
  renametab: [["type", "a name for this tab"], ["enter", "keep it"], ["esc", "leave it as it was"]],
  renamepane: [["type", "a name for this pane"], ["enter", "keep it"], ["esc", "leave it as it was"]],
  session: [["d", "detach: put the pane away, everything keeps running"], ["w", "the list of tabs"],
            ["enter", "back"]],
  tmux: [["\"", "split down"], ["%", "split right"], ["z", "fullscreen"], ["c", "new tab"],
         ["n p", "next / previous tab"], ["←↓↑→", "focus"], ["x", "close"], ["[", "scroll"]],
};

// ------------------------------------------------------- how panes are laid
/* A tab's tiled panes are a tree: a leaf is a pane, a split is some panes or
   splits side by side ("row") or one above another ("col"), each with its
   share of the room. Every operation returns a new tree, or changes sizes in
   place and says whether it did. */
const leaf = (id) => ({ t: "leaf", id });
const ids = (n) => (!n ? [] : n.t === "leaf" ? [n.id] : n.kids.flatMap(ids));

/* The way down to a pane: [{split, i}, ...] from the root, [] for a tree
   that is only that pane, null when it is not in the tree. */
function trail(n, id) {
  if (!n) return null;
  if (n.t === "leaf") return n.id === id ? [] : null;
  for (let i = 0; i < n.kids.length; i++) {
    const below = trail(n.kids[i], id);
    if (below) return [{ split: n, i }, ...below];
  }
  return null;
}

const even = (n) => new Array(n).fill(1 / n);
const copy = (n) => (n ? JSON.parse(JSON.stringify(n)) : n);

/* Put pane `id` beside pane `at`: to its right ("row") or below it ("col").
   The new pane takes half of the old one's room. */
function split(root, at, id, dir) {
  root = copy(root);
  if (!root) return leaf(id);
  const way = trail(root, at);
  if (!way) {                                   // nothing to put it beside: at the end
    if (root.t === "split" && root.dir === dir) {
      root.kids.push(leaf(id));
      root.sizes = even(root.kids.length);
      return root;
    }
    return { t: "split", dir, kids: [root, leaf(id)], sizes: [0.5, 0.5] };
  }
  if (!way.length) return { t: "split", dir, kids: [root, leaf(id)], sizes: [0.5, 0.5] };
  const { split: parent, i } = way[way.length - 1];
  if (parent.dir === dir) {
    const half = parent.sizes[i] / 2;
    parent.sizes.splice(i, 1, half, half);
    parent.kids.splice(i + 1, 0, leaf(id));
  } else {
    parent.kids[i] = { t: "split", dir, kids: [parent.kids[i], leaf(id)], sizes: [0.5, 0.5] };
  }
  return root;
}

/* Take a pane out. Its room goes to what is left beside it, and a split
   left with one thing in it is that thing. */
function remove(root, id) {
  root = copy(root);
  const way = trail(root, id);
  if (!way) return root;
  if (!way.length) return null;
  const { split: parent, i } = way[way.length - 1];
  parent.kids.splice(i, 1);
  parent.sizes.splice(i, 1);
  const total = parent.sizes.reduce((a, b) => a + b, 0) || 1;
  parent.sizes = parent.sizes.map((s) => s / total);
  if (parent.kids.length > 1) return root;
  const only = parent.kids[0];
  if (way.length === 1) return only;
  const above = way[way.length - 2];
  above.split.kids[above.i] = only;
  return root;
}

const AXIS = { left: ["row", -1], right: ["row", 1], up: ["col", -1], down: ["col", 1] };

/* The pane next to this one in a direction, or null at the edge. Coming into
   a group of panes from the side, the nearest one is taken; from above or
   below a group that is side by side, the first. */
function neighbour(root, id, direction) {
  const way = trail(root, id);
  if (!way) return null;
  const [axis, step] = AXIS[direction];
  for (let k = way.length - 1; k >= 0; k--) {
    const { split: s, i } = way[k];
    if (s.dir !== axis || !s.kids[i + step]) continue;
    let n = s.kids[i + step];
    while (n.t === "split") n = n.dir === axis && step < 0 ? n.kids[n.kids.length - 1] : n.kids[0];
    return n.id;
  }
  return null;
}

const SMALLEST = 0.08, STEP = 0.05;

/* Grow (or shrink) a pane toward a direction, taking the room from the pane
   on that side -- or, at an edge, giving it to the pane on the other. */
function resize(root, id, direction, grow = true) {
  const way = trail(root, id);
  if (!way) return false;
  const [axis, step] = AXIS[direction];
  for (let k = way.length - 1; k >= 0; k--) {
    const { split: s, i } = way[k];
    if (s.dir !== axis || s.kids.length < 2) continue;
    let other = i + step, amount = grow ? STEP : -STEP;
    if (!s.kids[other]) { other = i - step; amount = -amount; }      // at the edge
    if (!s.kids[other]) continue;
    if (s.sizes[i] + amount < SMALLEST || s.sizes[other] - amount < SMALLEST) return false;
    s.sizes[i] += amount;
    s.sizes[other] -= amount;
    return true;
  }
  return false;
}

/* Grow or shrink a pane every way it can, against everything beside it. */
function resizeAll(root, id, grow = true) {
  const way = trail(root, id);
  if (!way) return false;
  let changed = false;
  for (const { split: s, i } of way) {
    const rest = s.kids.length - 1;
    const amount = grow ? STEP : -STEP;
    const each_ = amount / rest;
    if (s.sizes[i] + amount < SMALLEST || s.sizes[i] + amount > 1 - SMALLEST * rest
        || s.sizes.some((v, k) => k !== i && v - each_ < SMALLEST)) continue;
    s.sizes = s.sizes.map((v, k) => (k === i ? v + amount : v - each_));
    changed = true;
  }
  return changed;
}

/* Two panes change places. */
function swap(root, a, b) {
  root = copy(root);
  const walk = (n) => {
    if (n.t === "leaf") n.id = n.id === a ? b : n.id === b ? a : n.id;
    else n.kids.forEach(walk);
  };
  if (root) walk(root);
  return root;
}

/* The same panes, laid out one of the plain ways zellij swaps between. */
const ARRANGEMENTS = ["side by side", "stacked", "main and stack"];
function arrange(list, kind) {
  if (!list.length) return null;
  if (list.length === 1) return leaf(list[0]);
  const group = (dir, some) => ({ t: "split", dir, kids: some.map(leaf), sizes: even(some.length) });
  if (kind === 0) return group("row", list);
  if (kind === 1) return group("col", list);
  const rest = list.slice(1);
  return { t: "split", dir: "row", sizes: [0.6, 0.4],
           kids: [leaf(list[0]), rest.length === 1 ? leaf(rest[0]) : group("col", rest)] };
}

/* A tree someone else kept for us, made safe to use: only panes that still
   exist, each once, and shares that add up. Null when nothing is left. */
function sound(tree, known) {
  const seen = new Set();
  const walk = (n, depth) => {
    if (!n || typeof n !== "object" || depth > 12) return null;
    if (n.t === "leaf") {
      if (!known.includes(n.id) || seen.has(n.id)) return null;
      seen.add(n.id);
      return leaf(n.id);
    }
    if (n.t !== "split" || !Array.isArray(n.kids) || (n.dir !== "row" && n.dir !== "col")) return null;
    const kids = [], sizes = [];
    n.kids.forEach((k, i) => {
      const got = walk(k, depth + 1);
      if (!got) return;
      kids.push(got);
      const share = Array.isArray(n.sizes) ? Number(n.sizes[i]) : NaN;
      sizes.push(share > 0 && share <= 1 ? share : 1 / n.kids.length);
    });
    if (!kids.length) return null;
    if (kids.length === 1) return kids[0];
    const total = sizes.reduce((a, b) => a + b, 0);
    return { t: "split", dir: n.dir, kids, sizes: sizes.map((v) => v / total) };
  };
  return walk(tree, 0);
}

const api = { keyName, actions, forProgram, MODES, SHARED, RIBBON, HINTS, leaf, ids, trail,
              split, remove, neighbour, resize, resizeAll, swap, arrange, ARRANGEMENTS, sound };
if (typeof window !== "undefined") window.Mux = api;
if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
