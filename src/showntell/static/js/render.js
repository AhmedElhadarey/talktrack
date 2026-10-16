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
        darkMode: v("--scheme") !== "light",
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
      flowchart: { htmlLabels: true, curve: "basis", padding: 14, subGraphTitleMargin: { top: 6, bottom: 10 } },
      sequence: { actorMargin: 60, messageFontSize: 18, actorFontSize: 18, noteFontSize: 16 },
    });
  }
  return window.mermaid;
}

async function katexReady() {
  await Promise.all([loadCss(`${STATIC}/vendor/katex/katex.min.css`), loadScript(`${STATIC}/vendor/katex/katex.min.js`)]);
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
        break;
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
          ctx.report(`Formula on slide ${slide.id} failed to render: ${e.message}`, "render_error", slide.id);
          katex.render(v.latex, m, { displayMode: true, throwOnError: false });
        }
      }).catch((e) => ctx.report(`Formula on slide ${slide.id}: ${e.message || e}`, "render_error", slide.id));
      break;
    }
    case "chart": {
      const c = el("div", "chart");
      c.setAttribute("role", "img");
      c.setAttribute("aria-label", chartLabel(v));
      c.innerHTML = drawChart(v);
      c.querySelectorAll("g.entry").forEach((g) => parts.items.set(g.dataset.id, g));
      parts.visual = c;
      parts.svg = null;
      body.append(c);
      break;
    }
    case "terminal": {
      const { box, lines } = terminalBox(v);
      parts.lines.new = lines;
      parts.visual = box;
      body.append(box);
      break;
    }
    case "tree": {
      const t = el("div", "tree");
      if (v.title) t.append(el("div", "tree-title", esc(v.title)));
      v.items.forEach((it, i) => {
        const row = el("div", `entry${it.folder ? " folder" : ""}`,
          `<span class="icon" aria-hidden="true">${it.folder ? FOLDER : FILE}</span><span class="name">${esc(it.name)}</span>${it.note ? `<span class="note">${inline(it.note)}</span>` : ""}`);
        row.style.paddingLeft = `${14 + it.depth * 30}px`;
        row.dataset.id = it.id;
        row.dataset.idx = i;
        parts.items.set(it.id, row);
        t.append(row);
      });
      parts.visual = t;
      body.append(t);
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
      ready = img.decode().catch(() => ctx.report(`Image on slide ${slide.id} failed to load.`, "render_error", slide.id));
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

// ---------------------------------------------------------------- terminal sessions

function terminalBox(v) {
  const box = el("figure", "codebox terminal");
  box.dataset.side = "new";
  box.append(el("figcaption", "file", `<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="name">${esc(v.title || "Terminal")}</span>`));
  const body = el("div", "code hljs");
  const lines = new Map();
  const start = v.start || 1;
  v.lines.forEach((ln, i) => {
    const n = start + i;
    const text = ln.cmd ? highlight(ln.text, "bash") : esc(ln.text);
    const row = el("div", `ln${ln.cmd ? " cmd" : " out"}`,
      `<span class="no" aria-hidden="true">${n}</span><span class="tx">${ln.cmd ? `<span class="prompt">${esc(ln.prompt)} </span>` : ""}${text || " "}</span>`);
    row.dataset.n = n;
    body.append(row);
    lines.set(n, row);
  });
  box.append(body);
  return { box, lines };
}

// ---------------------------------------------------------------- charts

const FOLDER = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.2l2 2h8.8A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" fill="currentColor"/></svg>';
const FILE = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M6 3h8l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zm7 1.5V9h4.5" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
const PALETTE = ["var(--accent)", "var(--node-line)", "var(--add-fg)", "var(--hl-title)", "var(--hl-built-in)", "var(--del-fg)"];

function chartLabel(v) {
  const head = v.title ? `${v.title}. ` : "";
  if (v.type === "line") return `${head}Line chart of ${v.series.map((s) => s.name).join(", ")} over ${v.x.join(", ")}.`;
  return `${head}Bar chart: ${v.categories.map((c) => `${c.label} ${c.values.join(" and ")}${v.unit ? ` ${v.unit}` : ""}`).join("; ")}.`;
}

// A round axis maximum and step: 1, 2 or 5 times a power of ten.
function niceScale(max, ticks = 5) {
  if (!(max > 0)) return { top: 1, step: 0.2 };
  const raw = max / ticks;
  const p = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw);
  return { top: Math.ceil(max / step - 1e-9) * step, step };
}

