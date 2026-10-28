// The title slide and chapter cards: the whole slide is the heading, no header or body. The title
// sits over a motif in the theme's palette (with the heading's icon, when it names one); a chapter
// card over its part number, drawn large in outline.

import { icon, motif } from "../deco.js";
import { FRAMES } from "../state.js";
import { inline } from "../util.js";

const size = (ctx) => FRAMES[ctx?.frame] || FRAMES.landscape;

export const title = {
  bare: true,
  render(slide, root, parts, ctx) {
    root.innerHTML = `${motif(slide.title, size(ctx), slide.icons?.[slide.icon])}<div class="rule"></div><h1>${inline(slide.title)}</h1>`
      + `${slide.shown ? `<p class="sub">${inline(slide.shown)}</p>` : ""}`;
    parts.visual = root.querySelector("h1");
  },
};

export const chapter = {
  bare: true,
  render(slide, root, parts, ctx) {
    const n = slide.visual?.n || "";
    root.innerHTML = `<div class="big-n" aria-hidden="true">${String(n).padStart(2, "0")}</div>`
      + `${icon(slide, slide.icon, "big")}<div class="chapter-n">Part ${n}</div><h1>${inline(slide.title)}</h1>`
      + `${slide.shown ? `<p class="sub">${inline(slide.shown)}</p>` : ""}`;
    parts.visual = root.querySelector("h1");
  },
};
