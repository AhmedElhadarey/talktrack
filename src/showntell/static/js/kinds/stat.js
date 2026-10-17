// Headline numbers that count up, from `before` when given, when the narration reaches them.

import { el, reducedMotion } from "../draw.js";
import { esc, inline } from "../util.js";

const STAT_COUNT_S = 1.2;

// `label: 5 -> 5` is a number that holds still: no "from" line, nothing to count.
const changed = (it) => it.before !== undefined && it.before !== it.after;

export function statValue(it, x) {
  const s = Number(x).toLocaleString("en", { minimumFractionDigits: it.decimals, maximumFractionDigits: it.decimals });
  return it.unit === "%" ? `${s}%` : it.unit ? `${s} ${it.unit}` : s;
}

// The number at progress f (0 to 1) of counting from `before` to `after`.
export function statText(it, f) {
  const from = it.before ?? 0;
  const ease = 1 - (1 - Math.max(0, Math.min(1, f))) ** 3;
  return statValue(it, from + (it.after - from) * ease);
}

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const g = el("div", "stats");
    g.style.gridTemplateColumns = `repeat(${ctx.frame === "portrait" ? 1 : Math.min(2, v.items.length) || 1}, minmax(0, 1fr))`;
    v.items.forEach((it, i) => {
      const card = el("div", "entry stat",
        `<div class="label">${inline(it.label)}</div><div class="num" aria-hidden="true">${esc(statText(it, 1))}</div>`
        + (changed(it) ? `<div class="was">from ${esc(statValue(it, it.before))}</div>` : ""));
      card.setAttribute("aria-label", `${it.label}: ${changed(it) ? `from ${statValue(it, it.before)} to ` : ""}${statValue(it, it.after)}`);
      card.dataset.id = it.id;
      card.dataset.idx = i;
      parts.items.set(it.id, card);
      g.append(card);
    });
    parts.visual = g;
    body.append(g);
  },

  // Each number counts when its cue fires, or from the slide's start when none are cued.
  frame(st, slide, parts, t) {
    const cued = new Map(st.cues.filter((c) => c.kind === "item").map((c) => [c.id, c._at]));
    const any = slide.says.some((x) => (x.cues || []).some((c) => c.kind === "item"));
    for (const it of slide.visual.items) {
      const el = parts.items.get(it.id)?.querySelector(".num");
      if (!el) continue;
      const at = cued.has(it.id) ? cued.get(it.id) : any && !st.revealAll ? null : t - st.sinceSlide;
      const text = at === null ? statText(it, 0) : statText(it, reducedMotion() ? 1 : (t - at) / STAT_COUNT_S);
      if (el.textContent !== text) el.textContent = text;
    }
  },
};
