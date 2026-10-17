// A screenshot or plot, optionally framed as a browser, window or phone. Region cues point at a
// box in percent of the picture.

import { el } from "../draw.js";
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
      wrap.append(img);
    }
    parts.visual = img;
    body.append(wrap);
    return img.decode().catch(() => ctx.report(`Image on slide ${slide.id} failed to load.`, "render_error", slide.id));
  },

  layout(body) { sizeImage(body); },

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

// A framed screenshot: limit the picture to the slide's height so the frame hugs it.
export function sizeImage(body) {
  const dev = body.querySelector(".device");
  const img = dev?.querySelector("img");
  if (!img) return;
  const bar = dev.querySelector(".bar")?.offsetHeight || 0;
  const edge = dev.offsetHeight - dev.clientHeight;
  img.style.maxHeight = `${Math.max(80, body.clientHeight - bar - edge - 4)}px`;
}
