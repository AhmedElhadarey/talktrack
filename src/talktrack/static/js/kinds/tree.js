// A file tree: folders and files, indented by depth, with optional notes.

import { el } from "../draw.js";
import { esc, inline } from "../util.js";

const FOLDER = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2l2 2h8.8A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" fill="currentColor"/></svg>';
const FILE = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M6 3h8l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm7 1.5V9h4.5" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const t = el("div", "tree");
    if (v.title) t.append(el("div", "tree-title", esc(v.title)));
    v.items.forEach((it, i) => {
      const row = el("div", `entry${it.folder ? " folder" : ""}`,
        `<span class="icon" aria-hidden="true">${it.folder ? FOLDER : FILE}</span><span class="name">${esc(it.name)}</span>${it.note ? `<span class="note">${inline(it.note)}</span>` : ""}`);
      row.style.paddingLeft = `${14 + it.depth * 30}px`;
      row.dataset.id = it.id;
      row.dataset.idx = i;
      parts.items.set(it.id, row);
      t.append(row);
    });
    parts.visual = t;
    body.append(t);
  },
};
