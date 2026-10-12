"""Semantic edges: the part that makes this a brain and not just a tree.

Two kinds of link are discovered in the background after launch:

  note -> note   [[wikilinks]] and relative markdown links
  code -> code   relative imports / requires / includes

Obsidian only does the first, and only inside one vault.  We do both, across
the whole home directory.

The same pass finds two links that are not there yet: a [[wikilink]] to a note
nobody has written becomes a ghost, and two notes that use the same unusual
words without linking to each other are suggested as a pair.
"""

from __future__ import annotations

import heapq
import math
import os
import re
import threading
import time
from urllib.parse import unquote
from bisect import bisect_right
from collections import Counter

from .scanner import EXT_GROUP

NOTE_EXT = {".md", ".markdown", ".mdx", ".txt", ".org", ".rst", ".adoc"}
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".c", ".h", ".cc", ".cpp", ".hpp", ".sh", ".bash"}

MAX_FILE_BYTES = 512 * 1024      # don't parse giant generated files
MAX_WALK_FILES = 400_000         # ceiling on the resolution universe
MAX_PARSE_FILES = 120_000        # ceiling on files we actually read
TIME_BUDGET = 150.0              # seconds; a huge home should not spin forever
PROGRESS_EVERY = 400             # publish partial results this often

# Rebuilding when files change is only sane for a project. A home directory
# takes seconds to index and would be re-indexing forever.
REBUILD_EVERY = 15.0             # seconds between rebuilds, at the most
REBUILD_CEILING = 2.5            # only re-index what indexed this fast

# Generated or vendored bundles: parsing them yields noise, not structure.
SKIP_SUFFIX = (".min.js", ".min.css", ".bundle.js", ".pack.js", "-lock.json",
               ".d.ts")

# --- note links -------------------------------------------------------------

# The target, then an optional `#heading` or `|alias`, then the closing `]]`.
# Requiring the close matters now that a miss becomes a ghost: `[[1, 2], [3]]`
# in prose is a nested list, not a note somebody meant to write.
RE_WIKILINK = re.compile(r"\[\[\s*([^\]\|#\n]+)(?:[#|][^\]\n]*)?\]\]")
RE_MDLINK = re.compile(r"\]\(\s*(?!https?:|mailto:|#)([^)\s]+)")

# --- ghost notes ------------------------------------------------------------
#
# A [[wikilink]] to a note that does not exist is a note somebody meant to
# write. It becomes a faint node of its own, and clicking it writes the note.

GHOST_PREFIX = "ghost:"
MAX_GHOSTS = 2000                # distinct ghosts; more would bury the notes

# `![[diagram.png]]` is a wikilink too, but a missing picture is not a note
# waiting to be written, and writing one would put markdown in a `.png`.
ATTACHMENT_EXT = {e for e, group in EXT_GROUP.items() if group != "note"}
ATTACHMENT_EXT.add(".canvas")    # Obsidian's whiteboards

# --- suggested links --------------------------------------------------------
#
# Two notes that lean on the same unusual words are probably about the same
# thing, whether or not anyone linked them. TF-IDF finds them: a word counts
# for being frequent in one note and rare across the rest.

SUGGEST_MIN = 0.15               # below this it is shared vocabulary, not a topic
SUGGEST_MIN_SHARED = 2           # one word in common is a coincidence
SUGGEST_PER_NOTE = 3             # partners per note; more and it is a hairball
SUGGEST_TERMS = 4                # shared words shown as the reason
SUGGEST_KEEP_TERMS = 40          # a note's strongest words; the tail is noise
SUGGEST_MAX_NOTES = 4000         # pairing grows faster than the notes do
SUGGEST_MAX_BYTES = 200 * 1024   # this long has every word, so pairs with all
SUGGEST_MAX_WORDS = 1_000_000    # (note, word) counts held at once: ~30 MB

