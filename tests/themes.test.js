/* Themes: the part that works colours out (cortex/ui/themes.js, which needs
 * no page), and the list in the window that previews them, driven through
 * the real cortex/ui/app.js.
 *
 * Run: node tests/themes.test.js   (or tests/run)
 */

const path = require("path");
const T = require(path.join(__dirname, "..", "cortex", "ui", "themes.js"));
const { harness, fsnode, reporter } = require("./harness");

const { check, done } = reporter();
const hexes = (o) => Object.values(o).every((v) => typeof v !== "string" || /^(#[0-9a-f]{6,8}|rgba\(.*\))$/.test(v));

// --- working colours out ---------------------------------------------------------
check("mixing goes part of the way from one colour to another",
      T.mix("#000000", "#ffffff", 0.5) === "#808080" && T.mix("#102030", "#102030", 0.7) === "#102030");
check("contrast is 21 between black and white, and 1 with itself",
      Math.round(T.contrast("#000", "#fff")) === 21 && T.contrast("#345", "#345") === 1);
check("text on a colour is whichever of light and dark reads there",
      T.on("#f5a524") === "#10131a" && T.on("#1d4ed8") === "#ffffff");
check("only hex is a colour", T.norm("#ABC") === "#aabbcc" && T.norm("red") === null
      && T.norm("url(x)") === null && T.norm("var(--x)") === null);

// --- the themes that come with it ------------------------------------------------
for (const t of T.BUILT_IN) {
  const r = T.resolve(t);
  check(`${t.name}: every colour it comes to is a colour`, hexes(r.vars) && hexes(r.paint.nodes)
        && Object.keys(r.vars).length > 60);
  check(`${t.name}: text, dim text and links can be read on it`,
        T.contrast(r.vars["--bg"], r.vars["--fg"]) >= 7
        && T.contrast(r.vars["--bg"], r.vars["--fg-dim"]) >= 4.5
        && T.contrast(r.vars["--bg"], r.vars["--link"]) >= 4.5
        && T.contrast(r.vars["--accent"], r.vars["--on-accent"]) >= 3,
        [r.vars["--fg-dim"], r.vars["--link"]].join(" "));
}
const midnight = T.resolve(T.find("midnight"));
check("Midnight is the look cortex always had",
      midnight.vars["--bg"] === "#070910" && midnight.vars["--line"] === "#1b2434"
      && midnight.vars["--accent-bg"] === "#12203a" && midnight.paint.hollow === "#0b1120"
      && midnight.vars["--dir"] === "#f5a524" && midnight.vars["--t1"] === "#f2616f");
const oled = T.resolve(T.find("oled"));
check("OLED is black that is off, panes too, and nothing glows",
      oled.vars["--bg"] === "#000000" && oled.vars["--pane"] === "#000000"
      && oled.vars["--t-bg"] === "#000000" && oled.paint.glow === false && midnight.paint.glow === true);
const paper = T.resolve(T.find("paper"));
check("a light theme has dark text, dark nodes and a dark-on-light terminal",
      paper.base === "light" && T.contrast(paper.vars["--bg"], paper.paint.nodes.code) >= 3
      && T.contrast(paper.vars["--t-bg"], paper.vars["--t-fg"]) >= 7
      && paper.vars["--syn-kw"] !== midnight.vars["--syn-kw"]);

// --- a theme of two colours --------------------------------------------------------
const two = T.check({ name: "Dusk", bg: "#1b1430", fg: "#e8e0ff" });
const dusk = T.resolve(two.theme);
check("two colours are a whole theme: the rest is worked out from them",
      two.theme.base === "dark" && dusk.vars["--pane"] !== dusk.vars["--bg"]
      && T.contrast(dusk.vars["--bg"], dusk.vars["--fg-dim"]) >= 4.5
      && dusk.vars["--accent"] === "#4c8dff" && dusk.vars["--t4"] === midnight.vars["--t4"]);
check("which way it leans is read off the background",
      T.check({ bg: "#fafafa", fg: "#111111" }).theme.base === "light");
check("what a theme says outright wins over what was worked out",
      T.resolve({ ...two.theme, set: { line: "#ff00ff", nonsense: "#000000" } }).vars["--line"] === "#ff00ff"
      && !("--nonsense" in T.resolve({ ...two.theme, set: { nonsense: "#000000" } }).vars));

// --- what is turned away -------------------------------------------------------------
check("a theme needs a background and a text colour",
      /needs a background/.test(T.check({ name: "x", bg: "#000000" }).error)
      && T.check("nope").error === "not a theme" && T.check(null).error === "not a theme");
check("text that could not be read on its background is refused",
      /hard to read/.test(T.check({ bg: "#202020", fg: "#404040" }).error));
const sly = T.check({ bg: "#000000", fg: "#ffffff", accent: "url(http://x)", ok: "var(--x)",
                      nodes: { code: "red", note: "#00ff00" }, term: ["#000"],
                      set: { "bg;x": "#111111", line: "expression(1)" } }).theme;
check("anything in a theme that is not a plain colour is dropped",
      sly.accent === undefined && sly.ok === undefined && sly.term === undefined
      && sly.set === undefined && JSON.stringify(sly.nodes) === '{"note":"#00ff00"}');

// --- a terminal's theme is a whole theme ----------------------------------------------
const TERM = ["#0b141c", "#e06c75", "#7fd18b", "#e5c07b", "#5fb3d9", "#c592d6", "#7fd1c7",
              "#b8c7d3", "#4d6477", "#f08a93", "#9be3a6", "#f2d79c", "#8ccdf0", "#dbb0ea",
              "#a3e6dd", "#eef5fa"];
const harbor = T.resolve(T.check({ name: "Harbor", bg: "#0f1a24", fg: "#d5e3ee", term: TERM,
                                   accent: TERM[4] }).theme);
check("sixteen colours, a background and a foreground colour the graph as well",
      harbor.vars["--t2"] === "#7fd18b" && harbor.paint.nodes.note === "#7fd18b"
      && harbor.paint.nodes.dir === "#e5c07b" && harbor.paint.nodes.code === "#5fb3d9"
      && harbor.vars["--bad"] === "#e06c75" && harbor.vars["--syn-kw"] === "#c592d6"
      && harbor.vars["--accent"] === "#5fb3d9");

// --- a folder's own colour --------------------------------------------------------------
const tinted = T.resolve(T.find("midnight"), "#fb923c");
check("a folder's colour is laid over the theme: what is selected, links, buttons",
      tinted.vars["--accent"] === "#fb923c" && tinted.vars["--accent-bg"] !== midnight.vars["--accent-bg"]
      && tinted.vars["--link"] !== midnight.vars["--link"] && tinted.paint.accent === "#fb923c"
      && tinted.vars["--bg"] === midnight.vars["--bg"] && tinted.vars["--ok"] === midnight.vars["--ok"]);
check("and a colour that is not one is not laid over anything",
      T.resolve(T.find("midnight"), "javascript:1").vars["--accent"] === "#4c8dff");
check("a theme is shown by a strip of what it is made of",
      T.swatches(T.find("oled")).length === 11 && T.swatches(T.find("oled"))[0] === "#000000");

// --- the list in the window -----------------------------------------------------------------
const ROOT = "/home/u/proj", A = `${ROOT}/a.md`;
const posted = [];
const { boot, pump, el, press, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: (p) => p === ROOT ? [fsnode(A, false, 0, ROOT)] : [],
  autoExpand: { depth: 1, budget: 100 },
  preload: ["themes.js"],
  config: { prefs: { theme: "oled", panel: 640, rail: "shut" },
            themes: [{ id: "Harbor", name: "Harbor", bg: "#0f1a24", fg: "#d5e3ee", term: TERM,
                       source: "yours" },
                     { id: "Broken", name: "Broken", bg: "#111111", fg: "#222222" }] },
  api: {
    "/api/prefs": (_q, body) => { posted.push(body.set); return { ok: true, prefs: {} }; },
    "/api/themes": () => ({ themes: [
      { id: "Dusk", name: "Dusk", bg: "#1b1430", fg: "#e8e0ff", source: "ghostty" },
      { id: "Harbor", name: "Harbor again", bg: "#000000", fg: "#ffffff" }] }),
  },
});
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const rows = () => el("th-list").children.filter((c) => c.classList.contains("th-row"));
const label = (row) => `${row.querySelector(".nm").textContent} — ${row.querySelector(".sub").textContent}`;
const chip = () => el("st-right").children.find((c) => c.classList.contains("theme"));

(async () => {
  const D = await boot();
  let th = D.theme();
  check("the window opens in the theme it was left in", th.id === "oled" && th.paint.bg === "#000000"
        && th.paint.glow === false && chip().textContent === "◐ OLED");
  check("and with the pane and the rail as they were left",
        D.workspace().panel === 640 && D.workspace().rail === false);

  chip().click();
  await settle(80);
  check("the list has cortex's own, yours, and what Ghostty has — and not one that cannot be read",
        D.theme().open && rows().map((r) => r.querySelector(".nm").textContent).join()
          === "Midnight,OLED,Graphite,Aurora,Ember,Paper,Sand,Harbor,Dusk",
        rows().map((r) => r.querySelector(".nm").textContent).join());
  check("it says which is in use, and where each came from",
        label(rows()[1]) === "OLED — dark · in use" && label(rows()[7]) === "Harbor — dark · yours"
        && label(rows()[8]) === "Dusk — dark · from Ghostty" && rows()[1].classList.contains("on"));

  const box = el("th-filter");
  press(box, "ArrowDown");
  check("moving through the list repaints the window in each, and saves nothing",
        D.theme().id === "graphite" && D.theme().paint.bg === "#16171a" && posted.length === 0
        && rows()[2].classList.contains("on"));
  rows()[5].dispatchEvent({ type: "mouseenter" });
  check("so does pointing at one", D.theme().id === "paper" && D.theme().colors.code === "#2563eb");
  press(box, "Escape");
  check("esc puts back what was there", !D.theme().open && D.theme().id === "oled"
        && D.theme().paint.bg === "#000000" && posted.length === 0);

  await D.themes();
  box.value = "har";
  box.dispatchEvent({ type: "input", target: box });
  check("typing narrows the list, and shows the first of what is left",
        rows().length === 1 && D.theme().id === "Harbor");
  press(box, "Enter");
  await settle();
  check("enter keeps it", !D.theme().open && D.theme().id === "Harbor"
        && JSON.stringify(posted) === '[{"theme":"Harbor"}]' && chip().textContent === "◐ Harbor"
        && D.theme().colors.note === "#7fd18b", JSON.stringify(posted));

  // --- a pair that follows the system ---------------------------------------------------------
  let light = false;
  global.matchMedia = () => ({ matches: light, addEventListener() {} });
  await D.themes();
  el("th-follow").checked = true;
  el("th-follow").dispatchEvent({ type: "change", target: el("th-follow") });
  rows().find((r) => r.querySelector(".nm").textContent === "Sand").click();
  await settle();
  check("with a pair following the system, a light theme chosen is the light one of the pair",
        JSON.stringify(posted.slice(-2)) === '[{"follow":true},{"light":"sand"}]'
        && D.theme().id === "Harbor" && /not the one on show/.test(el("toast").textContent),
        JSON.stringify(posted.slice(-2)) + el("toast").textContent);
  check("and the list says which is which",
        label(rows().find((r) => r.querySelector(".nm").textContent === "Sand")) === "Sand — light · the light one"
        && label(rows()[7]) === "Harbor — dark · yours · the dark one");
  light = true;
  press(box, "Escape");
  check("when the system goes light, the light one is what shows", D.theme().id === "sand");

  // --- a folder's colour ---------------------------------------------------------------------------
  await D.themes();
  const swatches = el("th-accents").children;
  swatches[6].click();
  await settle();
  check("a folder's colour is kept for that folder, and painted at once",
        JSON.stringify(posted[posted.length - 1]) === `{"accents":{"${ROOT}":"#fb923c"}}`
        && D.theme().paint.accent === "#fb923c" && el("th-accents").children[6].classList.contains("on"));
  el("th-accents").children[0].click();
  check("and can be given back to the theme", JSON.stringify(posted[posted.length - 1]) === '{"accents":{}}'
        && D.theme().paint.accent !== "#fb923c");

  done("themes");
})();
