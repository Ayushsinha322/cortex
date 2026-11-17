/* The window as a workspace, driven through the real cortex/ui/app.js and
 * the real ui/terminal.js: the pane's tabs, a terminal in one of them, the
 * rail, the status bar, and a preview.
 *
 * The server is a stand-in that keeps a list of terminals and a feed of what
 * they have printed, answered the way the real one answers it, so the page's
 * own feed loop and the real emulator are what is being exercised.
 *
 * Run: node tests/workspace.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const SRC = `${ROOT}/src`, MAIN = `${SRC}/main.py`;
const tree = { [ROOT]: [SRC], [SRC]: [MAIN] };
const childrenOf = (p) => (tree[p] || []).map((id) =>
  fsnode(id, id in tree, (tree[id] || []).length, ROOT));

// ------------------------------------------------------- the stand-in server
let seq = 0, waiters = [];
const terms = [], log = [], posted = [];
const b64 = (text) => Buffer.from(text, "utf8").toString("base64");
const print = (id, text) => { log.push([++seq, id, b64(text)]); waiters.splice(0).forEach((w) => w()); };
let usage = {};

function termOp(body) {
  posted.push(body);
  if (body.op === "open") {
    const id = "t" + (terms.length + 1);
    const t = { id, title: body.run ? "Claude Code" : "proj", cwd: body.cwd || ROOT,
                kind: body.run ? "agent" : "shell", owner: body.agent || null,
                alive: true, code: null, cols: body.cols, rows: body.rows };
    terms.push(t);
    print(id, "$ ");
    return { ok: true, id, term: t };
  }
  const t = terms.find((x) => x.id === body.id);
  if (!t) return { ok: false, error: "no such terminal" };
  if (body.op === "input") { if (body.data !== "\r") print(t.id, body.data); return { ok: true }; }
  if (body.op === "close") { terms.splice(terms.indexOf(t), 1); waiters.splice(0).forEach((w) => w()); return { ok: true }; }
  return { ok: true };
}

const { boot, pump, el, sent, press, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  preload: ["terminal.js", "mux.js"],
  api: {
    "/api/preview": ({ path }) => ({ kind: "text", lang: "python", text: `# ${path}`, ext: ".py" }),
    "/api/agents": (_q, body) => body ? { ok: false, error: "none" }
      : { rev: 1, sessions: [], conflicts: [], git: true, usage,
          agents: [{ id: "claude", label: "Claude Code", speaks: "claude", converses: true, asks: true }] },
    "/api/terms": async (params) => {
      const since = Number(params.since || 0);
      if (seq <= since && Number(params.wait)) {
        await new Promise((r) => { waiters.push(r); setTimeout(r, 300); });
      }
      return { seq, lost: false, terms: terms.map((t) => ({ ...t })),
               chunks: log.filter((c) => c[0] > since) };
    },
    "/api/term/replay": ({ id }) => ({ id, seq, data: b64("replayed\x1b[6n\r\n$ "),
                                       term: terms.find((t) => t.id === id) }),
    "/api/term": (_q, body) => termOp(body),
  },
});

const { check, done } = reporter();
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const winKey = (key) => fire("keydown", { key, target: null, ctrlKey: false,
                                          metaKey: false, altKey: false, shiftKey: false });
const tabs = () => el("p-tabs").children.filter((c) => c.classList.contains("p-tab")
                                                      && !c.classList.contains("add"));
const names = () => tabs().map((t) => t.querySelector(".nm").textContent);
const last = (op) => [...posted].reverse().find((p) => p.op === op);
const railRows = (id) => el(id).children.filter((c) => c.classList.contains("rail-row"));
const chips = () => el("st-right").children.map((c) => c.textContent);
/* The tool whose tab is on show: the others stay in the pane, taking no room. */
const onShow = () => el("p-tool").children.find((c) => !c.classList.contains("off"));

