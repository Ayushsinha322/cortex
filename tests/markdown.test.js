/* Regression tests for cortex/ui/markdown.js
   Run: node tests/markdown.test.js   (or tests/run) */

const fs = require("fs");
const path = require("path");

const SRC = path.join(__dirname, "..", "cortex", "ui", "markdown.js");
global.window = {};
new Function(fs.readFileSync(SRC, "utf8"))();
const MD = global.window.MD;

const NUL = String.fromCharCode(0);
const opts = { wiki: () => true, resolve: (u) => u };

let failures = 0;
function check(name, got, want) {
  const ok = want instanceof RegExp ? want.test(got) : got === want;
  if (!ok) {
    failures++;
    console.log(`FAIL  ${name}`);
    console.log(`      want ${want}`);
    console.log(`      got  ${JSON.stringify(got)}`);
  } else {
    console.log(`ok    ${name}`);
  }
}
const render = (src) => MD.render(src, opts);

// -- inline ----------------------------------------------------------------
check("inline code is left alone by the emphasis pass",
      render("use `npm **run** build` now"),
      /<code>npm \*\*run\*\* build<\/code>/);

check("code span at the very start of a line",
      render("`sudo ufw` opens it"),
      /^<p><code>sudo ufw<\/code> opens it<\/p>$/);

check("two spans on one line",
      render("`a` and `b`"),
      /<code>a<\/code> and <code>b<\/code>/);

check("bold outside a span still renders",
      render("**hi** and `**no**`"),
      /<strong>hi<\/strong> and <code>\*\*no\*\*<\/code>/);

check("italic with underscores",
      render("an _emphasised_ word"),
      /<em>emphasised<\/em>/);

check("strikethrough",
      render("~~gone~~"),
      /<del>gone<\/del>/);

check("external link opens in a new tab",
      render("[docs](https://example.com)"),
      /<a href="https:\/\/example\.com" target="_blank"/);

check("bare url is autolinked",
      render("see https://example.com/x here"),
      /<a href="https:\/\/example\.com\/x"/);

// -- links into the graph --------------------------------------------------
check("wikilink carries its target",
      render("see [[my note]] ok"),
      /data-wiki="my note"/);

check("wikilink with a label",
      render("see [[my note|the note]]"),
      /data-wiki="my note">the note<\/a>/);

check("relative markdown link is marked for graph reveal",
      render("[a](./b.md)"),
      /data-rel="\.\/b\.md"/);

// -- blocks ---------------------------------------------------------------
check("heading level", render("### Third"), /^<h3>Third<\/h3>$/);
check("horizontal rule", render("---"), /^<hr>$/);
check("blockquote", render("> quoted"), /<blockquote>/);

check("unordered list", render("- one\n- two"),
      /<ul><li>one<\/li><li>two<\/li><\/ul>/);

check("ordered list", render("1. one\n2. two"), /^<ol>/);

check("task list checkbox state", render("- [x] done\n- [ ] todo"),
      /checkbox" disabled checked/);

check("table with a header", render("| a | b |\n|---|---|\n| 1 | 2 |"),
      /<thead><tr><th>a<\/th>/);

check("an all-empty header row is dropped, not rendered as a blank band",
      render("| | |\n|---|---|\n| 1 | 2 |"),
      /^<table><tbody>/);

check("fenced code keeps its language", render("```js\nlet x = 1;\n```"),
      /<pre><code class="lang-js">/);

check("fenced code is html-escaped", render("```\n<b>x</b>\n```"),
      /&lt;b&gt;x&lt;\/b&gt;/);

// -- line handling --------------------------------------------------------
// Hard-wrapped files are everywhere; honouring every newline shreds them.
check("soft newlines reflow into one paragraph", render("one\ntwo"),
      /^<p>one two<\/p>$/);

check("two trailing spaces force a break", render("one  \ntwo"),
      /one<br>two/);

check("a trailing backslash forces a break", render("one\\\ntwo"),
      /one<br>two/);

// -- the placeholder must never escape ------------------------------------
check("no sentinel byte survives into the output",
      render("`x` plain `y`").includes(NUL), false);

check("a literal NUL in the source does not break rendering",
      render("before" + NUL + "after").includes("before"), true);

console.log(failures ? `\n${failures} failing` : `\nall markdown tests pass`);
process.exit(failures ? 1 : 0);
