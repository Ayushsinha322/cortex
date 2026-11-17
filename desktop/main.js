/* cortex, in a window of its own.

   cortex is a page served by a small Python program, and until now the window
   it was shown in was a browser's: Chrome with the tabs taken off. That is
   enough for the graph, the reader, the agents and the terminals, and it
   keeps cortex to "Python and nothing else". What a page in a browser cannot
   do is hold another site inside itself -- nearly every site forbids being
   framed -- so a browser tab beside your agent, showing the pull request or
   the docs or the app it is building, was out of reach.

   This is the same page in a window that can. The window is Chromium, by way
   of Electron, and inside it the page may make a <webview>: a real browser
   tab, a process of its own, with its own cookies, that any site will load
   in. Nothing else about cortex changes; the page finds itself here by the
   presence of window.cortexShell, and offers the tab.

   It starts one of two ways. `cortex` starts it, having started itself, and
   hands over the address of its page in the environment (never on the
   command line, which other users of the machine can read). Or it is started
   by hand -- `npm start -- ~/project` -- and starts cortex itself, reading
   the address from what cortex prints, and stopping it when the window goes.

   What it guards:
     - the cortex page is the only thing that gets the preload, and it can be
       navigated nowhere but its own address;
     - a browser tab gets no preload, no Node, and is always sandboxed,
       whatever the page that made it asked for;
     - a tab opening a window is given a tab, not a window;
     - a site asking for the camera, the microphone or your location is told
       no. */
"use strict";

const { app, BrowserWindow, Menu, clipboard, dialog, ipcMain, net, session, shell }
  = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const BROWSER = "persist:cortex-browser";    // the browser tabs' own cookies and logins
const REPO = path.join(__dirname, "..");

/* --shot file.png   save a picture of the window and quit
   --open <address>  open this in a browser tab once the window is up
   --after <ms>      how long to wait before the picture
   --hidden          do not show the window (for the picture)
   --close-after ms  close the window again by itself (for checking that
                     cortex goes with it)
   anything else     the folder to open, when started by hand */
function readArgs(argv) {
  const out = { folder: null, shot: null, open: null, after: 5000, hidden: false, run: null,
                closeAfter: 0 };
  const rest = argv.slice(app.isPackaged ? 1 : 2);
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === "--shot") out.shot = rest[++i];
    else if (a === "--open") out.open = rest[++i];
    else if (a === "--after") out.after = Number(rest[++i]) || out.after;
    else if (a === "--run") out.run = rest[++i];
    else if (a === "--hidden") out.hidden = true;
    else if (a === "--close-after") out.closeAfter = Number(rest[++i]) || 0;
    else if (!a.startsWith("-")) out.folder = a;
  }
  return out;
}

const args = readArgs(process.argv);
let server = null;
const homes = new Map();          // each window's webContents id -> the origin of its cortex page

// Its own folder for Chromium's profile, not cortex's config folder next door.
// CORTEX_SHELL_PROFILE names another: a second cortex that is to be a
// separate program -- a test, say -- rather than a new window of the one
// already running, since "already running" is decided per profile.
app.setPath("userData", process.env.CORTEX_SHELL_PROFILE
  || path.join(app.getPath("appData"), "cortex-desktop"));

/* Start cortex, and resolve with the address it prints. */
function startCortex(folder) {
  return new Promise((resolve, reject) => {
    const argv = [path.join(REPO, "cortex.py"), "--window", "none"];
    if (folder) argv.push(folder);
    server = spawn(process.env.CORTEX_PYTHON || "python3", argv, {
      env: { ...process.env, CORTEX_SHELL: "1", PYTHONUNBUFFERED: "1" },
      stdio: ["ignore", "pipe", "inherit"],
    });
    let seen = "";
    const give = setTimeout(() => reject(new Error("cortex did not say where its page is")), 20000);
    server.stdout.on("data", (chunk) => {
      seen += chunk.toString("utf8");
      const found = /http:\/\/127\.0\.0\.1:\d+\/\?t=[A-Za-z0-9_-]+/.exec(seen);
      if (found) { clearTimeout(give); resolve(found[0]); }
      if (seen.length > 65536) seen = seen.slice(-4096);
    });
    server.on("error", (err) => { clearTimeout(give); reject(err); });
    server.on("exit", (code) => {
      clearTimeout(give);
      server = null;
      reject(new Error(`cortex stopped (status ${code})`));
      app.quit();
    });
  });
}

