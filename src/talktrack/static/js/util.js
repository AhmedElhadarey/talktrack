// Small pure helpers shared by the renderer and tests.

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Inline Markdown for on-screen text: code, bold, italic, links shown as text. Escapes first.
export function inline(md) {
  const parts = String(md ?? "").split(/(`+[^`]*?`+)/g);
  return parts
    .map((p) => {
      const m = p.match(/^(`+)([^`]*?)\1$/);
      if (m) return `<code class="inline">${esc(m[2])}</code>`;
      return esc(p)
        .replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, "$1")
        .replace(/\[([^\]]+)\]\{[^}]*\}/g, "$1")
        .replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_, a, b) => `<strong>${a ?? b}</strong>`)
        .replace(/(^|[^\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/(^|[^\w])_(?!\s)(.+?)(?<!\s)_(?!\w)/g, "$1<em>$2</em>");
    })
    .join("");
}

// Split highlighted HTML into lines, closing and reopening spans that cross line breaks.
export function splitLines(html) {
  const lines = [];
  const stack = [];
  let cur = "";
  const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+|<)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) { stack.push(m[1]); cur += m[1]; }
    else if (m[2]) { stack.pop(); cur += m[2]; }
    else if (m[3]) { cur += "</span>".repeat(stack.length); lines.push(cur); cur = stack.join(""); }
    else cur += m[4];
  }
  lines.push(cur);
  return lines;
}

export function norm(s) {
  return String(s ?? "").replace(/\s+/g, " ").trim().toLowerCase();
}

// A paragraph as caption HTML: each spoken word (display offsets c0, c1) in a span the player
// lights up as it is said. With `isolate` (right-to-left walkthroughs) each code range is also
// wrapped in a left-to-right isolate, so `npm run build` or `f(x)` keeps its order and brackets
// inside Arabic text. A range with an edge inside a word is left unwrapped.
export function captionHtml(say, isolate = false) {
  const text = say.text || "";
  const code = say.code || [];
  const isCode = (a, b) => code.some(([x, y]) => a >= x && b <= y);
  const words = [];
  let pos = 0;
  (say.words || []).forEach((w, i) => {
    if (w.c0 < pos || w.c0 < 0 || w.c1 > text.length) return;
    words.push({ ...w, i });
    pos = w.c1;
  });
  const marks = []; // [offset, tag], in order
  if (isolate) {
    const cuts = (k) => words.some((w) => k > w.c0 && k < w.c1);
    let end = -1;
    for (const [x, y] of [...code].sort((p, q) => p[0] - q[0])) {
      if (x < end || x < 0 || y > text.length || x >= y || cuts(x) || cuts(y)) continue;
      marks.push([x, '<span class="cr" dir="ltr">'], [y, "</span>"]);
      end = y;
    }
  }
  let m = 0;
  const plain = (from, to) => {
    let out = "", at = from;
    for (; m < marks.length && marks[m][0] <= to; m++) {
      out += esc(text.slice(at, marks[m][0])) + marks[m][1];
      at = Math.max(at, marks[m][0]);
    }
    return out + esc(text.slice(at, to));
  };
  let html = "";
  pos = 0;
  for (const w of words) {
    html += plain(pos, w.c0);
    html += `<span class="w${isCode(w.c0, w.c1) ? " code" : ""}" data-i="${w.i}">${esc(text.slice(w.c0, w.c1))}</span>`;
    pos = w.c1;
  }
  return html + plain(pos, text.length);
}
