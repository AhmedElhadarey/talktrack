// Pipe tables; cues point at rows.

import { el, rtl } from "../draw.js";
import { inline } from "../util.js";

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const t = el("table", "data");
    // A plain |---| column is stored as "left"; in Arabic it should start on the right.
    const align = (v.align || []).map((a) => (a === "left" && rtl() ? "start" : a));
    t.innerHTML = `<thead><tr>${v.head.map((h, i) => `<th style="text-align:${align[i] || "start"}">${inline(h)}</th>`).join("")}</tr></thead>`;
    const tb = el("tbody");
    v.rows.forEach((r, i) => {
      const tr = el("tr", "entry", r.map((c, k) => `<td style="text-align:${align[k] || "start"}">${inline(c)}</td>`).join(""));
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
