/* Working without a terminal, driven through the real cortex/ui/app.js.
 *
 * What it guards: a note can be made, renamed and thrown away from the graph;
 * right-click offers what can be done to a node; another folder can be opened
 * from the window; the page tells the server when it opens and closes; and a
 * cortex started from an app menu labels terminal programs as opening in a
 * window of their own, and does not offer to page a file in a terminal that
 * does not exist.
 *
 * Run: node tests/files.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`;
const A = `${NOTES}/a.md`;
const B = `${NOTES}/b.md`;
const OTHER = "/home/u/elsewhere";

const tree = {
  [ROOT]: [NOTES],
  [NOTES]: [A, B],
};
const isDir = (p) => p in tree;
const childrenOf = (p) => (tree[p] || []).map((id) => fsnode(id, isDir(id), (tree[id] || []).length, ROOT));

const ops = [];
let failNext = false;          // the next file operation is refused
const opens = [];
const projects = [];
const launchers = [];
const places = () => ({
  root: ROOT, home: "/home/u", parent: "/home/u", canSwitch: true,
  projects: [{ name: "work", path: "/home/u/work", missing: false },
             { name: "old", path: "/home/u/old", missing: true }],
  recent: [OTHER],
  launcher: { supported: true, installed: false, where: "~/.local/share/applications/cortex.desktop" },
});

const { boot, pump, el, sent, press, type, reloads, beacons, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  config: { terminal: "window", canQuit: true, home: "/home/u", title: "~/proj" },
  api: {
    "/api/preview": ({ path }) => ({ kind: "markdown", text: `# ${path}`, ext: ".md" }),
    "/api/source": ({ path }) => ({ ok: true, text: "# new\n\n", version: "1:1", writable: true }),
    "/api/fs": (_q, body) => {
      ops.push(body);
      if (failNext) { failNext = false; return { ok: false, error: "disk full" }; }
      const dir = body.path;
      if (body.op === "new" || body.op === "mkdir") {
        const name = body.op === "new" && !body.name.includes(".") ? body.name + ".md" : body.name;
        const made = `${dir}/${name}`;
        tree[dir].push(made);
        if (body.op === "mkdir") tree[made] = [];
        return { ok: true, path: made };
      }
      const parent = body.path.slice(0, body.path.lastIndexOf("/"));
      tree[parent] = tree[parent].filter((p) => p !== body.path);
      if (body.op === "rename") {
        const moved = `${parent}/${body.name}`;
        tree[parent].push(moved);
        return { ok: true, path: moved };
      }
      return { ok: true };
    },
    "/api/places": places,
    "/api/dirs": ({ path }) => ({ dirs: path.startsWith("/home/u/el") ? [OTHER] : [] }),
    "/api/open": (_q, body) => { opens.push(body.path); return { ok: true, root: body.path }; },
    "/api/project": (_q, body) => { projects.push(body); return { ok: true, ...places() }; },
    "/api/launcher": (_q, body) => {
      launchers.push(body.op);
      return { ok: true, launcher: { supported: true, installed: body.op === "install" } };
    },
    "/api/hello": () => ({ ok: true }),
    "/api/quit": () => ({ ok: true }),
  },
});

const { check, done } = reporter();
const settle = async (ms = 40) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const askOpen = () => el("ask").classList.contains("open");
const answer = async (value) => {
  if (value !== undefined) el("ask-input").value = value;
  el("ask-ok").click();
  await settle(60);
};
const actsLabels = () => el("p-acts").children.map((c) => c.textContent);
const menuLabels = () => el("ctx").children
  .filter((c) => c.className.startsWith("ctx-item"))
  .map((c) => c.children[0].textContent);
const menuItem = (label) => el("ctx").children
  .find((c) => c.className.startsWith("ctx-item") && c.children[0].textContent === label);

/* Right-click where a node is drawn, the way the mouse would. */
function rightClick(D, id) {
  const { cam } = D();
  let x = 5000, y = 5000;
  if (id) {
    const n = D.at(id);
    x = n.x * cam.s + cam.x;
    y = n.y * cam.s + cam.y;
  }
  el("stage").dispatchEvent({ type: "contextmenu", clientX: x, clientY: y,
                              preventDefault() {} });
}

