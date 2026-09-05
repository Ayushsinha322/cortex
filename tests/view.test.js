/* Remembered layout, arrow-key navigation and export, through the real app.js.
 *
 * What it guards: a project opens where you left it rather than re-settling
 * somewhere new, the arrows walk the graph without the mouse, and the picture
 * you save is the view you are looking at.
 *
 * Run: node tests/view.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const LEFT = `${ROOT}/left.md`;
const RIGHT = `${ROOT}/right.md`;
const ABOVE = `${ROOT}/above.md`;
const FAR = `${ROOT}/far.md`;
const NEAR = `${ROOT}/near.md`;
const TAG = "tag:elsewhere";         // nearer than near.md, linked to neither

function childrenOf(p) {
  if (p !== ROOT) return [];
  return [LEFT, RIGHT, ABOVE, FAR, NEAR].map((id) => fsnode(id, false, 0, ROOT));
}

/* A layout saved by an earlier run, with the camera it was left at. */
const SAVED = {
  [ROOT]: [0, 0],
  [LEFT]: [-200, 0],
  [RIGHT]: [200, 0],
  [ABOVE]: [0, -200],
  [FAR]: [900, 0],
  [NEAR]: [-150, 0],
  [TAG]: [-120, 10],
};

const { pump, boot, el, sent, blobs, downloads } = harness({
  root: ROOT,
  children: childrenOf,
  rootNode: () => fsnode(ROOT, true, 4, ROOT),
  layout: () => ({ positions: SAVED, cam: { s: 1, x: 800, y: 450 } }),
  links: () => ({ ready: true,
                  edges: [[ROOT, RIGHT, "note"], [RIGHT, TAG, "tag"]],
                  notes: 5, sources: 0, tags: ["elsewhere"], elapsed: 0.1 }),
  config: { rememberLayout: true },
  autoExpand: { depth: 1, budget: 100 },
});

const { check, done } = reporter();
const at = (id) => global.window.CORTEX.debug.at(id);

(async () => {
  const D = await boot();
  pump(2);

  // --- the layout came back ------------------------------------------------
  check("it asked for the saved layout",
        sent.some((s) => s.url.includes("/api/layout")));
  check("a node is where it was left", at(LEFT) && at(LEFT).x === -200,
        at(LEFT) && `${at(LEFT).x},${at(LEFT).y}`);
  check("the camera came back too", D().cam.x === 800 && D().cam.y === 450,
        JSON.stringify(D().cam));
  check("and the layout starts nearly settled rather than flying apart",
        D().alpha <= 0.12, `alpha=${D().alpha}`);

  // --- arrows --------------------------------------------------------------
  D.select(ROOT);
  D.key("ArrowLeft");
  check("left goes to the nearest node on the left", D().selected === NEAR,
        `selected=${D().selected}`);
  D.key("ArrowLeft");
  check("and again goes further left", D().selected === LEFT,
        `selected=${D().selected}`);

  D.select(ROOT);
  D.key("ArrowRight");
  check("right goes to the node on the right", D().selected === RIGHT);

  D.select(ROOT);
  D.key("ArrowUp");
  check("up goes to the node above", D().selected === ABOVE);

  D.select(ROOT);
  D.key("ArrowDown");
  check("nothing below means nothing moves", D().selected === ROOT);

  D.select(RIGHT);
  D.key("ArrowRight");
  check("it keeps going in that direction", D().selected === FAR);

  // the tag sits nearer to root than near.md does, but nothing links them
  D.select(ROOT);
  D.key("ArrowLeft");
  check("a linked neighbour beats a nearer node linked to nothing",
        D().selected === NEAR, `selected=${D().selected}`);

  D.select(NEAR);
  D.key("ArrowUp");
  check("with no linked neighbour that way, anything visible will do",
        D().selected === ABOVE, `selected=${D().selected}`);

  // --- export --------------------------------------------------------------
  const before = downloads.length;
  el("btn-save").click();
  await new Promise((r) => setTimeout(r, 20));
  check("the save button hands over a png",
        downloads.length === before + 1 && downloads[before].name.endsWith(".png"),
        downloads.map((d) => d.name).join(","));
  check("the png is a png", blobs[blobs.length - 1].type === "image/png");

  el("btn-save").click({ shiftKey: true });
  await new Promise((r) => setTimeout(r, 20));
  const svgBlob = blobs[blobs.length - 1];
  check("shift-clicking hands over an svg instead",
        downloads[downloads.length - 1].name.endsWith(".svg")
        && svgBlob.type === "image/svg+xml",
        downloads[downloads.length - 1].name);

  const svg = await svgBlob.text();
  check("the svg is well formed", svg.startsWith("<svg") && svg.endsWith("</svg>"));
  check("it has a circle for every visible node",
        (svg.match(/<circle/g) || []).length >= 6,
        `${(svg.match(/<circle/g) || []).length} circles`);
  check("it draws the links", svg.includes("<line "));
  check("it names the files", svg.includes("left.md") && svg.includes("right.md"));
  check("it paints its own background", svg.includes('fill="#070910"'));
  check("the file is named after the folder",
        downloads[downloads.length - 1].name.startsWith("proj-"),
        downloads[downloads.length - 1].name);

  // --- saving it back ------------------------------------------------------
  await global.window.CORTEX.debug.saveLayout();
  const put = [...sent].reverse().find(
    (s) => s.url.includes("/api/layout") && s.body);
  check("the layout is written back", !!put);
  check("with the positions it is showing",
        put && put.body.positions[LEFT][0] === -200,
        put && JSON.stringify(put.body.positions[LEFT]));
  check("and the camera as it stands now, which the arrows have moved",
        put && put.body.cam.x === D().cam.x && put.body.cam.s === D().cam.s,
        put && JSON.stringify(put.body.cam));

  check("a tag is left out of the saved layout, having nowhere to belong",
        put && !(TAG in put.body.positions),
        put && Object.keys(put.body.positions).join(","));

  done("view");
})();
