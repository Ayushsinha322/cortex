"""Searching inside files, not just their names.

Filename search answers "where did I put it".  This answers "where did I say
that", which is the question you actually have about your own notes.

`rg` is used when it is installed, because it is much faster than anything
this module could do and most people who would run cortex already have it.
The fallback is a plain walk, so nothing is a hard requirement.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

MAX_HITS = 200               # per search; the UI cannot use more than this
MAX_LINE = 240               # characters of context kept per hit
MAX_FILE_BYTES = 2 * 1024 * 1024
TIMEOUT = 8.0                # seconds before we give up on the search

# Extensions worth reading. Searching a 4GB video for the word "budget" is
# time spent to learn nothing.
TEXT_EXT = {
    ".md", ".markdown", ".mdx", ".txt", ".org", ".rst", ".adoc", ".norg",
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".go", ".rs",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".java", ".kt", ".swift", ".rb",
    ".php", ".pl", ".lua", ".r", ".jl", ".sh", ".bash", ".zsh", ".fish",
    ".ps1", ".vim", ".el", ".sql", ".json", ".jsonc", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".env", ".tf", ".hcl", ".html", ".css",
    ".scss", ".xml", ".csv", ".tsv", ".tex", ".ipynb", ".log",
}

NO_EXT_OK = {"readme", "license", "changelog", "todo", "notes", "makefile",
             "dockerfile"}


def searchable(name: str) -> bool:
    stem, ext = os.path.splitext(name.lower())
    return ext in TEXT_EXT or (not ext and stem in NO_EXT_OK)


def search(scanner, term: str, limit: int = MAX_HITS) -> dict:
    """Find `term` inside files under the scanner's root.

    Returns {"hits": [...], "engine": "rg"|"python", "truncated": bool}, each
    hit carrying the path, the 1-based line number, and the line itself.
    """
    term = term.strip()
    if len(term) < 2:
        return {"hits": [], "engine": "none", "truncated": False}

    if shutil.which("rg"):
        found = _ripgrep(scanner, term, limit)
        if found is not None:
            return found
    return _python(scanner, term, limit)


def _clip(line: str) -> str:
    line = line.rstrip("\n").replace("\t", "    ")
    return line[:MAX_LINE] if len(line) > MAX_LINE else line


# -- ripgrep -----------------------------------------------------------------

def _ripgrep(scanner, term: str, limit: int) -> dict | None:
    argv = ["rg", "--no-heading", "--line-number", "--fixed-strings",
            "--ignore-case", "--no-messages", "--color=never",
            f"--max-count={limit}", f"--max-filesize={MAX_FILE_BYTES}",
            "--", term, scanner.root]
    if scanner.show_hidden:
        argv.insert(1, "--hidden")
    if not scanner.use_gitignore:
        argv.insert(1, "--no-ignore-vcs")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None                      # fall back rather than fail the search
    if proc.returncode not in (0, 1):    # 1 is "no matches", which is a result
        return None
    hits = parse_rg(scanner, proc.stdout, limit)
    return {"hits": hits, "engine": "rg", "truncated": len(hits) >= limit}


def parse_rg(scanner, output: str, limit: int) -> list[dict]:
    """`path:line:text` per line.

    A path may itself contain colons, and rg does not quote them, so the split
    is anchored on the line number instead: the first field that parses as an
    integer and leaves a path we recognise.
    """
    hits: list[dict] = []
    for raw in output.splitlines():
        path, sep, rest = raw.partition(":")
        while sep:
            lineno, sep2, text = rest.partition(":")
            if sep2 and lineno.isdigit():
                if scanner.inside(path) and searchable(os.path.basename(path)):
                    hits.append({"path": path, "line": int(lineno),
                                 "text": _clip(text)})
                break
            # the colon belonged to the path; take the next one
            path = path + ":" + lineno
            rest, sep = text, sep2
        if len(hits) >= limit:
            break
    return hits


# -- fallback ----------------------------------------------------------------

def _python(scanner, term: str, limit: int) -> dict:
    from . import ignore as ignore_rules

    needle = re.compile(re.escape(term), re.I)
    hits: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(scanner.root, followlinks=False):
        chain = scanner.ignore_chain(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if not scanner.skip(d, True)
            and not (chain and ignore_rules.decide(
                chain, os.path.join(dirpath, d), True))
        ]
        for name in filenames:
            if scanner.skip(name, False) or not searchable(name):
                continue
            full = os.path.join(dirpath, name)
            if chain and ignore_rules.decide(chain, full, False):
                continue
            for hit in _scan_file(full, needle, limit - len(hits)):
                hits.append(hit)
            if len(hits) >= limit:
                return {"hits": hits[:limit], "engine": "python",
                        "truncated": True}
    return {"hits": hits, "engine": "python", "truncated": False}


def _scan_file(path: str, needle: re.Pattern, room: int):
    if room <= 0:
        return
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for number, line in enumerate(fh, 1):
                if needle.search(line):
                    yield {"path": path, "line": number, "text": _clip(line)}
                    room -= 1
                    if room <= 0:
                        return
    except OSError:
        return