# Runs of letters and digits, three or more: "a", "of" and "is" never carry a
# topic, and cutting them in the regex is cheaper than in the loop.
RE_TERM = re.compile(r"[^\W_]{3,}")
RE_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
RE_LINK_TARGET = re.compile(r"\]\([^)\n]*\)")
# A front matter key (`title:`, `- tags:`) is in every note that has front
# matter at all, so it would make every one of those notes look alike.
RE_YAML_KEY = re.compile(r"^[ \t]*(?:-[ \t]+)?[\w-]+[ \t]*:", re.M)

# Words that say nothing about what a note is for: English glue (including
# what is left of "don't" once the apostrophe splits it), the debris of links
# and images, and the two words on every task list.
STOPWORDS = frozenset("""
    about above across after again against all almost along already also
    although always among and another any anyone anything are around because
    been before behind being below between both but can cannot come could
    did does doing down during each either else enough etc even ever
    every few for from further get gets getting give given going gone good
    got had has have having her here hers herself him himself his how however
    instead into its itself just least less let like made make makes many may
    maybe might mine more most much must myself need needs never next nor not
    nothing now off often once one only onto other others our ours ourselves
    out over own per perhaps put quite rather really said same see seem seems
    shall she should since some something still such sure take than that the
    their theirs them themselves then there these they thing things think this
    those though through thus till too toward under until upon use used uses
    using very want was way well were what whatever when where whether which
    while who whom whose why will with within without would yes yet you your
    yours yourself yourselves
    don doesn didn isn aren wasn weren won wouldn couldn shouldn haven hasn
    hadn
    http https www com org net html htm png jpg jpeg gif svg pdf nbsp
    todo done
""".split())

# --- tags -------------------------------------------------------------------
#
# The other way notes are organised. A tag is a node in the graph, so two notes
# that never link to each other still meet if they are about the same thing.

TAG_PREFIX = "tag:"
MAX_TAGS_PER_FILE = 40
MAX_TAGS = 600                   # distinct tags; beyond this it is not a graph

# `#thing`, after whitespace or at the start of a line. A markdown heading is
# `#` followed by a space or another `#`, so it cannot match. A URL fragment
# has something other than whitespace in front of it, so it cannot either.
RE_TAG = re.compile(r"(?:^|(?<=\s))#([A-Za-z][\w/-]*)")
RE_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
RE_INLINE_CODE = re.compile(r"`[^`\n]*`")
RE_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
RE_YAML_TAGS = re.compile(r"^(tags?|keywords)[ \t]*:[ \t]*(.*)$", re.M | re.I)

# --- code links -------------------------------------------------------------

RE_PY_FROM = re.compile(r"^\s*from\s+([.\w]+)\s+import", re.M)
RE_PY_IMPORT = re.compile(r"^\s*import\s+([.\w]+)", re.M)
RE_JS_FROM = re.compile(r"""(?:from|import)\s*\(?\s*['"](\.{1,2}/[^'"]+)['"]""")
RE_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"](\.{1,2}/[^'"]+)['"]""")
RE_C_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
RE_SH_SOURCE = re.compile(r"^\s*(?:\.|source)\s+([^\s;#]+)", re.M)

# Go imports name a package (a directory), not a file, and they are absolute
# under the module path declared in go.mod -- so resolving one means reading
# go.mod first and then listing the package directory.
RE_GO_ONELINE = re.compile(r'^\s*import\s+(?:[\w.]+\s+|_\s+)?"([^"]+)"', re.M)
RE_GO_BLOCK = re.compile(r"^\s*import\s*\(([^)]*)\)", re.M | re.S)
RE_GO_QUOTED = re.compile(r'"([^"]+)"')
RE_GO_MODULE = re.compile(r"^\s*module\s+(\S+)", re.M)

# Rust splits it in two: `mod x;` declares a file, `use crate::a::b` names a
# path through those files.
RE_RS_MOD = re.compile(
    r"^\s*(?:pub\s*(?:\([^)]*\)\s*)?)?mod\s+([A-Za-z_]\w*)\s*;", re.M)
RE_RS_USE = re.compile(
    r"^\s*(?:pub\s*(?:\([^)]*\)\s*)?)?use\s+(crate|super|self)"
    r"((?:::[A-Za-z_]\w*)+)", re.M)

GO_PACKAGE_CAP = 8               # files linked per imported package

JS_TRY = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
          "/index.ts", "/index.js", "/index.tsx", "/index.jsx"]


