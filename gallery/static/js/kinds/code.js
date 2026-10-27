// Code with real line numbers; `scroll` for long code, `step` blocks for code that evolves.

import { diffLines, lcs, tokenize } from "../diff.js";
import { el, fit, highlight, textRange } from "../draw.js";
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
  const raw = code.split("\n");
  splitLines(highlight(code, lang)).forEach((html, i) => {
    const n = start + i;
    const change = marks ? (marks[i] ? (side === "old" ? "del" : "add") : "") : "";
    const sign = change === "del" ? "−" : change === "add" ? "+" : "";
    const ln = el("div", `ln${change ? ` ${change}` : ""}`,
      `<span class="no" aria-hidden="true">${n}</span><span class="sign" aria-hidden="true">${sign}</span><span class="tx">${html || " "}</span>`);
    ln.dataset.n = n;
    const indent = (raw[i] || "").replace(/\t/g, "    ").match(/^ */)[0].length;
    if (indent) ln.style.setProperty("--ind", indent); // a wrapped line continues under its own indent
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
      parts.steps = v.steps.length;
      parts.setStep = (n, animate) => morphCode(parts, box, v, n, animate);
    }
    body.append(box);
  },

  // Code that evolves is sized for its biggest version, so the scale holds still between steps.
  layout(body, parts) {
    if (parts.steps > 1) {
      const box = parts.visual, was = parts.step;
      box.style.minWidth = box.style.minHeight = "";
      let w = 0, h = 0;
      for (let n = 1; n <= parts.steps; n++) {
        parts.setStep(n, false);
        w = Math.max(w, box.offsetWidth);
        h = Math.max(h, box.offsetHeight);
      }
      box.style.minWidth = `${w}px`;
      if (!box.classList.contains("scroll")) box.style.minHeight = `${h}px`; // scrolling code takes the slide's height
      parts.setStep(was, false);
    }
    fit(body);
  },
};

// Switch a code box to step n. Lines that stay glide to their new place; in lines edited in
// place, words that stay glide too; everything else fades in. Without `animate` (seeking,
// capture, reduced motion), the box simply shows step n.
export function morphCode(parts, box, v, n, animate) {
  n = Math.max(1, Math.min(v.steps.length, n));
  if (parts.step === n) return;
  const prev = v.steps[parts.step - 1], next = v.steps[n - 1];
  const oldEls = [...parts.lines.new.values()];
  const before = animate ? oldEls.map((e) => e.getBoundingClientRect().top) : null;
  const d = animate ? diffLines(prev.code, next.code) : null;
  const edits = animate ? editedLines(d, prev.code.split("\n"), next.code.split("\n")) : [];
  const oldWords = edits.map(([ia]) => words(oldEls[ia]));
  const fresh = codeBox(next.code, v.lang, next.start || 1, next.title || v.title, `Step ${n} of ${v.steps.length}`, "new", null);
  box.replaceChildren(...fresh.box.childNodes);
  parts.lines.new = fresh.lines;
  parts.step = n;
  const bodyEl = box.closest(".slide-body");
  if (bodyEl) fit(bodyEl);
  if (!animate) return;
  // Pair unchanged lines in order; the rest are new.
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
  const glided = glideWords(box, edits, oldWords, newEls, k || 1);
  newEls.forEach((el, i) => { if (d.added[i] && !glided.has(i)) el.classList.add("enter"); });
}

// Lines edited in place: within each changed stretch, each old line with the most similar new
// line after the last one matched, when enough words are shared that moving them reads as an
// edit (the same 40% diffLines uses for word highlights). [[old index, new index, word pairs]].
function editedLines(d, a, b) {
  const out = [];
  const similar = (x, y) => {
    const ta = tokenize(x), tb = tokenize(y);
    const pairs = lcs(ta, tb, (p, q) => p === q).filter(([i]) => ta[i].trim());
    const total = Math.max(ta.filter((t) => t.trim()).length, tb.filter((t) => t.trim()).length);
    return { pairs, share: total ? pairs.length / total : 0 };
  };
  for (let i = 0, j = 0; i < a.length || j < b.length; i++, j++) {
    const ra = [], rb = [];
    while (i < a.length && d.removed[i]) ra.push(i++);
    while (j < b.length && d.added[j]) rb.push(j++);
    let from = 0;
    for (const ia of ra) {
      let best = null;
      for (let t = from; t < rb.length; t++) {
        const m = similar(a[ia], b[rb[t]]);
        if (m.share >= 0.4 && (!best || m.share > best.share)) best = { ...m, t };
      }
      if (best) { out.push([ia, rb[best.t], best.pairs]); from = best.t + 1; }
    }
  }
  return out;
}

// Where each word of a line is on screen, with its colour: [{start, rect, color}] by token index.
function words(lineEl) {
  const tx = lineEl?.querySelector(".tx");
  const out = new Map();
  if (!tx) return { out, font: "" };
  let pos = 0;
  tokenize(tx.textContent).forEach((tok, i) => {
    if (tok.trim()) {
      const r = textRange(tx, pos, pos + tok.length);
      if (r) out.set(i, { text: tok, rect: r.getBoundingClientRect(), color: getComputedStyle(r.startContainer.parentElement).color });
    }
    pos += tok.length;
  });
  return { out, font: getComputedStyle(tx).font };
}

// Copies of the old words, placed where they were, glide to where the same words now are;
// words that went fade out. The new line shows once they arrive. Returns the new line indexes.
function glideWords(box, edits, oldWords, newEls, k) {
  const done = new Set();
  if (!edits.length) return done;
  const layer = el("div", "ghosts");
  box.append(layer);
  const origin = layer.getBoundingClientRect();
  const lines = [];
  edits.forEach(([, ib, pairs], e) => {
    const line = newEls[ib], now = words(line).out, was = oldWords[e];
    const to = new Map(pairs.map(([x, y]) => [x, now.get(y)]));
    for (const [i, w] of was.out) {
      const g = el("span", "ghost");
      g.textContent = w.text;
      g.style.font = was.font;
      g.style.color = w.color;
      g.style.left = `${(w.rect.left - origin.left) / k}px`;
      g.style.top = `${(w.rect.top - origin.top) / k}px`;
      layer.append(g);
      const dest = to.get(i);
      requestAnimationFrame(() => {
        if (dest) g.style.transform = `translate(${(dest.rect.left - w.rect.left) / k}px, ${(dest.rect.top - w.rect.top) / k}px)`;
        else g.classList.add("gone");
      });
    }
    line.classList.add("morphing");
    lines.push(line);
    done.add(ib);
  });
  setTimeout(() => {
    lines.forEach((l) => { l.classList.remove("morphing"); l.classList.add("settle"); });
    setTimeout(() => layer.remove(), 220);
  }, 480);
  return done;
}