function fmt(n, unit) {
  const abs = Math.abs(n);
  const s = abs >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : abs >= 1e4 ? `${+(n / 1e3).toFixed(1)}k` : `${+n.toFixed(abs < 10 ? 2 : abs < 100 ? 1 : 0)}`;
  return unit ? `${s} ${unit}` : s;
}

function drawChart(v) {
  const W = 1100, H = 600;
  const t = (x, y, text, cls, anchor = "middle") => `<text x="${x}" y="${y}" class="${cls}" text-anchor="${anchor}">${esc(text)}</text>`;
  const all = v.type === "line" ? v.series.flatMap((s) => s.values) : v.categories.flatMap((c) => c.values);
  const lo = Math.min(0, ...all);
  const { top, step } = niceScale(Math.max(...all, 0) - lo);
  const head = v.title ? t(W / 2, 34, v.title, "ctitle") : "";
  const m = { l: 96, r: 40, t: v.title ? 70 : 30, b: 86 };
  if (v.type === "hbar") return drawHbar(v, W, H, m, top, step, head, t);
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const y = (val) => m.t + ih - ((val - lo) / top) * ih;
  let grid = "";
  for (let k = 0; k <= top / step + 1e-9; k++) {
    const val = lo + k * step;
    grid += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(val)}" y2="${y(val)}" class="grid"/>` + t(m.l - 12, y(val) + 7, fmt(val, ""), "tick", "end");
  }
  const axes = (v.ylabel ? `<text class="alabel" text-anchor="middle" transform="translate(24 ${m.t + ih / 2}) rotate(-90)">${esc(v.ylabel + (v.unit ? ` (${v.unit})` : ""))}</text>` : "")
    + (v.xlabel ? t(m.l + iw / 2, H - 14, v.xlabel, "alabel") : "");
  let marks = "";
  if (v.type === "line") {
    const n = v.x.length;
    const x = (i) => m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    v.x.forEach((xv, i) => { marks += t(x(i), m.t + ih + 34, xv, "tick"); });
    v.series.forEach((s, si) => {
      const col = PALETTE[si % PALETTE.length];
      const pts = s.values.map((val, i) => `${x(i)},${y(val)}`).join(" ");
      const last = s.values.length - 1;
      marks += `<g class="entry series" data-id="${esc(s.id)}" data-idx="${si}" style="--c:${col}">`
        + `<polyline points="${pts}" class="line"/>`
        + s.values.map((val, i) => `<circle cx="${x(i)}" cy="${y(val)}" r="7" class="dot"><title>${esc(`${s.name}: ${fmt(val, v.unit)}`)}</title></circle>`).join("")
        + (last >= 0 ? t(Math.min(W - m.r, x(last)) - 8, y(s.values[last]) - 16, s.name, "slabel", "end") : "")
        + "</g>";
    });
  } else {
    const n = v.categories.length, k = Math.max(1, ...v.categories.map((c) => c.values.length));
    const band = iw / Math.max(1, n), gap = band * 0.24, bw = (band - gap) / k;
    v.categories.forEach((c, ci) => {
      let g = `<g class="entry bar" data-id="${esc(c.id)}" data-idx="${ci}">`;
      c.values.forEach((val, si) => {
        const x0 = m.l + ci * band + gap / 2 + si * bw;
        const y0 = y(Math.max(0, val)), h = Math.abs(y(val) - y(0));
        g += `<rect x="${x0}" y="${y0}" width="${Math.max(2, bw - 6)}" height="${Math.max(1, h)}" rx="6" style="fill:${PALETTE[(k > 1 ? si : 0) % PALETTE.length]}"/>`
          + t(x0 + (bw - 6) / 2, y0 - 12, fmt(val, v.unit), "vlabel");
      });
      g += t(m.l + ci * band + band / 2, m.t + ih + 36, c.label, "tick") + "</g>";
      marks += g;
    });
    if (k > 1) marks += legend(v.series, m.l, H - 22);
  }
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${head}${grid}${axes}<line x1="${m.l}" x2="${W - m.r}" y1="${y(Math.max(lo, 0))}" y2="${y(Math.max(lo, 0))}" class="axis"/>${marks}</svg>`;
}

function drawHbar(v, W, H, m, top, step, head, t) {
  const labelW = Math.min(360, 24 + 13 * Math.max(...v.categories.map((c) => c.label.length)));
  m.l = labelW;
  const iw = W - m.l - m.r - 90, ih = H - m.t - 40;
  const n = v.categories.length, band = ih / Math.max(1, n), bh = band * 0.66;
  const x = (val) => m.l + (val / top) * iw;
  let marks = "";
  v.categories.forEach((c, ci) => {
    const val = c.values[0] ?? 0, y0 = m.t + ci * band + (band - bh) / 2;
    marks += `<g class="entry bar" data-id="${esc(c.id)}" data-idx="${ci}">`
      + t(m.l - 14, y0 + bh / 2 + 8, c.label, "tick", "end")
      + `<rect x="${m.l}" y="${y0}" width="${Math.max(2, x(val) - m.l)}" height="${bh}" rx="6" style="fill:${PALETTE[0]}"/>`
      + t(x(val) + 12, y0 + bh / 2 + 8, fmt(val, v.unit), "vlabel", "start") + "</g>";
  });
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${head}<line x1="${m.l}" x2="${m.l}" y1="${m.t}" y2="${m.t + ih}" class="axis"/>${marks}</svg>`;
}