(async () => {
  // as the page has it: the stub's elements start out shown
  el("pathcard").hidden = true;
  const D = await boot();

  // --- the frame ---------------------------------------------------------------
  check("the rail is there from the start, with the things to begin from",
        D.workspace().rail && el("rail").hidden === false
        && el("rail-do").children.map((c) => c.querySelector(".nm").textContent).join()
           === "New agent,New terminal,Preview a local page,Open a folder,Hide the graph");
  check("left on the graph, it opens on the graph: no terminal is started for you",
        D.workspace().work === false && el("btn-graph").hidden === false
        && el("btn-graph").classList.contains("on") && posted.length === 0);
  check("with nothing open there are no tabs, and nothing is asked of the terminals feed twice",
        names().length === 0 && sent.filter((s) => s.url.includes("/api/terms")).length === 1);
  check("and the status bar is quiet", chips().length === 0);

  // --- a terminal ----------------------------------------------------------------
  winKey("`");
  await settle(120);
  const open = last("open");
  check("` opens a terminal in the folder being looked at",
        open && open.cwd === ROOT && open.cols > 20 && open.rows > 5, JSON.stringify(open));
  let W = D.workspace();
  check("it is a tab, and the tab on show", W.tabs.join() === "t1" && W.active === "t1"
        && el("panel").classList.contains("open") && el("panel").classList.contains("tool")
        && names().join() === "proj" && tabs()[0].classList.contains("on"));
  check("what the program prints is on its screen", W.terms[0].text[0] === "$", W.terms[0].text[0]);
  check("the rail lists it, and the status bar counts it",
        railRows("rail-terms").length === 1 && chips().join() === "1 terminal", chips().join());

  // whether the view is kept at the end is remembered, and only the person changes it
  const sc = onShow().querySelector(".t-scroll");
  const follows = () => D.workspace().terms[0].following;
  sc.scrollHeight = 2000; sc.clientHeight = 300; sc.scrollTop = 0;
  sc.dispatchEvent({ type: "scroll", target: sc });
  check("a view the page moved by itself still follows what is printed", follows() === true);
  sc.dispatchEvent({ type: "wheel", target: sc, preventDefault() {} });
  sc.dispatchEvent({ type: "scroll", target: sc });
  check("one the person scrolled back does not", follows() === false);
  sc.scrollTop = 1700;
  sc.dispatchEvent({ type: "wheel", target: sc, preventDefault() {} });
  sc.dispatchEvent({ type: "scroll", target: sc });
  check("and scrolled to the end again, it does", follows() === true);

  const box = onShow().querySelector("textarea");
  box.value = "ls";
  box.dispatchEvent({ type: "input", target: box });
  await settle(120);
  check("what is typed goes to the program", last("input").data === "ls" && box.value === "");
  check("and its echo comes back onto the screen", D.workspace().terms[0].text[0] === "$ ls");
  const enter = press(box, "Enter");
  const ctrlC = press(box, "c", { ctrlKey: true });
  await settle(120);
  check("keys are sent as a terminal sends them, in the order pressed",
        posted.filter((p) => p.op === "input").map((p) => p.data).join("|").endsWith("\r|\x03")
        || posted.filter((p) => p.op === "input").map((p) => p.data).join("|").endsWith("\r\x03"),
        JSON.stringify(posted.filter((p) => p.op === "input").map((p) => p.data)));
  check("and are the program's, not cortex's", enter.defaultPrevented && ctrlC.defaultPrevented
        && !el("palette").classList.contains("open"));

  print("t1", "\r\n\x1b[1;32mok\x1b[0m 3 passed\r\n$ ");
  await settle(160);
  check("colour codes are read, not shown", D.workspace().terms[0].text[1] === "ok 3 passed",
        JSON.stringify(D.workspace().terms[0].text.slice(0, 3)));
  print("t1", "\x1b]0;make test\x07");
  await settle(160);
  check("a program can name its own tab", names().join() === "make test");

  // --- tabs stay while the selection changes ----------------------------------------
  D.select(MAIN);
  await settle(80);
  W = D.workspace();
  check("selecting on the graph shows the selection, in a tab of its own",
        W.active === null && !el("panel").classList.contains("tool")
        && names().join() === "main.py,make test" && tabs()[0].classList.contains("on"));
  check("and the terminal is still there, still running", W.tabs.join() === "t1" && W.terms[0].alive);

  print("t1", "\x1b]9;Claude needs you\x07");
  await settle(160);
  check("a program asking for you marks its tab, and says so",
        D.workspace().terms[0].unread && tabs()[1].classList.contains("need")
        && el("toast").textContent === "make test — Claude needs you"
        && chips().join() === "1 terminal" && el("st-right").children[0].classList.contains("need"),
        el("toast").textContent);
  tabs()[1].click();
  await settle(80);
  check("going to the tab is having seen it", D.workspace().active === "t1"
        && !D.workspace().terms[0].unread && !tabs()[1].classList.contains("need"));
  tabs()[0].click();
  await settle(60);
  check("and the selection's tab goes back to the file", D.workspace().active === null
        && D().selected === MAIN);

  // --- an agent's own interface, from the + menu --------------------------------------
  el("p-tabs").children[el("p-tabs").children.length - 1].click();
  const menu = el("ctx").children.filter((c) => c.className.startsWith("ctx-item"))
    .map((c) => c.children[0].textContent);
  check("+ offers a terminal, an agent, a preview, and each agent's own interface",
        menu.join("|") === "New terminal|New agent…|Preview a local address…|Claude Code", menu.join("|"));
  const claude = el("ctx").children.filter((c) => c.className.startsWith("ctx-item"))
    .find((c) => c.children[0].textContent === "Claude Code");
  claude.click();
  await settle(120);
  check("which opens as a tab like any other", last("open").run === "claude"
        && D.workspace().tabs.join() === "t1,t2" && D.workspace().active === "t2");

  // --- ending, and closing --------------------------------------------------------------
  terms[1].alive = false;
  terms[1].code = 2;
  print("t2", "bye\r\n");
  await settle(160);
  check("a program that ends says so in its tab",
        D.workspace().terms[1].alive === false
        && D.workspace().terms[1].text.some((r) => r.includes("[ended with status 2")));
  const box2 = onShow().querySelector("textarea");
  press(box2, "Enter");
  await settle(120);
  check("and enter then closes the tab", last("close").id === "t2"
        && D.workspace().tabs.join() === "t1" && D.workspace().active === "t1");

  check("closing the window with a program running asks first",
        fire("beforeunload").defaultPrevented === true);
  tabs()[1].querySelector(".x").click();
  await settle(120);
  check("closing the last tab leaves the selection", D.workspace().tabs.length === 0
        && D.workspace().active === null && D().selected === MAIN
        && fire("beforeunload").defaultPrevented === false);

  // --- a window that reloads -----------------------------------------------------------------
  // (the stand-in's replay carries a question a program once asked: "where is the cursor?")
  const before = posted.filter((p) => p.op === "input").length;
  terms.push({ id: "t9", title: "old", cwd: ROOT, kind: "shell", owner: null, alive: true,
               code: null, cols: 80, rows: 24 });
  print("t9", "");
  await D.terminal({});                 // starts the feed again, which finds t9
  await settle(200);
  const old = D.workspace().terms.find((t) => t.id === "t9");
  check("a terminal this window did not open is picked up from its replay",
        old && old.text[0] === "replayed", old && JSON.stringify(old.text.slice(0, 2)));
  check("and questions in the replay are not answered a second time, into today's prompt",
        posted.filter((p) => p.op === "input").length === before);
  while (D.workspace().tabs.length) {
    tabs().find((t) => t.querySelector(".g").textContent === ">_").querySelector(".x").click();
    await settle(80);
  }

  // --- a preview ---------------------------------------------------------------------------
  await D.preview("https://example.com/");
  check("a preview is only for pages on this machine", D.workspace().tabs.length === 0
        && el("toast").textContent.startsWith("only pages on this machine"));
  await D.preview("3000");
  const frame = onShow().querySelector("iframe");
  check("a port is enough to name one", D.workspace().active === "web:1"
        && frame && frame.src === "http://localhost:3000" && names().join() === "main.py,localhost:3000",
        frame && frame.src);
  winKey("Escape");
  await settle(60);
  check("esc puts the pane away, and the tab is kept for later",
        !el("panel").classList.contains("open") && D.workspace().tabs.join() === "web:1"
        && railRows("rail-terms").length === 1);
  railRows("rail-terms")[0].click();
  check("the rail brings it back", D.workspace().active === "web:1"
        && el("panel").classList.contains("open"));

  done("workspace");
})();
