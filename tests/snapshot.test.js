/* A shared map, driven through the real cortex/ui/app.js and the real
 * cortex/ui/snapshot.js: the window, with no server behind it, answering from
 * data frozen into the page.
 *
 * Run: node tests/snapshot.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`, A = `${NOTES}/a.md`, B = `${NOTES}/b.md`;
const root = fsnode(ROOT, true, 1, ROOT);

const h = harness({
  root: ROOT,
  preload: ["snapshot.js"],
  config: {
    token: "snapshot", title: "proj", readOnly: true, terminal: null, canQuit: false,
    autoExpand: { depth: 0, budget: 0 }, pulseMs: 0, rememberLayout: true,
    snapshot: { taken: "9 October 2026", version: "0.1.0", contents: true, files: 2 },
    view: { name: "shared", scope: ROOT, hidden: [], solo: null, semantic: true,
            labels: true, expanded: [NOTES], selected: null, cam: { s: 1, x: 800, y: 450 } },
  },
});
// what snapshot.py writes into the page
global.window.SNAPSHOT = {
  root,
  children: { [ROOT]: [fsnode(NOTES, true, 2, ROOT)],
              [NOTES]: [fsnode(A, false, 0, ROOT), fsnode(B, false, 0, ROOT)] },
  previews: { [A]: { kind: "markdown", text: "# A\n\nlinks to [[b]]", ext: ".md" },
              [B]: { kind: "markdown", text: "# B\n\nthe budget", ext: ".md" } },
  raw: {},
  links: { ready: true, edges: [[A, B, "note"]], suggest: [], ghosts: {}, notes: 2, sources: 0 },
  git: { repo: null, branch: null, states: {} },
  layout: { positions: { [A]: [10, 20], [B]: [60, 20] }, cam: { s: 1, x: 800, y: 450 } },
  tours: [], tourText: {},
};
global.window.fetch = global.fetch;      // what the shim falls back to
global.Response = global.Response || class {
  constructor(body, init) { this.body = body; this.ok = (init || {}).status !== 404; this.status = 200; }
  async json() { return JSON.parse(this.body); }
};

const { check, done } = reporter();
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); h.pump(2); };

(async () => {
  const D = await h.boot();
  await settle(120);

  check("the map opens from the frozen data, folders as they were shared",
        D.select(A) === true && D.select(B) === true);
  check("where they sat", D.at(A).x === 10 && D.at(B).x === 60);
  check("it says it is a snapshot, and when it was taken",
        h.el("place-name").textContent === "proj · snapshot"
        && h.el("btn-places").title.includes("9 October 2026"));
  check("the modes that need cortex running are not offered",
        h.el("d-live").hidden && h.el("d-history").hidden && h.el("d-disk").hidden
        && h.el("d-today").hidden);

  D.select(A);
  await settle();
  check("the reader reads from the snapshot",
        h.el("p-body").querySelector(".md").innerHTML.includes("links to"));
  const labels = h.el("p-acts").children.map((c) => c.textContent);
  check("and offers nothing that would change a file, or open one elsewhere",
        !labels.some((l) => /edit|open|file|shell|terminal/.test(l)), labels.join("|"));
  D.key("i");
  D.key("n");
  await settle();
  check("the keys that write do nothing", !h.el("p-body").querySelector("textarea")
        && !h.el("ask").classList.contains("open"));

  await h.typeSearch("bud");
  h.el("mode").click();          // search inside files, answered by the shim
  await settle(400);
  check("searching inside files still works, from what was shared",
        h.el("hits").children.some((c) => (c.title || "").startsWith(B)),
        h.el("hits").children.map((c) => c.title).join(","));

  done("snapshot");
})();
