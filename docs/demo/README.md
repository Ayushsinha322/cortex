# The live demo

The real cortex UI, served as static files, reading a frozen copy of this
repository instead of a local server.

`demo-shim.js` replaces `window.fetch`, which is the only way the UI talks to
the outside world, so every screen works untouched: the graph, the reader,
both searches, the connections list, the sidebar. Two things cannot be frozen
and are reimplemented in the shim — name search and content search, which the
server computes per query — and one thing is honestly disabled: opening a file
in your editor needs cortex running on your machine, so it answers with a note
saying so.

## Regenerating it

The fixture is harvested from a real run, so it is never hand-written:

```bash
python3 cortex.py ~/cortex -w none -p 41288 --no-watch   # in one terminal
python3 docs/demo/harvest.py http://127.0.0.1:41288 <token> /tmp/demo-data.json
```

`harvest.py` walks every folder through `/api/children`, takes a preview of
every file, and records the link index, git status and saved layout. It
rewrites the real home directory out of every path on the way through.

Then copy `index.html`, `app.js`, `style.css`, `markdown.js` and
`highlight.js` from `cortex/ui/`, apply the two patches this directory needs —
relative asset paths and the `rawURL` fallback that lets images resolve to
files Pages already serves — and drop the fixture in as `demo-data.json`.

## What is served where

GitHub Pages serves this repository's `docs/` directory, so `docs/graph.jpg`
is one level above the demo page. That is why `demo-data.json` maps the few
binary files to `../`-relative URLs rather than shipping copies of them.
