/* A guard on the one performance shape that matters: opening a folder.
 *
 * Applying semantic edges used to mean walking the whole edge list every time
 * anything appeared -- so opening one folder in a home directory re-examined
 * tens of thousands of edges, and it happened again on every search and every
 * four seconds while the index was still building. The graph got slower the
 * more it knew, which is the wrong way round.
 *
 * The assertion is a ratio rather than a millisecond budget, so it means the
 * same thing on a slow continuous-integration box as on a fast laptop: opening
 * a folder in a heavily linked graph must not cost dramatically more than
 * opening one in a graph with no links at all.
 *
 * Run: node tests/perf.test.js   (or tests/run)
 */

const path = require("path");
const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/big";
const DIRS = 40, FILES = 40;

const files = [];
for (let d = 0; d < DIRS; d++) {
  for (let f = 0; f < FILES; f++) files.push(`${ROOT}/d${d}/f${f}.md`);
}
/* Every file links to two others, spread across the whole tree. */
const WEB = [];
for (let i = 0; i < files.length; i++) {
  WEB.push([files[i], files[(i * 7 + 3) % files.length], "note"]);
  WEB.push([files[i], files[(i * 13 + 5) % files.length], "note"]);
}

function childrenOf(p) {
  if (p === ROOT) {
    return Array.from({ length: DIRS }, (_, i) =>
      fsnode(`${ROOT}/d${i}`, true, FILES, ROOT));
  }
  if (!/\/d\d+$/.test(p)) return [];
  return Array.from({ length: FILES }, (_, i) =>
    fsnode(`${p}/f${i}.md`, false, 0, ROOT));
}

async function openEverything(edges) {
  const { boot } = harness({
    root: ROOT,
    children: childrenOf,
    rootNode: () => fsnode(ROOT, true, DIRS, ROOT),
    links: () => ({ ready: true, edges, notes: files.length, sources: 0,
                    tags: [], elapsed: 0.2 }),
    config: { pulseMs: 0, rememberLayout: false },
    autoExpand: { depth: 0, budget: 5000 },
  });
  const D = await boot();
  await new Promise((r) => setTimeout(r, 60));

  const expand = global.window.CORTEX.debug.expand;
  const started = process.hrtime.bigint();
  for (let i = 0; i < DIRS; i++) await expand(`${ROOT}/d${i}`);
  const ms = Number(process.hrtime.bigint() - started) / 1e6;
  return { ms, state: D() };
}

const { check, done } = reporter();

(async () => {
  const bare = await openEverything([]);
  const linked = await openEverything(WEB);

  console.log(`      ${WEB.length} edges, ${DIRS} folders of ${FILES} files`);
  console.log(`      no links: ${bare.ms.toFixed(0)} ms` +
              `   fully linked: ${linked.ms.toFixed(0)} ms` +
              `   ratio ${(linked.ms / bare.ms).toFixed(1)}x`);

  check("the whole tree opened", linked.state.nodes >= DIRS * FILES,
        `${linked.state.nodes} nodes`);
  check("the edges were applied", linked.state.links > WEB.length,
        `${linked.state.links} links`);

  // Indexing the edges makes this near flat. Walking them made it about 10x.
  check("a linked graph opens at close to the cost of an unlinked one",
        linked.ms < bare.ms * 4 + 40,
        `${linked.ms.toFixed(0)} ms against ${bare.ms.toFixed(0)} ms`);

  done("performance");
})();
