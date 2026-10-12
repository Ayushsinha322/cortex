# cortex

[![tests](https://github.com/Ayushsinha322/cortex/actions/workflows/tests.yml/badge.svg)](https://github.com/Ayushsinha322/cortex/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/cortex-fs.svg)](https://pypi.org/project/cortex-fs/)
[![Python](https://img.shields.io/pypi/pyversions/cortex-fs.svg)](https://pypi.org/project/cortex-fs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Ayushsinha322/cortex/blob/main/LICENSE)
[![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](https://github.com/Ayushsinha322/cortex/blob/main/pyproject.toml)

**See your files as a graph. Read them, write in them, and open them in your
editor — from your app menu, or from the terminal you started in.**

**[Try it in your browser →](https://ayushsinha322.github.io/cortex/demo/)** —
the real UI on a frozen copy of this repository, nothing to install.

Point `cortex` at a folder and it draws everything inside as a living map:
folders, notes, code, PDFs, and the links between them. Click a node to read
the file. Press <kbd>i</kbd> and edit it right there. Press <kbd>Enter</kbd> and
it opens in Neovim, in your terminal, right where you were.

Then look at it in ways a file manager cannot: watch files light up as an AI
agent or a build works through them, replay the project's whole git history
as it grows, find the chain of imports from one file to another, and walk a
newcomer through the code with a tour that lives in the repository.

![Clicking a node opens the file; pressing Enter opens it in Neovim in the terminal cortex was launched from](https://raw.githubusercontent.com/Ayushsinha322/cortex/main/docs/demo.gif)

No dependencies. No database. No import step. It reads your disk directly, and
it is about 14,000 lines of Python standard library and vanilla JavaScript.

---

## Contents

- [What problem it solves](#what-problem-it-solves)
- [Install](#install)
- [First run](#first-run)
- [Without a terminal](#without-a-terminal)
- [Reading files](#reading-files)
- [Editing files](#editing-files)
- [New, rename, trash](#new-rename-trash)
- [Today's note, and quick capture](#todays-note-and-quick-capture)
- [Following the links](#following-the-links)
- [Searching](#searching)
- [Opening files in your editor](#opening-files-in-your-editor)
- [Working on one project](#working-on-one-project)
- [Opening another folder](#opening-another-folder)
- [Ways of looking](#ways-of-looking) — live, history, the path, tours, views, disk
- [Reading the graph](#reading-the-graph)
- [Every command and key](#every-command-and-key)
- [Saving and sharing](#saving-and-sharing)
- [How it works](#how-it-works)
- [Tests](#tests)
- [FAQ](#faq)

---

## What problem it solves

Note apps like Obsidian, Logseq and Anytype draw a beautiful graph — of one
vault you set up for them, containing only markdown. Your actual work is not in
one vault. It is scattered across forty project folders, and half of it is code.

cortex maps what is really on your disk:

|                             | Obsidian / Logseq / Anytype | cortex |
| --------------------------- | --------------------------- | ------ |
| What it can see             | one vault you curate        | any folder, including your whole home directory |
| Understands code            | no                          | yes — Python, JS/TS, Go, Rust, C, shell |
| Links it draws              | `[[wikilinks]]`             | wikilinks **and** resolved code imports |
| Edits a note in place       | yes                         | yes, and refuses to save over a change it did not see |
| Notes you have not written  | unresolved links            | ghost nodes; click one to write it |
| Suggests links you missed   | no                          | yes, from what the notes say |
| Watch files change live     | no                          | yes — agents, builds, `git pull` |
| Replay the git history      | no                          | yes, and record it as a video |
| Opens a file in Neovim      | no                          | yes, in your real terminal |
| Reads PDFs                  | via a plugin                | built in |
| Setup before first use      | create and index a vault    | none |
| Runtime                     | Electron                    | Python stdlib |

The part no note app can do: cortex is launched **from** a terminal and keeps
it. Clicking "Neovim" runs `nvim` on your TTY. You edit, you quit, and you are
back at the graph — still open, still where you left it.

And the terminal is optional. Put cortex in your app menu and it is an app:
open a folder, write a note, rename it, throw it away, all from the window.
[Without a terminal](#without-a-terminal) says what changes.

---

## Install

You need Python 3.9 or newer and a browser. That is all. Linux and macOS are
both supported.

```bash
pipx install cortex-fs
```

It installs as `cortex-fs` and runs as `cortex`. PyPI would not take the
shorter name — something unrelated already has it — and the command is the
one you type every day, so that is the one that kept the name.

Or from the source, which is the same thing without the packaging:

```bash
git clone https://github.com/Ayushsinha322/cortex.git ~/cortex
cd ~/cortex
./install.sh
```

`install.sh` writes a small `cortex` launcher into `~/.local/bin` pointing back
at this folder. If that folder is not on your `PATH`, add it:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Prefer not to install anything? Run it straight from the clone:

```bash
python3 ~/cortex/cortex.py
```

### In your app menu

```bash
cortex --install-launcher
```

That is the last time you need a terminal for it. cortex appears in your app
menu (Linux) or in `~/Applications` (macOS), and starts like any other app.
`install.sh` does this for you; with `pipx`, run it once yourself, or click
**add it to the app menu** in the window's folder sheet (<kbd>p</kbd>).
`cortex --remove-launcher` takes it out again.

---

## First run

```bash
cortex
```

A window opens showing your home directory. On Chrome, Brave or Edge it is a
real app window — no tabs, no address bar. Firefox gets a normal window.

Your terminal stays where it is and prints what it is doing:

```
   ___ ___  ___ _____ _____  __
  / __/ _ \| _ \_   _| ____|\ \/ /   your filesystem as a brain
 | (_| (_) |   / | | | _|    >  <    v0.2.0
  \___\___/|_|_\ |_| |___|  /_/\_\

  mapping   ~
  editors   Neovim, nano, vi, VSCodium
  url       http://127.0.0.1:41277/?t=...
  window    opened with google-chrome

  ready. click a node in the window; actions land here. close the window or ctrl-c to quit.
```

Leave that terminal alone — it is where your editor will appear. Close the
window when you are done, and cortex quits with it; <kbd>Ctrl-C</kbd> works
too.

**Getting around the graph:**

- **Click** a node to select it and open the reader
- **Double-click** a folder to grow it into the graph (again to collapse)
- **Drag** a node to move it; drag empty space to pan
- **Scroll** to zoom toward your cursor
- **Arrow keys** step from the selected node to the next one that way,
  following links first
- Press <kbd>/</kbd> to search everything, however deep
- Press <kbd>0</kbd> to fit the whole graph on screen

**It opens where you left it.** Positions are saved per folder, with the
camera, so a project you open every day is the same map every day instead of
re-settling somewhere new. That is a cache in `~/.cache/cortex/layouts`;
deleting it costs one re-settle, and `--no-layout` turns it off.

Nothing is loaded until you ask for it. A home directory can be hundreds of
gigabytes; cortex reads one folder per double-click, so it does not care how
big yours is.

---

## Without a terminal

Started from the app menu, cortex has no terminal of its own, and nothing about
using it needs one:

- **It opens where you left off.** With no folder named, it reopens the last
  folder you had open. Press <kbd>p</kbd> for another one.
- **You can write without leaving the window.** <kbd>i</kbd> edits the selected
  file in place, and <kbd>n</kbd>, <kbd>F2</kbd> and <kbd>Delete</kbd> make,
  rename and trash files — see [Editing files](#editing-files).
- **Windowed editors come first.** The editor button is VS Code, Zed, Kate or
  whichever windowed editor you have, unless `$EDITOR` says otherwise.
- **Terminal editors still work.** Choose Neovim and it opens in a terminal
  window of its own — your desktop's terminal, or `$TERMINAL` — which closes
  when you quit the editor. If the editor fails, that window stays open long
  enough to read why.
- **Closing the window quits it.** A reload does not count; closing does.
  There is also a **quit cortex** button in the folder sheet.
- **If it stops, the window says so.** Rather than buttons that silently do
  nothing.
- **Errors go to a file**, `~/.cache/cortex/cortex.log`, since there is no
  terminal to print them to.

An app menu does not read your `~/.bashrc`, so a launcher would not normally
know about editors installed in `~/.local/bin` or the `$EDITOR` you set there.
`--install-launcher` writes down your `PATH`, `$EDITOR` and `$VISUAL` as they
are in the shell you run it from, in a small script beside the menu entry.
Install again after changing them, or edit the script.

---

## Reading files

Select a file and it is rendered in the panel on the right.

![Reading a note](https://raw.githubusercontent.com/Ayushsinha322/cortex/main/docs/reader.jpg)

| File type | What you get |
| --------- | ------------ |
| Markdown  | fully rendered — tables, task lists, code blocks, `[[wikilinks]]` |
| Code      | syntax highlighted, with line numbers |
| PDF       | your browser's own PDF viewer, inline |
| Images    | shown; **video and audio** play |
| CSV / TSV | a scrollable table |
| Notebooks | `.ipynb` flattened into readable markdown |
| Word      | `.docx` text extracted, with no dependencies |

**Clicking a `[[wikilink]]` grows the graph to that note and selects it.** Same
for a relative markdown link. This is how you follow a train of thought without
ever touching a file manager.

### Full screen

The panel is 520px, which is fine for a note and useless for a 500-page PDF.
Press <kbd>m</kbd>, or the **full ⤢** button, and the reader takes the whole
window — the document gets about 91% of it. <kbd>esc</kbd> brings the graph
back; a second <kbd>esc</kbd> closes the reader.

The file is not re-rendered when you switch, so a PDF keeps its page and scroll
position, and at full width your browser's viewer regains its page thumbnails,
outline and zoom controls.

---

## Editing files

Press <kbd>i</kbd>, or **edit** in the reader, and the reader becomes the
editor. <kbd>Ctrl-S</kbd> saves; <kbd>esc</kbd> goes back to reading.

```
  ● unsaved                         revert  preview  save  close
  ─────────────────────────────────────────────────────────────
  # Plan

  - ship the editor
  - [ ] write the release notes
  - [ ] |
```

It is a plain text box, on purpose: what it saves is exactly what you typed.
The few keys whose absence you would feel at once are there — <kbd>tab</kbd>
indents the way the file already does (tabs in a Go file, four spaces in
Python), and <kbd>enter</kbd> keeps the indentation and carries a list on to
its next item, or ends it on an empty one. **preview** renders a markdown file
as it stands, unsaved changes and all. Opened from a content-search hit, the
caret starts on that line.

**Nothing you type is lost.**

- Move to another file mid-edit and the draft is kept. Its node gets a dashed
  ring, the corner says how many are unsaved, and the file opens straight back
  into the draft when you return to it.
- Closing the window with a draft open asks first.
- The graph refreshing underneath you leaves the editor alone.

**A save never lands on top of a change it did not see.** If the file changed
on disk since you opened it — your editor, `git pull`, a sync client — the save
is refused and you choose: **keep mine** or **take theirs**. If the file was
deleted meanwhile, **save it again** puts it back.

**A save keeps the file the way it was.** Windows line endings stay Windows
line endings, a byte-order mark stays, and the file keeps its permissions, its
owner, its hard links and its extended attributes. On Linux the new text is
written beside the file and swapped in with one rename, so a full disk or a
crash mid-save leaves the old file whole — unless swapping would lose one of
those things, in which case it is written in place, as most editors write.
A file that mixes line endings says so when you open it: saving evens them out
to the one it mostly uses.

It will not open what it could not save faithfully: binary files, text that is
not UTF-8, and anything over 2 MB. Those go to your editor, as before.

---

## New, rename, trash

| Key | Does |
| --- | ---- |
| <kbd>n</kbd> | new note in the selected folder (or beside the selected file) |
| <kbd>F2</kbd> | rename the selection |
| <kbd>Delete</kbd> | move the selection to the trash |
| right-click | all of the above, and more, on any node or on empty space |

A name without an extension becomes a `.md` note, which opens ready to write
in. **Nothing is ever deleted.** "Delete" moves to your desktop's own trash —
the one Dolphin, Nautilus and Finder show — so anything thrown away from cortex
can be restored from there. It asks first.

Rename and trash act on what you clicked: a symlink is renamed or trashed
itself, never the file it points to. And as everywhere else in cortex, nothing
outside the folder you opened can be touched.

`cortex --read-only` takes all of this away — no editing, no new, rename or
trash — for when you want the guarantee that a browsing session changes
nothing.

---

## Today's note, and quick capture

Press <kbd>d</kbd>, or **today** in the dock, and today's note opens ready to
write in — `daily/2026-10-09.md`, headed with the date, made on first use. If
the folder already has a `Daily`, `journal` or `Journal`, that is where it goes
instead, beside the ones you already have.

**Quick note** is the half-second version. Right-click cortex's icon in your
app menu, choose **Quick note**, and a small window takes one line:

```
  ● QUICK NOTE  → today's note in ~/notes
  ┌──────────────────────────────────────────────┐
  │ ask about the Q3 budget forecast             │
  └──────────────────────────────────────────────┘
  enter adds it to today's note · shift enter new line · esc cancel
```

<kbd>Enter</kbd> adds it to today's note with the time in front —
`- 14:02 ask about the Q3 budget forecast` — and the window closes. No graph,
no folder to choose. `cortex --capture` does the same from a terminal or a
keyboard shortcut.

---

## Following the links

Under the actions in the reader is a list of every file connected to this one:
what it links to, and what links back to it.

```
  4 connections                         3 out · 1 in
    →  lexer.rs        src/parser/
    →  util.rs         src/
    →  ast.rs          src/parser/
    ←  main.rs         src/
```

The arrow says which way the link runs. Green names are note links, blue ones
are code imports, the same colours the graph draws them in.

**The list is not limited to what is on screen.** The index knows every link
under the root, so a file can be listed here long before its folder has been
grown into the graph. Click it and cortex opens the folders it needs to and
selects it. That is the fastest way through a codebase you do not know: open
one file, read what calls it, jump, repeat.

Press <kbd>c</kbd> to collapse the list when you want the height back. It is
hidden in full screen, where you are there to read the file.

### Notes you have not written yet

A `[[link]]` to a note that does not exist is a faint, dashed node of its own —
the note you said you would write. It sits beside the notes that mention it,
and in the reader the link is drawn as a link to nothing yet. Select it and the
reader lists who links to it, with a button that writes it: in the folder of
the note that mentions it, folders and all for `[[projects/Roadmap]]`, opened
ready to type. The **unwritten** chip hides them all.

Links inside code blocks are not links, and an image or other file named in
`[[...]]` never becomes a note.

### Links you have not made

cortex reads what your notes say, and when two of them keep talking about the
same things without ever linking, it says so. Select a note and its
suggestions are listed under its connections, with the words they share, and
drawn on the graph as dashed lines:

```
  SUGGESTED — THEY TALK ABOUT THE SAME THINGS
  ≈  costs.md        quarter, budget, forecast        link  ✕
```

**link** writes `See also [costs](costs.md)` into the note — an ordinary
relative link, so it works on GitHub too and cannot point at the wrong note
when two share a name. **✕** says they are not related, and that is
remembered. Nothing is suggested for code, or with fewer than three notes.

---

## Searching

Press <kbd>/</kbd> and the box searches **file names**, anywhere under the root,
however deep:

```
  plan.md                             notes/
  design.md                           notes/
  README.md                           ./
```

<kbd>↑</kbd> and <kbd>↓</kbd> move through the results and <kbd>enter</kbd>
goes to the highlighted one, opening the folders it needs to on the way. With
nothing highlighted, <kbd>enter</kbd> takes the first, so typing a name and
pressing enter simply gets you there. Focus leaves the box as you land, so the
graph's own keys work straight away.

Matches are also grafted into the graph with their real folder lineage, so the
list tells you which one you want and the graph tells you where it sits.

Press <kbd>tab</kbd>, or click the small **names** button in the box, and it
searches **inside** files instead:

```
  plan.md:2       the budget is fixed
  old.md:7        an old budget note
  costs.py:41     BUDGET = 10
```

That is the question you usually have about your own notes — not "where did I
put it" but "where did I say that". The same list, the same keys: arrow to one
and press <kbd>enter</kbd>, and cortex opens the folders it needs to, selects
the file, and remembers the line.

**The line then follows you into your editor.** With a hit selected,
<kbd>Enter</kbd> opens the file at that line rather than at the top —
`nvim +41`, `hx file:41`, `code -g file:41`, whichever you use. <kbd>r</kbd>
pages to it too. An editor cortex does not have a rule for is opened at the top
of the file, because guessing a flag would stop it opening at all.

Content search uses `rg` when you have it installed, and falls back to a plain
walk when you do not, so it is faster if you have ripgrep and still works if you
do not. Either way it reads only text files, and never anything `.gitignore`
excludes.

---

## Opening files in your editor

This is the point of the whole thing.

With a file selected, press <kbd>Enter</kbd> or click the blue editor button.
Your terminal — the one you ran `cortex` in — becomes your editor:

```
┌─ nvim ~/myproject/DEPLOY.md  (quit to return to the graph)
...
└─ back at the graph
```

Quit the editor and the graph is still there, still live, still on the same
node.

Other actions on every file:

| Button | What happens |
| ------ | ------------ |
| **edit** | edits it right here, in the reader — see [Editing files](#editing-files) |
| **Neovim** (or your first editor) | opens it in your terminal |
| **read** | pages through it with `bat` or `less` |
| **editor ▾** | any other editor you have installed |
| **shell here** | drops you into `$SHELL` in that folder; exit to return |
| **open ⧉** | hands the file to your desktop's default app |
| **file ▾** | show in your file manager, copy the path, rename, trash |
| **focus** | hides every node not linked to this one |

Started from the app menu, **read** is gone — the reader is right there — and
**shell here** becomes **terminal here**, which opens a terminal window in that
folder.

**If you have set `$EDITOR` or `$VISUAL`, that is what <kbd>Enter</kbd> uses.**
You have already told your system which editor you want; cortex is not going to
argue. `$VISUAL` wins over `$EDITOR`, and arguments are kept, so
`EDITOR="emacs -nw"` and `EDITOR="code --wait"` both work.

Failing that, editors are found on your `PATH` automatically. Terminal
editors — `nvim`, `vim`, `nano`, `micro`, `helix`, `kakoune`, `emacs -nw` — run
in the foreground on your TTY. Window editors — `code`, `zed`, `subl`, `kate` —
are launched detached and marked `⧉`.

---

## Working on one project

Pointing cortex at a single project opens the **whole project at once** — three
folder levels deep, smallest folders first — instead of making you click through
rings of folders:

```bash
cortex ~/myproject
cortex .                # just this folder
```

Name it once and it is a keystroke away forever:

```bash
cortex ~/myproject --save myproject   # save under a name, and open it
cortex -P myproject                   # open it again later
cortex --list                         # what have I saved?
cortex --forget myproject             # remove one
```

Saved as plain JSON in `~/.config/cortex/projects.json`, so you can edit it by
hand — or from the window, below. With cortex in your app menu, each saved
project is also a right-click action on its icon.

How much opens on launch:

```bash
cortex -P myproject -d 5           # five levels deep
cortex -P myproject -d 0           # nothing; click in yourself
cortex ~/big-repo --max-nodes 300  # stop after 300 nodes
```

Default is 3 levels for a folder you name, and **0 for your home directory** —
a home directory is far too big to open eagerly.

### What it leaves out

cortex skips caches, build output and dependency trees by name — `node_modules`,
`__pycache__`, `target`, `.venv` and the rest — because they are noise in a
graph of what you wrote.

That list cannot know your project writes to `generated/`, so **cortex also
reads the project's `.gitignore`**, the file where you already wrote that down.
Negation, directory-only patterns and nested `.gitignore` files all behave the
way git behaves, so a folder that is clean in `git status` is clean here.
Pass `--no-gitignore` when you want to see what git is hiding.

### The directory sidebar

You do not have to decide up front. Click **☰** in the top left, or press
<kbd>s</kbd>, and a sidebar lists every folder in the root. Click one and the
graph is rebuilt around just that folder — everything else is not dimmed or
filtered, it is gone:

![The directory sidebar](https://raw.githubusercontent.com/Ayushsinha322/cortex/main/docs/scope.jpg)

Click another folder to jump straight to it. **Close the sidebar and the whole
map comes back**, exactly as it looked when you launched — closing is always the
way out, so it is not a mode you can get stuck in.

There is a filter box for when the root has a lot of folders, and the row you
are currently in is highlighted.

### Narrowing from the graph itself

Same idea without the sidebar: select any folder node and press <kbd>o</kbd>, or
click **only this**. A pill appears in the top bar with the folder's name — click
it, or press <kbd>b</kbd>, for the whole map again.

Search narrows along with the view, and says so when a match exists but is out of
sight (`0 matches for "roadmap" (1 outside this folder)`), so a narrowed graph
never quietly hides results. Following a `[[wikilink]]` that points outside the
folder widens back out on its own.

This is a view, not a permission: the folder you launched on is still the
security boundary, and narrowing never lets you reach outside it.

**Single projects are where the graph pays off.** Across a whole home directory
most links have one end off-screen, so the graph looks like a plain tree. Inside
one project nearly every link resolves at once. The screenshots above are a
26-file project: 37 nodes and **22 of 22** semantic links drawn.

---

## Opening another folder

Click the folder's name next to the logo, or press <kbd>p</kbd>:

```
  OPEN A FOLDER
  ┌──────────────────────────────────────────┐
  │ ~/projects/al                            │
  └──────────────────────────────────────────┘
    ~/projects/alpha/
  ↑ up one level   home

  PROJECTS
    work      ~/code/work
    thesis    ~/Documents/thesis              missing
  RECENT
    alpha     ~/projects/alpha                open now
```

Type a path and folders complete as you go — <kbd>tab</kbd> completes the way
a shell does, <kbd>enter</kbd> opens. Or pick a saved project or a recent
folder. The window reloads around the new folder; nothing from the old one
comes along.

**save as project** names the folder you are in, the same as `--save`. The ✕
beside a project forgets its name; the folder itself is not touched.

Unsaved edits keep you where you are until you save or revert them, so a
switch can never quietly throw your typing away.

---

## Ways of looking

Along the bottom of the window is a dock: **live**, **history**, **tours**,
**views**, **disk** and **today**. Each is a different question to ask of the
same folder.

### Live — watch it change

Press <kbd>w</kbd> and every file lights up the moment it changes: green when
it is created, amber when it is written, red where it was deleted. A dashed
trail joins them in the order they happened, and fades over a minute or so.
A list in the corner says what changed and when.

This is the one to leave open while an AI agent works on your project. You
see which files it reads its way towards, what it touches, and in what order,
as it does it — or what a build writes, or what `git pull` brought in.

- **follow** flies the camera to each change as it happens, and stops the
  moment you take the camera yourself.
- **reveal** brings changed files into the graph even from folders you had
  not opened.

On Linux the kernel tells cortex what changed (inotify), so it is instant and
costs nothing while nothing happens. Elsewhere the folders you have open are
checked every second and a half. Nothing is watched at all until you first
turn live on.

### History — replay the project

Press <kbd>h</kbd> in a folder that is in a git repository and a timeline
appears. Drag it, and the graph becomes the folder as it was after that
commit: what had not been written yet is not there, what has since been
deleted or renamed is back where it was. Each commit's changes light up as you
reach it, in the same colours as live.

It follows the main line, the way `git log --first-parent` does: a merged
branch arrives in one step, at its merge, rather than as its own commits
interleaved by date — which would show files together that never were.

<kbd>space</kbd> plays it, at 1×, 4×, 16× or 64×, and <kbd>[</kbd> <kbd>]</kbd>
step a commit at a time. Everything that ever existed is laid out together
first, so nothing jumps about while you scrub.

**record** plays it from the first commit to the last and saves it as a video,
with the date and the commit message drawn on — the life of a project in thirty
seconds, for a README or a talk.

### The path between two files

How does `main.py` reach `db.py`? Select one and <kbd>shift</kbd>-click the
other:

```
  PATH  cli.py  imports  server.py  imports  reader.py     only the path  ✕
```

The chain lights up in gold with each step numbered, following imports the
way they run when such a chain exists, and any link at all when it does not.
**only the path** hides everything else. Right-click a node for
**Path from … to here** as well.

### Tours — walk someone through it

A tour is a path through the project with something said at each stop: start
here, this calls that, the interesting part is on line 120. Press
<kbd>T</kbd>, **record a tour**, then select files in the order you would show
them and write a line about each. Play it back and the graph flies from stop
to stop while the note for each one is shown; the arrow keys step through.

A tour is saved as an ordinary markdown file in the project's `.tours` folder:

```markdown
# How a request flows

1. [cortex/cli.py](../cortex/cli.py)
   It all starts here: **main()** builds the server.

2. [cortex/server.py, line 120](../cortex/server.py#L120)
   The routes.
```

So it is committed with the code, reviewed in pull requests, edited by hand,
and every stop is a link that works on GitHub.

### Views — come back to how you were looking

Narrow the graph to a folder, hide the kinds you do not want, open the folders
you care about, frame it — then <kbd>v</kbd> and **save this view**. Keys
<kbd>1</kbd> to <kbd>9</kbd> bring the first nine back, exactly as they were.
"Frontend", "thesis", "the bug I am chasing".

Views belong to the folder they were made in and live in
`~/.config/cortex/views/`.

### Disk — what is filling the drive

Press <kbd>u</kbd> and every node is sized by the space it takes on disk, its
size beside its name. A folder counts everything inside it — including
`node_modules`, `.git` and build output, which the graph never shows and which
are usually the answer. Select a folder and the reader lists where its space
goes, biggest first:

```
  4.9 MB in 141 files
  docs/        ██████████████████   2.5 MB
  .git/        ██████████████       1.9 MB   not in the graph
  cortex/      ██                 232.0 KB
```

A big folder says "≥" until it has finished counting. Hard links are counted
once, and other drives mounted inside are not counted at all, the way `du -x`
would have it.

---

## Reading the graph

**Colour** is the kind of file — folder, note, code, config, document, media,
archive — matching the chips along the top bar.

**Click a chip to see only that kind.** Click "notes" and the graph is your
notes; click it again and everything comes back. Folders stay either way,
because they are the skeleton the rest hangs from and a graph without them is a
cloud of unconnected dots. Shift-click hides one kind on its own, for when a
folder full of images is the only thing in the way.

**Size** is how much is inside: child count for a folder, file size for a file.

**A ring instead of a dot** means an open folder. **A soft glow** means the file
changed in the last week.

**A small dot on the shoulder of a node is what git thinks of it:**

| Dot | Meaning |
| --- | ------- |
| amber | modified, and not staged |
| green | staged |
| grey | untracked |
| red | conflicted |
| faint amber | a folder with one of the above somewhere inside it |

The glow is a guess at what you are working on; this is the answer. The
selected file says the same thing in words at the top of the reader, next to
the branch name in the corner. <kbd>g</kbd> turns the marks off for a clean
screenshot.

Only the repository above the folder in view is consulted, so a home directory
full of projects shows nothing until you narrow to one — the same moment the
rest of the graph starts paying off. Nothing outside the folder you mapped is
ever reported, even when the repository extends above it.

**Edges** come in four kinds:

| Edge | Meaning |
| ---- | ------- |
| faint grey-blue | the filesystem — a folder to what is inside it |
| **green** | a note link: `[[wikilinks]]` and relative markdown links |
| **blue** | a code import, resolved to a real file on disk |
| **purple** | a tag, joining every note that carries it |

### Tags

A tag is a node in the graph, drawn as a purple ring. That is the point: two
notes that never link to each other still meet, at the thing they are both
about.

Both ways of writing one are read:

```markdown
---
tags: [work, urgent]
---

Some thinking about #deep/focus.
```

Front matter takes an inline list, a comma-separated value or a YAML block
list. In the prose, a `#tag` is one only after whitespace or at the start of a
line, so `# Heading` is a heading and `example.com/a#frag` is a URL. Fenced and
inline code is skipped, because a shell script is not a tag list.

Select a tag and the reader lists the notes carrying it; click one to go there.
A tag is not a file, so it has no editor button, no path and no size — there is
nothing on disk to open. The **tags** chip in the top bar hides them all when
you would rather see the folders.

Code links are found by actually resolving the import:

| Language | Read from |
| -------- | --------- |
| Python   | `import a.b`, `from .x import y`, `from . import a, b` |
| JS / TS  | `import … from './x'`, `require('./x')` |
| Go       | `import "yourmodule/pkg"`, resolved through `go.mod` |
| Rust     | `mod x;`, `use crate::a::b`, `use super::x` |
| C / C++  | `#include "x.h"` |
| Shell    | `source ./x.sh`, `. ./x.sh` |

Go and Rust are resolved the way their compilers see them, not by guessing at
filenames. A Go import names a package, so cortex reads `go.mod` for the module
path and links to every source file in the imported directory — third-party and
standard-library imports are left alone, because they are not on your disk under
this root. Rust is the opposite shape: `mod` declares a file and `use` walks a
path through those files, including `foo.rs` owning the `foo/` beside it.

The index is built in the background as soon as you launch, and links appear as
it goes. On a 133,000-file home directory it finishes in about three seconds.

Toggle semantic links with the **links** button or <kbd>l</kbd>.

A tag carried by only one note is not drawn: it labels that note but joins
nothing to anything, and a graph full of them is harder to read, not richer.
The reader still lists it, and clicking it there brings it in.

---

## Every command and key

You need not remember any of this. <kbd>ctrl</kbd> <kbd>k</kbd> opens a palette
of everything cortex can do, with the key for each beside it, and every file
by name:

```
  ┌──────────────────────────────────────────────┐
  │ hist                                         │
  ├──────────────────────────────────────────────┤
  │ History: replay through git               h  │
  │ file  history.py                   cortex/   │
  └──────────────────────────────────────────────┘
```

```
cortex [folder] [options]

  folder                  what to map (default: your home directory)

  -P, --project NAME      open a saved project
      --save NAME         save this folder under NAME, then open it
      --list              list saved projects
      --forget NAME       delete a saved project

  -d, --depth N           folder levels to open on launch
      --max-nodes N       stop auto-opening after N nodes (default 700)

  -a, --hidden            include dotfiles and dot-folders
      --ignore a,b,c      extra folder names to skip
      --no-gitignore      show what git hides, too
      --no-links          skip the semantic index (instant start)
      --no-watch          do not notice files changing while the window is open
      --no-layout         do not remember where the nodes were last time
      --read-only         never change a file: no editing, new, rename or trash

      --install-launcher  put cortex in your app menu, then exit
      --remove-launcher   take it out again, then exit
      --capture           a small window that adds a line to today's note

  -w, --window MODE       app (default) | tab | none
  -b, --browser BIN       force a particular browser
  -p, --port N            pin the port
  -V, --version
```

| Key | Does |
| --- | ---- |
| <kbd>/</kbd> | search everything under the root |
| <kbd>tab</kbd> | in the search box: file names, or inside files |
| <kbd>↑</kbd> <kbd>↓</kbd> | in the search box: move through the results |
| <kbd>enter</kbd> | in the search box: go to the highlighted result |
| <kbd>Enter</kbd> | open the selection in your editor, in the terminal |
| <kbd>i</kbd> | edit the selection right here |
| <kbd>ctrl</kbd> <kbd>s</kbd> | in the editor: save |
| <kbd>esc</kbd> | in the editor: back to reading, keeping any draft |
| <kbd>n</kbd> | new note in the selected folder |
| <kbd>F2</kbd> | rename the selection |
| <kbd>Delete</kbd> | move the selection to the trash |
| <kbd>p</kbd> | open another folder, or a saved project |
| <kbd>ctrl</kbd> <kbd>k</kbd> | everything cortex can do, and every file, by name |
| right-click | everything you can do to a node |
| <kbd>shift</kbd>-click | the chain of links from the selection to there |
| <kbd>w</kbd> | live: watch files change as they change |
| <kbd>h</kbd> | history: replay this folder through git |
| <kbd>space</kbd> <kbd>[</kbd> <kbd>]</kbd> | in the history: play, and step a commit |
| <kbd>T</kbd> | tours: play one, or record your own |
| <kbd>→</kbd> <kbd>←</kbd> | in a tour: the next and previous stop |
| <kbd>v</kbd> | views: save how you are looking |
| <kbd>1</kbd>–<kbd>9</kbd> | go back to a saved view |
| <kbd>u</kbd> | disk: size everything by the space it takes |
| <kbd>d</kbd> | today's note |
| <kbd>r</kbd> | page through the selection in the terminal |
| <kbd>m</kbd> | full screen the reader |
| <kbd>e</kbd> | expand / collapse the selected folder |
| <kbd>f</kbd> | focus mode — hide everything not linked |
| <kbd>c</kbd> | collapse / expand the connections list |
| <kbd>s</kbd> | open / close the directory sidebar |
| <kbd>o</kbd> | only this folder — rebuild the graph around the selection |
| <kbd>b</kbd> | back to the whole map |
| <kbd>l</kbd> | show / hide semantic links |
| <kbd>g</kbd> | show / hide what git thinks of each file |
| <kbd>0</kbd> | fit the graph on screen |
| <kbd>arrows</kbd> | step to the next node that way, following links |
| <kbd>x</kbd> | save this view as a picture — <kbd>X</kbd> for SVG |
| <kbd>?</kbd> | the shortcut list |
| <kbd>esc</kbd> | one thing at a time: a menu, a tour, a path, full screen, the reader |

Optional extras, if you have them: `rg` for faster content search, `bat` for
nicer terminal reading, `pdftotext` to page a PDF in the terminal.

---

## Saving and sharing

Press <kbd>x</kbd>, or **share ▾** then **Picture**, and the view you are
looking at becomes a PNG in your downloads. <kbd>X</kbd> makes it an SVG
instead, redrawn from the same numbers rather than captured, so it stays sharp
on a slide or in print. Either way it is the view as it stands — pan, zoom,
whatever the chips are hiding, labels on or off. <kbd>t</kbd> first if you want
the shape without the names.

**Interactive map** is the view as one HTML file that someone else can open in
any browser, with nothing to install — the same window, the same graph where
you left it, with search, the reader and the tours, but no cortex behind it.
It is read-only by construction and does not update. Before it is made you are
told what goes in: the map always, and the text of the files on screen only if
you tick the box, because a file you have sent is a file you cannot take back.

In the history, **share ▾** also offers the replay as a video.

---

## How it works

```
you type `cortex`
        │
        ├─ a local HTTP server starts on 127.0.0.1, with a fresh random token
        ├─ a browser window opens pointing at it
        └─ your terminal waits, holding your TTY
                 │
        window ──┤ "open this file in nvim"
                 ▼
        the terminal runs nvim in the foreground, on your TTY
        (started from the app menu: a terminal window of its own)
```

Six ideas worth knowing:

**Nothing is read until you ask.** One folder per expansion. Whether you point
it at a 20-file project or a 140GB home directory, launch takes the same time.

**The graph keeps up with the disk.** Write a file in your editor and it
appears in the graph, in its folder, without a relaunch; delete one and it
leaves. The server remembers the folders the window has read and compares their
modification times when the window asks, every two and a half seconds, so an
idle cortex with no window attached does no work at all. The semantic index is
rebuilt on the same signal, but only for a folder that indexed in under a
couple of seconds and never more than once every fifteen — a home directory
takes long enough that it would spend its life re-indexing. `--no-watch` turns
the whole thing off.

**The link index is separate from the graph.** It walks the whole folder once in
a background thread and the UI adds edges as both ends become visible. You never
wait for it.

**The layout settles and then stops.** Force-directed graphs that simulate
forever jitter, and jitter reads as flicker — labels sit right on the collision
threshold and blink. cortex cools the simulation to a freeze and then stops
drawing entirely, so an idle graph costs no CPU and no battery. Touching
anything wakes it.

**Nothing is watched until you ask.** Live mode's feed of changes, disk
usage and the history are each started the first time they are wanted, not at
launch, so an ordinary session pays nothing for them — not even inotify watches,
which other programs on the machine draw from the same allowance.

**Closing the window is quitting.** The page says hello as it opens and
goodbye as it closes, and once the last goodbye has gone a few seconds without a
new hello — a reload is both, a moment apart — cortex exits. There is
deliberately no timeout on silence: a browser may freeze a minimised window for
as long as it likes, and a cortex that quit under a minimised window would be
found dead when you came back to it. `--window none` never quits by itself,
since the page could be anywhere, even at the far end of an SSH tunnel.

**Security.** This API can start editors and shells and write files, so: it
binds to `127.0.0.1` only, and requires a random token, regenerated every run,
for everything including the page itself. It answers only requests addressed to
`127.0.0.1` or `localhost`, so a web page whose DNS has been pointed at your
machine (DNS rebinding) is turned away before it can read the token out of the
page. Every path is resolved with `realpath` to prove it is genuinely inside the
folder you mapped before anything touches it, and symlinks pointing out are
refused. Writing follows the same rule; rename and trash check the folder an
item is in, so that a symlink is renamed or trashed itself rather than whatever
it points at.

The token is kept from other people on the same machine too. Any user can read
another's command lines, so the browser is never handed the address itself: it
is handed a page only you can read, which forwards to the address and is
deleted as soon as the window has loaded. The log, which can record the
address, is readable only by you. Changes must come from the window: a request
from any other site is refused, as is anything that is not JSON, which a page
elsewhere cannot send without asking first. A file shown raw — an SVG opened in
a tab of its own, say — is sandboxed, so a script inside it cannot act as
cortex.

The token never appears on a command line, where `ps` would show it to every
user on the machine: the browser is handed a page only you can read, which
forwards it to the address and is deleted once the window has loaded. (A
snap-packaged browser cannot read that page, so it is given the address
instead.) Anything that changes something must arrive as JSON from this
window's own origin, which another site's page cannot send. And a file from
disk opened on its own — an SVG from a repository you cloned, in a new tab — is
sandboxed, so whatever script it carries cannot reach the API.

The page is served under a strict `Content-Security-Policy`, because a markdown
file in a repository you cloned is untrusted input. The renderer escapes HTML;
the policy is the second lock, and it allows no script at all except the two
files cortex serves and one inline config blob carrying a nonce regenerated on
every request. All of that is asserted in the tests.

### Layout

```
cortex/
├── cortex.py             run it without installing
├── install.sh
├── pyproject.toml        so `pipx install` works; no dependencies to declare
├── cortex/
│   ├── cli.py            arguments, saved projects, window launch, action loop
│   ├── scanner.py        lazy folder scanning, ignore rules, node building
│   ├── ignore.py         .gitignore parsing, the way git reads it
│   ├── links.py          the semantic index: links, imports, tags, ghosts,
│   │                     and the links your notes are missing
│   ├── grep.py           content search, through ripgrep or a plain walk
│   ├── gitstatus.py      what git thinks of each file
│   ├── watch.py          noticing the disk change under an open window
│   ├── activity.py       live mode: every file as it changes (inotify, or a poll)
│   ├── history.py        the folder's life, from git
│   ├── du.py             what is filling the disk
│   ├── layout.py         where the nodes were, per mapped folder
│   ├── reader.py         per-filetype preview extraction
│   ├── files.py          saving, new, rename and trash
│   ├── places.py         saved projects, recent folders, typing a path
│   ├── views.py          named views, on keys 1 to 9
│   ├── tours.py          tours, as markdown in .tours
│   ├── journal.py        today's note, and quick capture
│   ├── snapshot.py       the map as one HTML file to send
│   ├── dismissed.py      suggestions someone said no to
│   ├── actions.py        editor detection, terminal handoff, terminal windows
│   ├── lifecycle.py      noticing the window has closed
│   ├── desktop.py        the app-menu launcher
│   ├── server.py         local HTTP API, token and host checks, path guards
│   └── ui/
│       ├── index.html
│       ├── style.css
│       ├── app.js        the graph, the reader and editor, the sheets
│       ├── markdown.js   markdown renderer, no dependencies
│       ├── highlight.js  syntax highlighter, no dependencies
│       ├── snapshot.js   the server, played by data inside a shared map
│       └── icon.svg      the favicon, and the launcher's icon
└── tests/
    ├── run                  runs everything
    ├── test_cortex.py       scanner, links, reader, actions, HTTP surface
    ├── test_app.py          saving, files, folders, quitting, the launcher
    ├── test_features.py     views, tours, notes, capture, sharing, the routes
    ├── test_activity.py     the live feed, both engines
    ├── test_history.py      git history, odd filenames and all
    ├── test_du.py           disk usage, hard links, partial answers
    ├── test_suggest.py      ghost notes and suggested links
    ├── harness.js           the stub DOM the browser-side tests run against
    ├── markdown.test.js     the markdown renderer
    ├── render-loop.test.js  cooling and repaint gating
    ├── connections.test.js  backlinks, and following one into the graph
    ├── search.test.js       both searches, and the line reaching the editor
    ├── refresh.test.js      the graph keeping up with the disk
    ├── perf.test.js         opening a folder staying cheap as links pile up
    ├── tags.test.js         tag nodes, and their refusal to act like files
    ├── view.test.js         remembered layout, arrow keys, saving a picture
    ├── edit.test.js         the editor, drafts and conflicts
    ├── files.test.js        new, rename, trash, right-click, other folders
    ├── readonly.test.js     --read-only, and the window outliving cortex
    ├── modes.test.js        live, history, disk, paths, views, tours, palette
    ├── notes.test.js        ghosts, suggestions, today's note, sharing
    ├── capture.test.js      the quick-note window
    └── snapshot.test.js     a shared map, with no server behind it
```

---

## Tests

```bash
tests/run                          # all of it
python3 tests/test_cortex.py       # python only
python3 tests/test_app.py
python3 tests/test_features.py
python3 tests/test_activity.py
python3 tests/test_history.py
python3 tests/test_du.py
python3 tests/test_suggest.py
node tests/markdown.test.js
node tests/render-loop.test.js
node tests/connections.test.js
node tests/search.test.js
node tests/refresh.test.js
node tests/tags.test.js
node tests/view.test.js
node tests/edit.test.js
node tests/files.test.js
node tests/readonly.test.js
node tests/modes.test.js
node tests/notes.test.js
node tests/capture.test.js
node tests/snapshot.test.js
node tests/perf.test.js
```

859 tests, no framework to install — `unittest` and plain `node`. They run on
every push against Python 3.9 and 3.13 on Linux, and 3.13 on macOS. `tests/run`
also byte-checks every source file, because raw NUL bytes once got into two UI
files and made git treat them as binary, silently breaking diffs and `grep`.

The browser-side suites drive the real `app.js` against a stub DOM
(`harness.js`) with a manual frame pump. That is not for speed: Chrome pauses
`requestAnimationFrame` in background tabs, so "has the layout stopped
repainting?" cannot be answered from an automated browser — it reports a frozen
canvas whether the code is right or not. Pumping frames by hand gives a real
answer.

---

## FAQ

**Does it change my files?**
Only when you ask it to: saving in the editor, or new, rename and trash. A save
is refused if the file changed since you opened it, and nothing is ever deleted
— trash is your desktop's trash. Start it with `--read-only` and it changes
nothing at all. Of its own accord it writes only its own state: saved projects
in `~/.config/cortex/`, and recent folders, layouts and a log in
`~/.cache/cortex/`.

**Does anything leave my machine?**
No. The server binds to `127.0.0.1`, there are no external requests, and no CDN
— the CSS and JavaScript are served from the folder you cloned. The one way
anything leaves is a map you choose to share, which is a file you save and send
yourself, and which holds the text of your files only if you said so.

**Can other users on this machine see it?**
No. It listens on loopback only, every request needs a token that is
regenerated on each launch, and that token is kept off the command line and out
of anything other users can read — see [Security](#how-it-works).

**Why a browser window and not a terminal UI?**
Because the graph needs to be pretty and PDFs need to be readable, and neither
survives being drawn in text. The browser is the renderer; the terminal is
there when you want it, and not needed when you do not.

**Which editor does <kbd>Enter</kbd> open?**
Whatever `$VISUAL` or `$EDITOR` says, if either is set and names something
installed. Otherwise, from a terminal, the first terminal editor found on your
`PATH`; from the app menu, the first windowed one. The **editor ▾** menu has the
rest, and the reader's button always shows which one <kbd>Enter</kbd> will use.

**My terminal editor opens in the wrong terminal app.**
Set `$TERMINAL` to the one you want — `TERMINAL=kitty` — and install the
launcher again so it picks the setting up. Otherwise cortex uses your desktop's
own terminal (Konsole on KDE, GNOME Terminal or Console on GNOME), then
whatever `x-terminal-emulator` is, then the first it can find. On macOS it is
Terminal.app.

**Where do errors go when there is no terminal?**
`~/.cache/cortex/cortex.log`. From a terminal, cortex still logs its own
internal errors there rather than printing them, because printing would land on
top of an editor running in the same terminal.

**Does it work on macOS?**
Yes. Terminal handoff, the graph and the reader all behave the same; `open ⧉`
uses `open` instead of `xdg-open`. The launcher is a small app bundle in
`~/Applications`, terminal editors open in Terminal.app, and trash goes through
Finder, so **Put Back** works — the first time, macOS asks whether your
terminal may control Finder.

**What are the dashed things on the graph?**
A dashed node is a note something links to that has not been written yet. A
dashed ring around a node means it has unsaved changes in the editor. With live
or history on, a dashed outline in red is where a file was just deleted, and the
dashed gold line is the order things changed in. Dashed cyan lines are suggested
links.

**Live says "checked every 1.5s", not "instant".**
That is the fallback for when the kernel's change notifications are not
available — on macOS, or on Linux when the per-user inotify limit is used up by
other programs. It still sees every change in the folders you have open, a
second or so later.

**Can I use it over SSH?**
Run `cortex -w none`, forward the port (`ssh -L 41277:127.0.0.1:41277`), and
open the printed URL locally. Editor actions run on the remote terminal, which
is usually what you want.

**It says no browser found.**
Use `-w none` and open the URL yourself, or point it at a binary with
`-b firefox`.

**The graph is a plain tree with no green or blue links.**
You are probably looking at your whole home directory, where most links have one
end off-screen. Open a single project (`cortex ~/myproject`) and they appear —
or select that project's folder in the graph and press <kbd>o</kbd>.

**I opened my home directory but only want one project now.**
Press <kbd>s</kbd> for the sidebar and click the project, or select its folder in
the graph and press <kbd>o</kbd>. No need to quit and relaunch. Closing the
sidebar — or <kbd>b</kbd> — brings the full map back.

**Can I map a folder outside my home directory?**
Yes — any folder you can read. That folder becomes the boundary, and nothing
outside it can be opened.

---

## Licence

MIT. Do what you like with it.
