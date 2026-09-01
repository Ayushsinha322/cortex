"""In-app reading: decide how a file should be shown and extract its content.

Markdown, code, notebooks, CSV and DOCX are turned into something readable
without leaving the graph.  PDFs and images are streamed raw and handed to the
browser's own viewer, which is already excellent.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile

TEXT_LIMIT = 600_000          # bytes of text we are willing to ship to the UI
LINE_LIMIT = 4000
CSV_ROWS = 300

MARKDOWN_EXT = {".md", ".markdown", ".mdx"}
PDF_EXT = {".pdf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico",
             ".avif"}
CSV_EXT = {".csv", ".tsv"}
AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov"}

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".fish": "bash", ".ps1": "powershell", ".sql": "sql", ".lua": "lua",
    ".vim": "vim", ".el": "lisp", ".json": "json", ".jsonc": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini",
    ".conf": "ini", ".cfg": "ini", ".env": "bash", ".tf": "hcl", ".hcl": "hcl",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "css",
    ".xml": "xml", ".svg": "xml", ".dockerfile": "docker", ".mk": "makefile",
    ".rst": "text", ".txt": "text", ".org": "text", ".log": "text",
}

MIME = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".avif": "image/avif", ".mp4": "video/mp4", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".flac": "audio/flac", ".m4a": "audio/mp4",
}


def mime_for(path: str) -> str:
    return MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def preview(path: str) -> dict:
    """Return {kind, ...payload} describing how to render this file."""
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path).lower()
    try:
        size = os.path.getsize(path)
    except OSError:
        return {"kind": "error", "message": "cannot stat file"}

    base = {"size": size, "ext": ext}

    if ext in PDF_EXT:
        return {**base, "kind": "pdf"}
    if ext in IMAGE_EXT:
        return {**base, "kind": "image"}
    if ext in VIDEO_EXT:
        return {**base, "kind": "video"}
    if ext in AUDIO_EXT:
        return {**base, "kind": "audio"}
    if ext == ".ipynb":
        return {**base, "kind": "markdown", "text": _notebook(path)}
    if ext == ".docx":
        return {**base, "kind": "text", "lang": "text", "text": _docx(path)}
    if ext in CSV_EXT:
        return {**base, "kind": "table", **_table(path, ext)}

    if size > TEXT_LIMIT * 4:
        return {**base, "kind": "toobig"}

    text = _read_text(path)
    if text is None:
        return {**base, "kind": "binary"}

    lines = text.count("\n")
    truncated = False
    if lines > LINE_LIMIT:
        text = "\n".join(text.split("\n")[:LINE_LIMIT])
        truncated = True

    if ext in MARKDOWN_EXT or name in {"readme", "license", "changelog", "todo"}:
        return {**base, "kind": "markdown", "text": text, "truncated": truncated}

    lang = LANG_BY_EXT.get(ext, "text")
    if not ext:
        first = text[:200]
        if first.startswith("#!"):
            lang = "bash" if "sh" in first.split("\n")[0] else "python"
    return {**base, "kind": "text", "lang": lang, "text": text,
            "truncated": truncated, "lines": min(lines, LINE_LIMIT)}


def _read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(TEXT_LIMIT)
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except Exception:
            return None


def _table(path: str, ext: str) -> dict:
    delim = "\t" if ext == ".tsv" else ","
    rows: list[list[str]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as fh:
            for i, row in enumerate(csv.reader(fh, delimiter=delim)):
                if i >= CSV_ROWS:
                    return {"rows": rows, "truncated": True}
                rows.append([c[:200] for c in row[:40]])
    except OSError as exc:
        return {"rows": [], "error": str(exc)}
    return {"rows": rows, "truncated": False}


def _notebook(path: str) -> str:
    """Flatten a Jupyter notebook into readable markdown."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            nb = json.load(fh)
    except (OSError, ValueError) as exc:
        return f"could not parse notebook: {exc}"
    lang = (nb.get("metadata", {}).get("language_info", {}).get("name")
            or "python")
    out: list[str] = []
    for cell in nb.get("cells", [])[:400]:
        src = "".join(cell.get("source", []))
        if not src.strip():
            continue
        if cell.get("cell_type") == "markdown":
            out.append(src)
        else:
            out.append(f"```{lang}\n{src}\n```")
    return "\n\n".join(out) or "(empty notebook)"


def _docx(path: str) -> str:
    """Pull visible paragraph text out of a .docx without any dependency."""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return f"could not read docx: {exc}"
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines[:LINE_LIMIT]).strip() or "(no text found)"


def stream(path: str, chunk: int = 256 * 1024):
    with open(path, "rb") as fh:
        while True:
            data = fh.read(chunk)
            if not data:
                return
            yield data