class LinkIndex:
    """Builds and holds the semantic edge list. Safe to read while building."""

    def __init__(self, scanner) -> None:
        self.scanner = scanner
        self.edges: list[list] = []          # [source, target, kind]
        self.ready = False
        self.notes = 0
        self.sources = 0
        self.tags: list[str] = []
        self.ghosts: dict[str, str] = {}     # ghost id -> name as written
        self.suggest: list[list] = []        # [a, b, score, [term, ...]]
        self.elapsed = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._partial = True         # publish as we go on the first build only
        self._last_build = 0.0
        self._stop = threading.Event()

    def maybe_rebuild(self) -> bool:
        """Re-index because files changed, if that is cheap enough to do.

        Refuses on anything that took a while to index the first time, and
        never more than once every REBUILD_EVERY seconds, so a busy directory
        cannot put the machine in a loop.
        """
        with self._lock:
            if not self.ready or self.elapsed > REBUILD_CEILING:
                return False
            now = time.monotonic()
            if now - self._last_build < REBUILD_EVERY:
                return False
            self._last_build = now
            self.ready = False
            self._partial = False    # keep the old edges until the new ones land
        self.start()
        return True

    def stop(self) -> None:
        """Abandon a build in progress, for when the window maps somewhere else.

        A home directory takes seconds to walk, and finishing that walk for a
        folder nobody is looking at any more is seconds of disk for nothing.
        """
        self._stop.set()

    def start(self) -> None:
        if self._stop.is_set():
            return
        self._thread = threading.Thread(target=self._build, daemon=True,
                                        name="cortex-linkindex")
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"ready": self.ready, "edges": list(self.edges),
                    "notes": self.notes, "sources": self.sources,
                    "tags": list(self.tags), "ghosts": dict(self.ghosts),
                    "suggest": list(self.suggest), "elapsed": self.elapsed}

    # -- build -------------------------------------------------------------

    def _build(self) -> None:
        started = time.monotonic()
        self._last_build = started
        _GO_MODULES.clear()          # go.mod contents may have changed
        note_paths: list[str] = []
        code_paths: list[str] = []
        by_stem: dict[str, list[str]] = {}   # note stem -> paths, walk order
        all_files: set[str] = set()

        sc = self.scanner
        walked = 0
        cut_short = False
        for dirpath, dirnames, filenames in os.walk(sc.root, followlinks=False):
            if self._stop.is_set():
                return
            dirnames[:] = [d for d in dirnames if not sc.skip(d, True)
                           and not sc.gitignored(os.path.join(dirpath, d), True)]
            for fn in filenames:
                if sc.skip(fn, False):
                    continue
                full = os.path.join(dirpath, fn)
                if sc.gitignored(full, False):
                    continue
                all_files.add(full)
                walked += 1
                lower = fn.lower()
                if lower.endswith(SKIP_SUFFIX):
                    continue
                ext = os.path.splitext(lower)[1]
                if ext in NOTE_EXT:
                    note_paths.append(full)
                    by_stem.setdefault(_fold(os.path.splitext(fn)[0]),
                                       []).append(full)
                elif ext in CODE_EXT:
                    code_paths.append(full)
            if walked >= MAX_WALK_FILES:
                cut_short = True
                break

        with self._lock:
            self.notes = len(note_paths)
            self.sources = len(code_paths)

        edges: list[list] = []
        seen: set[tuple[str, str]] = set()
        tag_names: set[str] = set()
        ghosts: dict[str, str] = {}
        docs: list[tuple[str, dict]] = []    # (note, its terms) for pairing
        copies: set[int] = set()             # hashes of the texts in `docs`
        vocab: dict[str, str] = {}           # one string per word, not per note
        held = 0                             # (note, word) counts in `docs`
        root = sc.root.rstrip(os.sep) + os.sep

        def emit(a: str, b: str, kind: str) -> None:
            if a == b:
                return
            key = (a, b) if a < b else (b, a)
            if key in seen:
                return
            seen.add(key)
            edges.append([a, b, kind])

        def over_budget() -> bool:
            return (time.monotonic() - started > TIME_BUDGET
                    or self._stop.is_set())

        def ghost(path: str, name: str) -> None:
            # A walk cut short cannot tell a missing note from one it never
            # reached, and a ghost for a note that exists would write a twin.
            if cut_short or _is_attachment(name):
                return
            key = ghost_key(name)
            if not key:
                return
            gid = GHOST_PREFIX + key
            if gid not in ghosts:
                if len(ghosts) >= MAX_GHOSTS:
                    return
                ghosts[gid] = name
            emit(path, gid, "ghost")

        def remember(path: str, text: str) -> None:
            nonlocal held
            if (len(docs) >= SUGGEST_MAX_NOTES or held >= SUGGEST_MAX_WORDS
                    or len(text) > SUGGEST_MAX_BYTES):
                return
            # A copy of a note (a symlink, a LICENSE in every repository) is
            # the same note: pairing it with itself suggests nothing, and
            # counting it twice makes its words look common.
            fingerprint = hash(text)
            if fingerprint in copies:
                return
            copies.add(fingerprint)
            terms = note_terms(text, vocab)
            if terms:
                docs.append((path, terms))
                held += len(terms)

        # Notes first: they are what a "second brain" is mostly made of, and
        # there are far fewer of them than source files.
        for i, path in enumerate(note_paths[:MAX_PARSE_FILES]):
            text = _read(path)
            if text:
                for raw in RE_WIKILINK.findall(_strip_code(text)):
                    name = raw.strip().rstrip("\\").rstrip()   # `\|` in a table
                    if not name:
                        continue
                    target = _resolve_wikilink(name, root, by_stem)
                    if target:
                        emit(path, target, "note")
                    else:
                        ghost(path, name)
                for raw in RE_MDLINK.findall(text):
                    target = _resolve_rel(path, raw, all_files)
                    if target:
                        emit(path, target, "note")
                for tag in note_tags(text):
                    if tag not in tag_names and len(tag_names) >= MAX_TAGS:
                        continue
                    tag_names.add(tag)
                    emit(path, TAG_PREFIX + tag, "tag")
                remember(path, text)
            if i % PROGRESS_EVERY == 0:
                if self._partial:
                    with self._lock:
                        self.edges = list(edges)
                        self.ghosts = dict(ghosts)
                if over_budget():
                    break

        budget_left = max(0, MAX_PARSE_FILES - len(note_paths))
        for i, path in enumerate(code_paths[:budget_left]):
            text = _read(path)
            if text:
                for target in _code_targets(path, text, all_files):
                    emit(path, target, "code")
            if i % PROGRESS_EVERY == 0:
                if self._partial:
                    with self._lock:
                        self.edges = list(edges)
                if over_budget():
                    break

        # Only a finished pairing is shown: a half-done one would suggest
        # whatever happened to be compared first, not what is most alike.
        suggest: list[list] = []
        if not over_budget():
            suggest = suggest_links(docs, seen, over_budget) or []

        if self._stop.is_set():
            return
        with self._lock:
            self.edges = edges
            self.tags = sorted(tag_names)
            self.ghosts = ghosts
            self.suggest = suggest
            self.ready = True
            self._partial = True
            self.elapsed = round(time.monotonic() - started, 1)


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(MAX_FILE_BYTES)
    except OSError:
        return None


