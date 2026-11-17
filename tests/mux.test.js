/* Panes in a terminal tab, the way zellij has them: the keys and the layout
 * arithmetic (cortex/ui/mux.js, which needs no page), then the panes
 * themselves, driven through the real cortex/ui/app.js by pressing zellij's
 * keys in them.
 *
 * Where zellij is installed, the keys are also checked against zellij's own
 * default configuration, key for key.
 *
 * Run: node tests/mux.test.js   (or tests/run)
 */

const path = require("path");
const { spawnSync } = require("child_process");
const M = require(path.join(__dirname, "..", "cortex", "ui", "mux.js"));
const { harness, fsnode, reporter } = require("./harness");

const { check, done } = reporter();
const J = JSON.stringify;
const ev = (key, mods = {}) => ({ key, ctrlKey: false, altKey: false, shiftKey: false, ...mods });

// --- what a key is called -------------------------------------------------------
check("keys are named as zellij names them",
      M.keyName(ev("p", { ctrlKey: true })) === "Ctrl p" && M.keyName(ev("n", { altKey: true })) === "Alt n"
      && M.keyName(ev("ArrowLeft", { altKey: true })) === "Alt Left" && M.keyName(ev("ArrowDown")) === "Down"
      && M.keyName(ev("P", { altKey: true, shiftKey: true })) === "Alt Shift p"
      && M.keyName(ev("+", { altKey: true, shiftKey: true })) === "Alt +" && M.keyName(ev("H", { shiftKey: true })) === "H"
      && M.keyName(ev("Escape")) === "Esc" && M.keyName(ev("Control", { ctrlKey: true })) === "");

// --- which key does what, in which mode -------------------------------------------
check("in normal mode a plain key is the program's", M.actions("normal", "x") === null
      && M.actions("normal", "Enter") === null && M.actions("normal", "Ctrl c") === null);
check("and the mode keys are not", J(M.actions("normal", "Ctrl p")) === '[["mode","pane"]]'
      && J(M.actions("normal", "Ctrl t")) === '[["mode","tab"]]' && J(M.actions("normal", "Ctrl n")) === '[["mode","resize"]]'
      && J(M.actions("normal", "Ctrl h")) === '[["mode","move"]]' && J(M.actions("normal", "Ctrl s")) === '[["mode","scroll"]]'
      && J(M.actions("normal", "Ctrl o")) === '[["mode","session"]]' && J(M.actions("normal", "Ctrl g")) === '[["mode","locked"]]'
      && J(M.actions("normal", "Ctrl q")) === '[["quit"]]');
check("alt works from anywhere but locked", J(M.actions("normal", "Alt n")) === '[["new"]]'
      && J(M.actions("pane", "Alt f")) === '[["floating"]]' && J(M.actions("scroll", "Alt Left")) === '[["focus-or-tab","left"]]'
      && J(M.actions("resize", "Alt +")) === '[["resize","increase"]]');
check("pane mode", J(M.actions("pane", "d")) === '[["new","down"],["mode","normal"]]'
      && J(M.actions("pane", "r")) === '[["new","right"],["mode","normal"]]'
      && J(M.actions("pane", "x")) === '[["close"],["mode","normal"]]' && J(M.actions("pane", "h")) === '[["focus","left"]]'
      && J(M.actions("pane", "f")) === '[["fullscreen"],["mode","normal"]]' && J(M.actions("pane", "c")) === '[["mode","renamepane"]]'
      && J(M.actions("pane", "Ctrl p")) === '[["mode","normal"]]');
check("tab, resize and move modes", J(M.actions("tab", "n")) === '[["tab","new"],["mode","normal"]]'
      && J(M.actions("tab", "3")) === '[["goto",3],["mode","normal"]]' && J(M.actions("tab", "s")) === '[["sync"],["mode","normal"]]'
      && J(M.actions("resize", "h")) === '[["resize","increase","left"]]' && J(M.actions("resize", "L")) === '[["resize","decrease","right"]]'
      && J(M.actions("move", "Tab")) === '[["move"]]' && J(M.actions("move", "k")) === '[["move","up"]]');
check("scroll and search", J(M.actions("scroll", "d")) === '[["scroll","half-down"]]'
      && J(M.actions("scroll", "s")) === '[["mode","entersearch"]]' && J(M.actions("entersearch", "Enter")) === '[["search-go"],["mode","search"]]'
      && J(M.actions("search", "n")) === '[["search","down"]]' && J(M.actions("search", "o")) === '[["search-option","word"]]'
      && J(M.actions("scroll", "Ctrl c")) === '[["scroll","bottom"],["mode","normal"]]');
