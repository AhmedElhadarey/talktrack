// Drawings: SVG the builder has already cleaned, drawn inside a shadow root so the drawing's own
// <style> rules stay inside it. Elements with ids are what cues point at; the outermost ones are
// the parts a step reveal shows in source order, strokes drawing themselves in.

import { el, READABLE_PX, reducedMotion } from "../draw.js";
import { norm } from "../util.js";

const SHADOW_CSS = `
:host { display: block; width: 100%; height: 100%; }
svg { display: block; width: 100%; height: 100%; overflow: visible; }
.part { transition: opacity 0.45s ease; }
.part.veiled { opacity: 0; }
.ink { stroke-dasharray: 1; stroke-dashoffset: 0; transition: stroke-dashoffset 0.9s ease; }
.part.veiled .ink, .part.veiled.ink { stroke-dashoffset: 1; }
:host(.still) *, :host(.still) *::before { transition: none !important; animation: none !important; }
`;
const STROKES = "path, line, polyline, polygon, circle, ellipse, rect";

export default {
  render(slide, body, parts) {
    const v = slide.visual || {};
    const host = el("div", "drawing");
    if (v.background) { host.classList.add("page"); host.style.setProperty("--page", v.background); }
    host.setAttribute("role", "img");
    host.setAttribute("aria-label", v.title || slide.title || "Drawing");
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${SHADOW_CSS}</style>${v.svg || ""}`;
    hoistFonts(root);
    host.classList.toggle("still", document.body.classList.contains("capture") || reducedMotion());
    (v.ids || []).forEach((id, i) => {
      const e = root.getElementById(id);
      if (!e) return;
      e.classList.add("part");
      e.dataset.idx = i;
      e.dataset.id = id;
      parts.items.set(id, e);
    });
    parts.visual = host;
    parts.drawing = root.querySelector("svg");
    parts.shadow = root;
    parts.build = (slide.classes || []).includes("build");
    body.append(host);
  },

  // Once laid out: a drawing on its own page hugs the drawing's shape; on a step-reveal slide,
  // outlines without a fill draw themselves in.
  layout(body, parts) {
    const vb = parts.drawing?.viewBox.baseVal, host = parts.visual;
    if (host.classList.contains("page") && vb?.width && vb.height) {
      const pad = 36, room = [body.clientWidth - pad, body.clientHeight - pad], k = Math.min(room[0] / vb.width, room[1] / vb.height);
      host.style.width = `${Math.round(vb.width * k + pad)}px`;
      host.style.height = `${Math.round(vb.height * k + pad)}px`;
      host.style.margin = "auto";
    }
    if (!parts.build) return;
    for (const part of parts.items.values()) {
      for (const e of [part, ...part.querySelectorAll(STROKES)]) {
        if (!e.matches(STROKES)) continue;
        const cs = getComputedStyle(e);
        if (cs.fill !== "none" || cs.stroke === "none" || cs.strokeDasharray !== "none") continue;
        e.setAttribute("pathLength", "1");
        e.classList.add("ink");
      }
    }
  },

  target(cue, parts, help) {
    const root = parts.shadow;
    if (!root) return undefined;
    if (cue.kind === "item") {
      const e = root.getElementById(cue.id);
      return e ? { rects: [e.getBoundingClientRect()] } : help.wholeVisual();
    }
    if (cue.kind === "text" && cue.text) {
      const want = norm(cue.text);
      const t = [...root.querySelectorAll("text")].find((x) => norm(x.textContent).includes(want));
      return t ? { rects: [t.getBoundingClientRect()] } : help.wholeVisual();
    }
    if (cue.kind === "region") {
      const r = drawn(parts.drawing), [x, y, w, h] = cue.box;
      const left = r.left + (r.width * x) / 100, top = r.top + (r.height * y) / 100;
      const width = (r.width * w) / 100, height = (r.height * h) / 100;
      return { rects: [{ left, top, width, height, right: left + width, bottom: top + height }] };
    }
    return undefined;
  },

  // Text drawn smaller than the player reads comfortably, once the drawing is scaled to the slide.
  measure(slide, body, parts, report) {
    const svg = parts.drawing, vb = svg?.viewBox.baseVal;
    if (!vb || !vb.width) return true;
    const stage = parseFloat(document.getElementById("stage")?.dataset.scale) || 1;
    const k = drawn(svg).width / vb.width / stage;
    const sizes = [...parts.shadow.querySelectorAll("text")].map((t) => parseFloat(getComputedStyle(t).fontSize)).filter((x) => x > 0);
    const px = sizes.length ? Math.min(...sizes) * k : 0;
    if (px && px < READABLE_PX - 3) {
      report(`Slide ${slide.id}: the smallest text in the drawing is about ${Math.round(px)} px high on the slide, too small to read.`
        + " Use larger text, or a viewBox closer to the slide's size.", "text_too_small", slide.id);
    }
    return true;
  },
};

// The picture as drawn: the viewBox fitted inside the element, centred (preserveAspectRatio meet).
function drawn(svg) {
  const box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
  if (!vb || !vb.width || !vb.height) return box;
  const k = Math.min(box.width / vb.width, box.height / vb.height);
  return { left: box.left + (box.width - vb.width * k) / 2, top: box.top + (box.height - vb.height * k) / 2,
    width: vb.width * k, height: vb.height * k };
}

// Browsers ignore @font-face inside a shadow root, so embedded fonts (as in Excalidraw exports)
// are declared on the page, once each.
function hoistFonts(root) {
  const faces = [...root.querySelectorAll("svg style")].flatMap((s) => s.textContent.match(/@font-face\s*\{[^}]*\}/g) || []);
  if (!faces.length) return;
  let sheet = document.getElementById("drawing-fonts");
  if (!sheet) {
    sheet = document.createElement("style");
    sheet.id = "drawing-fonts";
    document.head.append(sheet);
  }
  for (const f of faces) if (!sheet.textContent.includes(f)) sheet.textContent += `${f}\n`;
}
