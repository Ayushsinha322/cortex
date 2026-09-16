/* Serves a frozen cortex to the page, so the demo needs no server.
 *
 * Every call the UI makes goes through fetch(), so intercepting fetch is
 * enough to stand in for the whole local API. The data was harvested from a
 * real `cortex ~/cortex` run and is served verbatim; the only endpoints
 * reimplemented here are the two searches, which the server computes per
 * query and so cannot be frozen.
 */
(function () {
  const realFetch = window.fetch.bind(window);
  const DATA = realFetch("demo-data.json").then((r) => r.json());

  // Every node, with its ancestor chain, the way /api/search returns them.
  function buildIndex(d) {
    const byPath = { [d.root.id]: d.root };
    for (const kids of Object.values(d.children))
      for (const k of kids) byPath[k.id] = k;

    const ancestorsOf = (path) => {
      const rel = path.slice(d.root.id.length + 1).split("/").slice(0, -1);
      const out = [];
      let cur = d.root.id;
      for (const part of rel) {
        cur = cur + "/" + part;
        if (byPath[cur]) out.push(byPath[cur]);
      }
      return out;
    };
    return { byPath, ancestorsOf };
  }

  function search(d, ix, term) {
    term = (term || "").trim().toLowerCase();
    if (term.length < 2) return { results: [], count: 0 };
    const hits = [];
    for (const [path, node] of Object.entries(ix.byPath)) {
      if (path === d.root.id) continue;
      if (!node.name.toLowerCase().includes(term)) continue;
      hits.push({ node, ancestors: ix.ancestorsOf(path) });
    }
    hits.sort((a, b) => {
      const rank = (h) => {
        const n = h.node.name.toLowerCase();
        return n === term ? 0 : n.startsWith(term) ? 1 : 2;
      };
      return rank(a) - rank(b) || a.ancestors.length - b.ancestors.length
          || a.node.name.localeCompare(b.node.name);
    });
    const results = hits.slice(0, 140);
    return { results, count: results.length };
  }

  function grep(d, term) {
    term = (term || "").trim();
    if (term.length < 2) return { hits: [], engine: "demo", truncated: false };
    const needle = term.toLowerCase();
    const hits = [];
    for (const [path, p] of Object.entries(d.previews)) {
      if (typeof p.text !== "string") continue;   // only what the reader can read
      const lines = p.text.split("\n");
      for (let i = 0; i < lines.length; i++) {
        if (!lines[i].toLowerCase().includes(needle)) continue;
        let text = lines[i].trim();
        if (text.length > 160) text = text.slice(0, 160) + "…";
        hits.push({ path, line: i + 1, text });
        if (hits.length >= 200) break;
      }
      if (hits.length >= 200) break;
    }
    return { hits, engine: "demo", truncated: hits.length >= 200 };
  }

  const json = (body) =>
    new Response(JSON.stringify(body), {
      status: 200, headers: { "content-type": "application/json" },
    });

  let ix = null;

  window.fetch = async function (input, init) {
    // app.js calls fetch(new URL(...)), so handle a URL object as well as
    // a string or a Request; a URL has .href where a Request has .url.
    const href = typeof input === "string" ? input
               : input instanceof URL ? input.href
               : (input && input.url) || String(input);
    let url;
    try { url = new URL(href, location.href); }
    catch { return realFetch(input, init); }

    if (!url.pathname.endsWith("/api/") && !url.pathname.includes("/api/"))
      return realFetch(input, init);

    const d = await DATA;
    if (!ix) ix = buildIndex(d);

    const route = "/api/" + url.pathname.split("/api/").pop();
    const path = url.searchParams.get("path");
    const q = url.searchParams.get("q");
    const method = ((init && init.method) || "GET").toUpperCase();

    if (method === "POST") {
      if (route === "/api/layout") return json({ ok: true });   // nothing to save
      if (route === "/api/action")
        return json({ ok: false,
          error: "live demo — this opens your editor when you run cortex locally" });
      return json({ ok: false, error: "not available in the demo" });
    }

    switch (route) {
      case "/api/root":     return json(d.root);
      case "/api/children": return json(d.children[path] || []);
      case "/api/preview":  return json(d.previews[path] ||
                                  { kind: "error", message: "not in the demo" });
      case "/api/links":    return json(d.links);
      case "/api/git":      return json(d.git);
      case "/api/layout":   return json(d.layout);
      case "/api/editors":  return json({ editors: [] });
      case "/api/pulse":    return json({ changed: [], reindexed: false });
      case "/api/search":   return json(search(d, ix, q));
      case "/api/grep":     return json(grep(d, q));
      default:              return json({ error: "not found" });
    }
  };

  // The reader sets these straight onto src attributes, which never touch
  // fetch, so the few binary files are rewritten to real URLs at build time.
  DATA.then((d) => { window.__DEMO_RAW__ = d.raw || {}; });
})();