check("enter and esc leave any mode", ["pane", "tab", "resize", "move", "scroll", "session", "search"]
      .every((m) => J(M.actions(m, "Esc")) === '[["mode","normal"]]' && J(M.actions(m, "Enter")) === '[["mode","normal"]]'));
check("a key with no meaning in a mode is swallowed, not typed into the program",
      J(M.actions("pane", "q")) === "[]" && J(M.actions("resize", "x")) === "[]");
check("a name is typed, erased, kept or undone", J(M.actions("renamepane", "a", "a")) === '[["type","a"]]'
      && J(M.actions("renametab", "Backspace")) === '[["erase"]]' && J(M.actions("renametab", "Enter")) === '[["commit"],["mode","normal"]]'
      && J(M.actions("renamepane", "Esc")) === '[["undo-rename"],["mode","pane"]]');
check("locked gives every key but one to the program", M.forProgram("locked", "Ctrl p")
      && M.forProgram("locked", "Alt n") && !M.forProgram("locked", "Ctrl g") && !M.forProgram("normal", "Ctrl p")
      && J(M.actions("locked", "Ctrl g")) === '[["mode","normal"]]');
check("tmux's own keys, after ctrl-b", J(M.actions("normal", "Ctrl b")) === '[["mode","tmux"]]'
      && J(M.actions("tmux", "%")) === '[["new","right"],["mode","normal"]]' && J(M.actions("tmux", "\"")) === '[["new","down"],["mode","normal"]]');
check("every mode has something to say in the bar",
      Object.keys(M.MODES).every((m) => Array.isArray(M.HINTS[m]) && M.HINTS[m].length));

// --- against zellij itself, where it is installed --------------------------------------
const dumped = spawnSync("zellij", ["setup", "--dump-config"], { encoding: "utf8" });
if (dumped.status === 0 && /keybinds/.test(dumped.stdout)) {
  const lines = dumped.stdout.slice(dumped.stdout.indexOf("\nkeybinds")).split("\n");
  let mode = null, shared = null, missing = [], wrongMode = [], count = 0;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith("//")) continue;
    let m = /^([a-z]+) \{$/.exec(line);
    if (m && M.MODES[m[1]] !== undefined) { mode = m[1]; shared = null; continue; }
    m = /^shared_except((?: "[a-z]+")+) \{$/.exec(line);
    if (m) { shared = [...m[1].matchAll(/"([a-z]+)"/g)].map((x) => x[1]); mode = null; continue; }
    m = /^bind((?: "(?:[^"\\]|\\.)+")+) \{/.exec(line);
    if (!m || (!mode && !shared)) continue;
    const keys = [...m[1].matchAll(/"((?:[^"\\]|\\.)+)"/g)].map((x) => x[1].replace(/\\"/g, '"'));
    const target = /SwitchToMode "([A-Za-z]+)"/.exec(line);
    const modes = mode ? [mode] : Object.keys(M.MODES).filter((x) => !shared.includes(x));
    for (const md of modes) for (const key of keys) {
      count++;
      const acts = M.actions(md, key, key.length === 1 ? key : undefined);
      // a key a mode binds for itself outranks the shared one, in zellij as here
      const own = M.MODES[md] && M.MODES[md][key];
      if (!acts || (!acts.length && !own)) { missing.push(`${md}: ${key}`); continue; }
      if (target && mode) {
        const to = acts.filter((a) => a[0] === "mode").pop();
        if (!to || to[1] !== target[1].toLowerCase()) wrongMode.push(`${md}: ${key} -> ${target[1]}`);
      }
    }
  }
  check(`every key zellij binds by default is bound here (${count} checked)`,
        count > 200 && missing.length === 0, missing.slice(0, 12).join(" | "));
  check("and each takes you to the mode it does in zellij", wrongMode.length === 0,
        wrongMode.slice(0, 12).join(" | "));
} else {
  console.log("skip  zellij is not installed, so its own configuration is not compared");
}

// --- how panes are laid out ---------------------------------------------------------------
let t = M.split(null, null, "a", "row");
check("one pane is the whole tab", J(t) === '{"t":"leaf","id":"a"}');
t = M.split(t, "a", "b", "row");
t = M.split(t, "b", "c", "col");
check("a pane split to the right, then that one split down",
      J(M.ids(t)) === '["a","b","c"]' && t.dir === "row" && t.kids[1].dir === "col" && J(t.sizes) === "[0.5,0.5]");
