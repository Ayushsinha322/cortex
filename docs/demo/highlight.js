/* Tiny syntax highlighter — one pass, no dependencies.
   Not a parser; a good-enough tokenizer for reading code in a side panel. */

(function () {
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ESC[c]);

  const KW = {
    python: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield self match case",
    javascript: "async await break case catch class const continue debugger default delete do else export extends finally for function if import in instanceof let new of return static super switch this throw try typeof var void while with yield true false null undefined",
    typescript: "async await any as break case catch class const continue declare default delete do else enum export extends finally for function if implements import in infer instanceof interface keyof let namespace new of private protected public readonly return static super switch this throw try type typeof var void while yield true false null undefined never unknown string number boolean",
    go: "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false make new len cap append error string int int64 float64 bool byte rune",
    rust: "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while String Vec Option Result Some None Ok Err",
    c: "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while NULL true false",
    cpp: "auto bool break case catch char class const constexpr continue default delete do double else enum explicit export extern false float for friend goto if inline int long namespace new nullptr operator private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while",
    java: "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new package private protected public return short static super switch synchronized this throw throws transient try void volatile while true false null var",
    ruby: "alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require",
    php: "abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global if implements include instanceof interface isset list namespace new or print private protected public require return static switch throw trait try unset use var while xor yield true false null",
    bash: "if then else elif fi for while until do done case esac function return in local export readonly declare source alias unset shift trap set eval exec exit break continue echo printf read test",
    sql: "select from where insert update delete into values create table alter drop index view join inner left right outer full on group by order having limit offset union all distinct as and or not null is in exists between like primary key foreign references default constraint cascade begin commit rollback with",
    lua: "and break do else elseif end false for function goto if in local nil not or repeat return then true until while",
    kotlin: "as break by class continue do else false for fun if import in interface is null object open override package private protected public return super this throw true try typealias val var when while",
    swift: "associatedtype class deinit enum extension fileprivate func import init inout internal let open operator private protocol public static struct subscript typealias var break case continue default defer do else fallthrough for guard if in repeat return switch where while as catch false is nil rethrows self super throw throws true try",
    hcl: "resource variable provider module output data locals terraform for_each count depends_on true false null var local",
    yaml: "true false null yes no on off",
    json: "true false null",
    toml: "true false",
    ini: "true false",
    css: "important media import keyframes from to and not only",
    html: "",
    xml: "",
    vim: "function endfunction if endif for endfor while endwhile let set call return au autocmd nnoremap inoremap vnoremap map noremap silent execute",
    powershell: "function param begin process end if elseif else switch foreach for while do until return break continue try catch finally throw class enum filter",
    makefile: "ifeq ifneq ifdef ifndef else endif include define endef export",
    docker: "FROM RUN CMD LABEL MAINTAINER EXPOSE ENV ADD COPY ENTRYPOINT VOLUME USER WORKDIR ARG ONBUILD STOPSIGNAL HEALTHCHECK SHELL AS",
  };
  KW.mjs = KW.cjs = KW.jsx = KW.javascript;
  KW.tsx = KW.typescript;
  KW.h = KW.hpp = KW.cc = KW.c;
  KW.zsh = KW.sh = KW.fish = KW.bash;
  KW.scss = KW.css;
  KW.text = "";

  // Comment syntax varies more than anything else; pick per language family.
  const HASH = new Set(["python", "bash", "sh", "zsh", "fish", "yaml", "toml",
    "ini", "ruby", "perl", "r", "makefile", "docker", "hcl", "conf", "powershell"]);
  const SLASH = new Set(["javascript", "typescript", "jsx", "tsx", "mjs", "cjs",
    "go", "rust", "c", "cpp", "h", "hpp", "cc", "java", "kotlin", "swift",
    "php", "css", "scss", "scala", "dart", "zig", "json"]);
  const DASH = new Set(["sql", "lua", "haskell"]);
  const QUOTE = new Set(["vim"]);

  function commentRe(lang) {
    if (HASH.has(lang)) return "#[^\\n]*";
    if (SLASH.has(lang)) return "\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*";
    if (DASH.has(lang)) return "--\\[\\[[\\s\\S]*?\\]\\]|--[^\\n]*";
    if (QUOTE.has(lang)) return '"[^\\n]*';
    return null;
  }

  const STRINGS =
    '"""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\'' +
    '|"(?:\\\\.|[^"\\\\\\n])*"' +
    "|'(?:\\\\.|[^'\\\\\\n])*'" +
    "|`(?:\\\\.|[^`\\\\])*`";

  const NUMBER = "\\b(?:0[xXbBoO][0-9a-fA-F_]+|\\d[\\d_]*(?:\\.\\d+)?(?:[eE][+-]?\\d+)?)\\b";
  const FUNC = "\\b[A-Za-z_]\\w*(?=\\s*\\()";
  const TYPE = "\\b[A-Z][A-Za-z0-9_]*\\b";
  const OP = "[=+\\-*/%<>!&|^~?:]+";

  const cache = new Map();

  function build(lang) {
    if (cache.has(lang)) return cache.get(lang);
    const words = (KW[lang] || "").trim().split(/\s+/).filter(Boolean);
    const parts = [];
    const kinds = [];
    const com = commentRe(lang);
    if (com) { parts.push(com); kinds.push("com"); }
    parts.push(STRINGS); kinds.push("str");
    if (words.length) {
      parts.push("\\b(?:" + words.join("|") + ")\\b");
      kinds.push("kw");
    }
    parts.push(NUMBER); kinds.push("num");
    parts.push(FUNC); kinds.push("fn");
    parts.push(TYPE); kinds.push("type");
    parts.push(OP); kinds.push("op");
    const re = new RegExp(parts.map((p) => "(" + p + ")").join("|"), "g");
    const compiled = { re, kinds };
    cache.set(lang, compiled);
    return compiled;
  }

  /* Split source into [{kind, text}] — kind is null for plain runs. */
  function tokens(code, lang) {
    lang = (lang || "text").toLowerCase();
    if (lang === "text" || lang === "plain") return [{ kind: null, text: code }];
    const { re, kinds } = build(lang);
    const out = [];
    let last = 0, m;
    re.lastIndex = 0;
    while ((m = re.exec(code)) !== null) {
      if (m.index > last) out.push({ kind: null, text: code.slice(last, m.index) });
      let kind = "op";
      for (let g = 1; g < m.length; g++) {
        if (m[g] !== undefined) { kind = kinds[g - 1]; break; }
      }
      out.push({ kind, text: m[0] });
      last = m.index + m[0].length;
      if (m[0].length === 0) re.lastIndex++;
    }
    if (last < code.length) out.push({ kind: null, text: code.slice(last) });
    return out;
  }

  function highlight(code, lang) {
    return tokens(code, lang).map(({ kind, text }) =>
      kind ? `<span class="tok-${kind}">${esc(text)}</span>` : esc(text)
    ).join("");
  }

  /* Code block with a gutter of line numbers.
     Tokens that straddle a newline (block comments, docstrings) are closed and
     reopened per line so the markup stays valid. */
  function block(code, lang) {
    const lines = [[]];
    for (const { kind, text } of tokens(code, lang)) {
      const chunks = text.split("\n");
      chunks.forEach((chunk, i) => {
        if (i > 0) lines.push([]);
        if (chunk) lines[lines.length - 1].push({ kind, text: chunk });
      });
    }
    const width = String(lines.length).length;
    return lines.map((toks, i) => {
      const gutter = `<span class="ln">${String(i + 1).padStart(width, " ")}</span>`;
      const body = toks.map(({ kind, text }) =>
        kind ? `<span class="tok-${kind}">${esc(text)}</span>` : esc(text)
      ).join("");
      return gutter + body;
    }).join("\n");
  }

  window.HL = { highlight, block, tokens, esc };
})();
