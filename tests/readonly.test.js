/* A read-only cortex, and one whose server has gone, driven through the real
 * cortex/ui/app.js.
 *
 * What it guards: --read-only takes every way of changing a file out of the
 * window rather than leaving buttons that fail; and when the program behind
 * the window stops, the window says so once and plainly, instead of failing
 * quietly at whatever is clicked next.
 *
 * Run: node tests/readonly.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const A = `${ROOT}/a.md`;

const { boot, pump, el, sent, net } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: (p) => p === ROOT ? [fsnode(A, false, 0, ROOT)] : [],
  autoExpand: { depth: 1, budget: 100 },
  config: { readOnly: true },
  api: {
    "/api/preview": () => ({ kind: "markdown", text: "# a", ext: ".md" }),
  },
});

const { check, done } = reporter();
const settle = async (ms = 40) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };

(async () => {
  const D = await boot();

  check("the corner says read-only", el("stat-ro").hidden === false);

  D.select(A);
  await settle();
  check("there is no edit button",
        !el("p-acts").children.some((c) => c.textContent === "edit"));
  D.key("i");
  await settle();
  check("and i does not open an editor", !el("p-body").querySelector("textarea"));
  check("nor ask the server for the source",
        !sent.some((s) => s.url.includes("/api/source")));

  D.key("n");
  D.key("F2");
  D.key("Delete");
  await settle();
  check("new, rename and trash do nothing", !el("ask").classList.contains("open"));

  const { cam } = D();
  const n = D.at(A);
  el("stage").dispatchEvent({ type: "contextmenu", clientX: n.x * cam.s + cam.x,
                              clientY: n.y * cam.s + cam.y, preventDefault() {} });
  const items = el("ctx").children.filter((c) => c.className.startsWith("ctx-item"))
    .map((c) => c.children[0].textContent);
  check("right-click offers nothing that writes",
        !items.some((l) => /Edit|Rename|trash|New/.test(l)) && items.includes("Copy path"),
        items.join("|"));

  // --- the server goes away ------------------------------------------------
  check("nothing is wrong yet", !el("gone").classList.contains("open"));
  net.down = true;
  await D.pulse().catch(() => {});
  check("one failed request is not yet an outage", !el("gone").classList.contains("open"));
  await D.pulse().catch(() => {});
  await D.pulse().catch(() => {});
  await settle();
  check("a second one is, and the window says so",
        el("gone").classList.contains("open"));
  check("telling you to start it again from the terminal it came from",
        el("gone-text").textContent.includes("Start it again with cortex"));
  const before = sent.length;
  await D.pulse();
  check("after which it stops asking", sent.length === before);

  done("read-only");
})();
