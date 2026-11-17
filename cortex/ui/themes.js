/* cortex — themes.

   A theme is a handful of colours, and can be as few as two: the background
   and the text. Everything else the window paints -- the panes, the lines
   between them, the dimmer text, what is selected, what warns, the graph's
   nodes and links, the terminal's sixteen colours, the code in the reader,
   the green and red of a diff -- is worked out from those, unless the theme
   says otherwise. So one theme changes all of it together, and a theme made
   for a terminal (which is sixteen colours, a background and a foreground)
   is already a whole theme here.

   resolve() turns a theme into two things: the values of the stylesheet's
   variables, and the colours the graph is drawn with, which is a canvas and
   reads no stylesheet. Nothing here touches the page, so all of it can be
   tested by itself. */
(() => {
"use strict";

// ------------------------------------------------------------- colour math
const clamp = (v) => Math.max(0, Math.min(255, Math.round(v)));

/* "#rgb" or "#rrggbb" as [r, g, b], or null for anything else. Only hex is
   taken: a theme is data from a file, and a colour that can be a url() or a
   var() is not just a colour. */
function parse(hex) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const x = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
  return [0, 2, 4].map((i) => parseInt(x.slice(i, i + 2), 16));
}
const hex = (c) => "#" + c.map((v) => clamp(v).toString(16).padStart(2, "0")).join("");
const norm = (h) => { const c = parse(h); return c ? hex(c) : null; };

/* `a`, moved `p` (0 to 1) of the way to `b`. */
function mix(a, b, p) {
  const x = parse(a), y = parse(b);
  return hex(x.map((v, i) => v + (y[i] - v) * p));
}
const alpha = (h, a) => norm(h) + clamp(a * 255).toString(16).padStart(2, "0");
const rgba = (h, a) => { const c = parse(h); return `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})`; };

function luminance(h) {
  const [r, g, b] = parse(h).map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
/* How well two colours can be told apart, 1 to 21; 4.5 is readable text. */
function contrast(a, b) {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
/* Text to put on top of a colour: whichever of near-white and near-black
   reads better there. */
const on = (h) => contrast(h, "#ffffff") >= contrast(h, "#10131a") ? "#ffffff" : "#10131a";

// ------------------------------------------------- what is not said, by base
const BASE = {
  dark: {
    ok: "#34d399", warn: "#f5a524", bad: "#f43f5e", info: "#67e8f9", mark: "#ffd166",
    nodes: { dir: "#f5a524", note: "#34d399", code: "#60a5fa", config: "#a78bfa",
             doc: "#f472b6", media: "#fb7185", archive: "#94a3b8", other: "#64748b",
             tag: "#c084fc" },
    syn: { com: "#4f5b75", kw: "#c792ea", str: "#95e1a4", num: "#ffb86c",
           type: "#ffcb6b", fn: "#82aaff", op: "#89ddff" },
    term: ["#1b2434", "#f2616f", "#3ecf8e", "#f5b041", "#5b9dff", "#c58af9", "#39c5cf",
           "#c9d1e0", "#5c6880", "#ff8b96", "#6ee7b0", "#ffd166", "#8fb8ff", "#d8b4fe",
           "#7fe3ea", "#f4f7fc"],
  },
  light: {
    ok: "#0f9d6b", warn: "#c27803", bad: "#dc2647", info: "#0e7490", mark: "#b7791f",
    nodes: { dir: "#c27803", note: "#0f9d6b", code: "#2563eb", config: "#7c3aed",
             doc: "#c0267e", media: "#d43d5b", archive: "#64748b", other: "#8a94a6",
             tag: "#9333ea" },
    syn: { com: "#8a94a6", kw: "#8b3fc7", str: "#2f7d3b", num: "#b45309",
           type: "#9a6700", fn: "#1d5fd1", op: "#0e7490" },
    term: ["#1c2333", "#c7243a", "#1a7f4b", "#9a6700", "#1d5fd1", "#8b3fc7", "#0e7490",
           "#4b5563", "#8996ad", "#e0445c", "#1f9d5c", "#b7791f", "#2f7bf0", "#a855f7",
           "#0891b2", "#111827"],
  },
};

/* The themes that come with cortex. Midnight is the look cortex always had,
   and says more than it needs to so that it stays exactly that look. */
const BUILT_IN = [
  { id: "midnight", name: "Midnight", base: "dark",
    bg: "#070910", fg: "#e6ebf4", accent: "#4c8dff",
    set: { pane: "#0a0e17", "bg-2": "#0d1320", hover: "#141d2f", press: "#1c2740",
           line: "#1b2434", "line-2": "#2a374f", "fg-ghost": "#3a4560",
           "fg-faint": "#5c6880", "fg-dim": "#8996ad", "fg-soft": "#d3dbe9",
           "fg-strong": "#f4f7fc", "accent-bg": "#12203a", "accent-bg-2": "#173055",
           "accent-line": "#2c4a86", "accent-line-2": "#3d63ad", "accent-fg": "#bcd6ff",
           "accent-fg-2": "#dbe7ff", link: "#7db1ff" },
    paint: { hollow: "#0b1120", centre: "#0b1120", link: "#1e293b" } },
  /* Black that is off: on an OLED screen a black pixel is not lit at all.
     So the panes are black too, told apart by a line, and nothing glows. */
  { id: "oled", name: "OLED", base: "dark", glow: false,
    bg: "#000000", fg: "#e9eaee", accent: "#5b9dff",
    set: { pane: "#000000", "bg-2": "#0b0b0d", hover: "#131316", press: "#1d1d21",
           line: "#1a1a1e", "line-2": "#2b2b31" },
    paint: { hollow: "#000000", centre: "#000000" } },
  { id: "graphite", name: "Graphite", base: "dark",
    bg: "#16171a", fg: "#d9dbe1", accent: "#6aa5ff", tint: "#c9ced9" },
  { id: "aurora", name: "Aurora", base: "dark",
    bg: "#110e1d", fg: "#e9e2f8", accent: "#a78bfa", tint: "#b9a8f0",
    nodes: { code: "#7dd3fc", config: "#c4b5fd" } },
  { id: "ember", name: "Ember", base: "dark",
    bg: "#14100c", fg: "#f0e7dc", accent: "#f59e42", tint: "#e8c9a0",
    nodes: { code: "#7fb4ff" } },
  { id: "paper", name: "Paper", base: "light", glow: false,
    bg: "#f7f8fa", fg: "#1c2333", accent: "#2563eb" },
  { id: "sand", name: "Sand", base: "light", glow: false,
    bg: "#f6f1e7", fg: "#33291c", accent: "#b4541a", tint: "#6b5a3e" },
];

const COLOUR_KEYS = ["bg", "fg", "accent", "tint", "ok", "warn", "bad", "info", "mark"];
const NODE_KEYS = Object.keys(BASE.dark.nodes);
const SYN_KEYS = Object.keys(BASE.dark.syn);

/* A theme from a file, made safe to use -- or the reason it cannot be.
   Returns {theme} or {error}. Colours are hex and nothing else; the two a
   theme must have are the background and the text, and the text has to be
   readable on the background. */
function check(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { error: "not a theme" };
  const bg = norm(raw.bg), fg = norm(raw.fg);
  if (!bg || !fg) return { error: "a theme needs a background (bg) and a text colour (fg), as #hex" };
  if (contrast(bg, fg) < 4.5) {
    return { error: "the text would be hard to read on that background" };
  }
  const t = { id: String(raw.id || raw.name || "theme").slice(0, 80),
              name: String(raw.name || raw.id || "Theme").slice(0, 80),
              base: raw.base === "light" || raw.base === "dark" ? raw.base
                : luminance(bg) > 0.4 ? "light" : "dark",
              bg, fg };
  if (raw.source) t.source = String(raw.source).slice(0, 40);
  if (raw.glow === false) t.glow = false;
  for (const k of COLOUR_KEYS.slice(2)) if (norm(raw[k])) t[k] = norm(raw[k]);
  const group = (keys, given) => {
    const out = {};
    if (given && typeof given === "object") {
      for (const k of keys) if (norm(given[k])) out[k] = norm(given[k]);
    }
    return Object.keys(out).length ? out : null;
  };
  const nodes = group(NODE_KEYS, raw.nodes), syn = group(SYN_KEYS, raw.syn);
  if (nodes) t.nodes = nodes;
  if (syn) t.syn = syn;
  if (Array.isArray(raw.term) && raw.term.length === 16 && raw.term.every(norm)) {
    t.term = raw.term.map(norm);
  }
  if (raw.set && typeof raw.set === "object") {
    const set = {};
    for (const [k, v] of Object.entries(raw.set)) {
      if (/^[a-z0-9-]{1,24}$/.test(k) && norm(v)) set[k] = norm(v);
    }
    if (Object.keys(set).length) t.set = set;
  }
  return { theme: t };
}

/* Everything a theme comes to. `accent` overrides the theme's own, which is
   how one folder is given a colour of its own without a theme of its own. */
function resolve(theme, accent) {
  const base = BASE[theme.base === "light" ? "light" : "dark"];
  const dark = theme.base !== "light";
  const bg = norm(theme.bg), fg = norm(theme.fg);
  const ac = norm(accent) || norm(theme.accent) || (dark ? "#4c8dff" : "#2563eb");
  const tint = norm(theme.tint) || fg;                  // what the surfaces lean toward
  const term = theme.term || base.term;
  const pick = (k) => norm(theme[k]) || (theme.term ? fromTerm(k) : null) || base[k];
  // a terminal's palette says which red is red and which green is green
  const fromTerm = (k) => ({ ok: term[2], warn: term[3], bad: term[1], info: term[6],
                             mark: term[11] })[k];
  const ok = pick("ok"), warn = pick("warn"), bad = pick("bad"), info = pick("info");
  const mark = pick("mark");
  const termNodes = theme.term ? { dir: term[3], note: term[2], code: term[4],
    config: term[5], doc: term[13], media: term[9], archive: term[8],
    other: mix(term[8], bg, 0.25), tag: term[13] } : {};
  const termSyn = theme.term ? { com: mix(bg, fg, 0.42), kw: term[5], str: term[2],
    num: term[3], type: term[11], fn: term[4], op: term[6] } : {};
  const nodes = { ...base.nodes, ...termNodes, ...(theme.nodes || {}) };
  const syn = { ...base.syn, ...termSyn, ...(theme.syn || {}) };

  const v = {
    bg, fg, accent: ac,
    pane: mix(bg, tint, 0.022), "bg-2": mix(bg, tint, 0.045), hover: mix(bg, tint, 0.075),
    press: mix(bg, tint, 0.115), line: mix(bg, tint, 0.105), "line-2": mix(bg, tint, 0.19),
    // on a light page dim text has to be darker to be read at all
    "fg-ghost": mix(bg, fg, dark ? 0.28 : 0.34), "fg-faint": mix(bg, fg, dark ? 0.44 : 0.52),
    "fg-dim": mix(bg, fg, dark ? 0.64 : 0.74),
    "fg-soft": mix(bg, fg, 0.9), "fg-strong": mix(fg, dark ? "#ffffff" : "#000000", 0.5),
    "accent-2": mix(ac, dark ? "#000000" : "#000000", 0.16), "on-accent": on(ac),
    "accent-bg": mix(bg, ac, 0.18), "accent-bg-2": mix(bg, ac, 0.28),
    "accent-line": mix(bg, ac, 0.48), "accent-line-2": mix(bg, ac, 0.66),
    "accent-fg": mix(ac, fg, dark ? 0.7 : 0.45), "accent-fg-2": mix(ac, fg, dark ? 0.86 : 0.7),
    link: dark ? mix(ac, fg, 0.3) : mix(ac, fg, 0.12), select: alpha(ac, dark ? 0.5 : 0.3),
    warn, "warn-2": mix(warn, "#000000", 0.12), "on-warn": on(warn),
    "warn-bg": mix(bg, warn, 0.11), "warn-line": mix(bg, warn, 0.4),
    "warn-fg": mix(warn, fg, dark ? 0.55 : 0.4), mark,
    bad, "bad-2": mix(bad, "#000000", 0.18), "on-bad": on(bad),
    "bad-bg": mix(bg, bad, 0.13), "bad-line": mix(bg, bad, 0.42),
    "bad-fg": mix(bad, fg, dark ? 0.55 : 0.35),
    ok, "ok-bg": mix(bg, ok, 0.14), "ok-line": mix(bg, ok, 0.5),
    "ok-fg": mix(ok, fg, dark ? 0.5 : 0.4),
    info, "info-fg": mix(info, fg, dark ? 0.45 : 0.3),
    "t-fg": dark ? mix(bg, fg, 0.92) : fg, "t-bg": bg,
    glass: null, scrim: null, shadow: dark ? "#000000" : "#1c233355",
  };
  for (const [k, c] of Object.entries(nodes)) v[k] = c;
  for (const [k, c] of Object.entries(syn)) v["syn-" + k] = c;
  term.forEach((c, i) => { v["t" + i] = c; });
  // what the theme set outright wins over what was worked out
  for (const [k, c] of Object.entries(theme.set || {})) if (k in v) v[k] = c;
  if (norm(accent)) {
    // ...except where a folder's own accent has been asked for
    Object.assign(v, { accent: ac, "accent-2": mix(ac, "#000000", 0.16), "on-accent": on(ac),
      "accent-bg": mix(bg, ac, 0.18), "accent-bg-2": mix(bg, ac, 0.28),
      "accent-line": mix(bg, ac, 0.48), "accent-line-2": mix(bg, ac, 0.66),
      "accent-fg": mix(ac, fg, dark ? 0.7 : 0.45), "accent-fg-2": mix(ac, fg, dark ? 0.86 : 0.7),
      link: dark ? mix(ac, fg, 0.3) : mix(ac, fg, 0.12), select: alpha(ac, dark ? 0.5 : 0.3) });
  }
  v.glass = rgba(v.pane, 0.86);
  v.scrim = alpha(mix(bg, "#000000", dark ? 0.4 : 0.55), dark ? 0.8 : 0.35);

  const p = theme.paint || {};
  const paint = {
    bg: v.bg, centre: norm(p.centre) || mix(bg, ac, 0.045), hollow: norm(p.hollow) || v.pane,
    link: norm(p.link) || mix(bg, tint, 0.13), linkLit: alpha(v["accent-fg"], 0.8),
    ring: v["fg-strong"], mark, onMark: on(mark), outline: bg, labelBg: alpha(bg, 0.8),
    label: v["fg-dim"], labelStrong: v["fg-soft"], labelActive: v["fg-strong"],
    ok, warn, bad, info, accent: ac, untracked: v["fg-dim"], moved: nodes.code,
    nodes: { ...nodes, ghost: mix(bg, fg, 0.6) },
    glow: theme.glow !== false,
  };
  const vars = {};
  for (const [k, c] of Object.entries(v)) vars["--" + k] = c;
  return { id: theme.id, name: theme.name, base: dark ? "dark" : "light", vars, paint };
}

/* The few colours that say what a theme is like, for the list to show. */
function swatches(theme) {
  const r = resolve(theme);
  return [r.vars["--bg"], r.vars["--pane"], r.vars["--line-2"], r.vars["--fg"],
          r.vars["--accent"], r.paint.nodes.note, r.paint.nodes.dir, r.paint.nodes.code,
          r.paint.nodes.config, r.paint.nodes.doc, r.vars["--bad"]];
}

const find = (id, more = []) =>
  BUILT_IN.find((t) => t.id === id) || more.find((t) => t.id === id) || null;

const api = { BUILT_IN, resolve, check, swatches, find, mix, alpha, rgba, contrast, on, norm };
if (typeof window !== "undefined") window.Themes = api;
if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
