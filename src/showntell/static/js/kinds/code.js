// Code with real line numbers; `scroll` for long code, `step` blocks for code that evolves.

import { diffLines } from "../diff.js";
import { el, fit, highlight } from "../draw.js";
import { esc, splitLines } from "../util.js";

// A code panel with real line numbers. marks: {removed:[bool], added:[bool]} for diffs.
export function codeBox(code, lang, start, title, tag, side, marks) {
  const box = el("figure", "codebox");
  box.dataset.side = side;
  if (title || tag) {
    box.append(el("figcaption", "file", `<span class="name">${esc(title || "")}</span>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}`));
  }
  const body = el("div", "code hljs");
  const lines = new Map();
  splitLines(highlight(code, lang)).forEach((html, i) => {
    const n = start + i;
    const change = marks ? (marks[i] ? (side === "old" ? "del" : "add") : "") : "";
    const sign = change === "del" ? "−" : change === "add" ? "+" : "";
    const ln = el("div", `ln${change ? ` ${change}` : ""}`,
      `<span class="no" aria-hidden="true">${n}</span><span class="sign" aria-hidden="true">${sign}</span><span class="tx">${html || " "}</span>`);
    ln.dataset.n = n;
    body.append(ln);
    lines.set(n, ln);
  });
  box.append(body);
  return { box, lines };
}

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const tag = v.steps?.length > 1 ? `Step 1 of ${v.steps.length}` : null;
    const { box, lines } = codeBox(v.code, v.lang, v.start || 1, v.title, tag, "new", null);
    parts.lines.new = lines;
    parts.visual = box;
    if (v.scroll) box.classList.add("scroll");
    if (v.steps?.length > 1) {
      parts.step = 1;
      parts.setStep = (n, animate) => morphCode(parts, box, v, n, animate);
    }
    body.append(box);
  },
};

// Switch a code box to step n. Lines that stay glide to their new place, new lines fade in.
// Without `animate` (seeking, capture), the box simply shows step n.
export function morphCode(parts, box, v, n, animate) {
  n = Math.max(1, Math.min(v.steps.length, n));
  if (parts.step === n) return;
  const prev = v.steps[parts.step - 1], next = v.steps[n - 1];
  const oldEls = [...parts.lines.new.values()];
  const before = animate ? oldEls.map((e) => e.getBoundingClientRect().top) : null;
  const fresh = codeBox(next.code, v.lang, next.start || 1, next.title || v.title, `Step ${n} of ${v.steps.length}`, "new", null);
  box.replaceChildren(...fresh.box.childNodes);
  parts.lines.new = fresh.lines;
  parts.step = n;
  const bodyEl = box.closest(".slide-body");
  if (bodyEl) fit(bodyEl);
  if (!animate) return;
  // Pair unchanged lines in order; the rest are new.
  const d = diffLines(prev.code, next.code);
  const keptOld = oldEls.map((_, i) => i).filter((i) => !d.removed[i]);
  const newEls = [...fresh.lines.values()];
  const keptNew = newEls.map((_, i) => i).filter((i) => !d.added[i]);
  const k = parseFloat(box.dataset.scale || "1") * (parseFloat(document.getElementById("stage")?.dataset.scale) || 1);
  keptNew.forEach((ni, j) => {
    const oi = keptOld[j];
    if (oi === undefined) return;
    const el = newEls[ni];
    const dy = (before[oi] - el.getBoundingClientRect().top) / (k || 1);
    if (Math.abs(dy) < 0.5) return;
    el.style.transform = `translateY(${dy}px)`;
    el.style.transition = "none";
    requestAnimationFrame(() => { el.style.transition = ""; el.style.transform = ""; });
  });
  newEls.forEach((el, i) => { if (d.added[i]) el.classList.add("enter"); });
}
