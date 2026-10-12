/* The server, played by data frozen into the page.

   A snapshot is the cortex window saved as one file. Everything the window
   asks of the server goes through fetch(), so standing in for fetch is enough
   to stand in for the server: the answers come from window.SNAPSHOT, written
   into the file when it was made. The two searches are worked out here, since
   the server computes them per query and a frozen answer could not cover them.
   Anything that would change something says plainly that this is a snapshot. */
(function () {
  const D = window.SNAPSHOT;
  if (!D) return;
  const realFetch = window.fetch ? window.fetch.bind(window) : null;

  const byPath = { [D.root.id]: D.root };
  for (const kids of Object.values(D.children)) for (const k of kids) byPath[k.id] = k;

  const ancestorsOf = (path) => {
    const rel = path.slice(D.root.id.length + 1).split("/").slice(0, -1);
    const out = [];
    let cur = D.root.id;
    for (const part of rel) {
      cur = cur + "/" + part;
      if (byPath[cur]) out.push(byPath[cur]);
    }
    return out;
  };

  function search(term) {
    term = (term || "").trim().toLowerCase();
    if (term.length < 2) return { results: [], count: 0 };
    const hits = [];
    for (const [path, node] of Object.entries(byPath)) {
      if (path === D.root.id || !node.name.toLowerCase().includes(term)) continue;
      hits.push({ node, ancestors: ancestorsOf(path) });
    }
    const rank = (h) => {
      const n = h.node.name.toLowerCase();
      return n === term ? 0 : n.startsWith(term) ? 1 : 2;
    };
    hits.sort((a, b) => rank(a) - rank(b) || a.ancestors.length - b.ancestors.length
                        || a.node.name.localeCompare(b.node.name));
    const results = hits.slice(0, 140);
    return { results, count: results.length };
  }

  function grep(term) {
    term = (term || "").trim();
    if (term.length < 2) return { hits: [], engine: "snapshot", truncated: false };
    const needle = term.toLowerCase(), hits = [];
    for (const [path, p] of Object.entries(D.previews)) {
      if (typeof p.text !== "string") continue;
      const lines = p.text.split("\n");
      for (let i = 0; i < lines.length && hits.length < 200; i++) {
        if (!lines[i].toLowerCase().includes(needle)) continue;
        let text = lines[i].trim();
        if (text.length > 160) text = text.slice(0, 160) + "…";
        hits.push({ path, line: i + 1, text });
      }
    }
    return { hits, engine: "snapshot", truncated: hits.length >= 200 };
  }

  function nodes(paths) {
    const hits = [];
    for (const p of paths || []) {
      if (byPath[p]) hits.push({ node: byPath[p], ancestors: ancestorsOf(p) });
    }
    return { hits };
  }

  const json = (body, status = 200) => Promise.resolve(new Response(
    JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  const readOnly = { ok: false, error: "this is a snapshot — it cannot change anything" };

  // images small enough to travel inside the file, by path, for the reader
  if (window.CORTEX) window.CORTEX.raw = D.raw || {};

  window.fetch = function (input, init) {
    const href = typeof input === "string" ? input
               : input instanceof URL ? input.href : (input && input.url) || String(input);
    let url;
    try { url = new URL(href, location.href); } catch (e) { url = null; }
    if (!url || !url.pathname.includes("/api/")) {
      return realFetch ? realFetch(input, init) : json({ error: "not found" }, 404);
    }
    const route = "/api/" + url.pathname.split("/api/").pop();
    const q = url.searchParams;
    const method = ((init && init.method) || "GET").toUpperCase();
    let body = null;
    try { body = init && init.body ? JSON.parse(init.body) : null; } catch (e) { body = null; }

    if (method === "POST") {
      if (route === "/api/hello" || route === "/api/bye" || route === "/api/layout") {
        return json({ ok: true });
      }
      if (route === "/api/nodes") return json(nodes(body && body.paths));
      return json(readOnly);
    }
    switch (route) {
      case "/api/root": return json(D.root);
      case "/api/children": return json(D.children[q.get("path")] || []);
      case "/api/preview": return json(D.previews[q.get("path")] ||
        { kind: "error", message: "Not included in this snapshot." });
      case "/api/links": return json(D.links);
      case "/api/git": return json(D.git);
      case "/api/layout": return json(D.layout);
      case "/api/editors": return json({ editors: [], terminal: null });
      case "/api/pulse": return json({ changed: [], reindexed: false, root: D.root.id });
      case "/api/search": return json(search(q.get("q")));
      case "/api/grep": return json(grep(q.get("q")));
      case "/api/tours": return json({ tours: D.tours || [] });
      case "/api/tour": {
        const t = (D.tourText || {})[q.get("path")];
        return json(t ? { ok: true, ...t } : { ok: false, error: "not in this snapshot" });
      }
      case "/api/views": return json({ views: [] });
      case "/api/source": return json(readOnly);
      default: return json({ ok: false, error: "not in a snapshot" });
    }
  };
})();
