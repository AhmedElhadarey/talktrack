// An exchange of messages: the asker on the right, as in chat apps.

import { el } from "../draw.js";
import { esc, inline } from "../util.js";

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const c = el("div", "chat");
    v.items.forEach((it, i) => {
      // Wire messages (JSON, HTTP lines) read better in the code font, kept as written.
      const wire = /^\s*([[{]|(GET|POST|PUT|PATCH|DELETE|HEAD|HTTP\/\d)\b)/.test(it.text);
      const textHtml = wire ? esc(it.text).replace(/\n/g, "<br>") : it.text.split("\n").map((l) => inline(l)).join("<br>");
      const m = el("div", `entry msg ${it.side}`,
        `<div class="who">${esc(it.role)}</div><div class="bubble${wire ? " wire" : ""}">${textHtml}</div>`);
      m.dataset.id = it.id;
      m.dataset.idx = i;
      parts.items.set(it.id, m);
      c.append(m);
    });
    parts.visual = c;
    body.append(c);
  },
};
