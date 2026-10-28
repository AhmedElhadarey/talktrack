// A screenshot or plot, optionally framed as a browser, window or phone. Region cues point at a
// box in percent of the picture. Without them, the picture slowly pushes in towards a point picked
// from its name, on the narration clock.

import { el, reducedMotion } from "../draw.js";
import { esc } from "../util.js";

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const wrap = el("div", "image");
    const img = el("img");
    img.alt = v.alt || slide.title || "";
    img.src = v.src;
    if (v.frame) {
      const f = el("div", `device ${v.frame}`);
      if (v.frame === "browser") f.append(el("div", "bar", `<span class="dots"><i></i><i></i><i></i></span><span class="url">${esc(v.url || "")}</span>`));
      if (v.frame === "window") f.append(el("div", "bar", `<span class="dots"><i></i><i></i><i></i></span><span class="url">${esc(v.url || "")}</span>`));
      f.append(img);
      wrap.append(f);
    } else {
      const kb = el("div", "kb");
      kb.append(img);
      wrap.append(kb);
    }
    parts.visual = img;
    body.append(wrap);
    return img.decode().catch(() => ctx.report(`Image on slide ${slide.id} failed to load.`, "render_error", slide.id));
  },

  layout(body) { sizeImage(body); },

  // A region cue's box is measured once, so a picture that points at regions holds still.
  frame(st, slide, parts) {
    const img = parts.visual;
    if (img?.tagName !== "IMG" || reducedMotion() || slide.says.some((x) => (x.cues || []).some((c) => c.kind === "region"))) return "";
    const p = Math.min(1, Math.max(0, st.sinceSlide / DRIFT_S));
    const k = (1 + PUSH * (1 - (1 - p) ** 2)).toFixed(4);
    if (!img.style.transformOrigin) img.style.transformOrigin = ORIGINS[hash(slide.visual?.src || slide.id) % ORIGINS.length];
    img.style.transform = `scale(${k})`;
    return k;
  },

  target(cue, parts) {
    if (cue.kind !== "region" || parts.visual?.tagName !== "IMG") return undefined;
    // The picture as drawn: object-fit may leave bars inside the element.
    const box = parts.visual.getBoundingClientRect(), [x, y, w, h] = cue.box;
    const nw = parts.visual.naturalWidth || box.width, nh = parts.visual.naturalHeight || box.height;
    const k = Math.min(box.width / nw, box.height / nh);
    const r = { left: box.left + (box.width - nw * k) / 2, top: box.top + (box.height - nh * k) / 2, width: nw * k, height: nh * k };
    const left = r.left + (r.width * x) / 100, top = r.top + (r.height * y) / 100;
    const width = (r.width * w) / 100, height = (r.height * h) / 100;
    return { rects: [{ left, top, width, height, right: left + width, bottom: top + height }] };
  },
};

const DRIFT_S = 14; // seconds to push in fully
const PUSH = 0.06; // how much larger it ends up
const ORIGINS = ["30% 35%", "70% 35%", "50% 45%", "35% 65%", "65% 60%"];
const hash = (s) => [...s].reduce((h, c) => (Math.imul(h, 31) + c.charCodeAt(0)) >>> 0, 7);

// Limit the picture to the slide's height, so a frame, or the box that clips the push-in, hugs it.
export function sizeImage(body) {
  const kb = body.querySelector(".kb > img");
  if (kb) kb.style.maxHeight = `${Math.max(80, body.clientHeight)}px`;
  const dev = body.querySelector(".device");
  const img = dev?.querySelector("img");
  if (!img) return;
  const bar = dev.querySelector(".bar")?.offsetHeight || 0;
  const edge = dev.offsetHeight - dev.clientHeight;
  img.style.maxHeight = `${Math.max(80, body.clientHeight - bar - edge - 4)}px`;
}
