// DOM helpers shared by the slide kinds (kinds/*.js) and the renderer.

import { esc } from "./util.js";

export const STATIC = document.body.dataset.static || "static";
export const READABLE_PX = 15; // smallest comfortable text on the 1280-wide stage
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const loaded = {};

export function loadScript(src) {
  if (!loaded[src]) {
    loaded[src] = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error(`could not load ${src}`));
      document.head.append(s);
    });
  }
  return loaded[src];
}

export function loadCss(href) {
  if (!loaded[href]) {
    loaded[href] = new Promise((res) => {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = href;
      l.onload = l.onerror = res; // a missing stylesheet should not stop the slide
      document.head.append(l);
    });
  }
  return loaded[href];
}

export function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

export function highlight(code, lang) {
  const hljs = window.hljs;
  try {
    if (hljs && lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    if (hljs && code.length < 4000) return hljs.highlightAuto(code).value;
  } catch (e) {
    /* fall through to plain text */
  }
  return esc(code);
}

// DOM Range covering characters [start, end) of an element's text.
export function textRange(container, start, end) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let pos = 0, node, started = false;
  while ((node = walker.nextNode())) {
    const len = node.nodeValue.length;
    if (!started && start < pos + len) { range.setStart(node, start - pos); started = true; }
    if (started && end <= pos + len) { range.setEnd(node, end - pos); return range; }
    pos += len;
  }
  return started ? range : null;
}

// Shrink (or modestly grow) code, tables and formulas so they fit the slide body. A transform
// rather than CSS zoom: bounding boxes of text ranges inside a transform are reported exactly,
// which keeps the pointer on the words it points at.
export function fit(body) {
  const content = body.firstElementChild;
  if (!content || content.matches(".diagram, .image, .chart")) return;
  content.style.transform = "";
  const bw = body.clientWidth, bh = body.clientHeight;
  if (content.matches(".codebox.scroll")) {
    // Long code keeps its size and scrolls: fit the width only, and the box to the slide's height.
    content.style.maxHeight = "";
    const k = Math.max(0.6, Math.min(1.15, (bw / Math.max(content.offsetWidth, content.scrollWidth)) * 0.98));
    content.dataset.scale = k.toFixed(3);
    content.style.maxHeight = `${Math.floor(bh / k)}px`;
    content.style.transform = `scale(${k})`;
    return;
  }
  const cw = Math.max(content.offsetWidth, content.scrollWidth), ch = Math.max(content.offsetHeight, content.scrollHeight);
  if (!cw || !ch) return;
  const grow = content.matches(".codebox, .diff, .math, table.data, .tree") ? 1.3 : 1;
  let k = Math.min(grow, bw / cw, bh / ch) * 0.98;
  if (Math.abs(k - 1) <= 0.02) k = 1;
  k = Math.max(0.3, k);
  content.dataset.scale = k.toFixed(3);
  if (k !== 1) content.style.transform = `scale(${k})`;
}