def _strip_code(text: str) -> str:
    """Prose only: what sits in a code sample is an example, not a statement."""
    return RE_INLINE_CODE.sub("", RE_FENCE.sub("", text))


def _fold(name: str) -> str:
    """A note name as a link matches it: case and runs of spaces do not count."""
    return " ".join(name.split()).lower()


def _ends_with(path: str, tail: str) -> bool:
    return path == tail or path.endswith("/" + tail)


def _resolve_wikilink(name: str, root: str,
                      by_stem: dict[str, list[str]]) -> str | None:
    """The note a [[wikilink]] names: by its path if it gives one, else by stem.

    `[[sub/deep]]` means the `deep` in a `sub/` folder, not whichever `deep` the
    walk met first. `root` ends in a separator, and every path starts with it.
    """
    want = _fold(name).lstrip("/")
    while want.startswith("./"):
        want = want[2:]
    base = want.rsplit("/", 1)[-1]
    stem, ext = os.path.splitext(base)
    candidates = by_stem.get(stem.rstrip() if ext in NOTE_EXT else base)
    if not candidates:
        return None
    if "/" in want:
        nearest = None
        for cand in candidates:
            rel = _fold(cand[len(root):])
            if rel == want or os.path.splitext(rel)[0] == want:
                return cand                 # the whole path from the root
            if nearest is None and (_ends_with(rel, want) or
                                    _ends_with(os.path.splitext(rel)[0], want)):
                nearest = cand
        if nearest:
            return nearest
    return candidates[0]


