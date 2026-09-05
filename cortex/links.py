"""Semantic edges: the part that makes this a brain and not just a tree.

Two kinds of link are discovered in the background after launch:

  note -> note   [[wikilinks]] and relative markdown links
  code -> code   relative imports / requires / includes

Obsidian only does the first, and only inside one vault.  We do both, across
the whole home directory.
"""

from __future__ import annotations

import os
import re
import threading
import time

NOTE_EXT = {".md", ".markdown", ".mdx", ".txt", ".org", ".rst", ".adoc"}
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".c", ".h", ".cc", ".cpp", ".hpp", ".sh", ".bash"}

MAX_FILE_BYTES = 512 * 1024      # don't parse giant generated files
MAX_WALK_FILES = 400_000         # ceiling on the resolution universe
MAX_PARSE_FILES = 120_000        # ceiling on files we actually read
TIME_BUDGET = 150.0              # seconds; a huge home should not spin forever
PROGRESS_EVERY = 400             # publish partial results this often

# Generated or vendored bundles: parsing them yields noise, not structure.
SKIP_SUFFIX = (".min.js", ".min.css", ".bundle.js", ".pack.js", "-lock.json",
               ".d.ts")

# --- note links -------------------------------------------------------------

RE_WIKILINK = re.compile(r"\[\[\s*([^\]\|#\n]+)")
RE_MDLINK = re.compile(r"\]\(\s*(?!https?:|mailto:|#)([^)\s]+)")

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
        self.elapsed = 0.0
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._build, daemon=True,
                                        name="cortex-linkindex")
        self._thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"ready": self.ready, "edges": list(self.edges),
                    "notes": self.notes, "sources": self.sources,
                    "elapsed": self.elapsed}

    # -- build -------------------------------------------------------------

    def _build(self) -> None:
        started = time.monotonic()
        _GO_MODULES.clear()          # go.mod contents may have changed
        note_paths: list[str] = []
        code_paths: list[str] = []
        by_stem: dict[str, str] = {}       # note stem -> path (first wins)
        all_files: set[str] = set()

        sc = self.scanner
        walked = 0
        for dirpath, dirnames, filenames in os.walk(sc.root, followlinks=False):
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
                    by_stem.setdefault(os.path.splitext(fn)[0].lower(), full)
                elif ext in CODE_EXT:
                    code_paths.append(full)
            if walked >= MAX_WALK_FILES:
                break

        with self._lock:
            self.notes = len(note_paths)
            self.sources = len(code_paths)

        edges: list[list] = []
        seen: set[tuple[str, str]] = set()

        def emit(a: str, b: str, kind: str) -> None:
            if a == b:
                return
            key = (a, b) if a < b else (b, a)
            if key in seen:
                return
            seen.add(key)
            edges.append([a, b, kind])

        def over_budget() -> bool:
            return time.monotonic() - started > TIME_BUDGET

        # Notes first: they are what a "second brain" is mostly made of, and
        # there are far fewer of them than source files.
        for i, path in enumerate(note_paths[:MAX_PARSE_FILES]):
            text = _read(path)
            if text:
                for raw in RE_WIKILINK.findall(text):
                    target = by_stem.get(os.path.splitext(raw.strip())[0].lower())
                    if target:
                        emit(path, target, "note")
                for raw in RE_MDLINK.findall(text):
                    target = _resolve_rel(path, raw, all_files)
                    if target:
                        emit(path, target, "note")
            if i % PROGRESS_EVERY == 0:
                with self._lock:
                    self.edges = list(edges)
                if over_budget():
                    break

        budget_left = max(0, MAX_PARSE_FILES - len(note_paths))
        for i, path in enumerate(code_paths[:budget_left]):
            text = _read(path)
            if text:
                for target in _code_targets(path, text, all_files):
                    emit(path, target, "code")
            if i % PROGRESS_EVERY == 0:
                with self._lock:
                    self.edges = list(edges)
                if over_budget():
                    break

        with self._lock:
            self.edges = edges
            self.ready = True
            self.elapsed = round(time.monotonic() - started, 1)


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read(MAX_FILE_BYTES)
    except OSError:
        return None


def _resolve_rel(origin: str, ref: str, universe: set[str]) -> str | None:
    ref = ref.split("#")[0].strip()
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