check("focus moves to what is beside a pane, and stops at the edge",
      M.neighbour(t, "a", "right") === "b" && M.neighbour(t, "c", "left") === "a" && M.neighbour(t, "b", "down") === "c"
      && M.neighbour(t, "c", "up") === "b" && M.neighbour(t, "a", "left") === null && M.neighbour(t, "a", "up") === null);
let t2 = M.split(t, "a", "d", "row");
check("a split along the same line shares the old pane's room",
      J(M.ids(t2)) === '["a","d","b","c"]' && J(t2.sizes) === "[0.25,0.25,0.5]");
check("closing a pane gives its room back, and a split of one is just that one",
      J(M.remove(t, "c")) === '{"t":"split","dir":"row","kids":[{"t":"leaf","id":"a"},{"t":"leaf","id":"b"}],"sizes":[0.5,0.5]}'
      && J(M.remove(M.remove(t, "c"), "b")) === '{"t":"leaf","id":"a"}' && M.remove(M.leaf("a"), "a") === null);
let r = JSON.parse(J(t));
check("resizing takes room from the pane on that side", M.resize(r, "a", "right") && Math.abs(r.sizes[0] - 0.55) < 1e-9
      && Math.abs(r.sizes[1] - 0.45) < 1e-9);
check("and at an edge, gives it to the pane on the other", M.resize(r, "a", "left") && Math.abs(r.sizes[0] - 0.5) < 1e-9
      && !M.resize(M.leaf("a"), "a", "left"));
r = JSON.parse(J(t));
for (let i = 0; i < 30; i++) M.resize(r, "a", "right", false);
check("no pane is squeezed to nothing", r.sizes[0] >= 0.08 && r.sizes[1] <= 0.92);
r = JSON.parse(J(t));
check("growing every way grows it against everything beside it", M.resizeAll(r, "c", true)
      && r.sizes[1] > 0.5 && r.kids[1].sizes[1] > 0.5);
check("two panes change places", J(M.ids(M.swap(t, "a", "c"))) === '["c","b","a"]');
check("the plain arrangements", J(M.arrange(["a", "b", "c"], 0).dir) === '"row"' && M.arrange(["a", "b", "c"], 1).dir === "col"
      && J(M.ids(M.arrange(["a", "b", "c"], 2))) === '["a","b","c"]' && M.arrange(["a", "b", "c"], 2).kids[1].dir === "col"
      && M.arrange([], 0) === null && J(M.arrange(["a"], 2)) === '{"t":"leaf","id":"a"}');
check("an arrangement kept elsewhere is trusted only as far as it is sound",
      J(M.ids(M.sound({ t: "split", dir: "row", sizes: [0.7, 0.3, 9], kids: [M.leaf("a"), M.leaf("gone"), M.leaf("b")] }, ["a", "b"]))) === '["a","b"]'
      && M.sound({ t: "split", dir: "sideways", kids: [M.leaf("a")] }, ["a"]) === null
      && M.sound({ t: "leaf", id: "a" }, []) === null && M.sound("nonsense", ["a"]) === null
      && J(M.sound({ t: "split", dir: "col", kids: [M.leaf("a"), M.leaf("a")] }, ["a"])) === '{"t":"leaf","id":"a"}');

// --- the panes on the page -----------------------------------------------------------------
const ROOT = "/home/u/proj";
let seq = 0, waiters = [], made = 0;
const terms = [], log = [], posted = [];
let layouts = {};
const b64 = (text) => Buffer.from(text, "utf8").toString("base64");
const print = (id, text) => { log.push([++seq, id, b64(text)]); waiters.splice(0).forEach((w) => w()); };
const mk = (id, mux, body = {}) => ({ id, mux, title: "sh", cwd: ROOT, kind: "shell", owner: null,
                                     alive: true, code: null, cols: body.cols || 80, rows: body.rows || 24 });
// a window that reloads: three panes it had before, one of them floating
terms.push(mk("t1", "t1"), mk("t2", "t1"), mk("t3", "t1"));
layouts = { t1: { tree: { t: "split", dir: "col", sizes: [0.7, 0.3], kids: [M.leaf("t1"), M.leaf("t2")] },
                  floats: ["t3"], show: false, names: { t2: "logs" }, name: "server", frames: true } };

