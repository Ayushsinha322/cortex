# LinkedIn post — draft

Paste as plain text. LinkedIn strips markdown, so the comparison is written as
lines rather than a table, and there are no `**bold**` markers to leak.

Image: docs/graph.jpg — the synthetic demo project, safe to publish. Do not use
a screenshot of a real home directory.

---

I do security research. My work has never lived in one folder.

A WAF evaluation harness in one place. A scanner in another. Bug bounty notes in
a third. PDF findings, a dozen ROADMAP files, forty-odd project folders — and
half of it is code, not notes.

So every time I wanted to see the actual shape of my own research, I had two
options: hand-curate a vault that duplicates what is already on my disk, or
give up and go back to `find`.

Note apps like Obsidian, Logseq and Anytype draw a beautiful graph — of one
vault you set up for them, containing only markdown. That is the assumption I
kept hitting. My knowledge isn't in a vault. It's in ~/.

So I built the third option.

cortex maps what is really on your disk:

→ Sees any folder, including your entire home directory — not one vault you curate
→ Understands code: Python, JS/TS, Go, Rust, C, shell
→ Draws [[wikilinks]] AND resolved code imports as graph edges
→ Reads PDFs, notebooks, CSVs and .docx inline — no plugins
→ No setup, no vault, no import step. It reads the disk directly
→ Python standard library. No Electron, no dependencies

And the part no note app can do: cortex is launched from a terminal and keeps
it.

Click a node, press Enter, and nvim opens on your real TTY. You edit, you quit,
and you are back at the graph — still open, still exactly where you left it.

That is the entire reason it exists. When I spot something in a scan report at
2am I don't want to read it in a viewer. I want it open in my editor, in the
terminal I was already sitting in, and then I want to be back in the graph.

It is early and I'm honest about that: Linux-only, read-only, v0.1. What it
doesn't do yet is edit in place, show backlinks, or run on macOS — those are
next.

MIT licensed. ~3,000 lines. 92 tests.
github.com/Ayushsinha322/cortex

The vault model is wrong for anyone whose knowledge is half code. I'm building
the alternative.

#cybersecurity #opensource #devtools #knowledgemanagement #python

---

## Shorter variant (if the above runs long in the composer)

I do security research, and my work has never lived in one folder. A WAF
evaluation harness here, a scanner there, bug bounty notes somewhere else —
forty project folders, half of it code, not notes.

Obsidian, Logseq and Anytype all draw a beautiful graph — of one vault you
curate for them, containing only markdown. My knowledge isn't in a vault. It's
in ~/.

So I built cortex. It graphs any folder on your disk — your whole home
directory if you want — and it understands code, so Python imports and JS
requires become edges alongside [[wikilinks]]. PDFs, notebooks and CSVs render
inline. No vault, no setup, no Electron: it's Python standard library.

The part no note app can do: cortex is launched from a terminal and keeps it.
Click a node, press Enter, and nvim opens on your real TTY. Quit, and you're
back at the graph exactly where you left it. When I spot something in a scan
report at 2am, I want it in my editor — not in a viewer.

Early: Linux-only, read-only, v0.1. MIT, ~3,000 lines, 92 tests.
github.com/Ayushsinha322/cortex

#cybersecurity #opensource #devtools #python
