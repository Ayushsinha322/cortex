/* Tests the real cortex/ui/app.js render loop against a stub DOM.
 *
 * What it guards: the layout must cool to a stop and stop repainting. A
 * simulation that never settles jitters, and jitter reads as flicker -- labels
 * sit on the collision threshold and blink on and off.
 *
 * The stub itself lives in harness.js; see the note there on why this is not
 * driven through a real browser.
 *
 * Run: node tests/render-loop.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const DIRS = 14, FILES_PER_DIR = 14, ROOT_FILES = 18;

function childrenOf(p) {
  if (p === ROOT) {
    const out = [];
    for (let i = 0; i < DIRS; i++) {
      out.push(fsnode(`${ROOT}/dir${i}`, true, FILES_PER_DIR, ROOT));
    }
    for (let i = 0; i < ROOT_FILES; i++) {
      out.push(fsnode(`${ROOT}/file${i}.md`, false, 0, ROOT));
    }
    return out;
  }
  if (/\/dir\d+$/.test(p)) {
    const out = [];
    for (let i = 0; i < FILES_PER_DIR; i++) {
      out.push(fsnode(`${p}/mod${i}.py`, false, 0, ROOT));
    }
    return out;
  }
  return [];
}

const { pump, boot, el } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, DIRS + ROOT_FILES, ROOT),
});

const { check, done } = reporter();

(async () => {
  const D = await boot();
  check("app.js exposes its debug hook", typeof D === "function");

  const built = D();
  check("the root expanded into nodes", built.nodes > 20, `nodes=${built.nodes}`);
  check("tree links were created", built.links > 20, `links=${built.links}`);

  const samples = [];
  for (let i = 0; i < 12; i++) { pump(50); samples.push(D()); }
  const last = samples[samples.length - 1];
  const idleDraws = last.draws - samples[samples.length - 4].draws;

  console.log(`      alpha: ${samples.map((s) => s.alpha.toFixed(3)).join(" -> ")}`);
  console.log(`      frames=${last.frames} draws=${last.draws}`);

  check("the layout cools to a freeze", last.frozen, `alpha=${last.alpha}`);
  check("a frozen layout stops repainting", idleDraws === 0,
        `${idleDraws} draws in 150 idle frames`);
  check("it drew far fewer times than it ticked", last.draws < last.frames,
        `draws=${last.draws} frames=${last.frames}`);
  check("labels were laid out", last.labels > 0, `labels=${last.labels}`);

  const before = D().draws;
  el("z-in").click();
  pump(3);
  check("interaction repaints a frozen graph", D().draws > before,
        `draws went ${before} -> ${D().draws}`);

  const zoomed = D();
  el("btn-fit").click();
  pump(3);
  check("fit repaints too", D().draws > zoomed.draws);

  done("render-loop");
})();
