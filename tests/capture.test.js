/* The quick-note window, driven through the real cortex/ui/app.js: started
 * with --capture, the window is one box that adds a line to today's note and
 * then closes itself and cortex with it.
 *
 * Run: node tests/capture.test.js   (or tests/run)
 */

const { harness, reporter } = require("./harness");

const calls = [];
const { boot, el, press, type, sent } = harness({
  root: "/home/u/notes",
  config: { capture: true, title: "~/notes" },
  api: {
    "/api/capture": (_q, body) => { calls.push(body); return { ok: true, path: "/x" }; },
    "/api/quit": () => { calls.push("quit"); return { ok: true }; },
  },
});

const { check, done } = reporter();

(async () => {
  await boot();
  check("the window is only the box", global.document.body.classList.contains("capture")
        && el("capture").hidden === false);
  check("it says where the note goes", el("cap-where").textContent.includes("~/notes"));
  check("it draws no graph and asks for none",
        !sent.some((s) => s.url.includes("/api/root") || s.url.includes("/api/children")));
  check("the box has the keyboard", el("cap-text").focused === true);

  type(el("cap-text"), "call the bank");
  const shifted = press(el("cap-text"), "Enter", { shiftKey: true });
  check("shift-enter is a new line, not a send", !shifted.defaultPrevented && calls.length === 0);
  press(el("cap-text"), "Enter");
  await new Promise((r) => setTimeout(r, 40));
  check("enter adds it to today's note", calls[0] && calls[0].text === "call the bank");
  await new Promise((r) => setTimeout(r, 600));
  check("and then cortex is told to quit", calls.includes("quit"));

  done("capture");
})();
