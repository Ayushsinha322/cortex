/* Minimal, dependency-free markdown renderer.
   Handles what notes actually contain: headings, fences, lists (incl. tasks),
   tables, quotes, images, links and Obsidian-style [[wikilinks]].          */

(function () {
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ESC[c]);

  const RE = {
    fence:   /^(\s*)(`{3,}|~{3,})\s*([\w+-]*)\s*$/,
    heading: /^(#{1,6})\s+(.*)$/,
    hr:      /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/,
    quote:   /^\s*>\s?(.*)$/,
    li:      /^(\s*)(?:([-*+])|(\d+)[.)])\s+(.*)$/,
    task:    /^\[([ xX])\]\s+(.*)$/,
    tableSep:/^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/,
  };

  // ---------------------------------------------------------------- inline
  function inline(src, opts) {
    // Code spans are pulled out first and replaced with a NUL-delimited
    // placeholder, so the emphasis and link passes cannot rewrite their
    // contents. NUL is used because it cannot appear in the source text.
    const codes = [];
    let s = String(src).replace(/`([^`\n]+)`/g, (_, c) => {
      codes.push(c);
      return "\u0000C" + (codes.length - 1) + "\u0000";
    });

    s = esc(s);

    // images before links, so ![]() does not become a plain link
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      (_, alt, src2) => `<img alt="${alt}" src="${opts.resolve(src2)}">`);

    // [[wikilink]] and [[wikilink|label]]
    s = s.replace(/\[\[([^\]\|#]+)(?:#[^\]\|]*)?(?:\|([^\]]+))?\]\]/g,
      (_, target, label) => {
        const t = target.trim();
        const hit = opts.wiki(t);
        return `<a class="wiki${hit ? "" : " dead"}" data-wiki="${esc(t)}">`
             + `${esc(label || t)}</a>`;
      });

    s = s.replace(/\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
      (_, text, href) => {
        const external = /^(https?:|mailto:)/.test(href);
        return external
          ? `<a href="${href}" target="_blank" rel="noreferrer">${text}</a>`
          : `<a class="wiki" data-rel="${esc(href)}">${text}</a>`;
      });

    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noreferrer">${url}</a>`);

    s = s.replace(/\*\*\*(\S(?:.*?\S)?)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*(\S(?:.*?\S)?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = s.replace(/==([^=\n]+)==/g, "<mark>$1</mark>");

    return s.replace(/\u0000C(\d+)\u0000/g,
                     (_, i) => `<code>${esc(codes[i])}</code>`);
  }

  // ----------------------------------------------------------------- block
  function render(src, options) {
    const opts = Object.assign(
      { wiki: () => false, resolve: (u) => u }, options || {});
    const lines = String(src).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (!line.trim()) { i++; continue; }

      let m = line.match(RE.fence);
      if (m) {
        const close = m[2][0];
        const lang = m[3] || "";
        const buf = [];
        i++;
        while (i < lines.length &&
               !(lines[i].trim().startsWith(close.repeat(3)) &&
                 !lines[i].trim().slice(3).trim())) {
          buf.push(lines[i]); i++;
        }
        i++;
        const code = buf.join("\n");
        const body = (window.HL && lang)
          ? window.HL.highlight(code, lang) : esc(code);
        out.push(`<pre><code class="lang-${esc(lang)}">${body}</code></pre>`);
        continue;
      }

      m = line.match(RE.heading);
      if (m) {
        const lvl = m[1].length;
        out.push(`<h${lvl}>${inline(m[2], opts)}</h${lvl}>`);
        i++; continue;
      }

      if (RE.hr.test(line)) { out.push("<hr>"); i++; continue; }

      if (RE.quote.test(line)) {
        const buf = [];
        while (i < lines.length && RE.quote.test(lines[i])) {
          buf.push(lines[i].match(RE.quote)[1]); i++;
        }
        out.push(`<blockquote>${render(buf.join("\n"), opts)}</blockquote>`);
        continue;
      }

      if (line.includes("|") && i + 1 < lines.length &&
          RE.tableSep.test(lines[i + 1])) {
        const rows = [];
        const cells = (l) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "")
                              .split("|").map((c) => c.trim());
        const head = cells(line);
        i += 2;
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
          rows.push(cells(lines[i])); i++;
        }
        // A `| | |` header row is legal markdown; don't render an empty band.
        const hasHead = head.some((c) => c.trim());
        out.push("<table>" +
          (hasHead ? "<thead><tr>" +
            head.map((c) => `<th>${inline(c, opts)}</th>`).join("") +
            "</tr></thead>" : "") + "<tbody>" +
          rows.map((r) => "<tr>" +
            r.map((c) => `<td>${inline(c, opts)}</td>`).join("") + "</tr>").join("") +
          "</tbody></table>");
        continue;
      }

      if (RE.li.test(line)) { i = list(lines, i, out, opts); continue; }

      const para = [];
      while (i < lines.length && lines[i].trim() &&
             !RE.heading.test(lines[i]) && !RE.hr.test(lines[i]) &&
             !RE.fence.test(lines[i]) && !RE.quote.test(lines[i]) &&
             !RE.li.test(lines[i])) {
        para.push(lines[i]); i++;
      }
      // Soft newlines reflow (CommonMark); a break is explicit only when the
      // author asked for one with two trailing spaces or a backslash. Files
      // out in the wild are hard-wrapped, and honouring every newline shreds
      // their prose mid-sentence.
      const flowed = inline(para.join("\n"), opts)
        .replace(/(?:  |\\)\n/g, "<br>")
        .replace(/\n/g, " ");
      out.push(`<p>${flowed}</p>`);
    }

    return out.join("\n");
  }

  /* Consume one list block (with nesting) starting at `start`. */
  function list(lines, start, out, opts) {
    const first = lines[start].match(RE.li);
    const baseIndent = first[1].length;
    const ordered = !!first[3];
    const items = [];
    let i = start;

    while (i < lines.length) {
      const m = lines[i] && lines[i].match(RE.li);
      if (!m) {
        // a blank line inside a list is fine if a list item follows
        if (lines[i] !== undefined && !lines[i].trim() &&
            lines[i + 1] && RE.li.test(lines[i + 1])) { i++; continue; }
        break;
      }
      const indent = m[1].length;
      if (indent < baseIndent) break;
      if (indent > baseIndent) {
        const sub = [];
        const subStart = i;
        while (i < lines.length && lines[i] && RE.li.test(lines[i]) &&
               lines[i].match(RE.li)[1].length > baseIndent) { sub.push(lines[i]); i++; }
        const inner = [];
        list(sub.map((l) => l.slice(baseIndent + 2)), 0, inner, opts);
        if (items.length) items[items.length - 1].sub = inner.join("");
        else i = subStart + sub.length;
        continue;
      }
      items.push({ text: m[4], sub: "" });
      i++;
    }

    const body = items.map((it) => {
      const t = it.text.match(RE.task);
      if (t) {
        const done = t[1].toLowerCase() === "x";
        return `<li class="task"><input type="checkbox" disabled${done ? " checked" : ""}>`
             + inline(t[2], opts) + it.sub + "</li>";
      }
      return `<li>${inline(it.text, opts)}${it.sub}</li>`;
    }).join("");

    out.push(ordered ? `<ol>${body}</ol>` : `<ul>${body}</ul>`);
    return i;
  }

  window.MD = { render, inline, esc };
})();
