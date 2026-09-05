/* Backlinks in the reader, driven through the real cortex/ui/app.js.
 *
 * What it guards: the panel must list every semantic edge touching the
 * selected file -- including edges whose other end has not been grown into
 * the graph yet, which is the whole point of showing them. Following one has
 * to reach that file.
 *
 * Run: node tests/connections.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/vault";
const INDEX = `${ROOT}/notes/index.md`;
const DEEP = `${ROOT}/notes/archive/deep.md`;      // never auto-expanded
const OTHER = `${ROOT}/notes/other.md`;
const MAIN = `${ROOT}/app/main.py`;
const UTIL = `${ROOT}/app/util.py`;
const LONELY = `${ROOT}/notes/lonely.md`;

function childrenOf(p) {
  const kids = {
    [ROOT]: [[`${ROOT}/notes`, true], [`${ROOT}/app`, true]],
    [`${ROOT}/notes`]: [[`${ROOT}/notes/archive`, true], [INDEX, false],
                        [OTHER, false], [LONELY, false]],
    [`${ROOT}/notes/archive`]: [[DEEP, false]],
    [`${ROOT}/app`]: [[MAIN, false], [UTIL, false]],
  }[p] || [];
  return kids.map(([id, dir]) => fsnode(id, dir, 2, ROOT));
}

/* Stored source-first and deduplicated by unordered pair, exactly as the
   python index emits them. */
const EDGES = [
  [INDEX, DEEP, "note"],
  [OTHER, INDEX, "note"],
  [MAIN, UTIL, "code"],
];

const { pump, boot, el } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, 2, ROOT),
  links: () => ({ ready: true, edges: EDGES, notes: 4, sources: 2, elapsed: 0.1 }),
  autoExpand: { depth: 1, budget: 400 },   // leaves archive/ unopened
});

const { check, done } = reporter();

const box = () => el("p-links");
const head = () => box().children[0];
const rows = () => (box().children[1] || { children: [] }).children;
const names = () => [...rows()].map((r) => r.children[1].textContent);
const arrows = () => [...rows()].map((r) => r.children[0].textContent);

(async () => {
  const D = await boot();

  check("the graph loaded", D().nodes > 4, `nodes=${D().nodes}`);
  check("a file with no links shows no panel",
        (D.select(LONELY), box().hidden === true));

  // --- both directions -----------------------------------------------------
  D.select(INDEX);
  check("a linked file shows its connections", box().hidden === false);
  check("both directions are listed", names().length === 2, names().join(", "));
  check("the outgoing link is there", names().includes("deep.md"), names().join(", "));
  check("the incoming link is there", names().includes("other.md"), names().join(", "));
  check("outgoing is drawn first and pointing out", arrows()[0] === "→");
  check("incoming points in", arrows()[1] === "←");
  check("the count is stated", /2 connections/.test(head().children[1].textContent),
        head().children[1].textContent);
  check("the split is stated", /1 out . 1 in/.test(head().children[2].textContent),
        head().children[2].textContent);

  // --- the point of the feature -------------------------------------------
  const drawn = D().nodes;
  check("the far end is genuinely not in the graph yet", D.select(DEEP) === false);

  D.select(INDEX);
  const target = [...rows()].find((r) => r.children[1].textContent === "deep.md");
  target.click();
  await new Promise((r) => setTimeout(r, 80));
  pump(2);
  check("following a connection grows that file into the graph and selects it",
        D().selected === DEEP, `selected=${D().selected} nodes ${drawn}->${D().nodes}`);

  // --- kinds and direction -------------------------------------------------
  D.select(DEEP);
  check("a file that is only linked to shows one incoming edge",
        names().length === 1 && arrows()[0] === "←", arrows().join(""));

  D.select(UTIL);
  check("code edges are found too", names().length === 1 && names()[0] === "main.py",
        names().join(", "));
  check("a code edge is styled as code",
        rows()[0].classList.contains("code") && !rows()[0].classList.contains("note"));

  D.select(INDEX);
  const noteRow = [...rows()].find((r) => r.children[1].textContent === "deep.md");
  check("a note edge is styled as a note", noteRow.classList.contains("note"));
  check("the row names the folder it is in",
        noteRow.children[2].textContent === "notes/archive/",
        noteRow.children[2].textContent);
  check("the row carries the full path for its tooltip", noteRow.title === DEEP);

  // --- collapsing ----------------------------------------------------------
  head().click();
  check("the header collapses the list", box().classList.contains("closed"));
  check("a collapsed list renders no rows", box().children.length === 1);
  head().click();
  check("and expands it again",
        !box().classList.contains("closed") && names().length === 2);

  // --- closing the reader --------------------------------------------------
  el("p-close").click();
  check("closing the reader hides the connections", box().hidden === true);

  done("connections");
})();
