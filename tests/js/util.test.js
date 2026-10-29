import assert from "node:assert/strict";
import { test } from "node:test";
import { captionHtml, esc, inline, splitLines } from "../../src/talktrack/static/js/util.js";

test("escapes before formatting", () => {
  assert.equal(esc(`<a href="x">`), "&lt;a href=&quot;x&quot;&gt;");
  assert.equal(inline("**<b>** and `x < y`"), "<strong>&lt;b&gt;</strong> and <code class=\"inline\">x &lt; y</code>");
});

test("inline drops link targets and say overrides", () => {
  assert.equal(inline("[the loop](#L4) and [SQL]{say=\"sequel\"}"), "the loop and SQL");
});

test("splitLines reopens spans across line breaks", () => {
  const lines = splitLines('<span class="s">"a\nb"</span>\nc');
  assert.deepEqual(lines, ['<span class="s">"a</span>', '<span class="s">b"</span>', "c"]);
});

// Offsets of each space-separated word, as the narrator reports them.
const words = (text) => [...text.matchAll(/\S+/g)].map((m) => ({ c0: m.index, c1: m.index + m[0].length }));

test("captionHtml wraps each spoken word, code marked, text between kept", () => {
  const say = { text: "Run npm test now", words: words("Run npm test now"), code: [[4, 12]] };
  assert.equal(captionHtml(say),
    '<span class="w" data-i="0">Run</span> <span class="w code" data-i="1">npm</span> '
    + '<span class="w code" data-i="2">test</span> <span class="w" data-i="3">now</span>');
  // Words out of order or past the end are skipped, as before.
  assert.equal(captionHtml({ text: "a b", words: [{ c0: 2, c1: 3 }, { c0: 0, c1: 1 }, { c0: 2, c1: 9 }] }), 'a <span class="w" data-i="0">b</span>');
  assert.equal(captionHtml({ text: "x < y" }), "x &lt; y");
});

test("captionHtml isolates code ranges left to right in an Arabic caption", () => {
  const text = "شغّل npm run build وبعدين f(x) تاني";
  const at = (s) => [text.indexOf(s), text.indexOf(s) + s.length];
  const say = { text, words: words(text), code: [at("npm run build"), at("f(x)")] };
  const html = captionHtml(say, true);
  // The whole range is one isolate, so its words keep their order and its brackets their place.
  assert.match(html, /<span class="cr" dir="ltr"><span class="w code" data-i="1">npm<\/span> <span class="w code" data-i="2">run<\/span> <span class="w code" data-i="3">build<\/span><\/span>/);
  assert.match(html, /<span class="cr" dir="ltr"><span class="w code" data-i="5">f\(x\)<\/span><\/span>/);
  // Stripped of the isolates, it is the English builder's output: the word offsets still line up.
  assert.equal(html.replaceAll('<span class="cr" dir="ltr">', "").replaceAll("</span></span>", "</span>"), captionHtml(say));
  // A range whose edge falls inside a word is not wrapped.
  assert.doesNotMatch(captionHtml({ text, words: words(text), code: [[text.indexOf("npm") + 1, text.indexOf("npm") + 3]] }, true), /class="cr"/);
  // Plain text between words, and a range ending the paragraph.
  assert.equal(captionHtml({ text: "قول ls", code: [[4, 6]] }, true), 'قول <span class="cr" dir="ltr">ls</span>');
});
