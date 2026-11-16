/* The terminal's half that has no page in it: bytes in, a grid of cells out.
 *
 * Each check feeds a Screen what a program would print and reads back the
 * text, the cursor, or the colour of a cell -- the sequences shells, editors,
 * pagers and the agents' own interfaces send, and the ways they go wrong at
 * the edges of the screen.
 *
 * Run: node tests/terminal.test.js   (or tests/run)
 */

const path = require("path");
const { Screen, keyBytes, width } = require(path.join(__dirname, "..", "cortex", "ui", "terminal.js"));
const { reporter } = require("./harness");

const { check, done } = reporter();
const E = "\x1b";
const scr = (cols = 20, rows = 5) => new Screen(cols, rows);
const fed = (text, cols, rows) => { const s = scr(cols, rows); s.write(text); return s; };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const key = (k, mods = {}) => keyBytes({ key: k, ctrlKey: false, altKey: false,
                                         shiftKey: false, metaKey: false, ...mods }, false);

// --- text, and where it goes -------------------------------------------------
let s = fed("hello\r\nworld");
check("text lands on rows, a line at a time", same(s.text().slice(0, 2), ["hello", "world"]));
check("and the cursor follows it", s.x === 5 && s.y === 1);

s = fed("a\nb");
check("a line feed alone goes down, not back", same(s.text().slice(0, 2), ["a", " b"]));

s = fed("12345", 5, 3);
check("a row filled to the edge does not wrap until there is more",
      s.y === 0 && same(s.text(), ["12345", "", ""]));
s.write("6");
check("and then it does", same(s.text(), ["12345", "6", ""]) && s.x === 1 && s.y === 1);

s = fed("ab\bc\tz");
check("backspace and tab move without writing", s.text()[0] === "ac      z");

s = fed("x" + E + "[3;5Hy" + E + "[1;1Hz");
check("the cursor is put where it is told", same(s.text().slice(0, 3), ["z", "", "    y"]));

s = fed(E + "[99;99Hq", 10, 4);
check("and never off the screen", s.text()[3] === "         q");

s = fed("abcdef" + E + "[3D" + E + "[K");
check("erase to the end of the line", s.text()[0] === "abc");
s = fed("abcdef" + E + "[3D" + E + "[1K");
check("erase to the start of it", s.text()[0] === "    ef");
s = fed("one\r\ntwo\r\nthree" + E + "[2;2H" + E + "[J");
check("erase from here down", same(s.text().slice(0, 3), ["one", "t", ""]));
s = fed("one\r\ntwo" + E + "[2J");
check("erase everything", s.text().every((r) => r === ""));

s = fed("abcdef" + E + "[1;3H" + E + "[2P");
check("delete characters closes the gap", s.text()[0] === "abef");
s = fed("abcdef" + E + "[1;3H" + E + "[2@");
check("insert characters opens one", s.text()[0] === "ab  cdef");
s = fed("abcdef" + E + "[1;2H" + E + "[3X");
check("erase characters blanks them where they are", s.text()[0] === "a   ef");
s = fed("a" + E + "[3b");
check("repeat prints the last character again", s.text()[0] === "aaaa");

// --- scrolling ----------------------------------------------------------------
s = fed("1\r\n2\r\n3\r\n4", 10, 3);
check("a full screen scrolls, and what leaves the top is kept to scroll back to",
      same(s.text(), ["2", "3", "4"]) && s.scrollback.length === 1
      && s.scrollback[0].c.join("").trim() === "1" && s.scrolled === 1);
s.write(E + "[3J");
check("until the program clears it", s.scrollback.length === 0 && s.cleared === 1);

s = fed("top\r\na\r\nb\r\nc\r\nbottom", 10, 5);
s.write(E + "[2;4r" + E + "[4;1H\nd");
check("a scroll region scrolls only itself",
      same(s.text(), ["top", "b", "c", "d", "bottom"]) && s.scrollback.length === 0);
s.write(E + "[2;1H" + E + "M");
check("and scrolls the other way from its top", same(s.text(), ["top", "", "b", "c", "bottom"]));