def _resolve_rel(origin: str, ref: str, universe: set[str]) -> str | None:
    ref = ref.split("#")[0].strip()
    if "%" in ref:
        ref = unquote(ref)            # [my note](my%20note.md), as GitHub wants it
    if not ref or ref.startswith(("/", "http")):
        return None
    base = os.path.dirname(origin)
    cand = os.path.normpath(os.path.join(base, ref))
    if cand in universe:
        return cand
    for ext in (".md", ".markdown", ".txt", ".org", ".rst"):
        if cand + ext in universe:
            return cand + ext
    return None


def _code_targets(path: str, text: str, universe: set[str]):
    ext = os.path.splitext(path)[1].lower()
    base = os.path.dirname(path)
    out: list[str] = []

    if ext in {".py", ".pyi"}:
        for mod in RE_PY_FROM.findall(text) + RE_PY_IMPORT.findall(text):
            for cand in _py_candidates(base, mod):
                if cand in universe:
                    out.append(cand)
                    break
    elif ext in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
        for ref in RE_JS_FROM.findall(text) + RE_JS_REQUIRE.findall(text):
            stem = os.path.normpath(os.path.join(base, ref))
            for suffix in JS_TRY:
                if stem + suffix in universe:
                    out.append(stem + suffix)
                    break
    elif ext in {".c", ".h", ".cc", ".cpp", ".hpp"}:
        for ref in RE_C_INCLUDE.findall(text):
            cand = os.path.normpath(os.path.join(base, ref))
            if cand in universe:
                out.append(cand)
    elif ext == ".go":
        out.extend(_go_targets(path, text, universe))
    elif ext == ".rs":
        out.extend(_rust_targets(path, text, universe))
    elif ext in {".sh", ".bash"}:
        for ref in RE_SH_SOURCE.findall(text):
            if ref.startswith(("$", "-", "/")):
                continue
            cand = os.path.normpath(os.path.join(base, ref))
            if cand in universe:
                out.append(cand)
    return out


def _py_candidates(base: str, mod: str):
    """Resolve `a.b.c` relative to the file's dir and a few parents up."""
    leading = len(mod) - len(mod.lstrip("."))
    parts = [p for p in mod.lstrip(".").split(".") if p]
    if not parts:
        return
    start = base
    for _ in range(max(0, leading - 1)):
        start = os.path.dirname(start)
    roots = [start]
    up = start
    for _ in range(3):                      # tolerate src/ package layouts
        up = os.path.dirname(up)
        roots.append(up)
    for root in roots:
        stem = os.path.join(root, *parts)
        yield stem + ".py"
        yield os.path.join(stem, "__init__.py")


# --- go ---------------------------------------------------------------------

_GO_MODULES: dict[str, tuple[str, str] | None] = {}


