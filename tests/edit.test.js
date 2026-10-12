/* Editing in the reader, driven through the real cortex/ui/app.js.
 *
 * What it guards: a file can be edited and saved without leaving the window;
 * a save carries the version it was based on and a conflict is put to the
 * person rather than settled behind their back; and typing is never lost --
 * not by moving to another file, not by the graph refreshing underneath, not
 * by leaving the editor.
 *
 * Run: node tests/edit.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`;
const A = `${NOTES}/a.md`;
const B = `${NOTES}/b.md`;
const PY = `${NOTES}/tool.py`;

const disk = {
  [A]: "# A\n\n- one\n",
  [B]: "# B\n",
  [PY]: "def f():\n    return 1\n",
};
const versions = { [A]: "1:10", [B]: "1:4", [PY]: "1:20" };
const saves = [];
let nextSave = null;           // a canned refusal for the next save
let hold = null;               // a promise the next save waits on, to answer late
let changed = [];

const tree = {
  [ROOT]: [fsnode(NOTES, true, 3, ROOT)],
  [NOTES]: [A, B, PY].map((p) => fsnode(p, false, 0, ROOT)),
};

const { boot, pump, el, press, type, sent } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: (p) => tree[p] || [],
  autoExpand: { depth: 3, budget: 300 },
  pulse: () => ({ changed: changed.splice(0), reindexed: false, root: ROOT }),
  api: {
    "/api/preview": ({ path }) => path.endsWith(".py")
      ? { kind: "text", lang: "python", text: disk[path], ext: ".py", size: 9 }
      : { kind: "markdown", text: disk[path], ext: ".md", size: 9 },
    "/api/source": ({ path }) => ({ ok: true, text: disk[path], version: versions[path],
                                     eol: "lf", bom: false, writable: true,
                                     mixed: path === PY }),
    "/api/save": async (_q, body) => {
      saves.push(body);
      if (hold) { const h = hold; hold = null; await h; }
      if (nextSave) { const a = nextSave; nextSave = null; return a; }
      disk[body.path] = body.text;
      versions[body.path] = `v${saves.length}`;
      return { ok: true, version: versions[body.path], size: body.text.length };
    },
  },
});

const { check, done } = reporter();
const settle = async (ms = 40) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const area = () => el("p-body").querySelector("textarea");
const button = (root, label) => root.querySelectorAll("button")
  .find((b) => b.textContent === label) || null;
const editButton = () => el("p-acts").children.find((c) => c.textContent === "edit");
const lastSave = () => saves[saves.length - 1];

(async () => {
  const D = await boot();

  // --- opening the editor --------------------------------------------------
  D.select(A);
  await settle();
  check("a note offers to be edited once it is known to be text",
        editButton() && editButton().hidden === false);

  D.key("i");
  await settle();
  check("i opens it for editing, with the whole file",
        area() && area().value === disk[A], area() && JSON.stringify(area().value));
  check("the panel knows it is editing", el("panel").classList.contains("editing"));
  check("the source was asked for, not the preview",
        sent.some((s) => s.url.includes("/api/source")));

  // --- typing, then saving -------------------------------------------------
  area().setSelectionRange(area().value.length, area().value.length);
  type(area(), "- two\n");
  const bar = el("p-body").querySelector(".ed-bar");
  check("typing marks it unsaved", bar.querySelector(".ed-state").textContent === "● unsaved");
  check("and says so in the corner", el("stat-drafts").hidden === false
        && el("stat-drafts").textContent === "1 unsaved");

  press(area(), "s", { ctrlKey: true });
  await settle();
  check("ctrl-s saves what was typed",
        lastSave() && lastSave().text === "# A\n\n- one\n- two\n", JSON.stringify(lastSave()));
  check("with the version it was based on",
        lastSave().version === "1:10" && lastSave().force === false);
  check("after which it is no longer unsaved",
        bar.querySelector(".ed-state").textContent === "editing"
        && el("stat-drafts").hidden === true);

  press(area(), "s", { metaKey: true });
  await settle();
  check("saving again with nothing changed writes nothing", saves.length === 1, saves.length);

  // --- a conflict is asked about, not settled ------------------------------
  type(area(), "mine\n");
  nextSave = { ok: false, conflict: true, version: "theirs", error: "changed" };
  press(area(), "s", { ctrlKey: true });
  await settle();
  const notice = el("p-body").querySelector(".ed-notice");
  check("a conflict is shown, not overwritten",
        notice.hidden === false && button(notice, "keep mine") && button(notice, "take theirs"));
  check("and the typing is still there", area().value.endsWith("mine\n"));
  button(notice, "keep mine").click();
  await settle();
  check("keeping mine overwrites on purpose", lastSave().force === true
        && lastSave().text.endsWith("mine\n"));
  check("and the notice goes away", notice.hidden === true);

  // --- a draft survives moving away ----------------------------------------
  type(area(), "draft\n");
  D.select(B);
  await settle();
  check("selecting another file leaves the editor", !el("panel").classList.contains("editing"));
  check("but keeps the draft", el("stat-drafts").textContent === "1 unsaved");
  D.select(A);
  await settle();
  check("coming back opens straight into the draft",
        area() && area().value.endsWith("draft\n"), area() && area().value);
  const back = el("p-body").querySelector(".ed-notice");
  check("and says the changes are back", back.hidden === false
        && back.children[0].textContent.includes("unsaved changes are back"),
        back.children.map((c) => c.textContent).join("|"));

  // --- the graph refreshing does not wipe the editor -----------------------
  changed = [NOTES];
  await D.pulse();
  await settle();
  check("a refresh of the folder leaves the editor alone",
        area() && area().value.endsWith("draft\n"));

  // --- escape leaves, keeping the draft ------------------------------------
  press(area(), "Escape");
  await settle();
  check("esc leaves the editor", !el("panel").classList.contains("editing"));
  check("with the draft still kept", el("stat-drafts").textContent === "1 unsaved");
  changed = [NOTES];
  await D.pulse();
  await settle();
  check("and a refresh does not reopen what was closed on purpose",
        !el("p-body").querySelector("textarea"));
  D.select(B);
  await settle();
  D.select(A);
  await settle();
  check("but coming back to it does", !!el("p-body").querySelector("textarea"));
  press(area(), "Escape");
  await settle();

  // --- revert --------------------------------------------------------------
  D.key("i");
  await settle();
  button(el("p-body"), "revert").click();
  check("revert puts back what is on disk", area().value === disk[A]);
  check("and drops the draft", el("stat-drafts").hidden === true);

  // --- lists carry on ------------------------------------------------------
  const ta = area();
  const enter = (value) => {
    ta.value = value;
    ta.setSelectionRange(value.length, value.length);
    const ev = press(ta, "Enter");
    return ev.defaultPrevented ? ta.value : "(not handled)";
  };
  check("enter on a list item starts the next one", enter("- one") === "- one\n- ");
  check("enter on an empty item ends the list", enter("- one\n- ") === "- one\n");
  check("a numbered list counts on", enter("1. first") === "1. first\n2. ");
  check("a ticked task starts an unticked one", enter("- [x] done") === "- [x] done\n- [ ] ");
  check("an indented line keeps its indentation", enter("  nested") === "  nested\n  ");
  check("an ordinary line is left to the browser", enter("plain") === "(not handled)");

  // --- tab -----------------------------------------------------------------
  D.select(PY);
  await settle();
  D.key("i");
  await settle();
  check("a file with mixed line endings says what saving will do",
        el("p-body").querySelector(".ed-notice").children[0].textContent
          .includes("mixes line endings"));
  const py = area();
  py.value = "x";
  py.setSelectionRange(0, 0);
  press(py, "Tab");
  check("tab indents the way the file already does", py.value === "    x",
        JSON.stringify(py.value));
  py.value = "    a\n    b";
  py.setSelectionRange(0, py.value.length);
  press(py, "Tab", { shiftKey: true });
  check("shift-tab outdents every selected line", py.value === "a\nb",
        JSON.stringify(py.value));
  button(el("p-body"), "revert").click();

  // --- a file deleted by something else, mid-edit --------------------------
  D.select(B);
  await settle();
  D.key("i");
  await settle();
  type(area(), "unsaved words\n");
  const keep = tree[NOTES];
  tree[NOTES] = keep.filter((n) => n.id !== B);
  changed = [NOTES];
  await D.pulse();
  await settle();
  check("a file deleted underneath keeps its editor, and the typing",
        !!area() && area().value.includes("unsaved words"));
  const gone = el("p-body").querySelector(".ed-notice");
  check("and says it is no longer on the disk", gone.hidden === false
        && gone.children[0].textContent.includes("no longer on the disk"));
  button(gone, "save it again").click();
  await settle();
  check("saving it again writes it back, on purpose",
        lastSave().path === B && lastSave().force === true
        && lastSave().text.includes("unsaved words"));
  tree[NOTES] = keep;

  // --- a save that answers late --------------------------------------------
  D.select(A);
  await settle();
  D.key("i");
  await settle();
  let release;
  hold = new Promise((r) => { release = r; });
  type(area(), "first\n");
  press(area(), "s", { ctrlKey: true });
  type(area(), "second\n");
  press(area(), "Escape");
  release();
  await settle(80);
  check("typing after a save that was still on its way is not lost",
        el("stat-drafts").textContent === "1 unsaved", el("stat-drafts").textContent);
  D.select(PY);
  await settle();
  D.select(A);
  await settle();
  check("and comes back in the editor",
        area() && area().value.includes("first\nsecond\n"), area() && area().value);

  done("editing");
})();
