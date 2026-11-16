/* Agents, driven through the real cortex/ui/app.js: starting one, its node on
 * the graph and the marks on the files it changes, the conversation in the
 * reader, a question and its answer, two agents on one file, the review and
 * its notes, and forgetting one.
 *
 * The server here is a stand-in that keeps a list of agents and answers the
 * long poll the way the real one does -- at once when something is newer,
 * after a wait when nothing is -- so the page's own feed loop is what is
 * being exercised.
 *
 * Run: node tests/agents.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const SRC = `${ROOT}/src`;
const MAIN = `${SRC}/main.py`, CONFIG = `${SRC}/config.py`, DB = `${SRC}/db.py`;

const tree = { [ROOT]: [SRC], [SRC]: [MAIN, CONFIG, DB] };
const isDir = (p) => p in tree;
const childrenOf = (p) => (tree[p] || []).map((id) =>
  fsnode(id, isDir(id), (tree[id] || []).length, ROOT));

// ------------------------------------------------------- the stand-in server
let rev = 1, waiters = [];
const sessions = [], events = {}, posted = [];
let conflicts = [];
const bump = () => { rev++; waiters.splice(0).forEach((w) => w()); };
const session = (id) => sessions.find((s) => s.id === id);

function push(id, k, extra = {}) {
  const s = session(id);
  s.seq++;
  events[id].push({ k, seq: s.seq, t: Date.now() / 1000, ...extra });
  s.updated = Date.now() / 1000;
  bump();
}

function begin(id, body, color) {
  sessions.push({
    id, agent: body.agents[0], label: body.agents[0] === "claude" ? "Claude Code" : "Codex",
    converses: true, live: body.agents[0] === "claude", title: body.prompt.slice(0, 40),
    folder: body.folder, origin: ROOT, tree: `/data/trees/${id}`, branch: `cortex/${id}`,
    mode: body.mode, perm: body.perm || "edits", state: "working", ended: null, seq: 0,
    unread: false, ask: null, files: {}, recent: [], stats: null, cost: null,
    group: null, color, started: Date.now() / 1000, updated: Date.now() / 1000,
    last: "", review: true, resumable: true,
  });
  events[id] = [];
  push(id, "user", { text: body.prompt });
}

function op(body) {
  posted.push(body);
  const s = session(body.id);
  if (body.op === "start") {
    const id = "s" + (sessions.length + 1);
    begin(id, body, sessions.length);
    return { ok: true, ids: [id], errors: [] };
  }
  if (!s) return { ok: false, error: "no such agent" };
  if (body.op === "seen") { s.unread = false; bump(); return { ok: true }; }
  if (body.op === "answer") {
    s.ask = null; s.state = "working"; s.unread = false;
    push(s.id, "answered", { id: body.request, allow: body.allow });
    return { ok: true };
  }
  if (body.op === "send") { s.state = "working"; push(s.id, "user", { text: body.text }); return { ok: true }; }
  if (body.op === "stop") { s.state = "idle"; push(s.id, "turn", { ok: true, text: "stopped" }); return { ok: true }; }
  if (body.op === "apply") return { ok: true, files: 1 };
  if (body.op === "forget") {
    if (!body.discard) return { ok: false, needs: "discard", error: "it has changes" };
    sessions.splice(sessions.indexOf(s), 1);
    conflicts = [];
    bump();
    return { ok: true };
  }
  return { ok: false, error: "unknown" };
}

const PATCH = ["diff --git a/src/main.py b/src/main.py", "index 1..2 100644",
               "--- a/src/main.py", "+++ b/src/main.py", "@@ -1,3 +1,3 @@",
               " import config", "-x = 1", "+x = 2", " print(x)"].join("\n");

const { boot, pump, el, sent, press, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  git: () => ({ repo: ROOT, branch: "main", states: {} }),
  links: () => ({ ready: true, notes: 0, sources: 3, edges: [
    [CONFIG, MAIN, "code"], [DB, MAIN, "code"]] }),
  api: {
    "/api/preview": ({ path }) => ({ kind: "text", lang: "python", text: `# ${path}`, ext: ".py" }),
    "/api/nodes": (_q, body) => ({ ok: true, hits: [] }),
    "/api/agents": async (params, body) => {
      if (body) return op(body);
      if (rev <= Number(params.since || 0) && Number(params.wait)) {
        await new Promise((r) => { waiters.push(r); setTimeout(r, 400); });
      }
      return { rev, sessions: sessions.map((s) => ({ ...s, files: { ...s.files } })),
               conflicts, git: true,
               agents: [{ id: "claude", label: "Claude Code", speaks: "claude", converses: true, asks: true },
                        { id: "codex", label: "Codex", speaks: "codex", converses: true, asks: false }] };
    },
    "/api/agent": ({ id, after }) => ({
      id, seq: session(id) ? session(id).seq : 0,
      events: (events[id] || []).filter((e) => e.seq > Number(after)) }),
    "/api/agent/diff": ({ id, path }) => path
      ? { ok: true, path, patch: PATCH, truncated: false }
      : { ok: true, branch: `cortex/${id}`, mode: "copy", added: 1, removed: 1, more: 0,
          files: [{ path: "src/main.py", home: MAIN, status: "changed", added: 1,
                    removed: 1, binary: false }] },
  },
});

const { check, done } = reporter();
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const winKey = (key) => fire("keydown", { key, target: null, ctrlKey: false,
                                          metaKey: false, altKey: false, shiftKey: false });
const buttons = (root) => root.querySelectorAll("button");
const button = (root, label) => buttons(root).find((b) => b.textContent === label) || null;
const starts = (root, label) => buttons(root).find((b) => b.textContent.startsWith(label)) || null;
const polls = () => sent.filter((s) => s.url.includes("/api/agents") && !s.body).length;
const last = (name) => [...posted].reverse().find((p) => p.op === name);
const log = () => el("p-body").children[0];
const classes = () => log().children.map((c) => c.className);

(async () => {
  const D = await boot();

  // --- before there are any --------------------------------------------------
  check("with no agents, the page asks once and does not keep asking",
        polls() === 1 && D.agents().watching === false, `${polls()} polls`);
  winKey("a");
  check("a opens the agents list, which says what an agent is",
        !el("pop").hidden && el("pop").children.some((c) => c.textContent.startsWith("None yet")));

  // --- starting one -----------------------------------------------------------
  button(el("pop"), "new agent…").click();
  check("new agent opens the sheet, with what is installed to choose from",
        el("agent-new").classList.contains("open")
        && buttons(el("an-agents")).map((b) => b.textContent).join() === "Claude Code,Codex");
  check("in a repository it defaults to a copy, and to editing freely there",
        button(el("an-where"), "a copy of its own").classList.contains("on")
        && button(el("an-perm"), "edit freely").classList.contains("on"));
  el("an-ok").click();
  await settle();
  check("nothing is started without a task", !last("start"));

  el("an-prompt").value = "make x two";
  el("an-ok").click();
  await settle(120);
  const started = last("start");
  check("start sends the task, the agent, the folder and the mode",
        started && started.prompt === "make x two" && started.agents.join() === "claude"
        && started.folder === ROOT && started.mode === "copy", JSON.stringify(started));
  check("the sheet closes and the agent is a node on the graph",
        !el("agent-new").classList.contains("open") && !!D.at("agent:s1")
        && D.at("agent:s1").name === "make x two");
  check("it is selected, and the reader is its conversation",
        D().selected === "agent:s1" && el("panel").classList.contains("agent")
        && el("p-kind").textContent === "agent"
        && el("p-path").textContent === "Claude Code · in a copy of its own · working"
        && el("p-meta").textContent.includes("cortex/s1"),
        el("p-path").textContent);
  check("and now the feed is being watched", D.agents().watching === true);
  check("the task is the first thing in the conversation",
        classes().join() === "ag-msg user" && log().children[0].textContent === "make x two",
        classes().join());
  check("while it works it can be stopped", !!button(el("p-acts"), "stop"));

  // --- what it does shows on the graph, and in the conversation ---------------
  const s1 = session("s1");
  s1.files = { [CONFIG]: "r", [MAIN]: "w" };
  s1.recent = [[CONFIG, "r", Date.now() / 1000 + 1], [MAIN, "w", Date.now() / 1000 + 2]];
  s1.stats = { files: 1, added: 1, removed: 1 };
  push("s1", "tool", { id: "t1", name: "Read", title: "", detail: "", path: CONFIG, write: false });
  push("s1", "done", { id: "t1", ok: true, text: "PORT = 1" });
  push("s1", "tool", { id: "t2", name: "Edit", title: "", detail: "- x = 1\n+ x = 2", path: MAIN, write: true });
  push("s1", "done", { id: "t2", ok: true, text: "updated" });
  await settle(160);
  const A = D.agents();
  check("a file it is changing is marked, one it only read is not",
        A.marks.join() === MAIN, A.marks.join());
  check("the agent is joined to what it changed",
        JSON.stringify(A.links) === JSON.stringify([["agent:s1", MAIN]]), JSON.stringify(A.links));
  check("and both pinged as it touched them",
        A.pings.includes(CONFIG) && A.pings.includes(MAIN), A.pings.join());
  check("each thing it did is a line, with the file it concerns",
        classes().slice(1).every((c) => c.startsWith("ag-tool"))
        && log().children[2].querySelector(".tt").textContent === "src/main.py", classes().join());
  check("a finished step says how it went, and keeps the detail for a click",
        log().children[2].classList.contains("ok") && log().children[2].classList.contains("has-more")
        && log().children[2].querySelector("pre").textContent.includes("+ x = 2"));
  check("the reader's line counts what changed",
        el("p-meta").textContent.includes("1 file +1 −1"), el("p-meta").textContent);

  // --- a question -------------------------------------------------------------
  s1.state = "waiting";
  s1.unread = true;
  s1.ask = { id: "r1", name: "Bash", title: "pytest -q", detail: "run the tests" };
  push("s1", "ask", s1.ask);
  await settle(160);
  const askBox = el("p-body").children[1];
  check("its question arrives with the command in it", askBox.hidden === false
        && askBox.querySelector(".ag-ask-title").textContent === "Claude Code wants to run a command"
        && askBox.querySelector(".ag-ask-what").textContent === "pytest -q");
  check("looking at it is having seen it", !!last("seen") && session("s1").unread === false);
  button(askBox, "always allow this command").click();
  await settle(120);
  const answer = last("answer");
  check("the answer goes back for that question, and can cover the next time too",
        answer && answer.request === "r1" && answer.allow === true && answer.always === true,
        JSON.stringify(answer));
  check("and the question is put away",
        el("p-body").children[1].hidden === true && classes().includes("ag-note"));

  // --- it finishes ------------------------------------------------------------
  s1.state = "idle";
  s1.cost = 0.26;
  push("s1", "say", { text: "Made `x` two." });
  push("s1", "turn", { ok: true, text: "", cost: 0.26 });
  await settle(160);
  check("what it says is rendered, and the turn is closed off",
        classes().slice(-2).join() === "ag-msg say md,ag-turn"
        && log().children[log().children.length - 2].innerHTML.includes("<code>x</code>")
        && log().children[log().children.length - 1].textContent === "done · $0.26",
        classes().slice(-2).join());
  check("finished, it offers its work instead of a stop button",
        !button(el("p-acts"), "stop") && !!starts(el("p-acts"), "review · 1 file")
        && !!button(el("p-acts"), "apply") && !!button(el("p-acts"), "keep on a branch"));

  // --- a reply ----------------------------------------------------------------
  const input = el("p-body").querySelector("textarea");
  input.value = "and add a test";
  press(input, "Enter", { shiftKey: true });
  check("shift-enter is a new line, not a send", !last("send"));
  const sentKey = press(input, "Enter");
  await settle(120);
  check("enter sends the reply and clears the box",
        last("send") && last("send").text === "and add a test" && input.value === ""
        && sentKey.defaultPrevented);

  // --- a second agent, on the same file ---------------------------------------
  winKey("Escape");
  begin("s2", { agents: ["codex"], prompt: "rename x", folder: ROOT, mode: "copy" }, 1);
  await settle(120);
  const s2 = session("s2");
  s2.files = { [MAIN]: "w" };
  s2.state = "idle";
  s2.unread = true;
  conflicts = [{ path: MAIN, sessions: ["s1", "s2"] }];
  push("s2", "turn", { ok: true, text: "" });
  await settle(160);
  check("two agents changing one file is flagged before either is merged",
        D.agents().clashes.join() === MAIN && D.agents().links.length === 2);
  check("the dock counts who needs you, and so does the window's title",
        el("d-agents").children.some((c) => c.textContent === "1")
        && document.title.startsWith("(1) "), document.title);
  check("and the window says so, since its conversation is not on show",
        el("toast").textContent.startsWith("Codex finished — rename x"), el("toast").textContent);
  winKey("a");
  const rows = el("pop").children.filter((c) => c.classList.contains("pop-row"));
  check("the list puts the one that needs you first, and names the clash",
        rows[0].querySelector(".nm").textContent === "rename x" && rows[0].classList.contains("need")
        && rows[rows.length - 1].classList.contains("clash")
        && rows[rows.length - 1].querySelector(".nm").textContent === "main.py");
  winKey("Escape");
  winKey("A");
  await settle(120);
  check("A goes to the agent that needs you", D().selected === "agent:s2");

  // --- the review, and a note on a line ----------------------------------------
  D.select("agent:s1");
  await settle(80);
  starts(el("p-acts"), "review").click();
  await settle(120);
  const review = el("p-body").children[0];
  check("review lists what changed", D.agents().view.mode === "review"
        && review.querySelector(".ag-rev-sum").textContent === "1 file changed · +1 −1 · on cortex/s1",
        review.querySelector(".ag-rev-sum") && review.querySelector(".ag-rev-sum").textContent);
  check("says who else is changing it", review.querySelector(".ag-rev-warn")
        && review.querySelector(".ag-rev-warn").textContent.includes("“rename x”"));
  check("and how many other files lean on it", review.querySelector(".ag-rev-lean")
        && review.querySelector(".ag-rev-lean").textContent.startsWith("2 other files import"));
  review.querySelector(".ag-rev-lean").click();
  await settle(80);
  check("which light up on the graph when asked", D.visible(CONFIG) && D.visible(DB));

  review.querySelector(".ag-file-head").click();
  await settle(120);
  const lines = review.querySelector(".ag-patch").children;
  check("a file opens into its changes, without git's own headers",
        lines.map((l) => l.className).join() === "ag-line hunk,ag-line same,ag-line del,ag-line add,ag-line same",
        lines.map((l) => l.className).join());
  lines[3].click();
  await settle();
  check("clicking a line asks for a note about it", el("ask").classList.contains("open")
        && el("ask-text").textContent === "src/main.py, line 2", el("ask-text").textContent);
  el("ask-input").value = "use a constant";
  el("ask-ok").click();
  await settle();
  const sendNotes = starts(review, "send 1 note");
  check("the note marks the line and waits to be sent",
        lines[3].classList.contains("noted") && !!sendNotes && !review.querySelector(".ag-rev-foot").hidden);
  sendNotes.click();
  await settle(160);
  check("notes go back to the agent as one message that says where each belongs",
        last("send").text.includes("src/main.py, line 2 (`x = 2`): use a constant"), last("send").text);
  check("and the reader goes back to the conversation", D.agents().view.mode === "chat");

  // --- applying, and closing the window on one that is working -----------------
  session("s1").state = "idle";
  bump();
  await settle(120);
  button(el("p-acts"), "apply").click();
  await settle(80);
  check("apply puts it in your folder and says so", !!last("apply")
        && el("toast").textContent === "applied to your folder — 1 file, uncommitted", el("toast").textContent);
  session("s1").state = "working";
  bump();
  await settle(120);
  check("closing the window while one is working asks first",
        fire("beforeunload").defaultPrevented === true);
  session("s1").state = "idle";
  bump();
  await settle(120);
  check("and does not when none is", fire("beforeunload").defaultPrevented === false);

  // --- forgetting ---------------------------------------------------------------
  button(el("p-acts"), "Forget…").click();
  await settle(80);
  check("forgetting work that is nowhere else has to be meant",
        el("ask").classList.contains("open") && el("ask-ok").textContent === "throw away"
        && last("forget").discard === undefined);
  el("ask-ok").click();
  await settle(160);
  check("meant, it goes: the node, its links, and the reader",
        last("forget").discard === true && !D.at("agent:s1") && D().selected === null
        && D.agents().links.every((l) => l[0] !== "agent:s1") && D.agents().clashes.length === 0);

  done("agents");
})();
