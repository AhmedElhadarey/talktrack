// Pipe tables; cues point at rows.

import { el } from "../draw.js";
import { inline } from "../util.js";

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const t = el("table", "data");
    const align = v.align || [];
    t.innerHTML = `<thead><tr>${v.head.map((h, i) => `<th style="text-align:${align[i] || "left"}">${inline(h)}</th>`).join("")}</tr></thead>`;
    const tb = el("tbody");
    v.rows.forEach((r, i) => {
      const tr = el("tr", "entry", r.map((c, k) => `<td style="text-align:${align[k] || "left"}">${inline(c)}</td>`).join(""));
      const id = (v.ids || [])[i] || `row-${i + 1}`;
      tr.dataset.id = id;
      tr.dataset.idx = i;
      parts.rows.set(id, tr);
      tb.append(tr);
    });
    t.append(tb);
    parts.visual = t;
    body.append(t);
  },
};