function stopCortex() {
  if (server) {
    try { server.kill("SIGTERM"); } catch (err) { /* already gone */ }
    server = null;
  }
}

/* The window has closed, so cortex is to stop. The page says goodbye itself
   as it goes, but a page being torn down with its window is not always
   heard; this says it for certain, with the token the page was opened with. */
const goodbyes = [];
function sayGoodbye(address) {
  const quit = new URL("/api/quit", address);
  quit.search = new URL(address).search;
  return net.fetch(quit.toString(), {
    method: "POST", headers: { "content-type": "application/json" }, body: "{}",
  }).catch(() => {});
}

const isWeb = (url) => /^https?:\/\//i.test(url || "");
const inCortex = (page, url) => {
  try { return new URL(url).origin === homes.get(page.id); } catch (err) { return false; }
};

/* A browser tab: a guest of one cortex page, and treated as a stranger. */
function tameGuest(guest, host) {
  const toPage = (channel, ...rest) => { if (!host.isDestroyed()) host.send(channel, ...rest); };
  // window.open, target=_blank, a middle click: all of them are "a new tab"
  guest.setWindowOpenHandler(({ url }) => {
    if (isWeb(url)) toPage("shell:open", url);
    return { action: "deny" };
  });
  guest.on("will-navigate", (e, url) => {
    if (!isWeb(url) && !/^about:blank$/i.test(url)) e.preventDefault();
  });
  // keys that are the browser's rather than the page's, as in any browser
  guest.on("before-input-event", (e, input) => {
    if (input.type !== "keyDown") return;
    const mod = input.control || input.meta;
    if (input.key === "F12" || (mod && input.shift && input.key.toLowerCase() === "i")) {
      guest.isDevToolsOpened() ? guest.closeDevTools() : guest.openDevTools({ mode: "detach" });
    } else if (mod && !input.shift && input.key.toLowerCase() === "r") {
      guest.reload();
    } else if (mod && input.key.toLowerCase() === "l") {
      toPage("shell:key", "address");
    } else if (input.alt && input.key === "ArrowLeft") {
      if (guest.navigationHistory.canGoBack()) guest.navigationHistory.goBack();
    } else if (input.alt && input.key === "ArrowRight") {
      if (guest.navigationHistory.canGoForward()) guest.navigationHistory.goForward();
    } else {
      return;
    }
    e.preventDefault();
  });
  guest.on("context-menu", (_e, p) => {
    const items = [];
    if (p.linkURL && isWeb(p.linkURL)) {
      items.push({ label: "Open link in a new tab", click: () => toPage("shell:open", p.linkURL) },
                 { label: "Copy link address", click: () => clipboard.writeText(p.linkURL) },
                 { type: "separator" });
    }
    if (p.isEditable) {
      items.push({ role: "cut" }, { role: "copy" }, { role: "paste" }, { type: "separator" });
    } else if (p.selectionText) {
      items.push({ role: "copy" }, { type: "separator" });
    }
    items.push(
      { label: "Back", enabled: guest.navigationHistory.canGoBack(),
        click: () => guest.navigationHistory.goBack() },
      { label: "Forward", enabled: guest.navigationHistory.canGoForward(),
        click: () => guest.navigationHistory.goForward() },
      { label: "Reload", click: () => guest.reload() },
      { type: "separator" },
      { label: "Open in your browser", click: () => shell.openExternal(guest.getURL()) },
      { label: "Inspect", click: () => guest.inspectElement(p.x, p.y) });
    Menu.buildFromTemplate(items).popup({ window: BrowserWindow.fromWebContents(host) });
  });
}

