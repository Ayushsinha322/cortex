/* What the cortex page is told about the window it is in, and nothing more.

   The page runs with no access to Node or to Electron. This puts one small
   object on it -- window.cortexShell -- whose presence is how the page knows
   it may make browser tabs, and through which the window tells it two
   things: "open this address in a tab" (a link in a tab that wanted a new
   window), and "a key that is yours was pressed inside a tab". */
"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cortexShell", {
  browser: true,
  partition: "persist:cortex-browser",
  /* fn(address) for every address the window wants opened in a tab */
  onOpen(fn) {
    ipcRenderer.on("shell:open", (_event, url) => { if (typeof url === "string") fn(url); });
  },
  /* fn(name) for a browser key pressed in a tab: "address" is ctrl-l */
  onKey(fn) {
    ipcRenderer.on("shell:key", (_event, name) => fn(String(name)));
  },
  /* hand an address to the person's own browser */
  external(url) {
    return ipcRenderer.invoke("shell:external", String(url));
  },
});
