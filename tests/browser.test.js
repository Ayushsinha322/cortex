/* Browser tabs, which exist only in cortex's own window (desktop/), driven
 * through the real cortex/ui/app.js with a stand-in for that window: the
 * object its preload puts on the page, and <webview> elements that record
 * what is asked of them.
 *
 * What is checked is the page's half: what it makes of an address, what it
 * asks the tab to do, what it does with what the tab reports, and where a
 * picked element goes.
 *
 * Run: node tests/browser.test.js   (or tests/run)
 */

const { harness, fsnode, reporter } = require("./harness");

const ROOT = "/home/u/proj";
const MAIN = `${ROOT}/main.py`;
const posted = [];
let sessions = [];

const { boot, pump, el, press, fire } = harness({
  root: ROOT,
  rootNode: () => fsnode(ROOT, true, 1, ROOT),
  children: (p) => p === ROOT ? [fsnode(MAIN, false, 0, ROOT)] : [],
  autoExpand: { depth: 2, budget: 100 },
  api: {
    "/api/preview": () => ({ kind: "text", lang: "python", text: "x = 1", ext: ".py" }),
    "/api/agents": (_q, body) => {
      if (body) { posted.push(body); return { ok: true }; }
      return { rev: 1, sessions, conflicts: [], git: true, usage: {}, agents: [] };
    },
    "/api/agent": ({ id }) => ({ id, seq: 0, events: [] }),
  },
});

// the window, as its preload presents it
const shell = { opens: [], keys: [], sent: [] };
global.window.cortexShell = {
  browser: true, partition: "persist:cortex-browser",
  onOpen: (fn) => shell.opens.push(fn),
  onKey: (fn) => shell.keys.push(fn),
  external: (url) => shell.sent.push(url),
};

const { check, done } = reporter();
const settle = async (ms = 60) => { await new Promise((r) => setTimeout(r, ms)); pump(2); };
const winKey = (key, mods = {}) => fire("keydown", { key, target: null, ctrlKey: false,
                                                     metaKey: false, altKey: false,
                                                     shiftKey: false, ...mods });
const tabs = () => el("p-tabs").children.filter((c) => c.classList.contains("p-tab")
                                                      && !c.classList.contains("add"));
const names = () => tabs().map((t) => t.querySelector(".nm").textContent);
const onShow = () => el("p-tool").children.find((c) => !c.classList.contains("off"));
const say = (view, type, more = {}) => view.dispatchEvent({ type, ...more });

