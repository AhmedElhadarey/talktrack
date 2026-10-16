import assert from "node:assert/strict";
import { test } from "node:test";
import { diffLines, lcs } from "../../src/showntell/static/js/diff.js";

test("lcs pairs equal items in order", () => {
  assert.deepEqual(lcs([1, 2, 3, 4], [2, 4, 5], (a, b) => a === b), [[1, 0], [3, 1]]);
});

test("removed and added lines", () => {
  const d = diffLines("a\nb\nc", "a\nB\nc\nd");
  assert.deepEqual(d.removed, [false, true, false]);
  assert.deepEqual(d.added, [false, true, false, true]);
});

test("word ranges for an edited line, none for a replaced one", () => {
  const d = diffLines("total = sum(xs)", "total = sum(ys)");
  assert.deepEqual(d.words.old, [[0, 12, 14]]);
  assert.deepEqual(d.words.new, [[0, 12, 14]]);
  const r = diffLines("print('hello world')", "return compute(a, b, c)");
  assert.deepEqual(r.words.old, []);
});