(async () => {
  const D = await boot();

  // --- the page says hello -------------------------------------------------
  check("the page says hello when it opens",
        sent.some((s) => s.url.includes("/api/hello") && s.url.includes("c=")));
  check("the folder button names the folder", el("place-name").textContent === "proj",
        el("place-name").textContent);
  check("with the whole path in its tooltip", el("btn-places").title.startsWith("~/proj"));

  // --- labels when there is no terminal of our own -------------------------
  D.select(A);
  await settle();
  const labels = actsLabels();
  check("no paging in a terminal that does not exist", !labels.includes("read"),
        labels.join("|"));
  check("a terminal is offered as a window of its own", labels.includes("terminal here"));
  const primary = el("p-acts").children.find((c) => c.className === "primary");
  check("the editor button says where it will open",
        primary && primary.title.includes("in a terminal window"), primary && primary.title);

  // --- a new note ----------------------------------------------------------
  D.select(NOTES);
  D.key("n");
  await settle();
  check("n asks for a name", askOpen());
  check("offering untitled.md with the name part selected",
        el("ask-input").value === "untitled.md" && el("ask-input").selectionEnd === 8,
        `${el("ask-input").value} ${el("ask-input").selectionStart}-${el("ask-input").selectionEnd}`);
  await answer("ideas");
  check("creating it asks the server for a note in that folder",
        ops[0] && ops[0].op === "new" && ops[0].path === NOTES && ops[0].name === "ideas",
        JSON.stringify(ops[0]));
  check("the new note is selected", D().selected === `${NOTES}/ideas.md`, D().selected);
  check("and open for writing", !!el("p-body").querySelector("textarea"));

  // --- an empty name is not an answer --------------------------------------
  D.key("Escape");               // out of the editor's selection: the key goes to the graph
  D.select(NOTES);
  D.key("n");
  await settle();
  el("ask-input").value = "   ";
  el("ask-ok").click();
  check("an empty name keeps the question open", askOpen()
        && el("ask-error").hidden === false);
  el("ask-cancel").click();
  await settle();
  check("cancel closes it and makes nothing", !askOpen() && ops.length === 1);

  // --- rename --------------------------------------------------------------
  D.select(B);
  D.key("F2");
  await settle();
  check("F2 asks for the new name, with the old one selected up to its extension",
        askOpen() && el("ask-input").value === "b.md"
        && el("ask-input").selectionStart === 0 && el("ask-input").selectionEnd === 1);
  await answer("beta.md");
  check("renaming asks the server to rename that file",
        ops[1].op === "rename" && ops[1].path === B && ops[1].name === "beta.md",
        JSON.stringify(ops[1]));
  check("and the renamed file is the one selected", D().selected === `${NOTES}/beta.md`,
        D().selected);
  check("the old name has left the graph", D.select(B) === false);

  // --- trash ---------------------------------------------------------------
  D.select(A);
  D.key("Delete");
  await settle();
  check("delete asks first, in red", askOpen() && el("ask-ok").classList.contains("danger"));
  el("ask-cancel").click();
  await settle();
  check("and does nothing when told no", ops.length === 2 && D.select(A) === true);

  D.key("Delete");
  await settle();
  await answer();
  check("told yes, it moves it to the trash", ops[2].op === "trash" && ops[2].path === A);
  check("and it leaves the graph", D.select(A) === false);

  // --- right-click ---------------------------------------------------------
  D.select(`${NOTES}/beta.md`);
  rightClick(D, `${NOTES}/beta.md`);
  const fileMenu = menuLabels();
  check("right-clicking a file offers what can be done to it",
        ["Edit here", "Rename…", "Move to trash", "Show in file manager", "Copy path"]
          .every((l) => fileMenu.includes(l)), fileMenu.join("|"));
  check("the menu is open", el("ctx").hidden === false);
  menuItem("Rename…").click();
  await settle();
  check("and an item does what it says", askOpen() && el("ask-input").value === "beta.md");
  el("ask-cancel").click();
  check("choosing closes the menu", el("ctx").hidden === true);

  rightClick(D, NOTES);
  const dirMenu = menuLabels();
  check("a folder offers new notes and folders inside it",
        dirMenu.includes("New note here…") && dirMenu.includes("New folder here…")
        && dirMenu.includes("Open a terminal here"), dirMenu.join("|"));

  rightClick(D, null);
  const bg = menuLabels();
  check("empty space offers a new note, and another folder",
        bg.some((l) => l.startsWith("New note in")) && bg.includes("Open another folder…"),
        bg.join("|"));

  // --- opening another folder ----------------------------------------------
  D.key("Escape");
  D.key("p");
  await settle();
  check("p opens the folder sheet", el("places").classList.contains("open"));
  const rows = el("pl-lists").querySelectorAll(".pl-row");
  check("with the saved projects and the recent folders",
        rows.length === 3, rows.map((r) => r.title).join("|"));
  const missing = rows.find((r) => r.title === "/home/u/old");
  check("a project whose folder is gone says so",
        missing.children.some((c) => c.textContent === "missing"));

  type(el("pl-path"), "/home/u/el");
  const tab = press(el("pl-path"), "Tab");    // at once, before any suggestion is back
  await settle(60);
  check("tab completes even when pressed before the suggestions arrive",
        tab.defaultPrevented && el("pl-path").value === "~/elsewhere/",
        el("pl-path").value);

  el("pl-path").value = "";
  type(el("pl-path"), "/home/u/el");
  await settle(200);
  const offered = el("pl-complete").querySelectorAll(".pl-row");
  check("typing a path offers the folders it could mean",
        el("pl-complete").hidden === false && offered.length === 1
        && offered[0]._path === OTHER);
  press(el("pl-path"), "Enter");
  await settle(60);
  check("enter on the start of the only folder it could be opens that folder",
        opens[0] === OTHER, JSON.stringify(opens));
  check("and the window reloads around the new folder", reloads.length === 1);

  // --- projects and the launcher -------------------------------------------
  el("pl-name").value = "proj";
  el("pl-save-btn").click();
  await settle();
  check("the folder can be saved as a project",
        projects[0] && projects[0].op === "save" && projects[0].name === "proj");

  const addButton = el("pl-launcher").children.find((c) => c.tagName === "BUTTON");
  check("the sheet offers to put cortex in the app menu",
        addButton && addButton.textContent === "add it to the app menu");
  addButton.click();
  await settle();
  check("and does it when asked", launchers[0] === "install");

  // --- unsaved work blocks a switch ----------------------------------------
  el("pl-close").click();
  D.select(`${NOTES}/beta.md`);
  D.key("i");
  await settle();
  type(el("p-body").querySelector("textarea"), "unsaved");
  rows[2].click();
  await settle();
  check("a folder is not switched away from under unsaved typing", opens.length === 1);
  check("it asks first", askOpen() && el("ask-ok").classList.contains("danger"));
  el("ask-cancel").click();
  await settle();
  check("and staying keeps the typing", el("stat-drafts").textContent === "1 unsaved");

  // --- a rename that fails -------------------------------------------------
  failNext = true;
  D.key("F2");
  await settle();
  await answer("nope.md");
  const ta = el("p-body").querySelector("textarea");
  check("a rename that fails leaves the editor open", !!ta);
  type(ta, " more");
  check("and still working", ta.value.includes("unsaved more")
        && el("stat-drafts").textContent === "1 unsaved");

  // --- trashing a folder with unsaved work in it ---------------------------
  D.select(NOTES);
  D.key("Delete");
  await settle();
  check("trashing a folder warns about unsaved changes inside it",
        askOpen() && el("ask-text").textContent.includes("1 file in it"),
        el("ask-text").textContent);
  el("ask-cancel").click();
  await settle();
  const leaving = fire("beforeunload");
  check("and closing the window asks first", leaving.defaultPrevented === true);

  // --- goodbye -------------------------------------------------------------
  fire("pagehide");
  check("closing the window says goodbye", beacons.length === 1
        && beacons[0].url.includes("/api/bye") && beacons[0].url.includes("c="),
        JSON.stringify(beacons));

  // --- quit ----------------------------------------------------------------
  el("pl-quit").click();
  await settle();
  check("quitting with unsaved changes asks first", askOpen());
  await answer();
  check("then quits", sent.some((s) => s.url.includes("/api/quit")));
  check("and says so where the graph was", el("gone").classList.contains("open")
        && el("gone-text").textContent.includes("You quit it"));

  done("file and folder");
})();
