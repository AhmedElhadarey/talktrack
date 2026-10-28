// A terminal session: commands after a prompt, then their output. Cues count lines as in code.

import { el, highlight } from "../draw.js";
import { esc } from "../util.js";

function terminalBox(v) {
  const box = el("figure", "codebox terminal");
  box.dataset.side = "new";
  box.append(el("figcaption", "file", `<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="name">${esc(v.title || "Terminal")}</span>`));
  const body = el("div", "code hljs");
  const lines = new Map();
  const start = v.start || 1;
  v.lines.forEach((ln, i) => {
    const n = start + i;
    const text = ln.cmd ? highlight(ln.text, "bash") : esc(ln.text);
    const row = el("div", `ln${ln.cmd ? " cmd" : " out"}`,
      `<span class="no" aria-hidden="true">${n}</span><span class="tx">${ln.cmd ? `<span class="prompt">${esc(ln.prompt)} </span>` : ""}${text || " "}</span>`);
    row.dataset.n = n;
    body.append(row);
    lines.set(n, row);
  });
  box.append(body);
  return { box, lines };
}

export default {
  render(slide, body, parts) {
    const { box, lines } = terminalBox(slide.visual || {});
    parts.lines.new = lines;
    parts.visual = box;
    body.append(box);
  },
};
