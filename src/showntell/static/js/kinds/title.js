// The title slide and chapter cards: the whole slide is the heading, no header or body.

import { inline } from "../util.js";

export const title = {
  bare: true,
  render(slide, root, parts) {
    root.innerHTML = `<div class="rule"></div><h1>${inline(slide.title)}</h1>${slide.shown ? `<p class="sub">${inline(slide.shown)}</p>` : ""}`;
    parts.visual = root.querySelector("h1");
  },
};

export const chapter = {
  bare: true,
  render(slide, root, parts) {
    root.innerHTML = `<div class="chapter-n">Part ${slide.visual?.n || ""}</div><h1>${inline(slide.title)}</h1>${slide.shown ? `<p class="sub">${inline(slide.shown)}</p>` : ""}`;
    parts.visual = root.querySelector("h1");
  },
};