s = fed("1\r\n2\r\n3\r\n4", 10, 4);
s.write(E + "[2;1H" + E + "[L");
check("insert a line pushes the rest down", same(s.text(), ["1", "", "2", "3"]));
s.write(E + "[M");
check("delete a line pulls it back, and is not history",
      same(s.text(), ["1", "2", "3", ""]) && s.scrollback.length === 0);

// --- the second screen ----------------------------------------------------------
s = fed("shell$ vim", 20, 3);
s.write(E + "[?1049h" + E + "[2J" + E + "[Hfull screen");
check("a program takes the screen over", same(s.text(), ["full screen", "", ""]) && s.alt);
s.write("\r\n1\r\n2\r\n3\r\n4");
check("with no history of its own", s.scrollback.length === 0);
s.write(E + "[?1049l");
check("and gives it back as it was, cursor and all",
      same(s.text(), ["shell$ vim", "", ""]) && !s.alt && s.x === 10 && s.y === 0);

// --- colour and weight ----------------------------------------------------------
s = fed(E + "[1;31mred" + E + "[0m plain");
check("colour and bold are on the cells they were written with",
      s.lines[0].a[0].fg === 1 && (s.lines[0].a[0].f & 1) === 1
      && s.lines[0].a[4].fg === -1 && s.lines[0].a[4].f === 0);
s = fed(E + "[38;5;208ma" + E + "[48;2;10;20;30mb" + E + "[38:2::1:2:3mc" + E + "[39;49md");
const a = s.lines[0].a;
check("palette colours, true colours, and the colon way of writing them",
      a[0].fg === 208 && a[1].bg === (0x1000000 | (10 << 16) | (20 << 8) | 30)
      && a[2].fg === (0x1000000 | (1 << 16) | (2 << 8) | 3) && a[3].fg === -1 && a[3].bg === -1);
s = fed(E + "[97;104mx" + E + "[7my" + E + "[27mz");
check("bright colours and inverse", s.lines[0].a[0].fg === 15 && s.lines[0].a[0].bg === 12
      && (s.lines[0].a[1].f & 16) === 16 && (s.lines[0].a[2].f & 16) === 0);
s = fed(E + "[44m" + E + "[K");
check("erasing paints the background that is set", s.lines[0].a[10].bg === 4);

// --- characters that are not one cell wide --------------------------------------
check("how wide a character is", width(0x41) === 1 && width(0x4f60) === 2
      && width(0x301) === 0 && width(0x1f600) === 2 && width(0x2500) === 1);
s = fed("你好a");
check("a wide character takes two cells", same(s.lines[0].c.slice(0, 5), ["你", "", "好", "", "a"])
      && s.x === 5);
s.write(E + "[1;2Hx");
check("writing over half of one leaves no half behind", same(s.lines[0].c.slice(0, 3), [" ", "x", "好"]));
s = fed("abc你", 4, 2);
check("one that will not fit at the edge goes to the next row", same(s.text(), ["abc", "你"]));
s = fed("éx");
check("an accent joins the letter before it", s.lines[0].c[0] === "é" && s.lines[0].c[1] === "x");
s = fed(E + "(0lqk" + E + "(B lqk");
check("the line-drawing set, and leaving it", s.text()[0] === "┌─┐ lqk");

// --- what the program asks, and is told -----------------------------------------
const replies = [];
s = scr();
s.onReply = (r) => replies.push(r);
s.write("ab\r\nc" + E + "[6n" + E + "[c" + E + "[>c" + E + "]11;?" + E + "\\" + E + "[?2004$p" + E + "[18t");
check("where the cursor is", replies[0] === E + "[2;2R");
check("what kind of terminal this is", replies[1] === E + "[?1;2c" && replies[2] === E + "[>0;276;0c");
check("what colour the background is, so it can pick a dark theme",
      replies[3] === E + "]11;rgb:0707/0909/1010" + E + "\\");
