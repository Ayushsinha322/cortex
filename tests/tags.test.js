/* Tag nodes, driven through the real cortex/ui/app.js.
 *
 * What it guards: a tag becomes a node in the graph, two notes that never link
 * to each other meet at it, and it never pretends to be a file -- no editor,
 * no pager, no path to copy, because there is nothing on disk to open.
 *
 * Run: node tests/tags.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/vault";
const A = `${ROOT}/a.md`;
const B = `${ROOT}/b.md`;
const C = `${ROOT}/c.md`;
const WORK = "tag:work";
const SOLO = "tag:solo";

function childrenOf(p) {
  if (p !== ROOT) return [];
  return [A, B, C].map((id) => fsnode(id, false, 0, ROOT));
}

/* a.md and b.md share #work but never link to each other */
const EDGES = [
  [A, WORK, "tag"],
  [B, WORK, "tag"],
  [A, SOLO, "tag"],
  [A, C, "note"],
];

const { pump, boot, el } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, 3, ROOT),
  links: () => ({ ready: true, edges: EDGES, notes: 3, sources: 0,
                  tags: ["solo", "work"], elapsed: 0.1 }),
  autoExpand: { depth: 0, budget: 100 },
});

const { check, done } = reporter();

const conns = () => {
  const box = el("p-links");
  const list = box.children[1];
  return list ? list.children : [];
};
const connNames = () => [...conns()].map((r) => r.children[1].textContent);

(async () => {
  const D = await boot();

  check("a tag two notes share becomes a node", D.select(WORK) === true);
  check("a tag only one note carries is not drawn, being no meeting point",
        D.select(SOLO) === false);

  // --- the point of tags ---------------------------------------------------
  D.select(WORK);
  check("the tag is named with its hash", el("p-name").textContent === "#work",
        el("p-name").textContent);
  check("it is labelled a tag, not a file", el("p-kind").textContent === "tag");
  check("both notes meet at it", connNames().sort().join(",") === "a.md,b.md",
        connNames().join(","));

  D.select(A);
  check("a note lists every tag it carries, drawn or not",
        connNames().includes("#work") && connNames().includes("#solo"),
        connNames().join(","));
  check("and still lists the note it links to", connNames().includes("c.md"));

  // clicking one that is not drawn brings it in rather than refusing
  [...conns()].find((r) => r.children[1].textContent === "#solo").click();
  await new Promise((r) => setTimeout(r, 60));
  pump(2);
  check("clicking an undrawn tag brings it into the graph",
        D().selected === SOLO && D.select(SOLO) === true,
        `selected=${D().selected}`);

  // --- a tag is not a file -------------------------------------------------
  D.select(WORK);
  check("it says so rather than showing a path",
        el("p-path").textContent === "a tag, not a file",
        el("p-path").textContent);
  check("there is no size or date", el("p-meta").textContent === "",
        el("p-meta").textContent);
  const labels = el("p-acts").children.map((b) => b.textContent);
  check("it offers no editor, pager or shell",
        !labels.some((l) => /Neovim|read|shell|open|expand/.test(l)),
        labels.join(", "));
  check("focus is all it offers", labels.join(",") === "focus", labels.join(","));

  // --- its body lists what carries it --------------------------------------
  const body = el("p-body");
  check("the reader says how many notes carry it",
        body.children[0].textContent === "Carried by 2 notes.",
        body.children[0].textContent);
  const links = body.querySelectorAll(".wiki");
  check("and lists them", links.length === 2,
        links.map((l) => l.textContent).join(","));

  // --- following one back --------------------------------------------------
  links.find((l) => l.textContent === "b.md").click();
  await new Promise((r) => setTimeout(r, 60));
  pump(2);
  check("clicking a carrier selects that note", D().selected === B,
        `selected=${D().selected}`);

  // --- and back to the tag from the note -----------------------------------
  const tagRow = [...conns()].find((r) => r.children[1].textContent === "#work");
  tagRow.click();
  await new Promise((r) => setTimeout(r, 60));
  check("clicking a tag from a note selects the tag", D().selected === WORK);

  // --- the filter chip ------------------------------------------------------
  const shown = () => parseInt(el("stat-nodes").textContent, 10);
  const chip = el("filters").children.find((c) => c.innerHTML.includes("tags"));
  check("tags have a filter chip of their own", !!chip);

  const withAll = shown();
  const total = D().nodes;
  const tagCount = 2;                  // #work, and #solo once asked for

  // shift-click still hides one kind on its own
  chip.click({ shiftKey: true });
  pump(2);
  check("shift-clicking hides tags and leaves the rest",
        shown() === withAll - tagCount, `${withAll} shown -> ${shown()}`);
  check("but does not delete them", D().nodes === total,
        `${total} nodes -> ${D().nodes}`);
  chip.click({ shiftKey: true });
  pump(2);
  check("and they come back", shown() === withAll, `${shown()}`);

  // a plain click shows that kind and takes the rest off
  chip.click();
  pump(2);
  check("clicking shows only tags, and the folders they hang from",
        shown() === tagCount + 1, `${shown()} shown`);
  check("the chip says which kind is soloed", chip.classList.contains("solo"));
  chip.click();
  pump(2);
  check("clicking it again brings everything back", shown() === withAll,
        `${shown()}`);
  check("and the chip stops saying so", !chip.classList.contains("solo"));

  done("tags");
})();