function legend(series, x, y) {
  let out = "", cx = x;
  series.forEach((s, i) => {
    out += `<rect x="${cx}" y="${y - 16}" width="18" height="18" rx="4" style="fill:${PALETTE[i % PALETTE.length]}"/><text x="${cx + 26}" y="${y}" class="legend">${esc(s.name)}</text>`;
    cx += 60 + s.name.length * 12;
  });
  return out;
}

let seq = 0;
async function renderDiagram(container, v, slide, ctx) {
  let mermaid;
  try {
    mermaid = await mermaidReady();
  } catch (e) {
    container.innerHTML = `<div class="err">Diagrams are unavailable.</div>`;
    ctx.report(`Diagram on slide ${slide.id}: the diagram library did not load (${e.message || e}).`, "render_error", slide.id);
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
    ctx.report(`Diagram on slide ${slide.id} failed to render: ${String(e.message || e).split("\n")[0]}`, "render_error", slide.id);
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

// Shrink (or modestly grow) code, tables and formulas so they fit the slide body. A transform
// rather than CSS zoom: bounding boxes of text ranges inside a transform are reported exactly,
// which keeps the pointer on the words it points at.
export function fit(body) {
  const content = body.firstElementChild;
  if (!content || content.matches(".diagram, .image, .chart")) return;
  content.style.transform = "";
  const bw = body.clientWidth, bh = body.clientHeight;
  const cw = Math.max(content.offsetWidth, content.scrollWidth), ch = Math.max(content.offsetHeight, content.scrollHeight);
  if (!cw || !ch) return;
  const grow = content.matches(".codebox, .diff, .math, table.data, .tree") ? 1.3 : 1;
  let k = Math.min(grow, bw / cw, bh / ch) * 0.98;
  if (Math.abs(k - 1) <= 0.02) k = 1;
  k = Math.max(0.3, k);
  content.dataset.scale = k.toFixed(3);
  if (k !== 1) content.style.transform = `scale(${k})`;
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
  // Mermaid draws each participant twice; prefer the box at the top.
  for (const t of [...svg.querySelectorAll("rect.actor-top"), ...svg.querySelectorAll("rect.actor")]) {
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
