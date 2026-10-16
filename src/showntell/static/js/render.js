// Build the DOM for one slide. Returns the element, a promise that resolves once it is laid
// out (diagrams and images load asynchronously), and handles the pointer and reveals use.

import { diffLines } from "./diff.js";
import { esc, inline, splitLines } from "./util.js";

const STATIC = document.body.dataset.static || "static";
const loaded = {};

function loadScript(src) {
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

function loadCss(href) {
  if (!loaded[href]) {
    const l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    document.head.append(l);
    loaded[href] = Promise.resolve();
  }
  return loaded[href];
}

async function mermaidReady() {
  await loadScript(`${STATIC}/vendor/mermaid.min.js`);
  if (!window.__mermaidInit) {
    window.__mermaidInit = true;
    const css = getComputedStyle(document.body);
    const v = (n) => css.getPropertyValue(n).trim();
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      fontFamily: v("--sans"),
      themeVariables: {
        darkMode: true,
        background: v("--stage"),
        fontSize: "20px",
        primaryColor: v("--node"),
        primaryBorderColor: v("--node-line"),
        primaryTextColor: v("--fg"),
        secondaryColor: v("--panel-2"),
        tertiaryColor: v("--panel"),
        lineColor: v("--muted"),
        textColor: v("--fg"),
        mainBkg: v("--node"),
        nodeBorder: v("--node-line"),
        clusterBkg: v("--panel"),
        clusterBorder: v("--line"),
        edgeLabelBackground: v("--stage"),
        actorBkg: v("--node"),
        actorBorder: v("--node-line"),
        actorTextColor: v("--fg"),
        signalColor: v("--muted"),
        signalTextColor: v("--fg"),
        labelBoxBkgColor: v("--node"),
        labelTextColor: v("--fg"),
        loopTextColor: v("--fg"),
        noteBkgColor: v("--note"),
        noteTextColor: v("--fg"),
        noteBorderColor: v("--accent"),
      },
      flowchart: { htmlLabels: true, curve: "basis", padding: 14 },
      sequence: { actorMargin: 60, messageFontSize: 18, actorFontSize: 18, noteFontSize: 16 },
    });
  }
  return window.mermaid;
}

