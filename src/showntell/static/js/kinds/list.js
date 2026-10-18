// Lists: bullets, numbered steps, cards, a stack of layers, a timeline, or a checklist whose ticks draw in.

import { el } from "../draw.js";
import { inline } from "../util.js";

const TICK = '<svg viewBox="0 0 24 24" width="30" height="30"><rect x="2.5" y="2.5" width="19" height="19" rx="5" class="box"/><path d="M7 12.5l3.2 3.2L17 8.8" class="mark" pathLength="1"/></svg>';

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const style = v.style || "bullets";
    const list = el(style === "cards" || style === "stack" ? "div" : style === "steps" || style === "timeline" ? "ol" : "ul", `list ${style}`);
    // A timeline runs across a landscape slide, and down a tall one or when it has many dates.
    if (style === "timeline" && (ctx.frame !== "landscape" || v.items.length > 6)) list.classList.add("down");
    if (style === "cards") {
      const n = v.items.length;
      const cols = ctx.frame === "portrait" ? 1 : ctx.frame === "square" ? (n <= 2 ? n : 2) : n <= 3 ? n : n === 4 ? 2 : 3;
      list.style.gridTemplateColumns = `repeat(${Math.max(1, cols)}, minmax(0, 1fr))`;
    }
    v.items.forEach((it, i) => {
      const tick = style === "checklist" ? `<span class="tick${it.check ? " todo-done" : ""}" aria-hidden="true">${TICK}</span>` : "";
      const item = el(style === "cards" || style === "stack" ? "div" : "li", "entry",
        `${tick}<div class="entry-text">${it.title ? `<h3>${inline(it.title)}</h3>` : ""}${it.body ? `<p>${inline(it.body)}</p>` : ""}</div>`);
      if (style === "checklist") item.setAttribute("aria-label", `${it.check ? "Done" : "Not done"}: ${it.title || it.body}`);
      if (style === "stack") item.style.setProperty("--layer", i);
      if (style === "steps") item.dataset.n = i + 1;
      item.dataset.id = it.id;
      item.dataset.idx = i;
      parts.items.set(it.id, item);
      list.append(item);
    });
    parts.visual = list;
    body.append(list);
  },

  // Checklist ticks draw in as their item is pointed at, or all at once when none are.
  frame(st, slide, parts) {
    if (slide.visual.style !== "checklist") return;
    const cued = new Set(st.cues.filter((c) => c.kind === "item").map((c) => c.id));
    const any = slide.says.some((x) => (x.cues || []).some((c) => c.kind === "item"));
    const ticked = slide.visual.items.filter((it) => !!it.check && (cued.has(it.id) || !any || st.revealAll));
    for (const it of slide.visual.items) parts.items.get(it.id)?.classList.toggle("ticked", ticked.includes(it));
    return ticked.map((it) => it.id).join("|");
  },
};
