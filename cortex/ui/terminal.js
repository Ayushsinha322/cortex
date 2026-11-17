/* cortex — a terminal, for the tabs that hold one.

   The server runs a program on a pseudo-terminal and passes its bytes along
   (terminals.py). What those bytes mean is decided here: text to put on a
   grid of cells, and escape sequences that move the cursor about that grid,
   colour it, clear it, scroll part of it, or swap it for a second screen and
   back -- which is how an editor, or an agent's own interface, takes over
   the window and then returns it as it was.

   Two halves. Screen is the grid and the sequences, with no page in it, so
   it can be tested by feeding it bytes and reading the text back. mount()
   draws a Screen into an element and turns keys into what a program expects
   to read. It is written by hand rather than brought in, like the rest of
   cortex; what it understands is what shells, editors, pagers, test runners
   and the coding agents' interfaces send. */
(() => {
"use strict";

const BOLD = 1, DIM = 2, ITALIC = 4, UNDER = 8, INVERSE = 16, HIDDEN = 32, STRIKE = 64;
const DEFAULT = Object.freeze({ fg: -1, bg: -1, f: 0 });
const RGB = 0x1000000;                 // set on a colour that is not from the palette

const GROUND = 0, ESC = 1, CSI = 2, OSC = 3, OSC_ESC = 4, STR = 5, STR_ESC = 6,
      CHARSET = 7, SKIP = 8;

/* The line-drawing set a program selects with ESC ( 0: letters that stand
   for corners and edges. Older than Unicode, and still what ncurses sends. */
const LINES = { j: "┘", k: "┐", l: "┌", m: "└", n: "┼", q: "─", t: "├", u: "┤",
                v: "┴", w: "┬", x: "│", a: "▒", "`": "◆", f: "°", g: "±",
                o: "⎺", p: "⎻", r: "⎼", s: "⎽", "~": "·", y: "≤", z: "≥" };

/* How many cells a character takes: none for one that joins the character
   before it, two for the wide scripts and most emoji, one for the rest. */
function width(code) {
  if (code < 0x300) return 1;
  if ((code >= 0x300 && code <= 0x36f) || (code >= 0x200b && code <= 0x200f)
      || (code >= 0x20d0 && code <= 0x20ff) || (code >= 0xfe00 && code <= 0xfe0f)
      || code === 0xfeff || (code >= 0xe0100 && code <= 0xe01ef)) return 0;
  if ((code >= 0x1100 && code <= 0x115f) || (code >= 0x2e80 && code <= 0xa4cf)
      || (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff)
      || (code >= 0xfe30 && code <= 0xfe4f) || (code >= 0xff00 && code <= 0xff60)
      || (code >= 0xffe0 && code <= 0xffe6) || (code >= 0x1f300 && code <= 0x1f64f)
      || (code >= 0x1f680 && code <= 0x1f6ff) || (code >= 0x1f900 && code <= 0x1f9ff)
      || (code >= 0x20000 && code <= 0x3fffd)) return 2;
  return 1;
}

class Screen {
  constructor(cols = 80, rows = 24) {
    this.cols = cols;
    this.rows = rows;
    this.sbMax = 5000;
    this.onReply = null;           // what the program is told when it asks something
    this.fgColour = "#e6ebf4";     // what it is told when it asks what colours it is on
    this.bgColour = "#070910";
    this.reset();
  }

  reset() {
    this.attr = DEFAULT;
    this.lines = [];
    for (let i = 0; i < this.rows; i++) this.lines.push(this.blank());
    this.scrollback = [];
    this.scrolled = 0;             // rows ever pushed off the top; the page counts along
    this.cleared = 0;              // times the scrollback was thrown away
    this.x = 0; this.y = 0;
    this.pending = false;          // at the right edge, about to wrap
    this.top = 0; this.bottom = this.rows - 1;
    this.saved = null;
    this.alt = false; this.held = null;
    this.g = [false, false]; this.gl = 0; this.gsel = 0;
    this.wrap = true; this.origin = false;
    this.appCursor = false; this.cursorVisible = true;
    this.bracketed = false; this.focusEvents = false; this.mouse = false;
    this.title = ""; this.cwd = null;
    this.bells = 0; this.notes = [];
    this.st = GROUND; this.buf = ""; this.last = "";
  }

  blank() {
    const a = this.attr.bg === -1 ? DEFAULT : { fg: -1, bg: this.attr.bg, f: 0 };
    return { c: new Array(this.cols).fill(" "), a: new Array(this.cols).fill(a),
             dirty: true };
  }

  /* The screen as text, a string per row, for anything that wants to read
     it rather than look at it. */
  text() {
    return this.lines.map((r) => r.c.slice(0, this.cols).join("").replace(/\s+$/, ""));
  }

  write(s) {
    for (let i = 0; i < s.length; i++) {
      let code = s.charCodeAt(i), ch = s[i];
      if (code >= 0xd800 && code <= 0xdbff && i + 1 < s.length) {
        code = s.codePointAt(i);
        ch = s.slice(i, i + 2);
        i++;
      }
      this.feed(ch, code);
    }
  }

  feed(ch, code) {
    switch (this.st) {
      case GROUND:
        if (code === 0x1b) this.st = ESC;
        else if (code < 0x20 || code === 0x7f) this.control(code);
        else this.print(ch, code);
        return;
      case ESC:
        this.esc(ch);
        return;
      case CSI:
        if (code >= 0x40 && code <= 0x7e) { this.st = GROUND; this.csi(ch); }
        else if (code === 0x1b) this.st = ESC;
        else if (code < 0x20) this.control(code);
        else if (this.buf.length < 256) this.buf += ch;
        return;
      case OSC:
        if (code === 0x07) { this.st = GROUND; this.osc(); }
        else if (code === 0x1b) this.st = OSC_ESC;
        else if (this.buf.length < 4096) this.buf += ch;
        return;
      case OSC_ESC:
        this.st = GROUND;
        this.osc();
        if (ch !== "\\") { this.st = ESC; this.esc(ch); }
        return;
      case STR:
        if (code === 0x1b) this.st = STR_ESC;
        else if (code === 0x07) this.st = GROUND;
        return;
      case STR_ESC:
        this.st = GROUND;
        if (ch !== "\\") { this.st = ESC; this.esc(ch); }
        return;
      case CHARSET:
        if (this.gsel < 2) this.g[this.gsel] = ch === "0";
        this.st = GROUND;
        return;
      default:
        this.st = GROUND;
    }
  }

  control(code) {
    switch (code) {
      case 0x07: this.bells++; break;
      case 0x08: if (this.x > 0) this.x--; this.pending = false; break;
      case 0x09:
        this.x = Math.min(this.cols - 1, (Math.floor(this.x / 8) + 1) * 8);
        this.pending = false;
        break;
      case 0x0a: case 0x0b: case 0x0c: this.index(); break;
      case 0x0d: this.x = 0; this.pending = false; break;
      case 0x0e: this.gl = 1; break;
      case 0x0f: this.gl = 0; break;
    }
  }

  print(ch, code) {
    const w = width(code);
    if (w === 0) {
      // joins what came before it: an accent, a variation selector
      const row = this.lines[this.y];
      let at = this.pending ? this.x : this.x - 1;
      if (at >= 0 && row.c[at] === "" && at > 0) at--;
      if (at >= 0) { row.c[at] += ch; row.dirty = true; }
      return;
    }
    if (this.g[this.gl] && LINES[ch]) ch = LINES[ch];
    if (this.pending) {
      this.pending = false;
      if (this.wrap) { this.x = 0; this.index(); }
    }
    if (w === 2 && this.x === this.cols - 1) {
      if (!this.wrap) return;
      this.put(this.x, " ", this.attr);
      this.x = 0;
      this.index();
    }
    this.put(this.x, ch, this.attr);
    if (w === 2) this.put(this.x + 1, "", this.attr);
    this.last = ch;
    if (this.x + w >= this.cols) { this.x = this.cols - 1; this.pending = true; }
    else this.x += w;
  }

  /* Write one cell. Half of a wide character cannot be left standing when
     the other half is written over. */
  put(x, ch, attr) {
    const row = this.lines[this.y];
    if (row.c[x] === "" && x > 0) row.c[x - 1] = " ";
    else if (x + 1 < this.cols && row.c[x + 1] === "" && ch !== "") row.c[x + 1] = " ";
    row.c[x] = ch;
    row.a[x] = attr;
    row.dirty = true;
  }

  index() {
    this.pending = false;
    if (this.y === this.bottom) this.scrollUp(1, this.top, this.bottom, true);
    else if (this.y < this.rows - 1) this.y++;
  }

  reverseIndex() {
    this.pending = false;
    if (this.y === this.top) this.scrollDown(1, this.top, this.bottom);
    else if (this.y > 0) this.y--;
  }

  /* Rows leave at `top` and blank ones arrive at `bottom`. Only a row
     scrolled off the very top of the main screen is kept to scroll back
     to: a row an editor deletes mid-screen was never history. */
  scrollUp(n, top, bottom, keep) {
    for (n = Math.min(n, bottom - top + 1); n > 0; n--) {
      const [gone] = this.lines.splice(top, 1);
      if (keep && top === 0 && !this.alt) {
        this.scrollback.push(gone);
        this.scrolled++;
        if (this.scrollback.length > this.sbMax) this.scrollback.shift();
      }
      this.lines.splice(bottom, 0, this.blank());
    }
    for (let i = top; i <= bottom; i++) this.lines[i].dirty = true;
  }

  scrollDown(n, top, bottom) {
    for (n = Math.min(n, bottom - top + 1); n > 0; n--) {
      this.lines.splice(bottom, 1);
      this.lines.splice(top, 0, this.blank());
    }
    for (let i = top; i <= bottom; i++) this.lines[i].dirty = true;
  }

  erase(y, from, to) {
    const row = this.lines[y];
    // to the end of the line is to the end of what the row holds, which is
    // more than can be seen after the screen was narrowed
    if (to >= this.cols) to = Math.max(to, row.c.length);
    const a = this.attr.bg === -1 ? DEFAULT : { fg: -1, bg: this.attr.bg, f: 0 };
    for (let x = from; x < to; x++) { row.c[x] = " "; row.a[x] = a; }
    row.dirty = true;
  }

  saveCursor() {
    this.saved = { x: this.x, y: this.y, attr: this.attr, g: [...this.g], gl: this.gl };
  }

  restoreCursor() {
    const s = this.saved;
    if (!s) { this.x = 0; this.y = 0; return; }
    this.x = Math.min(s.x, this.cols - 1);
    this.y = Math.min(s.y, this.rows - 1);
    this.attr = s.attr; this.g = [...s.g]; this.gl = s.gl;
    this.pending = false;
  }

  /* The second screen: what a full-screen program draws on, so that leaving
     it puts back what was there before. It has no history of its own. */
  swap(on) {
    if (on === this.alt) return;
    if (on) {
      this.held = this.lines;
      this.lines = [];
      for (let i = 0; i < this.rows; i++) this.lines.push(this.blank());
    } else {
      this.lines = this.held;
      this.held = null;
      for (const r of this.lines) r.dirty = true;
    }
    this.alt = on;
    this.top = 0; this.bottom = this.rows - 1;
  }

  esc(ch) {
    this.st = GROUND;
    switch (ch) {
      case "[": this.buf = ""; this.st = CSI; break;
      case "]": this.buf = ""; this.st = OSC; break;
      case "P": case "^": case "_": case "X": this.st = STR; break;
      case "(": this.gsel = 0; this.st = CHARSET; break;
      case ")": this.gsel = 1; this.st = CHARSET; break;
      case "*": case "+": case "-": case ".": case "/": this.gsel = 2; this.st = CHARSET; break;
      case "#": case "%": case " ": this.st = SKIP; break;
      case "7": this.saveCursor(); break;
      case "8": this.restoreCursor(); break;
      case "D": this.index(); break;
      case "E": this.x = 0; this.index(); break;
      case "M": this.reverseIndex(); break;
      case "c": this.reset(); break;
    }
  }

  csi(final) {
    let b = this.buf, pre = "", inter = "";
    if (b && "?>=<".includes(b[0])) { pre = b[0]; b = b.slice(1); }
    while (b && b.charCodeAt(b.length - 1) >= 0x20 && b.charCodeAt(b.length - 1) <= 0x2f) {
      inter = b[b.length - 1] + inter;
      b = b.slice(0, -1);
    }
    if (final === "m" && !pre && !inter) { this.sgr(b); return; }
    const p = b === "" ? [] : b.split(/[;:]/).map((v) => v === "" ? NaN : parseInt(v, 10));
    const n = (i, d) => (p[i] === undefined || Number.isNaN(p[i])) ? d : p[i];
    const n1 = (i) => Math.max(1, n(i, 1));
    const col = (v) => Math.max(0, Math.min(this.cols - 1, v));
    const row = (v) => Math.max(0, Math.min(this.rows - 1, v));

    if (inter === "$" && final === "p" && pre === "?") {
      // "do you know this mode?" -- say so for the ones kept track of
      const m = n(0, 0);
      const on = { 1: this.appCursor, 7: this.wrap, 25: this.cursorVisible,
                   1049: this.alt, 2004: this.bracketed, 2026: false }[m];
      this.reply(`\x1b[?${m};${on === undefined ? 0 : on ? 1 : 2}$y`);
      return;
    }
    if (inter === "!" && final === "p") {       // soft reset
      this.attr = DEFAULT; this.top = 0; this.bottom = this.rows - 1;
      this.wrap = true; this.origin = false; this.cursorVisible = true;
      return;
    }
    if (inter) return;                          // cursor shapes and the like

    if (pre === "?") {
      if (final === "h" || final === "l") { for (const m of p) this.mode(m, final === "h"); return; }
      if (final === "n" && n(0, 0) === 6) { this.reply(`\x1b[?${this.y + 1};${this.x + 1}R`); return; }
      if (final !== "J" && final !== "K") return;
    } else if (pre === ">") {
      if (final === "c") this.reply("\x1b[>0;276;0c");
      return;
    } else if (pre) {
      return;                                   // a keyboard protocol we do not speak
    }

    switch (final) {
      case "A": this.y = Math.max(this.y - n1(0), this.y >= this.top ? this.top : 0); break;
      case "B": case "e":
        this.y = Math.min(this.y + n1(0), this.y <= this.bottom ? this.bottom : this.rows - 1);
        break;
      case "C": case "a": this.x = col(this.x + n1(0)); break;
      case "D": this.x = col(this.x - n1(0)); break;
      case "E": this.x = 0; this.y = row(this.y + n1(0)); break;
      case "F": this.x = 0; this.y = row(this.y - n1(0)); break;
      case "G": case "`": this.x = col(n1(0) - 1); break;
      case "d": this.y = row(n1(0) - 1 + (this.origin ? this.top : 0)); break;
      case "H": case "f":
        this.y = row(n1(0) - 1 + (this.origin ? this.top : 0));
        this.x = col(n1(1) - 1);
        break;
      case "J": {
        const how = n(0, 0);
        if (how === 0) {
          this.erase(this.y, this.x, this.cols);
          for (let y = this.y + 1; y < this.rows; y++) this.erase(y, 0, this.cols);
        } else if (how === 1) {
          for (let y = 0; y < this.y; y++) this.erase(y, 0, this.cols);
          this.erase(this.y, 0, this.x + 1);
        } else if (how === 2) {
          for (let y = 0; y < this.rows; y++) this.erase(y, 0, this.cols);
        } else if (how === 3) {
          this.scrollback = [];
          this.cleared++;
        }
        break;
      }
      case "K": {
        const how = n(0, 0);
        if (how === 0) this.erase(this.y, this.x, this.cols);
        else if (how === 1) this.erase(this.y, 0, this.x + 1);
        else this.erase(this.y, 0, this.cols);
        break;
      }
      case "X": this.erase(this.y, this.x, Math.min(this.cols, this.x + n1(0))); break;
      case "L":
        if (this.y >= this.top && this.y <= this.bottom) this.scrollDown(n1(0), this.y, this.bottom);
        break;
      case "M":
        if (this.y >= this.top && this.y <= this.bottom) this.scrollUp(n1(0), this.y, this.bottom, false);
        break;
      case "@": case "P": {
        const r = this.lines[this.y], k = Math.min(n1(0), this.cols - this.x);
        const a = this.attr.bg === -1 ? DEFAULT : { fg: -1, bg: this.attr.bg, f: 0 };
        if (final === "@") {
          const was = r.c.length;
          r.c.splice(this.x, 0, ...new Array(k).fill(" "));
          r.a.splice(this.x, 0, ...new Array(k).fill(a));
          r.c.length = was; r.a.length = was;
        } else {
          r.c.splice(this.x, k); r.a.splice(this.x, k);
          for (let i = 0; i < k; i++) { r.c.push(" "); r.a.push(a); }
        }
        r.dirty = true;
        break;
      }
      case "S": this.scrollUp(n1(0), this.top, this.bottom, true); break;
      case "T": this.scrollDown(n1(0), this.top, this.bottom); break;
      case "r": {
        const top = n(0, 1) - 1, bottom = n(1, this.rows) - 1;
        if (top >= 0 && bottom < this.rows && top < bottom) { this.top = top; this.bottom = bottom; }
        else { this.top = 0; this.bottom = this.rows - 1; }
        this.x = 0; this.y = this.origin ? this.top : 0;
        break;
      }
      case "h": case "l": break;                // insert mode and friends: not kept
      case "n":
        if (n(0, 0) === 5) this.reply("\x1b[0n");
        else if (n(0, 0) === 6) this.reply(`\x1b[${this.y + 1};${this.x + 1}R`);
        return;
      case "c": this.reply("\x1b[?1;2c"); return;
      case "s": this.saveCursor(); return;
      case "u": this.restoreCursor(); return;
      case "t": if (n(0, 0) === 18) this.reply(`\x1b[8;${this.rows};${this.cols}t`); return;
      case "b":
        if (this.last) for (let k = Math.min(n1(0), 2000); k > 0; k--) this.print(this.last, this.last.codePointAt(0));
        return;
      default: return;
    }
    this.pending = false;
  }

  mode(m, on) {
    switch (m) {
      case 1: this.appCursor = on; break;
      case 6: this.origin = on; this.x = 0; this.y = on ? this.top : 0; break;
      case 7: this.wrap = on; break;
      case 25: this.cursorVisible = on; break;
      case 47: case 1047: this.swap(on); break;
      case 1048: if (on) this.saveCursor(); else this.restoreCursor(); break;
      case 1049:
        if (on) { this.saveCursor(); this.swap(true); this.x = 0; this.y = 0; }
        else { this.swap(false); this.restoreCursor(); }
        break;
      case 1000: case 1002: case 1003: this.mouse = on; break;
      case 1004: this.focusEvents = on; break;
      case 2004: this.bracketed = on; break;
    }
  }

  /* Colours and weight. Either `38;2;r;g;b` with semicolons, or the newer
     `38:2::r:g:b` with colons and a place for a colour space nobody fills. */
  sgr(b) {
    let { fg, bg, f } = this.attr;
    const groups = b === "" ? [[0]] : b.split(";").map((g) =>
      g.split(":").map((v) => v === "" ? 0 : parseInt(v, 10) || 0));
    const flat = [];
    for (const g of groups) {
      if (g.length > 1 && (g[0] === 38 || g[0] === 48)) {
        const c = g[1] === 5 ? g[2] : g[1] === 2
          ? RGB | ((g[g.length - 3] & 255) << 16) | ((g[g.length - 2] & 255) << 8) | (g[g.length - 1] & 255)
          : -1;
        if (g[0] === 38) fg = c; else bg = c;
      } else if (g.length > 1 && g[0] === 4) {
        f = g[1] ? f | UNDER : f & ~UNDER;
      } else {
        flat.push(g[0]);
      }
    }
    for (let i = 0; i < flat.length; i++) {
      const v = flat[i];
      if (v === 0) { fg = -1; bg = -1; f = 0; }
      else if (v === 1) f |= BOLD;
      else if (v === 2) f |= DIM;
      else if (v === 3) f |= ITALIC;
      else if (v === 4) f |= UNDER;
      else if (v === 7) f |= INVERSE;
      else if (v === 8) f |= HIDDEN;
      else if (v === 9) f |= STRIKE;
      else if (v === 21 || v === 22) f &= ~(BOLD | DIM);
      else if (v === 23) f &= ~ITALIC;
      else if (v === 24) f &= ~UNDER;
      else if (v === 27) f &= ~INVERSE;
      else if (v === 28) f &= ~HIDDEN;
      else if (v === 29) f &= ~STRIKE;
      else if (v >= 30 && v <= 37) fg = v - 30;
      else if (v >= 40 && v <= 47) bg = v - 40;
      else if (v >= 90 && v <= 97) fg = v - 90 + 8;
      else if (v >= 100 && v <= 107) bg = v - 100 + 8;
      else if (v === 39) fg = -1;
      else if (v === 49) bg = -1;
      else if (v === 38 || v === 48) {
        let c = -1;
        if (flat[i + 1] === 5) { c = flat[i + 2] & 255; i += 2; }
        else if (flat[i + 1] === 2) {
          c = RGB | ((flat[i + 2] & 255) << 16) | ((flat[i + 3] & 255) << 8) | (flat[i + 4] & 255);
          i += 4;
        }
        if (v === 38) fg = c; else bg = c;
      }
    }
    this.attr = (fg === -1 && bg === -1 && f === 0) ? DEFAULT : { fg, bg, f };
  }

  osc() {
    const b = this.buf, k = b.indexOf(";");
    const code = k === -1 ? b : b.slice(0, k), rest = k === -1 ? "" : b.slice(k + 1);
    if (code === "0" || code === "2") {
      this.title = rest.slice(0, 200);
    } else if (code === "9") {
      // "tell the person": what an agent sends when it wants you. 9;4 is a
      // progress bar some programs draw in the title, and not that.
      if (rest && !/^4;/.test(rest)) this.notes.push(rest.slice(0, 300));
    } else if (code === "777") {
      const parts = rest.split(";");
      if (parts[0] === "notify") this.notes.push(parts.slice(1).filter(Boolean).join(" — ").slice(0, 300));
    } else if (code === "7") {
      const m = /^file:\/\/[^/]*(\/.*)$/.exec(rest);
      if (m) { try { this.cwd = decodeURIComponent(m[1]); } catch (err) { /* left as it was */ } }
    } else if ((code === "10" || code === "11") && rest === "?") {
      // "what colour is your background?" -- how a program picks a dark theme
      const c = (code === "10" ? this.fgColour : this.bgColour).slice(1);
      this.reply(`\x1b]${code};rgb:${[0, 2, 4].map((i) => c.slice(i, i + 2).repeat(2)).join("/")}\x1b\\`);
    }
  }

  reply(s) {
    if (this.onReply) this.onReply(s);
  }

  /* The colours the page is drawing this screen in, as #rrggbb. */
  setColours(fg, bg) {
    if (/^#[0-9a-f]{6}$/i.test(fg || "")) this.fgColour = fg;
    if (/^#[0-9a-f]{6}$/i.test(bg || "")) this.bgColour = bg;
  }

  /* The tab changed size. Text is not re-wrapped. A narrower screen shows
     less of each row but keeps all of it, so that widening it again shows
     what was there -- a pane squeezed for a moment loses nothing. When the
     screen gets shorter, what leaves is the top, into the history, so the
     line being typed on stays in sight. */
  resize(cols, rows) {
    if (cols === this.cols && rows === this.rows) return;
    const fit = (lines, moving) => {
      for (const r of lines) {
        while (r.c.length < cols) { r.c.push(" "); r.a.push(DEFAULT); }
        r.dirty = true;
      }
      while (lines.length > rows) {
        const end = lines[lines.length - 1];
        const empty = end.c.every((ch) => ch === " ");
        if (empty && (!moving || this.y < lines.length - 1)) { lines.pop(); continue; }
        const gone = lines.shift();
        if (moving) {
          if (!this.alt) { this.scrollback.push(gone); this.scrolled++; }
          this.y = Math.max(0, this.y - 1);
        }
      }
    };
    this.cols = cols;
    fit(this.lines, true);
    if (this.held) fit(this.held, false);
    this.rows = rows;
    while (this.lines.length < rows) this.lines.push(this.blank());
    if (this.held) while (this.held.length < rows) this.held.push(this.blank());
    this.top = 0; this.bottom = rows - 1;
    this.x = Math.min(this.x, cols - 1);
    this.y = Math.min(this.y, rows - 1);
    this.pending = false;
  }
}

/* What a key sends. Null for a key that types a character: that arrives
   through the text box, which is also how a composed character or a
   dictated word arrives. `app` is the mode in which arrows are sent the way
   full-screen programs ask for them. */
function keyBytes(e, app) {
  const k = e.key;
  if (e.ctrlKey && !e.altKey && !e.metaKey && k.length === 1) {
    const c = k.toLowerCase().charCodeAt(0);
    if (c >= 97 && c <= 122) return String.fromCharCode(c - 96);
    const odd = { "@": "\x00", " ": "\x00", "2": "\x00", "[": "\x1b", "\\": "\x1c",
                  "]": "\x1d", "^": "\x1e", "6": "\x1e", "_": "\x1f", "-": "\x1f",
                  "/": "\x1f", "?": "\x7f" }[k];
    if (odd !== undefined) return odd;
  }
  const mod = (e.shiftKey ? 1 : 0) + (e.altKey ? 2 : 0) + (e.ctrlKey ? 4 : 0);
  const cursor = (letter) => mod ? `\x1b[1;${mod + 1}${letter}`
    : app ? `\x1bO${letter}` : `\x1b[${letter}`;
  const tilde = (num) => mod ? `\x1b[${num};${mod + 1}~` : `\x1b[${num}~`;
  switch (k) {
    // shift-enter and alt-enter are "a new line, not a send" in the agents'
    // own interfaces, and this is the form they read that in
    case "Enter": return e.altKey || e.shiftKey ? "\x1b\r" : "\r";
    case "Backspace": return e.altKey ? "\x1b\x7f" : e.ctrlKey ? "\x08" : "\x7f";
    case "Tab": return e.shiftKey ? "\x1b[Z" : "\t";
    case "Escape": return "\x1b";
    case "ArrowUp": return cursor("A");
    case "ArrowDown": return cursor("B");
    case "ArrowRight": return cursor("C");
    case "ArrowLeft": return cursor("D");
    case "Home": return cursor("H");
    case "End": return cursor("F");
    case "Insert": return tilde(2);
    case "Delete": return tilde(3);
    case "PageUp": return tilde(5);
    case "PageDown": return tilde(6);
    case "F1": return "\x1bOP";
    case "F2": return "\x1bOQ";
    case "F3": return "\x1bOR";
    case "F4": return "\x1bOS";
    case "F5": return tilde(15);
    case "F6": return tilde(17);
    case "F7": return tilde(18);
    case "F8": return tilde(19);
    case "F9": return tilde(20);
    case "F10": return tilde(21);
    case "F11": return tilde(23);
    case "F12": return tilde(24);
  }
  if (e.altKey && !e.ctrlKey && !e.metaKey && k.length === 1) return "\x1b" + k;
  return null;
}

/* Colour 16 to 255 of the palette: a cube of 216, then 24 greys. */
function palette(i) {
  if (i < 232) {
    const v = i - 16, lv = [0, 95, 135, 175, 215, 255];
    return `rgb(${lv[Math.floor(v / 36)]},${lv[Math.floor(v / 6) % 6]},${lv[v % 6]})`;
  }
  const g = 8 + (i - 232) * 10;
  return `rgb(${g},${g},${g})`;
}
const rgb = (c) => `rgb(${(c >> 16) & 255},${(c >> 8) & 255},${c & 255})`;

/* A character the terminal's own font may not have, which the browser would
   then draw from another font at another width, pushing the rest of the row
   out of line. Those get a cell of exactly the right width each. Box and
   block drawing are left to flow: every monospace font has them. */
const loose = (code) => code > 0x2000 && !(code >= 0x2500 && code <= 0x259f);

/* Draw `screen` into `host` and send it what is typed.
   opts: send(text), resize(cols, rows), changed(screen) after each paint,
   key(event) -- asked first about every key, true if it has dealt with it --
   and focus() when this terminal is the one being typed into. */
function mount(host, screen, opts) {
  const doc = host.ownerDocument || document;
  const make = (tag, cls) => { const e = doc.createElement(tag); e.className = cls; return e; };
  const scroller = make("div", "t-scroll");
  const sb = make("div", "t-sb");
  const live = make("div", "t-live");
  const input = make("textarea", "t-in");
  input.setAttribute && input.setAttribute("aria-label", "terminal input");
  input.autocapitalize = "off"; input.autocomplete = "off"; input.spellcheck = false;
  scroller.append(sb, live);
  host.append(scroller, input);

  // what the screen already holds -- a window that reloaded, a pane moved to
  // another tab -- is drawn the first time round like anything else
  let rows = [], frame = 0, cleared = screen.cleared;
  let sbSeen = screen.scrolled - screen.scrollback.length;
  let cur = { x: -1, y: -1, on: false }, focused = false, cell = { w: 8, h: 17 };
  const SB_DOM = 3000;

  /* Whether the view is kept at the end as more is printed. It is, until the
     person scrolls back, and again once they return to the end or type.

     This is remembered, not read off the page each time. Read off the page
     it was wrong whenever the page had just forgotten: a pane taken out and
     put back to make room for another, a tab that was out of sight, come
     back scrolled to the very top -- and, looking like someone who had
     scrolled there, were left there while the prompt went on below. */
  let following = true, held = false, handled = 0;
  const atEnd = () => scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < cell.h * 2;
  const toEnd = () => { scroller.scrollTop = scroller.scrollHeight; };

  const style = (el, a) => {
    let fg = a.fg, bg = a.bg, cls = "";
    if (a.f & INVERSE) { const t = fg; fg = bg === -1 ? -2 : bg; bg = t === -1 ? -2 : t; }
    if (a.f & BOLD) cls += " t-b";
    if (a.f & DIM) cls += " t-d";
    if (a.f & ITALIC) cls += " t-i";
    if (a.f & UNDER) cls += " t-u";
    if (a.f & STRIKE) cls += " t-s";
    if (a.f & HIDDEN) cls += " t-h";
    if (fg === -2) cls += " t-fi";
    else if (fg >= 0 && fg < 16) cls += " t-f" + fg;
    else if (fg >= 16) el.style.color = fg & RGB ? rgb(fg) : palette(fg);
    if (bg === -2) cls += " t-gi";
    else if (bg >= 0 && bg < 16) cls += " t-g" + bg;
    else if (bg >= 16) el.style.backgroundColor = bg & RGB ? rgb(bg) : palette(bg);
    if (cls) el.className = (el.className + cls).trim();
  };

  /* One row, as runs of cells that look alike. Blank cells at the end of a
     row are not drawn at all. */
  const paint = (el, row, cx) => {
    el.textContent = "";
    let end = Math.min(row.c.length, screen.cols);
    while (end > 0 && end - 1 !== cx && row.c[end - 1] === " "
           && row.a[end - 1].bg === -1 && !(row.a[end - 1].f & INVERSE)) end--;
    let run = "", attr = null;
    const flush = () => {
      if (!run) return;
      if (attr === DEFAULT) el.appendChild(doc.createTextNode(run));
      else { const s = make("span", ""); style(s, attr); s.textContent = run; el.appendChild(s); }
      run = "";
    };
    for (let x = 0; x < end; x++) {
      const ch = row.c[x];
      if (ch === "") continue;                       // the second half of a wide one
      const a = row.a[x], code = ch.codePointAt(0);
      const odd = loose(code), here = x === cx;
      if (odd || here) {
        flush();
        const wide = x + 1 < row.c.length && row.c[x + 1] === "";
        const s = make("span", (odd ? (wide ? "t-w2" : "t-w1") : "") + (here ? " t-cur" : ""));
        style(s, a);
        s.textContent = ch;
        el.appendChild(s);
        continue;
      }
      if (a !== attr && !(attr && a.fg === attr.fg && a.bg === attr.bg && a.f === attr.f)) {
        flush();
        attr = a;
      }
      run += ch;
    }
    flush();
  };

  const draw = () => {
    frame = 0;
    if (screen.cleared !== cleared) { cleared = screen.cleared; sb.textContent = ""; }
    const fresh = Math.min(screen.scrolled - sbSeen, screen.scrollback.length);
    sbSeen = screen.scrolled;
    for (let i = screen.scrollback.length - fresh; i < screen.scrollback.length; i++) {
      const el = make("div", "t-row");
      paint(el, screen.scrollback[i], -1);
      sb.appendChild(el);
    }
    while (sb.childElementCount > SB_DOM && sb.firstChild) sb.removeChild(sb.firstChild);
    let made = false;
    while (rows.length < screen.rows) {
      const el = make("div", "t-row");
      live.appendChild(el);
      rows.push(el);
      made = true;
    }
    while (rows.length > screen.rows) { const el = rows.pop(); if (el.remove) el.remove(); }
    const on = screen.cursorVisible;
    const moved = cur.x !== screen.x || cur.y !== screen.y || cur.on !== on;
    for (let y = 0; y < screen.rows; y++) {
      const row = screen.lines[y];
      if (!made && !row.dirty && !(moved && (y === cur.y || y === screen.y))) continue;
      paint(rows[y], row, on && y === screen.y ? screen.x : -1);
      row.dirty = false;
    }
    cur = { x: screen.x, y: screen.y, on };
    if (following || fresh === 0 && screen.alt) toEnd();
    if (opts.changed) opts.changed(screen);
  };
  const refresh = () => {
    if (frame) return;
    const raf = (typeof requestAnimationFrame === "function") ? requestAnimationFrame
      : (fn) => setTimeout(fn, 16);
    frame = raf(draw) || 1;
  };

  /* How many cells fit, from the size one cell really is in this font.

     A terminal that is out of sight -- another tab on show, the pane put
     away -- is given no room, and "no room" is not a size: telling the
     program its screen is twenty columns wide, because that is the least it
     may be, cut every line it had printed to twenty. So a size that is not a
     real one changes nothing. */
  const fit = () => {
    const w = scroller.clientWidth - 14, h = scroller.clientHeight - 6;
    if (!(w > 0) || !(h > 0)) return false;
    const probe = make("span", "t-probe");
    probe.textContent = "MMMMMMMMMMMMMMMMMMMM";
    live.appendChild(probe);
    const box = probe.getBoundingClientRect();
    if (probe.remove) probe.remove();
    if (box.width > 0 && box.height > 0) cell = { w: box.width / 20, h: box.height };
    let cols = Math.floor(w / cell.w), rws = Math.floor(h / cell.h);
    if (cols < 8 || rws < 2) return false;            // too small to be meant
    // and never more than the window could show: if the box is ever sized by
    // what is in it, counting that as rows makes it grow without end
    const page = doc.documentElement || {};
    if (page.clientHeight > 0) rws = Math.min(rws, Math.ceil(page.clientHeight / cell.h));
    if (page.clientWidth > 0) cols = Math.min(cols, Math.ceil(page.clientWidth / cell.w));
    if (cols === screen.cols && rws === screen.rows) {
      if (following) toEnd();                         // back in sight, or a new height
      return false;
    }
    screen.resize(cols, rws);
    if (opts.resize) opts.resize(cols, rws);
    refresh();
    return true;
  };

  const send = (text) => {
    if (!text) return;
    following = true;
    toEnd();
    opts.send(text);
  };

  input.addEventListener("keydown", (e) => {
    e.stopPropagation();           // the keys are the program's, not cortex's
    if (e.isComposing) return;
    // ...unless whatever holds this terminal -- panes, modes -- wants one
    if (opts.key && opts.key(e)) { e.preventDefault(); input.value = ""; return; }
    if (e.shiftKey && (e.key === "PageUp" || e.key === "PageDown")) {
      e.preventDefault();
      scroller.scrollTop += (e.key === "PageUp" ? -1 : 1) * scroller.clientHeight * 0.9;
      following = atEnd();
      return;
    }
    if (e.ctrlKey && e.shiftKey && (e.key === "C" || e.key === "c")) {
      e.preventDefault();
      const picked = typeof getSelection === "function" ? String(getSelection()) : "";
      if (picked && navigator.clipboard) navigator.clipboard.writeText(picked).catch(() => {});
      return;
    }
    if (e.ctrlKey && e.shiftKey && (e.key === "V" || e.key === "v")) return;   // the paste event follows
    const bytes = keyBytes(e, screen.appCursor);
    if (bytes !== null) { e.preventDefault(); send(bytes); }
  });
  input.addEventListener("keyup", (e) => e.stopPropagation());
  const typed = () => { const v = input.value; if (v) { input.value = ""; send(v); } };
  input.addEventListener("input", (e) => { if (!e.isComposing) typed(); });
  input.addEventListener("compositionend", () => typed());
  input.addEventListener("paste", (e) => {
    const text = e.clipboardData ? e.clipboardData.getData("text") : "";
    e.preventDefault();
    if (!text) return;
    const body = text.replace(/\r\n?|\n/g, "\r");
    send(screen.bracketed ? `\x1b[200~${body}\x1b[201~` : body);
  });
  input.addEventListener("focus", () => {
    focused = true;
    host.classList.add("focus");
    if (opts.focus) opts.focus();
    if (screen.focusEvents) opts.send("\x1b[I");
  });
  input.addEventListener("blur", () => {
    focused = false;
    host.classList.remove("focus");
    if (screen.focusEvents) opts.send("\x1b[O");
  });
  // a click is for typing, unless it was the end of selecting some text
  host.addEventListener("mouseup", () => {
    const picked = typeof getSelection === "function" ? String(getSelection()) : "";
    if (!picked) input.focus();
  });
  // on the second screen there is nothing to scroll back to, so the wheel
  // moves what the program is showing, the way it does in a pager
  scroller.addEventListener("wheel", (e) => {
    if (!screen.alt) return;
    e.preventDefault();
    const key = keyBytes({ key: e.deltaY < 0 ? "ArrowUp" : "ArrowDown" }, screen.appCursor);
    opts.send(key.repeat(3));
  }, { passive: false });

  // Scrolling the person did -- the wheel, the bar, a finger, a selection
  // dragged past the edge -- decides whether the view follows. Scrolling the
  // page did to itself decides nothing.
  const byHand = () => { handled = Date.now(); };
  scroller.addEventListener("wheel", byHand, { passive: true });
  scroller.addEventListener("touchmove", byHand, { passive: true });
  scroller.addEventListener("pointerdown", () => { held = true; byHand(); });
  for (const up of ["pointerup", "pointercancel", "pointerleave"]) {
    scroller.addEventListener(up, () => { held = false; byHand(); });
  }
  scroller.addEventListener("scroll", () => {
    if (!scroller.clientHeight) return;
    if (held || Date.now() - handled < 1500) following = atEnd();
  });

  let watcher = null;
  if (typeof ResizeObserver === "function") {
    watcher = new ResizeObserver(() => fit());
    watcher.observe(host);
  }
  /* Move through what has been printed: a line, half a page, a page, or
     back to the end. */
  const scroll = (what) => {
    const page = scroller.clientHeight || cell.h * screen.rows;
    const by = { up: -cell.h, down: cell.h, "half-up": -page / 2, "half-down": page / 2,
                 "page-up": -page * 0.9, "page-down": page * 0.9 }[what];
    if (what === "bottom") { following = true; toEnd(); }
    else if (by) { scroller.scrollTop = (scroller.scrollTop || 0) + by; following = by > 0 && atEnd(); }
  };

  /* Find `term` in what has been printed, history and screen together.
     `way` is null for the first look (the nearest above the end), "down" or
     "up" from the last one found. Returns {index, count, of} or null. */
  let hit = { el: null, index: -1 };
  const find = (term, way, o = {}) => {
    if (hit.el) { hit.el.classList.remove("t-hit"); hit.el = null; }
    if (!term) { hit.index = -1; return null; }
    const lines = [...screen.scrollback, ...screen.lines];
    const flags = o.case ? "" : "i";
    const body = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const test = new RegExp(o.word ? `(^|\\W)${body}(\\W|$)` : body, flags);
    const found = [];
    lines.forEach((r, i) => { if (test.test(r.c.join(""))) found.push(i); });
    if (!found.length) { hit.index = -1; return { index: -1, count: 0, of: 0 }; }
    let at;
    if (way === "down") {
      at = found.find((i) => i > hit.index);
      if (at === undefined) at = o.wrap ? found[0] : hit.index;
    } else {
      const before = found.filter((i) => (hit.index === -1 ? true : i < hit.index));
      at = before.length ? before[before.length - 1]
        : o.wrap ? found[found.length - 1] : hit.index;
    }
    if (!found.includes(at)) at = found[found.length - 1];
    hit.index = at;
    const kept = sb.children.length;                 // the history's rows still on the page
    const inSb = at < screen.scrollback.length;
    const el = inSb ? sb.children[at - (screen.scrollback.length - kept)]
                    : rows[at - screen.scrollback.length];
    if (el) {
      el.classList.add("t-hit");
      hit.el = el;
      if (el.scrollIntoView) el.scrollIntoView({ block: "center" });
      following = false;                             // looking at what was found
    }
    return { index: at, count: found.length, of: found.indexOf(at) + 1 };
  };

  /* Where the view is, counted from the end, and going back there: for when
     the pane is taken off the page and put back, which forgets it. */
  const where = () => (following ? 0 : scroller.scrollHeight - (scroller.scrollTop || 0));
  const restore = (fromEnd) => {
    if (following || !fromEnd) toEnd();
    else scroller.scrollTop = scroller.scrollHeight - fromEnd;
  };

  refresh();
  return {
    fit, refresh, scroll, find, where, restore,
    following: () => following,
    focus: () => input.focus(),
    focused: () => focused,
    dispose: () => { if (watcher) watcher.disconnect(); },
  };
}

const api = { Screen, mount, keyBytes, width };
if (typeof window !== "undefined") window.Term = api;
if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
