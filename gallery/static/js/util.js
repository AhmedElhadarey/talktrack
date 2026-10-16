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
