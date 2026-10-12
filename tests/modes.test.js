/* The ways of looking, driven through the real cortex/ui/app.js: the path
 * between two files, saved views, tours, the command palette, live changes,
 * the history, and disk usage.
 *
 * Run: node tests/modes.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`, SRC = `${ROOT}/src`;
const A = `${NOTES}/a.md`, B = `${NOTES}/b.md`, NEW = `${NOTES}/new.md`;
const MAIN = `${SRC}/main.py`, CONFIG = `${SRC}/config.py`, DB = `${SRC}/db.py`;
const OLD = `${NOTES}/old.md`, DATABASE = `${SRC}/database.py`;

const tree = { [ROOT]: [NOTES, SRC], [NOTES]: [A, B], [SRC]: [MAIN, CONFIG, DB] };
const isDir = (p) => p in tree;
const childrenOf = (p) => (tree[p] || []).map((id) =>
  fsnode(id, isDir(id), (tree[id] || []).length, ROOT));
const exists = (p) => p === ROOT || Object.values(tree).some((l) => l.includes(p));
const lineage = (p) => {
  const parts = p.slice(ROOT.length + 1).split("/");
  const ancestors = [];
  let cur = ROOT;
  for (const part of parts.slice(0, -1)) {
    cur += "/" + part;
    ancestors.push(fsnode(cur, true, 1, ROOT));
  }
  const node = fsnode(p, false, 0, ROOT);
  if (!exists(p)) node.missing = true;
  return { node, ancestors };
};

const saved = { views: [], tours: [] };
const posted = [];
let activity = [];                        // canned answers, then quiet
let duCalls = 0;

const { boot, pump, el, sent, press, type, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 2, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  links: () => ({ ready: true, notes: 2, sources: 3, edges: [
    [MAIN, CONFIG, "code"], [CONFIG, DB, "code"], [A, B, "note"]] }),
  search: (q) => ({ results: [CONFIG].filter((p) => p.includes(q)).map(lineage), count: 1 }),
  api: {
    "/api/preview": ({ path }) => ({ kind: "text", lang: "python", text: `# ${path}`, ext: ".py" }),
    "/api/nodes": (_q, body) => ({ ok: true, hits: body.paths.map(lineage) }),
    "/api/views": (_q, body) => {
      if (!body) return { views: saved.views };
      posted.push(body);
      if (body.op === "save") saved.views = [body.view];
      return { ok: true, views: saved.views };
    },
    "/api/tours": (_q, body) => {
      if (!body) return { tours: saved.tours };
      posted.push(body);
      saved.tours = [{ path: `${ROOT}/.tours/walk.md`, title: body.title, stops: body.stops.length,
                       intro: "" }];
      saved.tour = { ok: true, path: `${ROOT}/.tours/walk.md`, title: body.title, intro: "Hi.",
                     stops: body.stops.map((s) => ({ ...s, label: s.path.split("/").pop(),
                                                     missing: false })) };
      return { ok: true, path: `${ROOT}/.tours/walk.md` };
    },
    "/api/tour": () => saved.tour,
    "/api/activity": async () => {
      if (activity.length) return activity.shift();
      await new Promise((r) => setTimeout(r, 30));
      return { seq: 9, events: [], engine: "inotify" };
    },
    "/api/history": () => ({
      repo: ROOT, head: "c3", truncated: false, base: [OLD],
      commits: [
        { sha: "c1", t: 1700000000, author: "ayu", subject: "begin",
          changes: [["A", A, null], ["A", MAIN, null], ["A", DATABASE, null]] },
        { sha: "c2", t: 1700086400, author: "ayu", subject: "config",
          changes: [["A", CONFIG, null], ["M", MAIN, null], ["D", OLD, null]] },
        { sha: "c3", t: 1700172800, author: "ayu", subject: "rename the database",
          changes: [["R", DB, DATABASE], ["A", B, null]] },
      ],
    }),
    "/api/du": ({ path }) => {
      duCalls++;
      const big = { [ROOT]: 20_000_000, [SRC]: 12_000_000, [NOTES]: 8_000_000 };
      return {
        path, bytes: big[path] || 0, files: 4, complete: duCalls > 2,
        children: (tree[path] || []).map((p) => ({
          name: p.split("/").pop(), path: p, dir: isDir(p), complete: true,
          bytes: big[p] || (p === CONFIG ? 10_000_000 : 1000) })),
      };
    },
  },
});

const { check, done } = reporter();
const settle = async (ms = 40) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const ctrl = (key) => fire("keydown", { key, ctrlKey: true, target: null,
                                        metaKey: false, altKey: false, shiftKey: false });
const winKey = (key) => fire("keydown", { key, target: null, ctrlKey: false,
                                          metaKey: false, altKey: false, shiftKey: false });

/* Click a node the way the mouse would, shift held or not. */
function click(D, id, shiftKey = false) {
  const n = D.at(id), { cam } = D();
  const x = n.x * cam.s + cam.x, y = n.y * cam.s + cam.y;
  el("stage").dispatchEvent({ type: "mousedown", clientX: x, clientY: y, shiftKey });
  fire("mouseup", { clientX: x, clientY: y });
}