function termOp(body) {
  posted.push(body);
  if (body.op === "open") {
    const id = "n" + (++made);
    const t = mk(id, body.mux || id, body);
    terms.push(t);
    print(id, "$ ");
    return { ok: true, id, term: t };
  }
  if (body.op === "layout") { layouts[body.mux] = body.layout; return { ok: true }; }
  const term = terms.find((x) => x.id === body.id);
  if (!term) return { ok: false, error: "no such terminal" };
  if (body.op === "close") { terms.splice(terms.indexOf(term), 1); waiters.splice(0).forEach((w) => w()); }
  if (body.op === "move") term.mux = body.mux;
  return { ok: true, term };
}

const { boot, pump, el, press } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 0, ROOT),
  children: () => [],
  preload: ["terminal.js", "mux.js"],
  api: {
    "/api/agents": () => ({ rev: 1, sessions: [], conflicts: [], git: true, usage: {}, agents: [] }),
    "/api/terms": async (params) => {
      const since = Number(params.since || 0);
      if (seq <= since && Number(params.wait)) await new Promise((r) => { waiters.push(r); setTimeout(r, 250); });
      return { seq, lost: false, terms: terms.map((x) => ({ ...x })), layouts,
               chunks: log.filter((c) => c[0] > since) };
    },
    "/api/term/replay": ({ id }) => ({ id, seq, data: b64(`${id}$ `), term: terms.find((x) => x.id === id) }),
    "/api/term": (_q, body) => termOp(body),
  },
});
const settle = async (ms = 70) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const last = (op) => [...posted].reverse().find((p) => p.op === op);
const sizes = (n) => n.sizes.map((v) => Math.round(v * 100));

