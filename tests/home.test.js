/* The window opens on the place to work, and the graph is there when asked
 * for: driven through the real cortex/ui/app.js and the real terminal.
 *
 * Run: node tests/home.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const SRC = `${ROOT}/src`, MAIN = `${SRC}/main.py`;
const tree = { [ROOT]: [SRC], [SRC]: [MAIN] };
const childrenOf = (p) => (tree[p] || []).map((id) =>
  fsnode(id, id in tree, (tree[id] || []).length, ROOT));

// ------------------------------------------------------- the stand-in server
let seq = 0, waiters = [];
const terms = [], log = [], posted = [], kept = [];
const b64 = (text) => Buffer.from(text, "utf8").toString("base64");
const print = (id, text) => { log.push([++seq, id, b64(text)]); waiters.splice(0).forEach((w) => w()); };

function termOp(body) {
  posted.push(body);
  if (body.op === "open") {
    const id = "t" + (posted.filter((p) => p.op === "open").length);
    const t = { id, title: "proj", cwd: body.cwd || ROOT, kind: "shell", owner: null,
                alive: true, code: null, cols: body.cols, rows: body.rows };
    terms.push(t);
    print(id, "$ ");
    return { ok: true, id, term: t };
  }
  const t = terms.find((x) => x.id === body.id);
  if (!t) return { ok: false, error: "no such terminal" };
  if (body.op === "close") { terms.splice(terms.indexOf(t), 1); waiters.splice(0).forEach((w) => w()); }
  return { ok: true };
}

const { boot, pump, el, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  preload: ["terminal.js", "mux.js"],
  config: { prefs: { layout: "work" } },
  api: {
    "/api/preview": ({ path }) => ({ kind: "text", lang: "python", text: `# ${path}`, ext: ".py" }),
    "/api/agents": (_q, body) => body ? { ok: false, error: "none" }
      : { rev: 1, sessions: [], conflicts: [], git: true, usage: {}, agents: [] },
    "/api/terms": async (params) => {
      const since = Number(params.since || 0);
      if (seq <= since && Number(params.wait)) {
        await new Promise((r) => { waiters.push(r); setTimeout(r, 300); });
      }
      return { seq, lost: false, terms: terms.map((t) => ({ ...t })),
               chunks: log.filter((c) => c[0] > since) };
    },
    "/api/term/replay": ({ id }) => ({ id, seq, data: b64("$ "), term: terms.find((t) => t.id === id) }),
    "/api/term": (_q, body) => termOp(body),
    "/api/prefs": (_q, body) => { kept.push(body.set); return { ok: true, prefs: body.set }; },
  },
});

const { check, done } = reporter();
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const winKey = (key) => fire("keydown", { key, target: null, ctrlKey: false,
                                           metaKey: false, altKey: false });
const body = () => global.document.body.classList;
const tabs = () => el("p-tabs").children.filter((c) => c.classList.contains("p-tab")
                                                        && !c.classList.contains("add"));
const begin = () => el("home-do").children.map((c) => c.querySelector(".nm").textContent);
const opened = () => posted.filter((p) => p.op === "open");

(async () => {
  el("pathcard").hidden = true;
  const D = await boot();
  await settle(150);

  // --- it opens on a terminal ----------------------------------------------------
  let W = D.workspace();
  check("the window opens on the workspace", W.work === true && body().contains("work"));
  check("with a terminal already there to type into, in the folder",
        opened().length === 1 && opened()[0].cwd === ROOT && W.tabs.join() === "t1"
        && W.active === "t1" && el("panel").classList.contains("open"),
        JSON.stringify([opened(), W.tabs, W.active]));
  check("and the terminal is drawn and has what the shell printed",
        W.terms[0].text[0] === "$" && W.terms[0].following === true);
  check("the graph is a button away, and not lit",
        el("btn-graph").hidden === false && !el("btn-graph").classList.contains("on"));
  const before = D().draws;
  pump(20);
  check("a graph nobody is looking at is not drawn", D().draws === before);

  // --- the graph, when asked for -------------------------------------------------
  el("btn-graph").click();
  await settle();
  W = D.workspace();
  check("the button shows the graph, beside the terminal that was open",
        W.work === false && !body().contains("work") && W.active === "t1"
        && el("btn-graph").classList.contains("on"));
  check("and that is remembered", kept.length === 1 && kept[0].layout === "graph");
  pump(20);
  check("now it is drawn", D().draws > before);
  check("no second terminal was started for it", opened().length === 1);

  // G is the same thing from the keyboard; keys typed in a terminal are the terminal's
  winKey("G");
  await settle();
  check("G puts the graph away again", D.workspace().work === true && body().contains("work")
        && kept[kept.length - 1].layout === "work");

  // --- reading a file, and back --------------------------------------------------
  D.select(MAIN);
  await settle();
  check("a file opens over the terminal", D().selected === MAIN && D.workspace().active === null);
  winKey("Escape");
  await settle();
  check("and closing it goes back to the terminal, not to an empty window",
        D().selected === null && D.workspace().active === "t1"
        && el("panel").classList.contains("open"));

  // --- nothing open ----------------------------------------------------------------
  tabs()[0].querySelector(".x").click();
  await settle(120);
  W = D.workspace();
  check("with the last tab closed the workspace is still the window",
        W.work === true && W.tabs.length === 0 && !el("panel").classList.contains("open"));
  check("and says what there is to begin from",
        begin().join() === "New agent,New terminal,Preview a local page,Open a folder,Show the graph",
        begin().join());
  check("without starting another terminal by itself", opened().length === 1);
  el("home-do").children[1].click();
  await settle(120);
  check("a terminal from there", opened().length === 2 && D.workspace().active === "t2");

  done("workspace first");
})();