const popRows = () => el("pop").children.filter((c) => c.className === "pop-row");
const button = (root, label) => root.querySelectorAll("button")
  .find((b) => b.textContent === label) || null;

(async () => {
  const D = await boot();

  // --- the path between two files -------------------------------------------
  D.select(MAIN);
  click(D, DB, true);
  await settle(80);
  check("shift-click finds the chain of imports", JSON.stringify(D.modes().path)
        === JSON.stringify([MAIN, CONFIG, DB]), JSON.stringify(D.modes().path));
  const steps = el("pathcard").children.filter((c) => c.className === "pc-step")
    .map((c) => c.textContent);
  check("and lays it out step by step", el("pathcard").hidden === false
        && steps.join(">") === "main.py>config.py>db.py", steps.join(">"));
  check("saying what each step is", el("pathcard").children
        .filter((c) => c.className === "pc-arrow").every((c) => c.textContent === "imports"));
  check("the path stays in sight", D.visible(CONFIG));
  check("the far end is selected", D().selected === DB);

  winKey("Escape");
  await settle();
  check("esc clears the path first", D.modes().path === null && el("pathcard").hidden);

  D.select(A);
  click(D, DB, true);
  await settle(60);
  check("no chain says so plainly", el("pathcard").hidden === false
        && el("pathcard").children.some((c) => (c.textContent || "").startsWith("No chain")));
  winKey("Escape");

  // --- views ------------------------------------------------------------------
  D.key("v");
  await settle();
  check("v opens the views", el("pop").hidden === false);
  button(el("pop"), "save this view…").click();
  await settle();
  el("ask-input").value = "the notes";
  el("ask-ok").click();
  await settle(60);
  const savedView = posted.find((p) => p.op === "save" && p.view);
  check("saving sends the way the graph looks", savedView && savedView.view.name === "the notes"
        && savedView.view.expanded.includes(NOTES) && savedView.view.scope === ROOT,
        JSON.stringify(savedView));
  D.select(NOTES);
  D.key("e");                                   // shut the notes folder
  await settle();
  check("the notes are shut", D.select(A) === false);
  D.key("1");
  await settle(120);
  check("1 brings the view back, notes open again", D.select(A) === true);

  // --- tours ------------------------------------------------------------------
  D.key("T");
  await settle();
  check("T opens the tours, which says what a tour is when there are none",
        el("pop").hidden === false
        && el("pop").children.some((c) => c.className === "pop-empty"));
  button(el("pop"), "record a tour…").click();
  await settle();
  el("ask-input").value = "Walkthrough";
  el("ask-ok").click();
  await settle();
  check("recording starts with the selection as the first stop", D.modes().recording === 1);
  type(el("tourcard").querySelector("textarea"), "Start with the notes.");
  D.select(MAIN);
  await settle();
  check("each file selected is the next stop", D.modes().recording === 2);
  type(el("tourcard").querySelector("textarea"), "Then the code.");
  button(el("tourcard"), "save tour").click();
  await settle(60);
  const tourSave = posted.find((p) => p.op === "save" && p.stops);
  check("saving writes the stops and what was said at each",
        tourSave && tourSave.stops.length === 2 && tourSave.stops[0].note === "Start with the notes."
        && tourSave.stops[1].path === MAIN, JSON.stringify(tourSave));
  check("and the recording card goes away", el("tourcard").hidden === true);

  D.key("T");
  await settle();
  button(el("pop"), "record a tour…").click();
  await settle();
  el("ask-input").value = "Half done";
  el("ask-ok").click();
  await settle();
  const recording = D.modes().recording;
  D.key("T");
  await settle(60);
  popRows()[0].click();
  await settle(60);
  check("playing a tour does not throw away one being recorded",
        D.modes().recording === recording && D.modes().tour === null);
  button(el("tourcard"), "cancel").click();
  await settle(60);

  D.key("T");
  await settle(60);
  popRows()[0].click();
  await settle(120);
  check("playing a tour goes to its first stop", D.modes().tour === 0 && D().selected === A,
        `${D.modes().tour} ${D().selected}`);
  winKey("ArrowRight");
  await settle(120);
  check("the arrow keys step through it", D.modes().tour === 1 && D().selected === MAIN);
  check("the card counts the stops", el("tourcard").querySelector(".tc-step").textContent === "2 / 2");
  winKey("Escape");
  await settle();
  check("esc ends the tour", D.modes().tour === null && el("tourcard").hidden);

  // --- the palette --------------------------------------------------------------
  ctrl("k");
  await settle();
  check("ctrl-k opens the palette", el("palette").classList.contains("open"));
  const pal = el("pal-input");
  pal.value = "tour";
  pal.dispatchEvent({ type: "input", target: pal });
  await settle();
  const first = el("pal-list").children[0];
  check("typing finds the command", first && first.children[1].textContent === "Play a tour…",
        first && first.children[1].textContent);
  pal.value = "conf";
  pal.dispatchEvent({ type: "input", target: pal });
  await settle(220);
  const fileRow = el("pal-list").children.find((r) => r.children[0].textContent === "file");
  check("and files by name, too", fileRow && fileRow.children[1].textContent === "config.py");
  press(pal, "ArrowDown");
  const onIndex = el("pal-list").children.findIndex((r) => r.classList.contains("on"));
  press(pal, "Enter");
  await settle(80);
  check("enter does what is highlighted", !el("palette").classList.contains("open")
        && onIndex >= 0);

  // --- live -------------------------------------------------------------------------
  activity = [
    { seq: 3, engine: "inotify", events: [{ seq: 3, t: Date.now() / 1000, kind: "modified",
                                            path: A, dir: false }] },
    { seq: 5, engine: "inotify", events: [
      { seq: 4, t: Date.now() / 1000, kind: "modified", path: CONFIG, dir: false },
      { seq: 5, t: Date.now() / 1000, kind: "created", path: NEW, dir: false }] },
  ];
  tree[NOTES] = [A, B, NEW];
  D.key("w");
  await settle(150);
  check("w turns live on", D.modes().live && el("feed").hidden === false);
  check("it says how it is watching", el("feed-engine").textContent === "instant");
  const rows = el("feed-list").children.filter((c) => c.className === "feed-row");
  check("what changed before live was on is not news, what changed after is",
        rows.length === 2 && rows[0].children[1].textContent === "new.md",
        rows.map((r) => r.children[1].textContent).join(","));
  check("a new file is on the graph at once", D.select(NEW) === true);
  check("the dock counts the changes", el("d-live").children.some((c) => c.textContent === "2"));
  pump(5);
  D.key("w");
  const quietBefore = sent.filter((s) => s.url.includes("/api/activity")).length;
  await settle(150);
  const quietAfter = sent.filter((s) => s.url.includes("/api/activity")).length;
  check("w again stops watching", !D.modes().live && el("feed").hidden
        && quietAfter - quietBefore <= 1, `${quietAfter - quietBefore} more requests`);

  // --- history ------------------------------------------------------------------------
  D.select(SRC);
  D.key("e");                                    // shut src: its files will be grafted
  await settle();
  D.key("h");
  await settle(150);
  check("h opens the history at now", D.modes().history === 3 && el("timeline").hidden === false);
  check("now: the renamed file is there, its old name is not, nor the deleted file",
        D.visible(DB) && !D.visible(DATABASE) && !D.visible(OLD),
        `${D.visible(DB)} ${D.visible(DATABASE)} ${D.visible(OLD)}`);
  const range = el("tl-range");
  range.value = "0";
  range.dispatchEvent({ type: "input", target: range });
  await settle();
  check("at the start only what existed before the first commit",
        D.visible(OLD) && !D.visible(A) && !D.visible(MAIN));
  range.value = "1";
  range.dispatchEvent({ type: "input", target: range });
  await settle();
  check("after the first commit, its files", D.visible(A) && D.visible(MAIN) && D.visible(DATABASE)
        && !D.visible(CONFIG) && D.visible(OLD));
  check("the timeline says which commit", el("tl-info").children[1].textContent === "begin");
  for (let i = 0; i < 3; i++) el("tl-speed").click();
  winKey(" ");
  for (let i = 0; i < 12; i++) { await settle(25); }
  check("space plays it to the end", D.modes().history === 3, String(D.modes().history));
  await D.expand(SRC);                           // opened while in the history
  await settle();
  D.key("h");
  await settle();
  check("h again goes back to now", D.modes().history === null && el("timeline").hidden);
  check("what is in a folder opened meanwhile stays",
        D.select(MAIN) === true && D.select(CONFIG) === true);
  check("files that are gone leave with it", D.at(OLD) === undefined && D.at(DATABASE) === undefined);

  // --- disk ---------------------------------------------------------------------------
  const before = { config: D.at(CONFIG).r, main: D.at(MAIN).r };
  D.key("u");
  await settle(120);
  check("u sizes things by space on disk", D.modes().disk && D.at(CONFIG).r > D.at(MAIN).r * 3,
        `${D.at(CONFIG).r} vs ${D.at(MAIN).r}`);
  check("a total that is still being counted says so",
        el("d-disk").children.some((c) => (c.textContent || "").startsWith("≥")));
  await settle(1100);
  check("and keeps asking until it is done", duCalls > 3, String(duCalls));
  D.select(SRC);
  await settle(60);
  check("a folder's reader says where its space goes",
        el("p-body").querySelectorAll(".du-row").length === 3);
  D.key("u");
  await settle();
  check("u again puts the sizes back", !D.modes().disk && D.at(CONFIG).r === before.config);

  done("modes");
})();
