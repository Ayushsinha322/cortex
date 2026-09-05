/* The two searches, driven through the real cortex/ui/app.js.
 *
 * What it guards: the box switches between finding a file by name and finding
 * a line inside one, a content hit lists file, line and text, and following a
 * hit carries that line all the way to the editor command.
 *
 * Run: node tests/search.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/vault";
const PLAN = `${ROOT}/notes/plan.md`;
const OLD = `${ROOT}/archive/old.md`;          // in a folder left unopened

function childrenOf(p) {
  const kids = {
    [ROOT]: [[`${ROOT}/notes`, true], [`${ROOT}/archive`, true]],
    [`${ROOT}/notes`]: [[PLAN, false]],
    [`${ROOT}/archive`]: [[OLD, false]],
  }[p] || [];
  return kids.map(([id, dir]) => fsnode(id, dir, 1, ROOT));
}

const HITS = [
  { path: PLAN, line: 2, text: "the budget is fixed" },
  { path: OLD, line: 7, text: "an old budget note" },
];

/* What /api/search answers: a node plus the folders above it. */
const node = (id) => fsnode(id, false, 0, ROOT);
const NAMED = {
  plan: { results: [{ node: node(PLAN), ancestors: [fsnode(`${ROOT}/notes`, true, 1, ROOT)] }],
          count: 1 },
  md: { results: [
          { node: node(PLAN), ancestors: [fsnode(`${ROOT}/notes`, true, 1, ROOT)] },
          { node: node(OLD), ancestors: [fsnode(`${ROOT}/archive`, true, 1, ROOT)] },
        ], count: 3 },
};

const { pump, boot, el, sent, typeSearch, key } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, 2, ROOT),
  grep: (q) => (q === "budget"
    ? { hits: HITS, engine: "python", truncated: false }
    : { hits: [], engine: "python", truncated: false }),
  search: (q) => NAMED[q] || { results: [], count: 0 },
  autoExpand: { depth: 0, budget: 200 },  // leaves both folders shut
});

const { check, done } = reporter();

const hits = () => el("hits");
const rows = () => hits().children.filter((c) => c.classList.contains("hit"));
const lastPost = () => [...sent].reverse().find((s) => s.url.includes("/api/action"));
const notes = () => hits().children.filter((c) => c.className === "hits-note");
const marked = () => rows().findIndex((r) => r.classList.contains("on"));

