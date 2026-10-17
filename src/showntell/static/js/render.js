// Build the DOM for one slide. Returns the element, a promise that resolves once it is laid
// out (diagrams and images load asynchronously), and handles the pointer and reveals use.
//
// Each slide kind is a module in kinds/ whose default export has
//   render(slide, body, parts, ctx)  fills the slide body and `parts`; may return a promise
//                                    that resolves once it is drawn
// and, optionally,
//   bare: true                       render(slide, root, parts) draws the whole slide, no header
//   layout(body, parts)              size the visual once drawn (default: fit)
//   reveal(reveal, parts, veil)      step reveals other than items, rows and lines
//   target(cue, parts, help)         where a cue points ({rects, wide}), or undefined for the
//                                    generic pointer (lines, items, rows, text)
//   frame(st, slide, parts, t)       per-frame animation while playing or seeking
//   measure(slide, body, parts, report)  layout findings; true when it has checked the slide
// parts: visual (what the whole-slide pointer frames), lines {old, new} (line number -> row),
// items and rows (id -> element, with data-idx in source order), svg, wordRanges.

import { el } from "./draw.js";
import { inline } from "./util.js";
import chart from "./kinds/chart.js";
import chat from "./kinds/chat.js";
import code from "./kinds/code.js";
import diagram from "./kinds/diagram.js";
import diff from "./kinds/diff.js";
import image from "./kinds/image.js";
import list from "./kinds/list.js";
import math from "./kinds/math.js";
import stat from "./kinds/stat.js";
import svg from "./kinds/svg.js";
import table from "./kinds/table.js";
import terminal from "./kinds/terminal.js";
import { chapter, title } from "./kinds/title.js";
import tree from "./kinds/tree.js";

export const KINDS = { title, chapter, code, diff, list, table, diagram, math, chart, stat, chat, terminal, tree, image, svg };

// Narration-only slides: the shown text as paragraphs.
const text = {
  render(slide, body, parts) {
    const t = el("div", "textbody", (slide.shown || "").split(/\n+/).map((p) => `<p>${inline(p)}</p>`).join(""));
    parts.visual = t;
    body.append(t);
  },
};

export function kindOf(slide) {
  return KINDS[slide?.kind] || text;
}

export function renderSlide(slide, ctx) {
  const parts = { lines: { old: new Map(), new: new Map() }, items: new Map(), rows: new Map(), visual: null, svg: null, wordRanges: [] };
  const root = el("section", `slide k-${slide.kind}`);
  root.setAttribute("aria-roledescription", "slide");
  if (slide.classes?.includes("build")) root.classList.add("build");
  const kind = kindOf(slide);
  if (kind.bare) {
    kind.render(slide, root, parts, ctx);
    return { root, ready: Promise.resolve(), parts };
  }
  const head = el("header", "slide-head", `<h2>${inline(slide.title)}</h2>${slide.shown && slide.kind !== "text" ? `<p>${inline(slide.shown)}</p>` : ""}`);
  const body = el("div", "slide-body");
  root.append(head, body);
  const ready = kind.render(slide, body, parts, ctx) || Promise.resolve();
  return { root, ready, parts };
}