def _go_module(base: str) -> tuple[str, str] | None:
    """(module path, module directory) from the nearest go.mod above `base`.

    Cached per directory, including the misses, because most source files in a
    repository share one answer and the walk up is otherwise repeated per file.
    """
    if base in _GO_MODULES:
        return _GO_MODULES[base]

    chain: list[str] = []
    result: tuple[str, str] | None = None
    cur = base
    for _ in range(12):
        if cur in _GO_MODULES:
            result = _GO_MODULES[cur]
            break
        chain.append(cur)
        try:
            with open(os.path.join(cur, "go.mod"), "r", encoding="utf-8",
                      errors="ignore") as fh:
                match = RE_GO_MODULE.search(fh.read(8192))
            if match:
                result = (match.group(1), cur)
            break
        except OSError:
            pass
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    for d in chain:
        _GO_MODULES[d] = result
    return result


def _go_package_files(pkg: str, universe: set[str]) -> list[str]:
    """The source files of one Go package, which is just a directory."""
    try:
        with os.scandir(pkg) as it:
            names = sorted(e.name for e in it
                           if e.name.endswith(".go")
                           and not e.name.endswith("_test.go")
                           and e.is_file(follow_symlinks=False))
    except OSError:
        return []
    hits = [os.path.join(pkg, n) for n in names]
    return [h for h in hits if h in universe][:GO_PACKAGE_CAP]


def _go_targets(path: str, text: str, universe: set[str]) -> list[str]:
    module = _go_module(os.path.dirname(path))
    if not module:
        return []                       # no go.mod: every import is a stranger
    name, moddir = module

    refs = list(RE_GO_ONELINE.findall(text))
    for block in RE_GO_BLOCK.findall(text):
        refs.extend(RE_GO_QUOTED.findall(block))

    out: list[str] = []
    for ref in refs:
        if ref == name:
            rel = ""
        elif ref.startswith(name + "/"):
            rel = ref[len(name) + 1:]
        else:
            continue                    # a dependency, not code on this disk
        pkg = os.path.normpath(os.path.join(moddir, rel)) if rel else moddir
        if pkg != path and _inside(pkg, moddir):
            out.extend(f for f in _go_package_files(pkg, universe) if f != path)
    return out


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


# --- rust -------------------------------------------------------------------


def _crate_src(base: str) -> str:
    """The `src` directory that `crate::` counts from."""
    cur = base
    for _ in range(10):
        if os.path.isfile(os.path.join(cur, "Cargo.toml")):
            src = os.path.join(cur, "src")
            return src if os.path.isdir(src) else cur
        if os.path.basename(cur) == "src":
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return base


def _rs_module_file(directory: str, name: str, universe: set[str]) -> str | None:
    for cand in (os.path.join(directory, name + ".rs"),
                 os.path.join(directory, name, "mod.rs")):
        if cand in universe:
            return cand
    return None


def _rust_targets(path: str, text: str, universe: set[str]) -> list[str]:
    base = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    is_root = stem in ("mod", "lib", "main")
    # A 2018-edition module file `foo.rs` keeps its children in `foo/`.
    own = base if is_root else os.path.join(base, stem)
    out: list[str] = []

    for name in RE_RS_MOD.findall(text):
        for directory in ([base] if is_root else [own, base]):
            hit = _rs_module_file(directory, name, universe)
            if hit:
                out.append(hit)
                break

    for anchor, rest in RE_RS_USE.findall(text):
        if anchor == "crate":
            start = _crate_src(base)
        elif anchor == "super":
            start = os.path.dirname(base) if stem == "mod" else base
        else:
            start = own
        parts = [p for p in rest.split("::") if p]
        # `use a::b::c` may name a module or an item inside one, so walk the
        # path back until something on disk answers to it.
        while parts:
            hit = _rs_module_file(os.path.join(start, *parts[:-1]), parts[-1],
                                  universe)
            if hit:
                out.append(hit)
                break
            parts.pop()
    return out


# --- tags -------------------------------------------------------------------