(async () => {
  const D = await boot();
  const q = el("q");

  check("it starts on names", el("mode").textContent === "names",
        el("mode").textContent);
  check("the results list starts hidden", hits().hidden === true);

  // --- switching -----------------------------------------------------------
  el("mode").click();
  check("clicking the toggle switches to text",
        el("mode").textContent === "text" && el("mode").classList.contains("on"));
  check("the placeholder says what it now searches",
        q.placeholder === "search inside files", q.placeholder);

  el("mode").click();
  check("clicking again goes back to names", el("mode").textContent === "names");
  key(q, "Tab");
  check("tab in the box switches too", el("mode").textContent === "text");

  // --- a content search ----------------------------------------------------
  await typeSearch("budget");
  check("it asked the grep endpoint",
        sent.some((s) => s.url.includes("/api/grep") && s.url.includes("budget")));
  check("both hits are listed", rows().length === 2, `${rows().length} rows`);
  check("a row names the file and the line",
        rows()[0].children[0].textContent === "plan.md:2",
        rows()[0].children[0].textContent);
  check("a row shows the matching line",
        rows()[0].children[1].children.map((c) => c.textContent).join("") ===
          "the budget is fixed");
  check("the term is marked inside the line",
        rows()[0].children[1].children.some(
          (c) => c.tagName === "MARK" && c.textContent === "budget"));
  check("the row carries file and line for its tooltip",
        rows()[1].title === `${OLD}:7`, rows()[1].title);

  // --- following a hit -----------------------------------------------------
  check("the hit's folder is not open yet", D.select(OLD) === false);
  rows()[1].click();
  await new Promise((r) => setTimeout(r, 80));
  pump(2);
  check("following a hit grows that file in and selects it",
        D().selected === OLD, `selected=${D().selected}`);
  check("the list closes once you have followed one", hits().hidden === true);
  check("the reader says which line", el("p-meta").textContent.includes("line 7"),
        el("p-meta").textContent);

  // --- the line reaches the editor ----------------------------------------
  el("p-acts").children[0].click();
  await new Promise((r) => setTimeout(r, 20));
  check("the editor is asked to open at that line",
        lastPost() && lastPost().body.line === 7 && lastPost().body.kind === "edit",
        JSON.stringify(lastPost() && lastPost().body));

  // --- the line does not outlive the selection -----------------------------
  D.select(OLD);                                  // a plain select, not a hit
  el("p-acts").children[0].click();
  await new Promise((r) => setTimeout(r, 20));
  check("selecting a file the ordinary way forgets the line",
        lastPost().body.line === null || lastPost().body.line === undefined,
        JSON.stringify(lastPost().body));

  // --- nothing found -------------------------------------------------------
  await typeSearch("nothinglikethis");
  check("an empty result says so, rather than showing an empty box",
        hits().hidden === false && rows().length === 0
        && hits().children[0].textContent.includes("nothing contains"),
        hits().children.map((c) => c.textContent).join("|"));

  // --- closing -------------------------------------------------------------
  key(q, "Escape");
  check("escape closes the list", hits().hidden === true);
  await typeSearch("budget");
  check("it reopens on the next search", hits().hidden === false);
  el("mode").click();
  check("going back to names closes it", hits().hidden === true);

  // --- names answer with a list too, not just highlighted nodes ------------
  check("we are back on names", el("mode").textContent === "names",
        el("mode").textContent);

  await typeSearch("md");
  check("a name search lists what it found", rows().length === 2,
        `${rows().length} rows`);
  check("a row names the file", rows()[0].children[0].textContent === "plan.md",
        rows()[0].children[0].textContent);
  check("and says which folder it is in",
        rows()[0].children[1].textContent === "notes/",
        rows()[0].children[1].textContent);
  check("the folder is aligned as a path, not as prose",
        rows()[0].children[1].classList.contains("path"));
  check("it says how many it could not show here",
        notes().length === 1 && notes()[0].textContent.includes("1 more outside"),
        notes().map((n) => n.textContent).join("|"));

  // --- arrowing through it -------------------------------------------------
  check("nothing is highlighted before you move", marked() === -1);
  key(q, "ArrowDown");
  check("down highlights the first result", marked() === 0);
  key(q, "ArrowDown");
  check("and then the second", marked() === 1);
  key(q, "ArrowDown");
  check("it wraps round the end", marked() === 0);
  key(q, "ArrowUp");
  check("up wraps the other way", marked() === 1);
  check("only one is ever highlighted",
        rows().filter((r) => r.classList.contains("on")).length === 1);

  // --- enter goes there ----------------------------------------------------
  key(q, "Enter");
  await new Promise((r) => setTimeout(r, 80));
  pump(2);
  check("enter goes to the highlighted result", D().selected === OLD,
        `selected=${D().selected}`);
  check("and the list closes behind you", hits().hidden === true);
  check("focus leaves the box, so the graph's own keys work at once",
        q.blurs > 0 && q.focused === false, `blurs=${q.blurs}`);

  // --- enter without arrowing takes the first ------------------------------
  await typeSearch("plan");
  check("a single match is listed", rows().length === 1);
  key(q, "Enter");
  await new Promise((r) => setTimeout(r, 80));
  pump(2);
  check("enter with nothing highlighted takes the first result",
        D().selected === PLAN, `selected=${D().selected}`);

  // --- nothing found -------------------------------------------------------
  await typeSearch("zzzz");
  check("a name search with no matches says so",
        rows().length === 0 && notes().length === 1
        && notes()[0].textContent.includes("nothing called"),
        notes().map((n) => n.textContent).join("|"));

  done("search");
})();
