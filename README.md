# cortex

Your filesystem as a brain.

`cortex` maps your home directory into a force-directed graph, opens it in its
own window, and lets you hand any file straight to `nvim`, `nano`, or a pager —
**running in the terminal you launched it from**.

```
cortex
```

That's it. A window opens. Double-click a folder to grow it. Click a file to
read it. Press <kbd>Enter</kbd> and it opens in your editor, in your terminal.

---

## Why not just use Obsidian?

|                        | Obsidian / Logseq / Anytype | cortex |
|------------------------|------------------------------|--------|
| Scope                  | one vault you set up          | your whole home directory, as-is |
| Sees your code         | no                            | yes — Python, JS/TS, Go, Rust, C, shell |
| Link types             | `[[wikilinks]]` between notes | wikilinks **and** resolved code imports |
| Opens files in `nvim`  | no                            | yes, in your real terminal |
| Reads PDFs             | plugin                        | built in |
| Setup                  | import / index a vault        | none — it reads the disk directly |
| Runtime deps           | Electron                      | Python stdlib |

The thing Obsidian structurally cannot do: cortex is launched *from* a terminal
and keeps that terminal. Clicking "Neovim" runs `nvim` on your TTY. You quit
the editor and you are back at the graph, still live, still where you were.

---

## Install

```bash
git clone <this repo> ~/cortex
cd ~/cortex
./install.sh
```

That drops a `cortex` launcher into `~/.local/bin`. No packages to install —
it is Python standard library only.

Run it straight from the checkout instead if you prefer:

```bash
python3 ~/cortex/cortex.py
```

---

## Usage

```bash
cortex                      # map ~
cortex ~/projects           # map a specific directory
cortex .                    # map the current directory
cortex -a                   # include dotfiles and dot-directories
cortex -w tab               # open in a normal browser tab, not an app window
cortex -w none              # just print the URL
cortex -p 8800              # pin the port
cortex --ignore build,tmp   # skip extra directory names
cortex --no-links           # skip the semantic index (instant start)
```

### Projects

Point it at one project and it opens the *whole* project immediately — three
levels deep, smallest folders first — instead of making you click through rings
of folders. Name one once and it is a keystroke away after that:

```bash
cortex ~/myproject --save myproject   # save under a name, and open it
cortex -P myproject                          # open it again later
cortex --list                                 # show saved projects
cortex --forget myproject                    # delete one
```

Saved in `~/.config/cortex/projects.json`, so you can edit it by hand.

Control how much opens on launch:

```bash
cortex -P myproject -d 5          # five levels deep
cortex -P myproject -d 0          # nothing; stay lazy and click in yourself
cortex ~/big-repo --max-nodes 300  # stop after 300 nodes
```

Auto-opening defaults to 3 levels for a directory you name, and 0 for your home
directory — a home directory is far too big to open eagerly.

**This is also where the graph earns its keep.** Across a whole home directory
most semantic links have one end off-screen. Inside a single project nearly all
of them resolve at once: `cortex -P myproject` gives 411 nodes with **229 of
229** semantic edges live — every import and every note link, drawn.

### In the window

| Action | What it does |
|---|---|
| click | select a node, open the reader |
| double-click | grow a folder into the graph / collapse it |
| drag | move a node; drag empty space to pan |
| wheel | zoom toward the cursor |
| <kbd>/</kbd> | search everything under the root |
| <kbd>Enter</kbd> | open the selection in your editor, in the terminal |
| <kbd>r</kbd> | page through the selection in the terminal |
| <kbd>e</kbd> | expand / collapse the selected folder |
| <kbd>f</kbd> | focus mode — hide everything not linked to the selection |
| <kbd>0</kbd> | fit the graph to the screen |
| <kbd>l</kbd> | toggle semantic links |
| <kbd>?</kbd> | shortcuts |

### The reader

- **Markdown** — rendered, including tables, task lists, and `[[wikilinks]]`.
  Clicking a wikilink grows the graph to that note and selects it.
- **Code** — syntax highlighted with line numbers.
- **PDF** — the browser's own viewer, inline.
- **Images, audio, video** — played inline.
- **CSV / TSV** — as a scrollable table.
- **Jupyter notebooks** — flattened to readable markdown.
- **.docx** — text extracted, no dependencies.

### The terminal handoff

Every action in the panel that isn't a preview runs on your terminal:

```
┌─ nvim ~/myproject/DEPLOY.md  (quit to return to the graph)
...
└─ back at the graph
```

Editors are auto-detected from your `PATH`. Terminal editors (`nvim`, `vim`,
`nano`, `micro`, `helix`, `kakoune`, `emacs -nw`) run in the foreground on your
TTY. GUI editors (`code`, `zed`, `subl`, `kate`, …) are spawned detached and
marked with `⧉`.

`shell here` drops you into `$SHELL` in that directory. Exit and you are back.

---

## What the graph shows

**Grey-blue edges** are the filesystem tree — a folder to the things inside it.

**Green edges** are note links: `[[wikilinks]]` and relative markdown links,
resolved across the entire root, not just one vault.

**Blue edges** are code imports, resolved to real files on disk:

| Language | Resolved from |
|---|---|
| Python | `import a.b`, `from .x import y` |
| JS / TS | `import … from './x'`, `require('./x')` |
| C / C++ | `#include "x.h"` |
| Shell | `source ./x.sh`, `. ./x.sh` |

Node colour is the file group (folder, note, code, config, doc, media,
archive). Node size is child count for folders, file size for files. Anything
modified in the last week gets a soft glow. Open folders are drawn as rings.

The index is built in a background thread at startup and streams into the graph
as it goes — on a 133,000-file home directory it finishes in about three
seconds.

---

## Design notes

**The layout settles and then stops.** Force-directed graphs that simulate
forever jitter, and jitter reads as flicker — labels sit on the edge of the
collision threshold and blink on and off. Cortex cools the simulation to a
freeze and then stops repainting entirely; a settled graph costs no CPU. Any
change — expanding, searching, dragging, zooming, filtering — reheats it.

**Nothing is loaded until you ask for it.** A home directory can be hundreds of
gigabytes. Cortex reads exactly one directory per expansion, so it stays
responsive whether you point it at a small project or a 140 GB tree.

**The link index is separate from the graph.** It walks the whole root once,
in the background, and the UI grafts in edges as both endpoints become visible.
You never wait on it.

**Security.** The API can launch editors and shells, so it binds to `127.0.0.1`
only, requires a fresh random token every run, and resolves every path argument
with `realpath` to confirm it is genuinely inside the mapped root before
touching it. Symlinks pointing out of the root are rejected.

---

## Layout

```
cortex/
├── cortex.py           standalone entry point
├── install.sh
└── cortex/
    ├── cli.py          argument parsing, window launch, terminal loop
    ├── scanner.py      lazy directory scanning, ignore rules, node building
    ├── links.py        semantic index: wikilinks and code imports
    ├── reader.py       per-filetype preview extraction
    ├── actions.py      editor detection and terminal handoff
    ├── server.py       local HTTP API, token auth, path guards
    └── ui/
        ├── index.html
        ├── style.css
        ├── app.js       canvas force layout, reader panel
        ├── markdown.js  dependency-free markdown renderer
        └── highlight.js dependency-free syntax highlighter
```

## Requirements

- Python 3.9+
- A browser for the window. Chromium-family browsers get a real chromeless app
  window via `--app=`; Firefox falls back to a normal window.
- Optional: `bat`/`batcat` for nicer terminal reading, `pdftotext` to page a
  PDF in the terminal.