def _clean_tag(raw: str) -> str | None:
    tag = raw.strip().strip("#").strip("/").strip()
    tag = tag.strip("\"'")
    if not tag or len(tag) > 60 or not tag[0].isalpha():
        return None
    if not all(c.isalnum() or c in "_-/" for c in tag):
        return None
    return tag.lower()


def frontmatter_tags(text: str) -> list[str]:
    """`tags:` out of a YAML front matter block, in any of its three shapes.

    Deliberately not a YAML parser: front matter is read from files nobody
    validated, and the three list shapes people actually write are worth far
    less than a dependency.
    """
    match = RE_FRONTMATTER.match(text)
    if not match:
        return []
    block = match.group(1)
    out: list[str] = []
    for entry in RE_YAML_TAGS.finditer(block):
        value = entry.group(2).strip()
        if value.startswith("["):                   # tags: [a, b]
            value = value.strip("[]")
            out += value.split(",")
        elif value:                                 # tags: a, b   /  tags: a
            out += value.split(",")
        else:                                       # tags:\n  - a\n  - b
            rest = block[entry.end():]
            for line in rest.split("\n"):
                stripped = line.strip()
                if stripped.startswith("- "):
                    out.append(stripped[2:])
                elif stripped and not line.startswith((" ", "\t")):
                    break
    return out


def note_tags(text: str) -> list[str]:
    """Every tag in a note: front matter first, then `#tags` in the prose."""
    found: list[str] = []
    seen: set[str] = set()

    def take(raw):
        tag = _clean_tag(raw)
        if tag and tag not in seen:
            seen.add(tag)
            found.append(tag)

    for raw in frontmatter_tags(text):
        take(raw)

    body = RE_FRONTMATTER.sub("", text, count=1)
    body = _strip_code(body)               # a shell script is not a tag list
    for raw in RE_TAG.findall(body):
        take(raw)
        if len(found) >= MAX_TAGS_PER_FILE:
            break
    return found[:MAX_TAGS_PER_FILE]


# --- ghosts -----------------------------------------------------------------


def ghost_key(name: str) -> str:
    """What every spelling of one missing note has in common.

    `[[Meeting Notes]]`, `[[meeting  notes]]` and `[[Meeting Notes.md]]` are
    one note nobody has written yet, so they are one ghost.
    """
    key = _fold(name)
    if key.endswith(".md"):
        key = key[:-3].rstrip()
    return key


