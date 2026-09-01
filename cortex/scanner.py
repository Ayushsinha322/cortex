"""Filesystem scanning: turns directories and files into graph nodes.

Everything is lazy.  We never walk the whole tree up front -- a home directory
can be hundreds of gigabytes.  The UI asks for one directory at a time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Directories that are noise in a knowledge graph: caches, build output,
# dependency trees, browser profiles, language toolchains.
IGNORE_DIRS = {
    ".git", ".svn", ".hg", ".bzr",
    "node_modules", "bower_components", "vendor", "site-packages",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "virtualenv", ".eggs", "*.egg-info",
    "target", "dist", "build", "out", ".next", ".nuxt", ".parcel-cache",
    ".gradle", ".m2", ".ivy2", ".sbt", ".stack-work",
    ".cache", ".npm", ".yarn", ".pnpm-store", ".cargo", ".rustup", ".bun",
    ".local", ".config", ".dbus", ".gvfs", ".pki", ".gnupg", ".ssh",
    ".mozilla", ".thunderbird", ".chrome", ".chromium", "google-chrome",
    ".steam", ".wine", ".docker", ".kube", ".minikube", ".vagrant",
    ".terraform", ".serverless", ".idea", ".vscode-server", ".vs",
    "snap", "flatpak", ".java", ".gem", ".rbenv", ".pyenv", ".nvm", ".sdkman",
    ".npm-global", ".msf4", ".BurpSuite", ".android", ".gnome", ".kde",
    "lost+found", ".Trash", ".trash", ".thumbnails", ".fonts",
}

# Extensions that are compiler droppings or editor scratch, never interesting.
IGNORE_EXT = {
    ".pyc", ".pyo", ".pyd", ".o", ".obj", ".so", ".dylib", ".dll", ".a",
    ".lib", ".class", ".jar-cache", ".swp", ".swo", ".swn", ".tmp", ".temp",
    ".lock", ".pid", ".rlib", ".rmeta", ".d", ".gch", ".ilk", ".pdb",
}

IGNORE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".gitkeep"}

# Node groups drive colour and behaviour in the UI.
_GROUPS: list[tuple[str, set[str]]] = [
    ("note", {".md", ".markdown", ".mdx", ".txt", ".org", ".rst", ".adoc",
              ".norg", ".wiki", ".textile"}),
    ("code", {".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
              ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".java",
              ".kt", ".swift", ".rb", ".php", ".pl", ".lua", ".r", ".jl",
              ".sh", ".bash", ".zsh", ".fish", ".ps1", ".vim", ".el",
              ".sql", ".ipynb", ".hs", ".ml", ".ex", ".exs", ".erl", ".scala",
              ".dart", ".zig", ".nim", ".v", ".asm", ".s"}),
    ("config", {".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml", ".ini",
                ".cfg", ".conf", ".config", ".env", ".properties", ".service",
                ".socket", ".timer", ".rules", ".plist", ".tf", ".tfvars",
                ".hcl", ".gradle", ".cmake", ".mk", ".dockerfile"}),
    ("doc", {".pdf", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods",
             ".ppt", ".pptx", ".odp", ".csv", ".tsv", ".epub", ".mobi",
             ".djvu", ".tex"}),
    ("media", {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
               ".ico", ".tiff", ".avif", ".heic", ".mp4", ".mkv", ".mov",
               ".avi", ".webm", ".mp3", ".wav", ".flac", ".ogg", ".m4a"}),
    ("archive", {".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".zst", ".7z",
                 ".rar", ".deb", ".rpm", ".appimage", ".iso", ".img", ".dmg",
                 ".whl", ".pkl", ".pt", ".onnx", ".h5", ".bin", ".dat",
                 ".sqlite", ".db"}),
]
EXT_GROUP: dict[str, str] = {e: g for g, exts in _GROUPS for e in exts}

# Files with no extension that still deserve to be in the graph.
SPECIAL_NAMES = {
    "readme": "note", "license": "note", "changelog": "note", "todo": "note",
    "notes": "note", "makefile": "config", "dockerfile": "config",
    "vagrantfile": "config", "jenkinsfile": "config", "procfile": "config",
    "gemfile": "config", "rakefile": "config", "cmakelists.txt": "config",
}


def group_of(name: str, is_dir: bool) -> str:
    if is_dir:
        return "dir"
    lower = name.lower()
    if lower in SPECIAL_NAMES:
        return SPECIAL_NAMES[lower]
    stem, ext = os.path.splitext(lower)
    if stem in SPECIAL_NAMES and not ext:
        return SPECIAL_NAMES[stem]
    return EXT_GROUP.get(ext, "other")


@dataclass
class Scanner:
    root: str
    show_hidden: bool = False
    extra_ignores: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.root = os.path.realpath(os.path.expanduser(self.root))
        self._ignore_dirs = IGNORE_DIRS | self.extra_ignores

    # -- guards ------------------------------------------------------------

    def inside(self, path: str) -> bool:
        """True when path is the root or genuinely under it (no symlink escapes)."""
        try:
            real = os.path.realpath(path)
        except OSError:
            return False
        return real == self.root or real.startswith(self.root + os.sep)

    def skip(self, name: str, is_dir: bool) -> bool:
        if name in IGNORE_NAMES:
            return True
        if name.startswith("."):
            if is_dir and name in self._ignore_dirs:
                return True
            if not self.show_hidden:
                return True
        if is_dir:
            return name in self._ignore_dirs
        return os.path.splitext(name)[1].lower() in IGNORE_EXT

    # -- node building -----------------------------------------------------

    def node(self, path: str, entry: os.DirEntry | None = None) -> dict:
        try:
            is_dir = entry.is_dir(follow_symlinks=False) if entry else os.path.isdir(path)
            st = entry.stat(follow_symlinks=False) if entry else os.stat(path)
            size, mtime = st.st_size, st.st_mtime
        except OSError:
            is_dir, size, mtime = False, 0, 0.0

        name = os.path.basename(path) or path
        node = {
            "id": path,
            "name": name,
            "dir": is_dir,
            "group": group_of(name, is_dir),
            "size": size,
            "mtime": mtime,
            "parent": None if path == self.root else os.path.dirname(path),
        }
        if is_dir:
            node["kids"] = self.count_children(path)
        return node

    def count_children(self, path: str, cap: int = 400) -> int:
        """Cheap child count -- drives node size in the graph."""
        try:
            total = 0
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if self.skip(e.name, e.is_dir(follow_symlinks=False)):
                            continue
                    except OSError:
                        continue
                    total += 1
                    if total >= cap:
                        break
            return total
        except OSError:
            return 0

    def children(self, path: str, limit: int = 240) -> list[dict]:
        """Direct children of a directory: folders first, then files, A-Z."""
        if not self.inside(path) or not os.path.isdir(path):
            return []
        out: list[dict] = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if self.skip(e.name, is_dir):
                        continue
                    out.append(self.node(os.path.join(path, e.name), e))
        except OSError:
            return []
        out.sort(key=lambda n: (not n["dir"], n["name"].lower()))
        return out[:limit]

    # -- search ------------------------------------------------------------

    def search(self, term: str, limit: int = 140, max_dirs: int = 40000) -> list[dict]:
        """Substring match on names anywhere under root.

        Each hit carries its ancestor chain so the UI can graft it onto the
        graph with its real folder lineage instead of floating loose.
        """
        term = term.strip().lower()
        if len(term) < 2:
            return []

        hits: list[dict] = []
        seen_dirs = 0
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not self.skip(d, True))
            seen_dirs += 1
            if seen_dirs > max_dirs or len(hits) >= limit:
                break
            for name in dirnames + filenames:
                if term not in name.lower():
                    continue
                full = os.path.join(dirpath, name)
                is_dir = name in dirnames
                if not is_dir and self.skip(name, False):
                    continue
                hits.append(self._hit(full))
                if len(hits) >= limit:
                    break

        # Exact-ish matches first, then shallower paths.
        hits.sort(key=lambda h: (
            0 if h["node"]["name"].lower() == term else
            1 if h["node"]["name"].lower().startswith(term) else 2,
            len(h["ancestors"]),
        ))
        return hits

    def _hit(self, full: str) -> dict:
        rel = os.path.relpath(full, self.root)
        parts = rel.split(os.sep)[:-1]
        ancestors, cur = [], self.root
        for part in parts:
            cur = os.path.join(cur, part)
            ancestors.append(self.node(cur))
        return {"node": self.node(full), "ancestors": ancestors}