async function katexReady() {
  loadCss(`${STATIC}/vendor/katex/katex.min.css`);
  await loadScript(`${STATIC}/vendor/katex/katex.min.js`);
  return window.katex;
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

function highlight(code, lang) {
  const hljs = window.hljs;
  try {
    if (hljs && lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    if (hljs && code.length < 4000) return hljs.highlightAuto(code).value;
  } catch (e) {
    /* fall through to plain text */
  }
  return esc(code);
}

// A code panel with real line numbers. marks: {removed:[bool], added:[bool]} for diffs.
function codeBox(code, lang, start, title, tag, side, marks) {
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

export function renderSlide(slide, ctx) {
  const parts = { lines: { old: new Map(), new: new Map() }, items: new Map(), rows: new Map(), visual: null, svg: null, wordRanges: [] };
  const root = el("section", `slide k-${slide.kind}`);
  root.setAttribute("aria-roledescription", "slide");
  if (slide.classes?.includes("build")) root.classList.add("build");
  let ready = Promise.resolve();

  if (slide.kind === "title") {
    root.innerHTML = `<div class="rule"></div><h1>${inline(slide.title)}</h1>${slide.shown ? `<p class="sub">${inline(slide.shown)}</p>` : ""}`;
    parts.visual = root.querySelector("h1");
    return { root, ready, parts };
  }

  const head = el("header", "slide-head", `<h2>${inline(slide.title)}</h2>${slide.shown && slide.kind !== "text" ? `<p>${inline(slide.shown)}</p>` : ""}`);
  const body = el("div", "slide-body");
  root.append(head, body);
  const v = slide.visual || {};

  switch (slide.kind) {
    case "code": {
      const { box, lines } = codeBox(v.code, v.lang, v.start || 1, v.title, null, "new", null);
      parts.lines.new = lines;
      parts.visual = box;
      body.append(box);
      break;
    }
    case "diff": {
      const wrap = el("div", "diff");
      const d = diffLines(v.old.code, v.new.code);
      const o = codeBox(v.old.code, v.lang, v.old.start || 1, v.old.title, "Before", "old", d.removed);
      const n = codeBox(v.new.code, v.lang, v.new.start || 1, v.new.title, "After", "new", d.added);
      parts.lines.old = o.lines;
      parts.lines.new = n.lines;
      wrap.append(o.box, n.box);
      parts.visual = wrap;
      body.append(wrap);
      ready = new Promise((res) => requestAnimationFrame(() => {
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
      break;
    }
    case "list": {
      const style = v.style || "bullets";
      const list = el(style === "cards" ? "div" : style === "steps" ? "ol" : "ul", `list ${style}`);
      if (style === "cards") {
        const n = v.items.length;
        const cols = ctx.frame === "portrait" ? 1 : ctx.frame === "square" ? (n <= 2 ? n : 2) : n <= 3 ? n : n === 4 ? 2 : 3;
        list.style.gridTemplateColumns = `repeat(${Math.max(1, cols)}, minmax(0, 1fr))`;
      }
      v.items.forEach((it, i) => {
        const item = el(style === "cards" ? "div" : "li", "entry",
          `<div class="entry-text">${it.title ? `<h3>${inline(it.title)}</h3>` : ""}${it.body ? `<p>${inline(it.body)}</p>` : ""}</div>`);
        if (style === "steps") item.dataset.n = i + 1;
        item.dataset.id = it.id;
        item.dataset.idx = i;
        parts.items.set(it.id, item);
        list.append(item);
      });
      parts.visual = list;
      body.append(list);
      break;
    }
    case "table": {
      const t = el("table", "data");
      const align = v.align || [];
      t.innerHTML = `<thead><tr>${v.head.map((h, i) => `<th style="text-align:${align[i] || "left"}">${inline(h)}</th>`).join("")}</tr></thead>`;
      const tb = el("tbody");
      v.rows.forEach((r, i) => {
        const tr = el("tr", "entry", r.map((c, k) => `<td style="text-align:${align[k] || "left"}">${inline(c)}</td>`).join(""));
        const id = (v.ids || [])[i] || `row-${i + 1}`;
        tr.dataset.id = id;
        tr.dataset.idx = i;
        parts.rows.set(id, tr);
        tb.append(tr);
      });
      t.append(tb);
      parts.visual = t;
      body.append(t);
      break;
    }
    case "diagram": {
      const d = el("div", "diagram");
      d.setAttribute("role", "img");
      d.setAttribute("aria-label", v.alt || `Diagram: ${Object.values(v.nodes || {}).join(", ")}`);
      parts.visual = d;
      body.append(d);
      ready = renderDiagram(d, v, slide, ctx).then((svg) => { parts.svg = svg; });
      break;
    }
    case "math": {
      const m = el("div", "math");
      parts.visual = m;
      body.append(m);
      ready = katexReady().then((katex) => {
        try {
          katex.render(v.latex, m, { displayMode: true, throwOnError: true });
        } catch (e) {
          ctx.report(`Formula on slide ${slide.id} failed to render: ${e.message}`);
          katex.render(v.latex, m, { displayMode: true, throwOnError: false });
        }
      }).catch((e) => ctx.report(String(e.message || e)));
      break;
    }
    case "image": {
      const wrap = el("div", "image");
      const img = el("img");
      img.alt = v.alt || slide.title || "";
      img.src = v.src;
      wrap.append(img);
      parts.visual = img;
      body.append(wrap);
      ready = img.decode().catch(() => ctx.report(`Image on slide ${slide.id} failed to load.`));
      break;
    }
    default: {
      const t = el("div", "textbody", (slide.shown || "").split(/\n+/).map((p) => `<p>${inline(p)}</p>`).join(""));
      parts.visual = t;
      body.append(t);
    }
  }
  return { root, ready, parts };
}

let seq = 0;
async function renderDiagram(container, v, slide, ctx) {
  let mermaid;
  try {
    mermaid = await mermaidReady();
  } catch (e) {
    container.innerHTML = `<div class="err">Diagrams are unavailable.</div>`;
    return null;
  }
  try {
    const { svg } = await mermaid.render(`mmd-${++seq}`, v.source);
    container.innerHTML = svg;
    const s = container.querySelector("svg");
    s.removeAttribute("style");
    s.setAttribute("aria-hidden", "true");
    return s;
  } catch (e) {
    container.innerHTML = `<div class="err">This diagram could not be drawn.</div>`;
    ctx.report(`Diagram on slide ${slide.id} failed to render: ${String(e.message || e).split("\n")[0]}`);
    document.querySelectorAll('[id^="dmmd-"]').forEach((n) => n.remove());
    return null;
  }
}

// Scale the diagram to the space it has (in stage pixels, not screen pixels).
export function sizeDiagram(svg, body) {
  if (!svg) return;
  const vb = svg.viewBox.baseVal;
  if (!vb || !vb.width) return;
  const bw = body.clientWidth, bh = body.clientHeight;
  const k = Math.min(bw / vb.width, bh / vb.height, 1.8);
  svg.setAttribute("width", Math.floor(vb.width * k));
  svg.setAttribute("height", Math.floor(vb.height * k));
}

// Shrink (or modestly grow) code, tables and formulas so they fit the slide body.
export function fit(body) {
  const content = body.firstElementChild;
  if (!content || content.matches(".diagram, .image")) return;
  content.style.zoom = 1;
  const bw = body.clientWidth, bh = body.clientHeight;
  const cw = content.scrollWidth, ch = content.scrollHeight;
  if (!cw || !ch) return;
  const grow = content.matches(".codebox, .diff, .math, table.data") ? 1.3 : 1;
  const z = Math.min(grow, bw / cw, bh / ch) * 0.98;
  if (Math.abs(z - 1) > 0.02) content.style.zoom = Math.max(0.35, z);
}

// Mermaid flowchart nodes: map node id -> <g> element.
export function diagramNodes(svg) {
  const out = new Map();
  if (!svg) return out;
  for (const g of svg.querySelectorAll("g.node, g.actor, g.stateGroup, g[id^='state-']")) {
    const m = (g.id || "").match(/^(?:flowchart|state)-(.+?)-\d+$/);
    if (m) out.set(m[1], g);
    else if (g.dataset?.id) out.set(g.dataset.id, g);
  }
  // Sequence diagrams: actor boxes carry the participant name.
  for (const t of svg.querySelectorAll("rect.actor")) {
    const name = t.getAttribute("name");
    if (name && !out.has(name)) out.set(name, t.parentElement);
  }
  return out;
}

// Edges with their endpoints, for step reveals: [{el, from, to, label}]. Mermaid names edge
// paths `L_<from>_<to>_<n>` and draws edge labels in the same order as the edges.
export function diagramEdges(svg, nodeIds) {
  const edges = [];
  if (!svg) return edges;
  const ids = new Set(nodeIds || diagramNodes(svg).keys());
  const labels = [...svg.querySelectorAll("g.edgeLabels > g.edgeLabel, g.edgeLabel")];
  const paths = [...svg.querySelectorAll("path.flowchart-link")];
  paths.forEach((p, i) => {
    let from = null, to = null;
    const m = (p.id || "").match(/^L_(.+)_\d+$/);
    if (m) {
      const body = m[1];
      for (let k = body.indexOf("_"); k > 0; k = body.indexOf("_", k + 1)) {
        const a = body.slice(0, k), b = body.slice(k + 1);
        if (ids.has(a) && ids.has(b)) { from = a; to = b; break; }
      }
    }
    edges.push({ el: p, from, to, label: labels.length === paths.length ? labels[i] : null });
  });
  return edges;
}
