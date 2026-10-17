// A change: before and after side by side, or one pane when only lines were added or removed.

import { diffLines } from "../diff.js";
import { el, fit, textRange } from "../draw.js";
import { codeBox } from "./code.js";

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const oldEmpty = !v.old.code.trim(), newEmpty = !v.new.code.trim();
    if (oldEmpty !== newEmpty) {
      // Only added (or only removed) lines: one pane says it more clearly than an empty one.
      const side = oldEmpty ? "new" : "old";
      const vv = v[side];
      const { box, lines } = codeBox(vv.code, v.lang, vv.start || 1, vv.title, oldEmpty ? "Added" : "Removed", side,
        vv.code.split("\n").map(() => true));
      parts.lines[side] = lines;
      parts.visual = box;
      body.append(box);
      return undefined;
    }
    const wrap = el("div", "diff");
    const d = diffLines(v.old.code, v.new.code);
    const o = codeBox(v.old.code, v.lang, v.old.start || 1, v.old.title, "Before", "old", d.removed);
    const n = codeBox(v.new.code, v.lang, v.new.start || 1, v.new.title, "After", "new", d.added);
    parts.lines.old = o.lines;
    parts.lines.new = n.lines;
    wrap.append(o.box, n.box);
    parts.visual = wrap;
    body.append(wrap);
    // Changed words inside changed lines, as ranges the player highlights once laid out.
    return new Promise((res) => requestAnimationFrame(() => {
      for (const [side, list] of [["old", d.words.old], ["new", d.words.new]]) {
        const starts = side === "old" ? v.old.start || 1 : v.new.start || 1;
        for (const [i, a, b] of list) {
          const tx = parts.lines[side].get(starts + i)?.querySelector(".tx");
          const r = tx && textRange(tx, a, b);
          if (r) parts.wordRanges.push([side, r]);
        }
      }
      res();
    }));
  },
  layout(body, parts) {
    fit(body);
    if (window.CSS?.highlights && parts.wordRanges.length) {
      CSS.highlights.set("diff-del", new Highlight(...parts.wordRanges.filter(([s]) => s === "old").map(([, r]) => r)));
      CSS.highlights.set("diff-add", new Highlight(...parts.wordRanges.filter(([s]) => s === "new").map(([, r]) => r)));
    }
  },
};