function createWindow(address) {
  const win = new BrowserWindow({
    width: 1560, height: 960, minWidth: 900, minHeight: 560,
    show: !args.hidden, backgroundColor: "#070910", title: "cortex",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      webviewTag: true, contextIsolation: true, sandbox: true, nodeIntegration: false,
    },
  });
  const page = win.webContents;
  homes.set(page.id, new URL(address).origin);

  // whatever the page wrote on the tag, a tab is sandboxed and gets nothing of ours
  page.on("will-attach-webview", (e, prefs, params) => {
    delete prefs.preload;
    prefs.nodeIntegration = false;
    prefs.nodeIntegrationInSubFrames = false;
    prefs.contextIsolation = true;
    prefs.sandbox = true;
    prefs.webSecurity = true;
    if (params.partition !== BROWSER || (params.src && !isWeb(params.src)
                                         && params.src !== "about:blank")) {
      e.preventDefault();
    }
  });
  page.on("did-attach-webview", (_e, guest) => tameGuest(guest, page));

  // the window is cortex's page and nothing else's
  page.on("will-navigate", (e, url) => {
    if (inCortex(page, url)) return;
    e.preventDefault();
    if (isWeb(url)) page.send("shell:open", url);
  });
  page.setWindowOpenHandler(({ url }) => {
    if (isWeb(url)) shell.openExternal(url);      // "open in your browser" means that
    return { action: "deny" };
  });
  page.on("before-input-event", (e, input) => {
    if (input.type === "keyDown" && input.key === "F12") {
      page.isDevToolsOpened() ? page.closeDevTools() : page.openDevTools({ mode: "detach" });
      e.preventDefault();
    }
  });
  // "leave this page?" -- an agent is working, a terminal is running. The
  // page asked for it; a window with no address bar still has to ask.
  page.on("will-prevent-unload", (e) => {
    const leave = dialog.showMessageBoxSync(win, {
      type: "question", buttons: ["Stay", "Close anyway"], defaultId: 0, cancelId: 0,
      title: "cortex", message: "Something is still running.",
      detail: "Closing stops the agents that are working and ends what is running in "
            + "the terminals. Unsaved edits are lost.",
    });
    if (leave === 1) e.preventDefault();          // preventing the prevention: close
  });
  const id = page.id;
  win.on("closed", () => {
    homes.delete(id);
    if (!args.shot) goodbyes.push(sayGoodbye(address));
  });
  win.loadURL(address);
  if (args.shot) page.once("did-finish-load", () => takeShot(page));
  if (args.closeAfter) {
    page.once("did-finish-load", () => setTimeout(() => {
      if (!win.isDestroyed()) win.close();
    }, args.closeAfter));
  }
  return win;
}

/* A picture of the window, for the README and for checking it by eye. */
async function takeShot(page) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  await wait(2500);
  if (args.open) page.send("shell:open", args.open);
  if (args.run) {
    try { console.log("run:", await page.executeJavaScript(fs.readFileSync(args.run, "utf8"), true)); }
    catch (err) { console.log("run failed:", err.message); }
  }
  await wait(args.after);
  const image = await page.capturePage();
  fs.writeFileSync(args.shot, image.toPNG());
  console.log("shot:", args.shot, JSON.stringify(image.getSize()));
  // quit without the "something is still running" question
  page.removeAllListeners("will-prevent-unload");
  page.on("will-prevent-unload", (e) => e.preventDefault());
  app.quit();
}

if (!app.requestSingleInstanceLock({ url: process.env.CORTEX_URL || "" })) {
  // Another cortex window is already up, and two programs cannot share one
  // browser profile. It has just been handed this one's address, and opens a
  // window for it; this process has nothing left to do.
  app.quit();
} else {
  app.on("second-instance", (_event, _argv, _cwd, extra) => {
    const address = extra && typeof extra.url === "string" ? extra.url : "";
    if (/^http:\/\/127\.0\.0\.1:\d+\//.test(address)) createWindow(address);
  });

  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    const tabs = session.fromPartition(BROWSER);
    // sites that turn away "Electron" are turning away a browser they do not
    // know, not this one; it is Chromium, and says so
    tabs.setUserAgent(tabs.getUserAgent()
      .replace(/\s?Electron\/\S+/, "").replace(/\s?cortex-desktop\/\S+/, ""));
    tabs.setPermissionRequestHandler((_wc, permission, answer) => {
      answer(permission === "fullscreen" || permission === "clipboard-sanitized-write");
    });
    ipcMain.handle("shell:external", (e, url) => {
      if (homes.has(e.sender.id) && isWeb(url)) shell.openExternal(url);
    });

    let address = process.env.CORTEX_URL;
    delete process.env.CORTEX_URL;            // not for the programs started from here
    try {
      if (!address) address = await startCortex(args.folder);
      createWindow(address);
    } catch (err) {
      console.error(`cortex desktop: ${err.message}`);
      app.exit(1);
    }
  });

  app.on("window-all-closed", async () => {
    await Promise.allSettled(goodbyes);
    app.quit();
  });
  app.on("before-quit", stopCortex);
  process.on("exit", stopCortex);
}
