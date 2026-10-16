import assert from "node:assert/strict";
import { test } from "node:test";
import { esc, inline, splitLines } from "../../src/showntell/static/js/util.js";

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
