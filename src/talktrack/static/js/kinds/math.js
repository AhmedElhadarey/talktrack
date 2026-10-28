// A formula, typeset with KaTeX.

import { el, loadCss, loadScript, STATIC } from "../draw.js";

async function katexReady() {
  await Promise.all([loadCss(`${STATIC}/vendor/katex/katex.min.css`), loadScript(`${STATIC}/vendor/katex/katex.min.js`)]);
  return window.katex;
}

export default {
  render(slide, body, parts, ctx) {
    const v = slide.visual || {};
    const m = el("div", "math");
    parts.visual = m;
    body.append(m);
    return katexReady().then((katex) => {
      try {
        katex.render(v.latex, m, { displayMode: true, throwOnError: true });
      } catch (e) {
        ctx.report(`Formula on slide ${slide.id} failed to render: ${e.message}`, "render_error", slide.id);
        katex.render(v.latex, m, { displayMode: true, throwOnError: false });
      }
    }).catch((e) => ctx.report(`Formula on slide ${slide.id}: ${e.message || e}`, "render_error", slide.id));
  },
};