(async () => {
  el("pathcard").hidden = true;
  const D = await boot();
  const key = (k, mods = {}) => {
    const box = D.pane(D.mux().focus).querySelector("textarea");
    return press(box, k, mods);
  };
  const ctrl = (k) => key(k, { ctrlKey: true });
  const alt = (k) => key(k, { altKey: true });
  const typeIn = (text) => {
    const box = D.pane(D.mux().focus).querySelector("textarea");
    box.value = text;
    box.dispatchEvent({ type: "input", target: box });
  };
  const inputs = () => posted.filter((p) => p.op === "input").map((p) => `${p.id}:${p.data}`);
  const bar = () => D.mux().bar;
  const lit = () => bar().children[0].children.filter((c) => c.classList.contains("on"))
    .map((c) => c.children[1].textContent.trim()).join();
  const hints = () => bar().children[1].children.map((c) => c.children[0].textContent).join(" ");
  const tabNames = () => el("p-tabs").children.filter((c) => c.classList.contains("p-tab") && !c.classList.contains("add"))
    .map((c) => c.querySelector(".nm").textContent);

  // --- a window that reloads finds its panes where it left them -------------------------------
  D.show("t1");
  await settle(100);
  let X = D.mux();
  check("panes come back in the tab, and the arrangement, they were in",
        X.tree.dir === "col" && J(X.tiled) === '["t1","t2"]' && J(sizes(X.tree)) === "[70,30]"
        && J(X.floats) === '["t3"]' && X.shown === false, J(X.tree));
  check("with their names, and the tab's", X.name === "server" && X.names.t2 === "logs"
        && J(tabNames()) === '["server"]');
  check("the bar is zellij's: the modes, then what alt does from here",
        bar().children[0].children[0].textContent === "Ctrl +"
        && bar().children[0].children.some((c) => c.children[0] && c.children[0].textContent === "<p>"
                                                  && c.children[1].textContent === " PANE")
        && lit() === "" && hints().startsWith("Alt n Alt ←↓↑→ Alt f"), hints());
  check("and says panes are floating out of sight",
        bar().children[0].children.some((c) => c.textContent.includes("1 FLOATING HIDDEN")));

  // --- moving between panes ---------------------------------------------------------------------
  check("the keys start in a pane that can be seen", D.mux().focus === "t1");
  alt("j");
  check("alt and a direction moves to the pane that way", D.mux().focus === "t2");
  alt("ArrowUp");
  check("with the arrows as well as hjkl", D.mux().focus === "t1");
  typeIn("ls");
  await settle();
  check("what is typed goes to the pane with the keys, and only to it", J(inputs()) === '["t1:ls"]');
  const plain = key("x");
  check("a plain key is the program's", plain.defaultPrevented === false && D.mux().mode === "normal");

  // --- pane mode ----------------------------------------------------------------------------------
  const p = ctrl("p");
  check("ctrl-p is pane mode, and the bar changes to say what its keys do",
        p.defaultPrevented && D.mux().mode === "pane" && lit() === "PANE"
        && hints().startsWith("←↓↑→ n d r x f z w e c"), hints());
  key("r");
  await settle(120);
  X = D.mux();
  check("r opens a pane to the right of this one, in the folder this one is in, and takes the keys",
        last("open").mux === "t1" && last("open").beside === "t1" && X.focus === "n1" && X.mode === "normal"
        && X.tree.kids[0].dir === "row" && J(X.tiled) === '["t1","n1","t2"]', J(X.tree));
  ctrl("p"); key("d");
  await settle(120);
  X = D.mux();
  check("d opens one below", X.focus === "n2" && J(X.tiled) === '["t1","n1","n2","t2"]'
        && X.tree.kids[0].kids[1].dir === "col");
  ctrl("p"); key("h");
  check("the arrows move focus and stay in the mode", D.mux().focus === "t1" && D.mux().mode === "pane");
  key("p");
  check("p goes to the next pane", D.mux().focus === "n1");
  key("Escape");
  check("esc leaves the mode", D.mux().mode === "normal" && lit() === "");
  ctrl("p"); key("f");
  check("f fills the tab with this pane", D.mux().full === true && D.mux().mode === "normal"
        && bar().children[0].children.some((c) => c.textContent.includes("FULLSCREEN")));
  ctrl("p"); key("f");
  ctrl("p"); key("z");
  check("z takes the frames off", D.mux().full === false && D.mux().frames === false);
  ctrl("p"); key("z");
  ctrl("p"); key("x");
  await settle(120);
  X = D.mux();
  check("x closes the pane, ends what was in it, and gives its room back",
        last("close").id === "n1" && J(X.tiled) === '["t1","n2","t2"]' && X.tiled.includes(X.focus), J(X.tiled));

  // --- floating panes ---------------------------------------------------------------------------------
  alt("f");
  check("alt-f brings the floating panes out, and the keys go to one", D.mux().shown === true && D.mux().focus === "t3");
  ctrl("p"); key("e");
  X = D.mux();
  check("e puts a floating pane in among the tiled ones", J(X.floats) === "[]" && X.tiled.includes("t3")
        && X.shown === false && X.focus === "t3");
  ctrl("p"); key("e");
  check("and takes a tiled one out to float", J(D.mux().floats) === '["t3"]' && !D.mux().tiled.includes("t3")
        && D.mux().shown === true);
  alt("f");
  check("alt-f puts them away again, and the keys go back to a tiled pane",
        D.mux().shown === false && D.mux().tiled.includes(D.mux().focus));

  // --- resize, move, layouts ----------------------------------------------------------------------------
  D.show("t1");
  while (D.mux().focus !== "t1") { ctrl("p"); key("p"); key("Escape"); }
  const before = J(D.mux().tree);
  ctrl("n");
  check("ctrl-n is resize mode", D.mux().mode === "resize" && lit() === "RESIZE");
  key("l"); key("l");
  const grown = D.mux().tree.kids[0];
  check("a direction grows the pane that way", J(sizes(grown)) === "[60,40]", J(sizes(grown)));
  key("L");
  check("and its capital shrinks it", J(sizes(D.mux().tree.kids[0])) === "[55,45]");
  key("Enter");
  ctrl("h");
  key("l");
  check("ctrl-h moves the pane itself: it changes places with the one beside it",
        D.mux().mode === "move" && J(D.mux().tiled) === '["n2","t1","t2"]' && D.mux().focus === "t1", J(D.mux().tiled));
  key("Enter");
  alt("]");
  check("alt-] lays the same panes out another way", D.mux().tree.dir === "col" && D.mux().tree.kids.length === 3
        && el("toast").textContent === "panes stacked");
  check("the arrangement is kept where a reload will find it", !!before && await (async () => {
    await settle(350);
    return layouts.t1.tree.dir === "col" && J(layouts.t1.floats) === '["t3"]';
  })());

  // --- sync, and locked ------------------------------------------------------------------------------------
  posted.length = 0;
  ctrl("t"); key("s");
  typeIn("date");
  await settle();
  check("ctrl-t s sends what is typed to every pane in the tab",
        D.mux().sync === true && J(inputs().sort()) === '["n2:date","t1:date","t2:date","t3:date"]', J(inputs()));
  ctrl("t"); key("s");
  posted.length = 0;
  ctrl("g");
  check("ctrl-g locks: the bar says so", D.mux().mode === "locked" && lit() === "LOCK");
  const passed = ctrl("p");
  await settle();
  check("and now ctrl-p is the program's own ctrl-p", passed.defaultPrevented && D.mux().mode === "locked"
        && J(inputs()) === '["t1:\\u0010"]', J(inputs()));
  ctrl("g");
  check("until ctrl-g again", D.mux().mode === "normal");

  // --- names ------------------------------------------------------------------------------------------------
  ctrl("p"); key("c");
  for (const ch of "build") key(ch);
  check("ctrl-p c names the pane as it is typed", D.mux().mode === "renamepane" && D.mux().names.t1 === "build"
        && hints().startsWith("pane name:"));
  key("Backspace"); key("Enter");
  check("backspace and enter do what they say", D.mux().names.t1 === "buil" && D.mux().mode === "normal");
  ctrl("p"); key("c"); key("z"); key("Escape");
  check("esc leaves the name as it was, and goes back to pane mode", D.mux().names.t1 === "buil" && D.mux().mode === "pane");
  key("Escape");
  ctrl("t"); key("r");
  for (const ch of "api") key(ch);
  key("Enter");
  check("ctrl-t r names the tab", D.mux().name === "api" && J(tabNames()) === '["api"]');

  // --- scrolling and searching ------------------------------------------------------------------------------------
  print("t1", "\r\nalpha\r\nneedle one\r\nbeta\r\nNEEDLE two\r\ngamma\r\n$ ");
  await settle(160);
  ctrl("s");
  check("ctrl-s is scroll mode", D.mux().mode === "scroll" && lit() === "SEARCH");
  key("s");
  for (const ch of "needle") key(ch);
  check("s asks what to look for", D.mux().mode === "entersearch" && hints().startsWith("search for:"));
  key("Enter");
  let at = D.mux().search;
  check("enter finds it: the nearest above the end, and how many there are",
        D.mux().mode === "search" && at.count === 2 && at.of === 2, J(at));
  key("p");
  check("p is the one above", D.mux().search.of === 1);
  key("n");
  check("n the one below", D.mux().search.of === 2);
  key("c");
  check("c makes capitals matter", D.mux().search.count === 1, J(D.mux().search));
  key("Escape");
  check("esc goes back to typing in the pane", D.mux().mode === "normal" && D.mux().search === null);

  // --- tabs -------------------------------------------------------------------------------------------------------
  posted.length = 0;
  ctrl("t"); key("n");
  await settle(140);
  check("ctrl-t n is a new tab, starting where this pane is",
        last("open").mux === undefined && last("open").beside === "t1" && D.workspace().active === "n3"
        && J(tabNames()) === '["api","sh"]', J(tabNames()));
  ctrl("t"); key("h");
  check("left and right go between the tabs", D.workspace().active === "t1");
  key("Enter");
  ctrl("t"); key("2");
  check("a number goes to that tab", D.workspace().active === "n3");
  key("Enter");
  ctrl("t"); key("Tab");
  check("tab goes back to the one before", D.workspace().active === "t1");
  key("Escape");
  alt("i");
  check("alt-i and alt-o move the tab itself along", true);
  ctrl("t"); key("b");
  await settle(160);
  const moved = last("move");
  check("ctrl-t b breaks the pane out into a tab of its own",
        moved && moved.id === "t1" && D.workspace().tabs.length === 3 && D.workspace().active === moved.mux
        && J(D.mux().tiled) === '["t1"]' && !D.mux("t1").tiled.includes("t1"), J(D.workspace().tabs));

  // --- a program that ends, and quitting ----------------------------------------------------------------------------
  D.show("t1");
  await settle(80);
  const victim = D.mux().tiled[0];
  terms.find((x) => x.id === victim).alive = false;
  print(victim, "exit\r\n");
  await settle(200);
  check("a pane whose program ends goes, among others, as in zellij",
        !D.mux("t1").tiled.includes(victim) && last("close").id === victim);
  ctrl("q");
  await settle(80);
  check("ctrl-q asks before ending a tab with more than one thing running",
        el("ask").classList.contains("open") && el("ask-title").textContent === "Close this terminal tab?");
  el("ask-ok").click();
  await settle(200);
  check("and then closes every pane in it", !D.workspace().tabs.includes("t1")
        && posted.filter((x) => x.op === "close").length >= 3);

  done("panes");
})();