check("whether a mode is known", replies[4] === E + "[?2004;2$y");
check("and how big it is", replies[5] === E + "[8;5;20t");

s = fed(E + "]0;make test\x07" + E + "]9;Claude needs you" + E + "\\" + E + "]9;4;1;50\x07"
        + E + "]777;notify;build;done\x07" + E + "]7;file://host/home/u/my%20proj\x07\x07");
check("a title, set either way a title is ended", s.title === "make test");
check("a program asking for you is heard, and a progress bar is not that",
      same(s.notes, ["Claude needs you", "build — done"]));
check("the folder the shell says it is in", s.cwd === "/home/u/my proj");
check("the bell is counted", s.bells === 1);

s = fed(E + "[?1h" + E + "[?25l" + E + "[?2004h" + E + "[?1004h");
check("modes a program sets are kept", s.appCursor && !s.cursorVisible && s.bracketed && s.focusEvents);

// --- what is not understood does no harm ------------------------------------------
s = fed("a" + E + "[?2026h" + E + "[>1u" + E + "[=1;1u" + E + "[<u" + E + "[0 q" + E + "P+q544e" + E + "\\"
        + E + "[?1006;1000h" + E + "=" + E + ">" + E + "[>4;2m" + E + "#8" + "b");
check("sequences it does not speak are passed over, leaving no litter", s.text()[0] === "ab"
      && s.lines[0].a[1].fg === -1);
s = scr();
s.write("x" + E);
s.write("[3");
s.write("1my");
check("a sequence cut in half by the pipe is still one sequence",
      s.text()[0] === "xy" && s.lines[0].a[1].fg === 1);
s = fed(E + "[1;31m" + E + "c" + "z");
check("a full reset really is one", s.text()[0] === "z" && s.lines[0].a[0].fg === -1);

// --- the tab changing size ---------------------------------------------------------
s = fed("1\r\n2\r\n3\r\n4\r\n5", 10, 5);
s.resize(10, 3);
check("a shorter screen keeps the line being typed on, and sends the top to history",
      same(s.text(), ["3", "4", "5"]) && s.y === 2 && s.scrollback.length === 2);
s = fed("ab\r\ncd", 10, 5);
s.resize(10, 3);
check("unless there was room below it", same(s.text(), ["ab", "cd", ""]) && s.y === 1
      && s.scrollback.length === 0);
s.resize(4, 6);
check("narrower and taller: cut, and padded", s.lines.length === 6 && s.lines[0].c.length === 4
      && s.bottom === 5);
s = fed(E + "[?1049h" + "alt", 10, 4);
s.resize(6, 2);
s.write(E + "[?1049l");
check("the screen underneath is resized with it", s.lines.length === 2 && s.lines[0].c.length === 6);

// --- keys ----------------------------------------------------------------------------
check("keys that type are left to the text box", key("a") === null && key("é") === null);
check("enter, tab, escape, backspace", key("Enter") === "\r" && key("Tab") === "\t"
      && key("Escape") === E && key("Backspace") === "\x7f");
check("ctrl and a letter", key("c", { ctrlKey: true }) === "\x03" && key("D", { ctrlKey: true }) === "\x04"
      && key("[", { ctrlKey: true }) === E);
check("arrows, plain and as full-screen programs ask for them",
      key("ArrowUp") === E + "[A" && keyBytes({ key: "ArrowUp" }, true) === E + "OA");
check("arrows with a modifier say which", key("ArrowRight", { ctrlKey: true }) === E + "[1;5C"
      && key("ArrowLeft", { shiftKey: true, altKey: true }) === E + "[1;4D");
check("the editing keys", key("Delete") === E + "[3~" && key("PageDown") === E + "[6~"
      && key("Home") === E + "[H" && key("F5") === E + "[15~" && key("F1") === E + "OP");
check("alt and a key is escape and the key", key("b", { altKey: true }) === E + "b");
check("shift-enter is a new line to the agents' interfaces", key("Enter", { shiftKey: true }) === E + "\r"
      && key("Tab", { shiftKey: true }) === E + "[Z");

done("terminal");
