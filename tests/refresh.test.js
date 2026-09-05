/* Live refresh, driven through the real cortex/ui/app.js.
 *
 * What it guards: when the server says a folder changed, that folder is
 * re-listed and reconciled in place. A new file appears, a deleted one leaves
 * along with anything under it, and a file that merely grew keeps its node
 * rather than being torn down and rebuilt somewhere else on screen.
 *
 * Run: node tests/refresh.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const NOTES = `${ROOT}/notes`;
const KEEP = `${NOTES}/keep.md`;
const DOOMED = `${NOTES}/doomed`;
const UNDER = `${DOOMED}/inner.md`;
const FRESH = `${NOTES}/fresh.md`;

/* The disk, as the test rearranges it between pulses. */
const disk = {
  [ROOT]: [[NOTES, true]],
  [NOTES]: [[KEEP, false], [DOOMED, true]],
  [DOOMED]: [[UNDER, false]],
};
let keepSize = 4096;

function childrenOf(p) {
  return (disk[p] || []).map(([id, dir]) => {
    const n = fsnode(id, dir, 1, ROOT);
    if (id === KEEP) n.size = keepSize;
    return n;
  });
}

let changed = [];
const { pump, boot, el, sent } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  pulse: () => ({ changed: changed.splice(0), reindexed: false }),
  autoExpand: { depth: 3, budget: 300 },
});

const { check, done } = reporter();

/* The app polls on a timer; fire one round by hand instead of waiting. */
async function pulseNow(paths) {
  changed = paths;
  const before = sent.length;
  await new Promise((r) => setTimeout(r, 10));
  // drive the same code path the timer would
  await global.window.CORTEX.debug.pulse();
  await new Promise((r) => setTimeout(r, 60));
  pump(2);
  return sent.length - before;
}

(async () => {
  const D = await boot();

  check("the tree loaded", D.select(KEEP) && D().selected === KEEP);
  check("the doomed folder's contents are in", D.select(UNDER) === true);

  // --- a file appears ------------------------------------------------------
  disk[NOTES] = [[KEEP, false], [DOOMED, true], [FRESH, false]];
  await pulseNow([NOTES]);
  check("a new file appears without a relaunch", D.select(FRESH) === true,
        `selected=${D().selected}`);

  // --- a folder disappears -------------------------------------------------
  const had = D().nodes;
  disk[NOTES] = [[KEEP, false], [FRESH, false]];
  delete disk[DOOMED];
  await pulseNow([NOTES]);
  check("a deleted folder leaves the graph", D.select(DOOMED) === false);
  check("and takes what was under it", D.select(UNDER) === false);
  check("nothing else went with it", D.select(KEEP) === true,
        `${had} nodes before, ${D().nodes} after`);

  // --- a file that only changed keeps its node -----------------------------
  D.select(KEEP);
  keepSize = 999999;
  await pulseNow([NOTES]);
  check("a file that grew keeps its node and its selection",
        D().selected === KEEP);

  // --- quiet pulses do nothing ---------------------------------------------
  const quiet = await pulseNow([]);
  check("a pulse with no changes asks for nothing else", quiet === 1,
        `${quiet} requests`);

  // --- a folder that is shut is not re-listed ------------------------------
  const shutBefore = sent.length;
  await pulseNow([`${ROOT}/never-opened`]);
  const asked = sent.slice(shutBefore).filter((s) => s.url.includes("/api/children"));
  check("a folder that is not open is not re-listed", asked.length === 0,
        `${asked.length} calls`);

  done("refresh");
})();
