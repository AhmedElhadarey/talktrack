// Line and word differences between two versions of code. Pure functions.

export function lcs(a, b, eq) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = eq(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const pairs = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (eq(a[i], b[j])) { pairs.push([i, j]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return pairs;
}

const tokenize = (s) => s.match(/\w+|\s+|[^\w\s]/g) || [];

// Which lines were removed (old) and added (new), and character ranges of changed words
// inside lines that were edited rather than replaced.
export function diffLines(oldText, newText) {
  const a = oldText.split("\n"), b = newText.split("\n");
  const pairs = lcs(a, b, (x, y) => x.trim() === y.trim());
  const keptA = new Set(pairs.map((p) => p[0])), keptB = new Set(pairs.map((p) => p[1]));
  const removed = a.map((_, i) => !keptA.has(i));
  const added = b.map((_, j) => !keptB.has(j));
  const words = { old: [], new: [] }; // [lineIndex, start, end]
  const bounds = [[-1, -1], ...pairs, [a.length, b.length]];
  for (let k = 0; k < bounds.length - 1; k++) {
    const [a0, b0] = bounds[k], [a1, b1] = bounds[k + 1];
    const ra = [], rb = [];
    for (let i = a0 + 1; i < a1; i++) ra.push(i);
    for (let j = b0 + 1; j < b1; j++) rb.push(j);
    for (let t = 0; t < Math.min(ra.length, rb.length); t++) wordDiff(a[ra[t]], b[rb[t]], ra[t], rb[t], words);
  }
  return { removed, added, words };
}

function wordDiff(x, y, ia, ib, out) {
  const ta = tokenize(x), tb = tokenize(y);
  const pairs = lcs(ta, tb, (p, q) => p === q);
  const shared = pairs.filter(([i]) => ta[i].trim()).length;
  const total = Math.max(ta.filter((t) => t.trim()).length, tb.filter((t) => t.trim()).length);
  if (!total || shared / total < 0.4) return; // too different: the whole line is the change
  const keepA = new Set(pairs.map((p) => p[0])), keepB = new Set(pairs.map((p) => p[1]));
  const collect = (tokens, keep, line, list) => {
    let pos = 0;
    tokens.forEach((tok, i) => {
      if (!keep.has(i) && tok.trim()) list.push([line, pos, pos + tok.length]);
      pos += tok.length;
    });
  };
  collect(ta, keepA, ia, out.old);
  collect(tb, keepB, ib, out.new);
}