def _is_attachment(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in ATTACHMENT_EXT


# --- suggested links --------------------------------------------------------


def note_terms(text: str, vocab: dict | None = None) -> dict[str, int]:
    """The words a note is about, with how often it uses each.

    Prose only: code, URLs and link targets are examples and addresses, not
    what the note says. A plural folds into its singular so `forecasts` meets
    `forecast`, and no further: a real stemmer would be a dependency, and
    `forecasting` meeting `forecast` is not worth one.

    `vocab`, shared across notes, keeps one copy of each word: otherwise a
    word in a thousand notes is a thousand strings, and that is most of the
    memory the pairing needs.
    """
    match = RE_FRONTMATTER.match(text)
    if match:
        text = RE_YAML_KEY.sub(" ", match.group(1)) + "\n" + text[match.end():]
    text = _strip_code(text)
    text = RE_LINK_TARGET.sub(" ", text)
    text = RE_URL.sub(" ", text)

    terms: dict[str, int] = {}
    for word, count in Counter(RE_TERM.findall(text.lower())).items():
        if word in STOPWORDS or word.isdigit():
            continue
        # `ss`, `us` and `is` end words rather than plurals: class, focus,
        # analysis. Folding those would show "focu" as a reason to link.
        if len(word) > 4 and word[-1] == "s" and word[-2] not in "isu":
            word = word[:-1]
            if word in STOPWORDS:
                continue
        if vocab is not None:
            word = vocab.setdefault(word, word)
        terms[word] = terms.get(word, 0) + count
    return terms


def suggest_links(docs: list, joined=frozenset(),
                  over_budget=None) -> list | None:
    """Pairs of notes that talk about the same things but do not link.

    `docs` is [(path, {term: count}), ...] and `joined` holds the (a, b) pairs,
    a < b, that already link one way or the other. The answer is
    [[a, b, score, [term, ...]], ...] with a < b, strongest first -- or None
    when `over_budget` calls time part way, since half a pairing is not one.

    Each note becomes a TF-IDF vector cut to its strongest words, and is only
    compared with the notes that share one of them, through an inverted index.
    All pairs of four thousand notes is eight million comparisons, and nearly
    all of them are between notes with nothing in common.
    """
    n = len(docs)
    if n < 3:
        return []                   # a word cannot be rare among two notes

    df: Counter = Counter()
    for _, terms in docs:
        df.update(terms.keys())
    # A word in one note cannot pair it with anything, and a word in most of
    # them tells none of them apart. Under six notes "most" has to mean all:
    # half of five is two, and two notes sharing a word is the whole point.
    most = n / 2 if n >= 6 else n - 1
    idf = {t: math.log(n / d) for t, d in df.items() if 1 < d <= most}

    vectors: list[dict[str, float]] = []
    postings: dict[str, tuple[list[int], list[float]]] = {}
    for i, (_, terms) in enumerate(docs):
        weights = [(t, (1 + math.log(c)) * idf[t])
                   for t, c in terms.items() if t in idf]
        norm = math.sqrt(sum(w * w for _, w in weights))
        if len(weights) > SUGGEST_KEEP_TERMS:
            weights = heapq.nlargest(SUGGEST_KEEP_TERMS, weights,
                                     key=lambda tw: tw[1])
        vec: dict[str, float] = {}
        for t, w in weights:
            w /= norm
            vec[t] = w
            ids, ws = postings.setdefault(t, ([], []))
            ids.append(i)
            ws.append(w)
        vectors.append(vec)

    best: list[list] = [[] for _ in range(n)]   # min-heaps of (score, partner)
    for i, vec in enumerate(vectors):
        if over_budget is not None and over_budget():
            return None
        dots: dict[int, float] = {}
        for t, w in vec.items():
            ids, ws = postings[t]
            # Postings are in note order, so only the tail after this note is
            # read: each pair is scored once, from its first note.
            start = bisect_right(ids, i)
            for j, wj in zip(ids[start:], ws[start:]):
                dots[j] = dots.get(j, 0.0) + w * wj
        here = docs[i][0]
        for j, score in dots.items():
            if score < SUGGEST_MIN:
                continue
            if not (_has_room(best[i], score) or _has_room(best[j], score)):
                continue
            there = docs[j][0]
            if (here, there) in joined or (there, here) in joined:
                continue
            if sum(1 for t in vec if t in vectors[j]) < SUGGEST_MIN_SHARED:
                continue
            _offer(best[i], score, j)
            _offer(best[j], score, i)

    pairs: dict[tuple[int, int], float] = {}
    for i, heap in enumerate(best):
        for score, j in heap:
            pairs[(i, j) if i < j else (j, i)] = score

    out: list[list] = []
    for (i, j), score in pairs.items():
        a, b = sorted((docs[i][0], docs[j][0]))
        shared = sorted((-vectors[i][t] * vectors[j][t], t)
                        for t in vectors[i] if t in vectors[j])
        out.append([a, b, round(score, 2),
                    [t for _, t in shared[:SUGGEST_TERMS]]])
    out.sort(key=lambda s: (-s[2], s[0], s[1]))
    return out


def _has_room(heap: list, score: float) -> bool:
    return len(heap) < SUGGEST_PER_NOTE or score > heap[0][0]


def _offer(heap: list, score: float, partner: int) -> None:
    """Keep the best SUGGEST_PER_NOTE partners, in a min-heap so the worst
    of them is the one at hand to replace."""
    if len(heap) < SUGGEST_PER_NOTE:
        heapq.heappush(heap, (score, partner))
    elif score > heap[0][0]:
        heapq.heapreplace(heap, (score, partner))
