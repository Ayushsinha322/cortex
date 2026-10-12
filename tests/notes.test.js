/* Writing, driven through the real cortex/ui/app.js: notes not written yet,
 * notes that ought to link, today's note, and sharing the map.
 *
 * Run: node tests/notes.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`, DAILY = `${ROOT}/daily`;
const A = `${NOTES}/a.md`, B = `${NOTES}/b.md`, C = `${NOTES}/c.md`;
const ROADMAP = `${NOTES}/Roadmap.md`, TODAY = `${DAILY}/2026-10-09.md`;
const ODD = `${NOTES}/sub/r(1) [x].md`;
let stale = false;
const GHOST = "ghost:roadmap";

const tree = { [ROOT]: [NOTES], [NOTES]: [A, B, C] };
const isDir = (p) => p in tree;
const childrenOf = (p) => (tree[p] || []).map((id) =>
  fsnode(id, isDir(id), (tree[id] || []).length, ROOT));
const lineage = (p) => {
  const ancestors = [];
  let cur = ROOT;
  for (const part of p.slice(ROOT.length + 1).split("/").slice(0, -1)) {
    cur += "/" + part;
    ancestors.push(fsnode(cur, true, 1, ROOT));
  }
  return { node: fsnode(p, false, 0, ROOT), ancestors };
};

const disk = { [A]: "# A\n\nsee [[Roadmap]] and [[b]]\n", [B]: "# B\n\nbudget forecast\n",
               [C]: "# C\n\nbudget forecast quarter\n" };
const calls = [];

const { boot, pump, el, downloads } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: childrenOf,
  autoExpand: { depth: 3, budget: 300 },
  links: () => ({
    ready: true, notes: 3, sources: 0,
    edges: [[A, B, "note"], [A, GHOST, "ghost"]],
    ghosts: { [GHOST]: "Roadmap" },
    suggest: [[B, C, 0.42, ["budget", "forecast"]], [A, C, 0.21, ["quarter"]],
              [C, ODD, 0.3, ["odd"]]],
  }),
  api: {
    "/api/preview": ({ path }) => ({ kind: "markdown", text: disk[path] || "", ext: ".md" }),
    "/api/source": ({ path }) => ({ ok: true, text: disk[path] || "# new\n\n",
                                    version: "v1", writable: true }),
    "/api/save": (_q, body) => { calls.push(["save", body]); disk[body.path] = body.text;
                                 return { ok: true, version: "v2", size: body.text.length }; },
    "/api/fs": (_q, body) => {
      calls.push(["fs", body]);
      tree[NOTES].push(ROADMAP);
      disk[ROADMAP] = "# Roadmap\n\n";
      return { ok: true, path: ROADMAP };
    },
    "/api/dismiss": (_q, body) => { calls.push(["dismiss", body]); return { ok: true }; },
    "/api/daily": () => {
      calls.push(["daily"]);
      tree[ROOT] = [NOTES, DAILY];
      tree[DAILY] = [TODAY];
      disk[TODAY] = "# Friday, 9 October 2026\n\n";
      return { ok: true, path: TODAY, created: true };
    },
    "/api/nodes": (_q, body) => ({ ok: true, hits: body.paths.map(lineage) }),
    "/api/snapshot": (_q, body) => { calls.push(["snapshot", body]); return "<html>"; },
    "/api/pulse": () => (stale ? { __status: 403, error: "bad token" }
                               : { changed: [], reindexed: false, root: ROOT }),
  },
});

const { check, done } = reporter();
const settle = async (ms = 40) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const last = (kind) => [...calls].reverse().find((c) => c[0] === kind);
const button = (root, text) => root.querySelectorAll("button").find((b) =>
  (b.textContent || "").startsWith(text)) || null;

(async () => {
  const D = await boot();
  await settle(60);

  // --- ghosts ---------------------------------------------------------------
  const g = D.at(GHOST);
  check("a link to a note not written yet is a ghost on the graph", g && g.ghost === true);
  D.select(GHOST);
  await settle();
  check("selected, it says what it is", el("p-kind").textContent === "unwritten");
  const make = button(el("p-body"), "create it in");
  check("and offers to make it beside the note that mentions it",
        make && make.textContent.endsWith("~/notes"), make && make.textContent);

  D.select(A);
  await settle();
  const html = el("p-body").querySelector(".md").innerHTML;
  check("in the note, its link is drawn as a link to nothing yet",
        html.includes('class="wiki dead" data-wiki="Roadmap"')
        && html.includes('class="wiki" data-wiki="b"'), html);

  D.select(GHOST);
  await settle();
  button(el("p-body"), "create it in").click();
  await settle(120);
  const made = last("fs");
  check("making it asks for the note, folders and all",
        made && made[1].op === "new" && made[1].name === "Roadmap" && made[1].parents === true
        && made[1].path === NOTES, JSON.stringify(made));
  check("the ghost leaves", D.at(GHOST) === undefined);
  check("and the new note opens, ready to write in",
        D().selected === ROADMAP && !!el("p-body").querySelector("textarea"));
  D.key("Escape");

  // --- suggested links --------------------------------------------------------
  D.select(B);
  await settle();
  const box = el("p-links");
  const sub = box.children.find((c) => c.className === "conn-sub");
  check("a note lists the notes it ought to link to", !!sub);
  const lists = box.children.filter((c) => c.className === "conn-list");
  const row = lists[lists.length - 1].children[0];
  check("with the words they share", row.children[2].textContent === "budget, forecast",
        row.children[2].textContent);
  button(row, "link").click();
  await settle(80);
  const saved = last("save");
  check("linking writes a plain relative link into the note",
        saved && saved[1].path === B && saved[1].text.endsWith("See also [c](c.md)\n")
        && saved[1].version === "v1", saved && JSON.stringify(saved[1].text));
  check("and the suggestion is done with",
        !el("p-links").querySelectorAll(".conn").some((c) => c.className.includes("suggest")));

  D.select(A);
  await settle();
  const aRows = el("p-links").querySelectorAll(".conn").filter((c) => c.className.includes("suggest"));
  check("another note has its own suggestion", aRows.length === 1);
  button(aRows[0], "✕").click();
  await settle();
  const dis = last("dismiss");
  check("dismissing it is remembered", dis && dis[1].a === A && dis[1].b === C);

  // --- a name with brackets and parentheses ----------------------------------------
  D.select(C);
  await settle();
  const odd = el("p-links").querySelectorAll(".conn")
    .filter((c) => c.className.includes("suggest") && c.children[1].textContent === "r(1) [x].md");
  button(odd[0], "link").click();
  await settle(80);
  const oddSave = last("save");
  check("a link to an awkward name is still one link",
        oddSave && oddSave[1].text.endsWith("See also [r(1) x](sub/r%281%29%20%5Bx%5D.md)\n"),
        oddSave && JSON.stringify(oddSave[1].text));

  // --- today's note -------------------------------------------------------------
  D.key("d");
  await settle(150);
  check("d opens today's note, made if need be", last("daily") && D().selected === TODAY,
        D().selected);
  const area = el("p-body").querySelector("textarea");
  check("ready to write at the end", area && area.selectionStart === area.value.length);
  D.key("Escape");

  // --- share --------------------------------------------------------------------
  el("btn-save").click();
  el("save-menu").children.find((b) => b.children[1]
    && b.children[1].textContent.startsWith("Interactive map")).click();
  await settle();
  check("sharing says what will be in the file first", el("share").classList.contains("open")
        && el("share-contents-label").textContent.includes("file"));
  check("and leaves the text out unless asked", el("share-contents").checked === false);
  el("share-ok").click();
  await settle(80);
  const snap = last("snapshot");
  check("the file is asked for with the view as it stands",
        snap && snap[1].contents === false && snap[1].files.includes(A)
        && snap[1].positions[A] && snap[1].view && snap[1].root === ROOT,
        snap && JSON.stringify(Object.keys(snap[1])));
  check("and handed over as a download",
        downloads.some((d) => d.name.endsWith("-map.html")),
        downloads.map((d) => d.name).join(","));

  // --- cortex restarted behind the window -----------------------------------------
  stale = true;
  await D.pulse();
  await settle();
  check("a window whose cortex was restarted says it has stopped, once",
        el("gone").classList.contains("open"));

  done("notes");
})();