(async () => {
  el("pathcard").hidden = true;
  const D = await boot();

  // --- what an address is ---------------------------------------------------------
  const A = D.address;
  check("an address is taken as it is", A("https://example.com/a?b=1") === "https://example.com/a?b=1");
  check("a bare host is https", A("github.com/stablyai/orca") === "https://github.com/stablyai/orca");
  check("a port, or localhost, is this machine", A("3000") === "http://localhost:3000"
        && A(":8080") === "http://localhost:8080" && A("localhost:5173/app") === "http://localhost:5173/app"
        && A("127.0.0.1:8000") === "http://127.0.0.1:8000");
  check("anything else is something to look up",
        A("how to rebase a branch") === "https://duckduckgo.com/?q=how%20to%20rebase%20a%20branch"
        && A("javascript:alert(1)").startsWith("https://duckduckgo.com/?q=")
        && A("file:///etc/passwd").startsWith("https://duckduckgo.com/?q="), A("javascript:alert(1)"));
  check("and nothing is nothing", A("   ") === "");

  // --- a tab -------------------------------------------------------------------------
  check("in its own window, the rail offers a browser tab where it offered a preview",
        el("rail-do").children.map((c) => c.querySelector(".nm").textContent).includes("New browser tab"));
  const id = D.browser("github.com/stablyai/orca");
  const tool = D.tool(id), view = tool.view;
  check("a browser tab is a real tab: its own process, its own cookies",
        view.tagName === "WEBVIEW" && view.getAttribute("src") === "https://github.com/stablyai/orca"
        && view.getAttribute("partition") === "persist:cortex-browser"
        && view.getAttribute("allowpopups") === "");
  check("it is on show, named by where it is until the page names itself",
        D.workspace().active === id && names().join() === "github.com");

  say(view, "did-start-loading");
  check("loading shows", tool.el.classList.contains("loading"));
  say(view, "did-navigate", { url: "https://github.com/stablyai/orca", isMainFrame: true });
  say(view, "page-title-updated", { title: "stablyai/orca" });
  say(view, "did-stop-loading");
  const where = tool.el.querySelector("input");
  check("the page's title names the tab, and the box shows where it is",
        names().join() === "stablyai/orca" && where.value === "https://github.com/stablyai/orca"
        && !tool.el.classList.contains("loading"));
  say(view, "did-navigate-in-page", { url: "https://github.com/stablyai/orca/issues", isMainFrame: true });
  check("following a link moves the address with it", tool.url === "https://github.com/stablyai/orca/issues"
        && where.value === tool.url);
  say(view, "did-navigate", { url: "https://ads.example/frame", isMainFrame: false });
  check("but a frame inside the page going somewhere does not", tool.url.endsWith("/issues"));

  const went = [];
  view.loadURL = async (url) => { went.push(url); };
  where.value = "docs.python.org/3/";
  press(where, "Enter");
  check("typing an address and enter goes there", went.join() === "https://docs.python.org/3/"
        && where.value === "https://docs.python.org/3/");
  where.value = "half typed";
  press(where, "Escape");
  check("esc in the box puts the address back", where.value === "https://docs.python.org/3/");

  // --- the window's side of the conversation ---------------------------------------------
  shell.opens[0]("https://example.com/new");
  await settle();
  check("a link that wanted a window of its own gets a tab",
        D.workspace().tabs.length === 2 && D.tool(D.workspace().active).url === "https://example.com/new");
  check("the first tab is still in the pane, out of sight, not thrown away",
        el("p-tool").children.length === 2 && tool.el.classList.contains("off")
        && el("p-tool").children.includes(tool.el));
  tabs()[0].click();
  shell.keys[0]("address");
  check("ctrl-l inside a tab comes back out to the address box",
        D.workspace().active === id && where.focused === true);
  winKey("t", { ctrlKey: true });
  await settle();
  const blank = D.tool(D.workspace().active);
  check("ctrl-t is a new tab, empty, waiting for an address",
        D.workspace().tabs.length === 3 && blank.url === "" && names()[2] === "New tab"
        && blank.view.getAttribute("src") === "about:blank"
        && blank.el.querySelector("input").focused === true);
  blank.el.querySelectorAll("button").find((b) => b.textContent === "⧉").click();
  tool.el.querySelectorAll("button").find((b) => b.textContent === "⧉").click();
  check("⧉ hands a page to your own browser, and an empty tab has nothing to hand",
        shell.sent.join() === "https://docs.python.org/3/");

  // --- picking something for the agent -------------------------------------------------------
  const PICKED = { url: "https://app.local/", selector: "#save > span", size: "80×32",
                   look: "rgb(255, 255, 255) on rgb(0, 100, 200), 14px Inter",
                   text: "Save  changes", html: '<span class="label">Save changes</span>' };
  let asked = "";
  view.executeJavaScript = async (code) => { asked = code; return PICKED; };
  tabs()[0].click();
  tool.el.querySelectorAll("button").find((b) => b.textContent === "pick").click();
  await settle();
  check("pick runs in the tab's page", asked.includes("click something to give it to the agent"));
  check("with no agent to give it to, it is copied",
        el("toast").textContent === "copied — paste it into a message to an agent");

  sessions = [{ id: "s1", agent: "claude", label: "Claude Code", converses: true, live: true,
                title: "restyle the button", folder: ROOT, origin: ROOT, tree: null, branch: null,
                mode: "here", perm: "ask", state: "idle", ended: null, seq: 0, unread: false,
                ask: null, files: {}, recent: [], stats: null, cost: null, group: null,
                color: 0, started: Date.now() / 1000, updated: Date.now() / 1000, last: "",
                review: false, resumable: true }];
  await D.refreshAgents();
  await settle(80);
  tabs()[0].click();
  tool.el.querySelectorAll("button").find((b) => b.textContent === "pick").click();
  await settle(120);
  const reply = el("p-body").querySelector("textarea");
  check("with an agent there, what was picked goes into your reply to it",
        D().selected === "agent:s1" && D.workspace().active === null && !!reply
        && reply.value.startsWith("From https://app.local/\nElement: `#save > span` — 80×32")
        && reply.value.includes("It says: Save changes")
        && reply.value.includes('```html\n<span class="label">Save changes</span>\n```'),
        reply && reply.value);
  check("unsent, for you to say what to do with it",
        el("toast").textContent === "added to your reply to “restyle the button” — say what to do with it"
        && !posted.some((p) => p.op === "send"), el("toast").textContent);
  view.executeJavaScript = async () => null;
  tabs().find((t) => t.querySelector(".nm").textContent === "stablyai/orca").click();
  tool.el.querySelectorAll("button").find((b) => b.textContent === "pick").click();
  await settle(80);
  check("esc while picking gives nothing, and changes nothing",
        D.workspace().active === id && reply.value.split("From https://").length === 2);

  // --- closing -------------------------------------------------------------------------------
  const before = el("p-tool").children.length;
  tabs().find((t) => t.classList.contains("on")).querySelector(".x").click();
  await settle();
  check("closing a tab takes it out of the pane", el("p-tool").children.length === before - 1
        && !el("p-tool").children.includes(tool.el) && D.workspace().tabs.length === 2);

  done("browser");
})();
